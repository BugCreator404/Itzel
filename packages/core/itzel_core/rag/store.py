"""store.py — Base vectorial local (ChromaDB) para el RAG de Itzel.

ES: Envuelve ChromaDB en modo PersistentClient (sin servidor). Todo en disco,
    en ~/.itzel/data/vectors/ por defecto. Decisiones clave:

      🔒 Telemetría DESACTIVADA (principio #2). ChromaDB envía analytics a
         PostHog por defecto; lo apagamos por Settings Y por variable de
         entorno (defensa en profundidad). Un test lo verifica.

      📐 Dimensión BLOQUEADA por colección. Guardamos el nombre del modelo de
         embeddings y su dimensión en los metadatos de la colección. Si el
         modelo cambia (p.ej. de nomic-embed-text 768d a all-MiniLM 384d), la
         colección queda marcada como incompatible y hay que re-indexar — nunca
         se corrompe el índice mezclando dimensiones.

      📏 Similitud COSENO (hnsw:space=cosine). score = 1 - distancia.

    Nosotros calculamos los embeddings (embeddings.py) y los pasamos explícitos
    a ChromaDB; la colección NO lleva embedding_function, así controlamos la
    dimensión y no dependemos de la API de embedding de cada versión de Chroma.

EN: Local persistent ChromaDB wrapper. Telemetry off, cosine space, dimension
    locked per collection via metadata. We compute embeddings ourselves.

Uso / Usage:
    from itzel_core.rag.store import get_store

    store = get_store()
    store.add(ids=["a:0"], documents=["hola"], embeddings=[[...]],
              metadatas=[{"source": "/docs/a.md"}])
    hits = store.query(query_embedding=[...], top_k=5)
    store.reset()        # borra el índice (itzel memory clear --vectors)
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..logger import log_engine
from . import require_rag
from .embeddings import EmbeddingProvider, get_embedding_provider

# ─── apagar telemetría ANTES de importar el cliente ────────────────────────────
# Defensa en profundidad: la variable de entorno cubre versiones de chromadb que
# no respeten Settings(anonymized_telemetry=False).
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_TELEMETRY_ENABLED", "False")


# ─── helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_store_dir(store_dir: str) -> Path:
    """Ruta absoluta del índice. Relativa → dentro de ~/.itzel/."""
    p = Path(store_dir).expanduser()
    if p.is_absolute():
        return p
    return Path.home() / ".itzel" / store_dir


class IncompatibleIndexError(RuntimeError):
    """La colección fue creada con otro modelo de embeddings (otra dimensión).

    El índice existente no se puede reusar; hay que re-indexar tras limpiar.
    """

    def __init__(self, stored: str, current: str) -> None:
        self.stored = stored
        self.current = current
        super().__init__(
            f"El índice fue creado con embeddings '{stored}' pero ahora está "
            f"activo '{current}'. Re-indexa tras limpiar: "
            "itzel memory clear --vectors && (re-indexar)"
        )


# ─── store ─────────────────────────────────────────────────────────────────────

class VectorStore:
    """Colección ChromaDB persistente, con dimensión bloqueada y coseno."""

    def __init__(
        self,
        store_dir:  Optional[str] = None,
        collection: Optional[str] = None,
        provider:   Optional[EmbeddingProvider] = None,
    ) -> None:
        require_rag()  # lanza RAGUnavailableError si falta chromadb

        try:
            from ..config import config
            store_dir  = store_dir  or config.rag.store_dir
            collection = collection or config.rag.collection
        except Exception:
            store_dir  = store_dir  or "data/vectors"
            collection = collection or "itzel_docs"

        self._dir = resolve_store_dir(store_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._collection_name = collection
        self._provider = provider or get_embedding_provider()

        self._client = self._make_client()
        self._collection = self._get_or_create_collection()

    # ── cliente ────────────────────────────────────────────────────────────────

    def _make_client(self):
        """PersistentClient con telemetría apagada (principio #2)."""
        import chromadb
        from chromadb.config import Settings

        return chromadb.PersistentClient(
            path=str(self._dir),
            settings=Settings(
                anonymized_telemetry=False,   # 🔒 nada de analytics
                allow_reset=True,             # permite reset() en tests
            ),
        )

    def _get_or_create_collection(self):
        """Abre la colección o la crea, validando compatibilidad de embeddings."""
        name = self._collection_name
        existing = {c.name for c in self._client.list_collections()}

        if name in existing:
            col = self._client.get_collection(name)
            meta = col.metadata or {}
            stored_model = meta.get("embed_model")
            # Si la colección ya tiene datos y el modelo no coincide → incompatible.
            if stored_model and stored_model != self._provider.name and col.count() > 0:
                raise IncompatibleIndexError(stored_model, self._provider.name)
            return col

        # Crear nueva: coseno + metadatos de bloqueo de dimensión.
        return self._client.create_collection(
            name=name,
            metadata={
                "hnsw:space":  "cosine",
                "embed_model": self._provider.name,
                "embed_dim":   self._provider.dimension,
                "created_at":  _now(),
                "creator":     "itzel-rag",
            },
        )

    # ── propiedades ──────────────────────────────────────────────────────────────

    @property
    def provider(self) -> EmbeddingProvider:
        return self._provider

    @property
    def path(self) -> Path:
        return self._dir

    @property
    def embed_model(self) -> str:
        return self._provider.name

    def count(self) -> int:
        """Número de fragmentos (chunks) indexados."""
        return self._collection.count()

    # ── escritura ────────────────────────────────────────────────────────────────

    def add(
        self,
        ids:        list[str],
        documents:  list[str],
        embeddings: Optional[list[list[float]]] = None,
        metadatas:  Optional[list[dict[str, Any]]] = None,
    ) -> None:
        """Inserta o actualiza fragmentos (upsert). Calcula embeddings si faltan."""
        if not ids:
            return
        if embeddings is None:
            embeddings = self._provider.embed_documents(documents)
        self._collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    def delete_ids(self, ids: list[str]) -> None:
        """Borra fragmentos por id."""
        if ids:
            self._collection.delete(ids=ids)

    def delete_by_source(self, source: str) -> int:
        """Borra todos los fragmentos de un archivo. Devuelve cuántos borró."""
        existing = self._collection.get(where={"source": source})
        ids = existing.get("ids", []) if existing else []
        if ids:
            self._collection.delete(ids=ids)
        return len(ids)

    # ── lectura ──────────────────────────────────────────────────────────────────

    def get_source_chunks(self, source: str) -> dict[str, str]:
        """Devuelve {chunk_id: content_hash} de los fragmentos ya indexados de un
        archivo, para decidir incrementalmente qué cambió."""
        res = self._collection.get(where={"source": source}, include=["metadatas"])
        out: dict[str, str] = {}
        for cid, meta in zip(res.get("ids", []), res.get("metadatas", []) or []):
            out[cid] = (meta or {}).get("content_hash", "")
        return out

    def query(
        self,
        query_embedding: list[float],
        top_k:           int = 5,
        where:           Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """Búsqueda por similitud coseno. Devuelve hasta top_k resultados.

        Cada resultado: {id, document, metadata, distance, score}
        donde score = 1 - distance (coseno) en [0, 1], mayor = más similar.
        """
        if self.count() == 0:
            return []
        res = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, self.count()),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        ids       = (res.get("ids")       or [[]])[0]
        docs      = (res.get("documents") or [[]])[0]
        metas     = (res.get("metadatas") or [[]])[0]
        dists     = (res.get("distances") or [[]])[0]

        out: list[dict[str, Any]] = []
        for i, cid in enumerate(ids):
            dist = dists[i] if i < len(dists) else 1.0
            out.append({
                "id":       cid,
                "document": docs[i]  if i < len(docs)  else "",
                "metadata": metas[i] if i < len(metas) else {},
                "distance": dist,
                "score":    max(0.0, 1.0 - dist),   # coseno → similitud
            })
        return out

    def source_signatures(self) -> dict[str, str]:
        """Devuelve {source: file_sig} de TODO el índice en una sola consulta.

        El indexador lo usa para saltarse archivos que no cambiaron (incremental)
        sin tener que re-leer ni re-embedear nada.
        """
        res = self._collection.get(include=["metadatas"])
        out: dict[str, str] = {}
        for meta in res.get("metadatas", []) or []:
            src = (meta or {}).get("source")
            if src and src not in out:
                out[src] = (meta or {}).get("file_sig", "")
        return out

    def sources(self) -> list[dict[str, Any]]:
        """Lista de archivos indexados con su conteo de fragmentos.

        Agrega por metadata['source']. Suficiente para un índice personal.
        """
        res = self._collection.get(include=["metadatas"])
        agg: dict[str, dict[str, Any]] = {}
        for meta in res.get("metadatas", []) or []:
            src = (meta or {}).get("source")
            if not src:
                continue
            entry = agg.setdefault(src, {
                "source":   src,
                "chunks":   0,
                "file_type": (meta or {}).get("file_type", ""),
                "modified": (meta or {}).get("modified", ""),
            })
            entry["chunks"] += 1
        return sorted(agg.values(), key=lambda e: e["source"])

    # ── mantenimiento ────────────────────────────────────────────────────────────

    def reset(self) -> int:
        """Borra TODO el índice vectorial (itzel memory clear --vectors).

        Devuelve cuántos fragmentos había. Recrea una colección vacía con los
        metadatos del proveedor actual.
        """
        n = self.count()
        self._client.delete_collection(self._collection_name)
        self._collection = self._get_or_create_collection()
        log_engine.info(
            "Índice vectorial borrado (%d fragmentos)", n,
            extra={"component": "rag"},
        )
        return n

    def stats(self) -> dict[str, Any]:
        """Resumen del índice para diagnóstico / UI."""
        srcs = self.sources()
        return {
            "path":        str(self._dir),
            "collection":  self._collection_name,
            "embed_model": self._provider.name,
            "embed_dim":   self._provider.dimension,
            "chunks":      self.count(),
            "documents":   len(srcs),
            "telemetry":   "off",
        }


# ─── singleton ─────────────────────────────────────────────────────────────────

_store: Optional[VectorStore] = None
_store_lock = threading.Lock()


def get_store() -> VectorStore:
    """Devuelve el VectorStore global (singleton thread-safe)."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = VectorStore()
    return _store


def reset_store() -> None:
    """Olvida el singleton (para tests o tras cambiar la config)."""
    global _store
    with _store_lock:
        _store = None
