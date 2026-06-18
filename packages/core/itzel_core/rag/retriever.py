"""retriever.py — Búsqueda semántica sobre el índice de documentos.

ES: Dada una consulta en lenguaje natural, devuelve los fragmentos más
    relevantes de TUS documentos por similitud coseno. Soporta filtros:

      - tipo de archivo  → nativo en ChromaDB ($in sobre file_type)
      - fecha            → nativo ($gte/$lte sobre 'modified'; las fechas ISO
                           ordenan lexicográficamente = cronológicamente)
      - carpeta          → post-filtro por prefijo de ruta (ChromaDB no hace
                           prefix-match), permite acotar a una subcarpeta

    Cada resultado trae el fragmento completo (que ya incluye contexto: hasta
    ~512 tokens del documento) y un snippet corto (~200 tokens) centrado en los
    términos de la consulta, listo para mostrar con la fuente citada.

EN: Semantic top-K search with file-type/date (native) and folder (post-filter)
    constraints. Returns rich fragments with a query-centered snippet + source.

Uso / Usage:
    from itzel_core.rag.retriever import get_retriever

    hits = get_retriever().search("reunión del lunes", top_k=5)
    for h in hits:
        print(f"{h.score:.2f}  {h.source}\\n  {h.snippet}")
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .chunking import CHARS_PER_TOKEN
from .store import VectorStore, get_store


def _to_epoch(value: str | int | float | None) -> int | None:
    """Normaliza una fecha (ISO string o epoch) a epoch int. None si no aplica."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        # Acepta 'YYYY-MM-DD' o ISO completo, con o sin zona.
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int(dt.timestamp())
    except ValueError:
        return None


# ─── resultado ─────────────────────────────────────────────────────────────────

@dataclass
class RetrievedChunk:
    """Un fragmento recuperado, con su fuente y relevancia."""
    text:        str       # fragmento completo (contexto amplio)
    snippet:     str       # extracto corto centrado en la consulta
    source:      str       # ruta absoluta del documento
    file_type:   str
    score:       float     # similitud coseno 0–1 (mayor = más relevante)
    chunk_index: int
    modified:    str       # mtime ISO del documento

    @property
    def filename(self) -> str:
        return Path(self.source).name

    def to_dict(self) -> dict[str, Any]:
        return {
            "source":      self.source,
            "filename":    self.filename,
            "file_type":   self.file_type,
            "score":       round(self.score, 4),
            "chunk_index": self.chunk_index,
            "modified":    self.modified,
            "snippet":     self.snippet,
            "text":        self.text,
        }


# ─── retriever ─────────────────────────────────────────────────────────────────

class Retriever:
    """Búsqueda semántica top-K con filtros sobre el índice vectorial."""

    def __init__(self, store: VectorStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> VectorStore:
        if self._store is None:
            self._store = get_store()
        return self._store

    def _cfg(self):
        from ..config import config
        return config.rag

    # ── construcción del filtro nativo de ChromaDB ──────────────────────────────

    @staticmethod
    def _build_where(
        file_types:     list[str] | None,
        modified_after: str | None,
        modified_before: str | None,
    ) -> dict[str, Any] | None:
        """Arma el filtro `where` de ChromaDB. None si no hay condiciones."""
        clauses: list[dict[str, Any]] = []

        if file_types:
            exts = [e if e.startswith(".") else f".{e}" for e in file_types]
            exts = [e.lower() for e in exts]
            clauses.append({"file_type": {"$in": exts}})

        # ChromaDB exige operandos numéricos para $gte/$lte → usamos modified_ts.
        after_ts = _to_epoch(modified_after)
        before_ts = _to_epoch(modified_before)
        if after_ts is not None:
            clauses.append({"modified_ts": {"$gte": after_ts}})
        if before_ts is not None:
            clauses.append({"modified_ts": {"$lte": before_ts}})

        if not clauses:
            return None
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}

    # ── búsqueda principal ───────────────────────────────────────────────────────

    def search(
        self,
        query:           str,
        top_k:           int | None = None,
        file_types:      list[str] | None = None,
        folder:          str | None = None,
        modified_after:  str | None = None,
        modified_before: str | None = None,
        min_score:       float | None = None,
    ) -> list[RetrievedChunk]:
        """Devuelve los fragmentos más relevantes para `query`.

        Args:
            query:           consulta en lenguaje natural.
            top_k:           nº de resultados (default config.rag.top_k = 5).
            file_types:      p.ej. ['.md', '.pdf'] — filtra por extensión.
            folder:          acota a documentos bajo esta carpeta (prefijo).
            modified_after:  ISO date — solo docs modificados desde entonces.
            modified_before: ISO date — solo docs modificados hasta entonces.
            min_score:       umbral de similitud (default config.rag.min_score).
        """
        query = (query or "").strip()
        if not query:
            return []

        cfg = self._cfg()
        k = top_k or cfg.top_k
        floor = cfg.min_score if min_score is None else min_score

        # Si vamos a post-filtrar por carpeta, pedimos de más para no quedarnos cortos.
        fetch_k = k * 4 if folder else k

        where = self._build_where(file_types, modified_after, modified_before)
        embedding = self.store.provider.embed_query(query)
        raw = self.store.query(embedding, top_k=fetch_k, where=where)

        folder_resolved = self._resolve_folder(folder)
        results: list[RetrievedChunk] = []
        for item in raw:
            if item["score"] < floor:
                continue
            meta = item.get("metadata") or {}
            source = meta.get("source", "")

            if folder_resolved and not self._under_folder(source, folder_resolved):
                continue

            text = item.get("document", "") or ""
            results.append(RetrievedChunk(
                text=text,
                snippet=self._snippet(text, query, cfg.context_window_tokens),
                source=source,
                file_type=meta.get("file_type", ""),
                score=item["score"],
                chunk_index=int(meta.get("chunk_index", 0)),
                modified=meta.get("modified", ""),
            ))
            if len(results) >= k:
                break

        return results

    # ── helpers de filtro por carpeta ───────────────────────────────────────────

    @staticmethod
    def _resolve_folder(folder: str | None) -> str | None:
        if not folder:
            return None
        try:
            return str(Path(folder).expanduser().resolve())
        except Exception:
            return folder

    @staticmethod
    def _under_folder(source: str, folder_resolved: str) -> bool:
        """True si `source` está dentro de `folder_resolved` (o es exactamente él)."""
        try:
            Path(source).resolve().relative_to(folder_resolved)
            return True
        except ValueError:
            return False

    # ── snippet centrado en la consulta ──────────────────────────────────────────

    @staticmethod
    def _snippet(text: str, query: str, window_tokens: int) -> str:
        """Extrae ~window_tokens de texto alrededor del mejor match de la consulta.

        Busca el término más largo de la consulta dentro del fragmento; si lo
        encuentra, centra la ventana ahí. Si no, devuelve el inicio del fragmento.
        """
        if not text:
            return ""
        window_chars = max(80, window_tokens * CHARS_PER_TOKEN)
        if len(text) <= window_chars:
            return text.strip()

        lowered = text.lower()
        # Buscar el término más significativo (el más largo) de la consulta.
        terms = sorted(
            (t for t in query.lower().split() if len(t) > 2),
            key=len, reverse=True,
        )
        pos = -1
        for term in terms:
            pos = lowered.find(term)
            if pos != -1:
                break

        if pos == -1:
            return text[:window_chars].strip() + "…"

        half = window_chars // 2
        start = max(0, pos - half)
        end = min(len(text), start + window_chars)
        start = max(0, end - window_chars)  # reajustar si tocamos el final

        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(text) else ""
        return prefix + text[start:end].strip() + suffix


# ─── singleton ─────────────────────────────────────────────────────────────────

_retriever: Retriever | None = None
_retriever_lock = threading.Lock()


def get_retriever() -> Retriever:
    """Devuelve el retriever global (singleton thread-safe)."""
    global _retriever
    if _retriever is None:
        with _retriever_lock:
            if _retriever is None:
                _retriever = Retriever()
    return _retriever
