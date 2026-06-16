"""embeddings.py — Proveedores de embeddings locales para el RAG de Itzel.

ES: Convierte texto en vectores SIN salir del equipo (principios #1 y #2).
    Dos niveles, con caída elegante:

      1. Ollama  → "nomic-embed-text" (768 dims). Reusa la instancia local de
         Ollama (localhost:11434). Preferido: rápido y sin deps de Python ML.
      2. ONNX    → "all-MiniLM-L6-v2" (384 dims), el modelo que trae ChromaDB.
         Fallback sin Ollama. La PRIMERA vez descarga ~80 MB del modelo (una
         sola vez, como `ollama pull`); después es 100% offline.

    ⚠️ La dimensión queda BLOQUEADA por colección: no se pueden mezclar 768 y
       384 dims en el mismo índice. El nombre del proveedor (p.ej.
       "ollama:nomic-embed-text") se guarda en los metadatos de la colección;
       si cambia, hay que re-indexar. La dimensión se detecta dinámicamente
       embedeando una sonda, así no se hardcodea.

EN: Local embedding providers with graceful fallback (Ollama → bundled ONNX).
    Dimension is detected at runtime and locked per collection.

Uso / Usage:
    from itzel_core.rag.embeddings import get_embedding_provider

    emb = get_embedding_provider()
    vecs = emb.embed_documents(["hola mundo", "otro texto"])
    q    = emb.embed_query("búsqueda")
    print(emb.name, emb.dimension)   # "ollama:nomic-embed-text" 768
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Optional

import httpx

from ..logger import log_engine

# ─── constantes ───────────────────────────────────────────────────────────────

_OLLAMA_URL        = "http://127.0.0.1:11434"
_HEALTH_TIMEOUT_S  = 2.0
_EMBED_TIMEOUT_S   = 60.0     # embedear lotes grandes puede tardar
_PROBE_TEXT        = "itzel"  # sonda para detectar la dimensión


# ─── interfaz ─────────────────────────────────────────────────────────────────

class EmbeddingProvider(ABC):
    """Contrato común de un proveedor de embeddings local."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Identidad estable, p.ej. 'ollama:nomic-embed-text' o 'onnx:all-MiniLM-L6-v2'.

        Se guarda en la colección para bloquear la dimensión.
        """

    @property
    def dimension(self) -> int:
        """Dimensión del vector. Se detecta una sola vez con una sonda y se cachea."""
        if self._dim is None:
            vec = self.embed_query(_PROBE_TEXT)
            self._dim = len(vec)
        return self._dim

    @abstractmethod
    def available(self) -> bool:
        """True si el proveedor puede generar embeddings ahora mismo."""

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embedea una lista de textos (para indexar). Devuelve un vector por texto."""

    def embed_query(self, text: str) -> list[float]:
        """Embedea una consulta. Por defecto reusa embed_documents con un solo texto."""
        return self.embed_documents([text])[0]

    # almacenamiento de la dimensión detectada
    _dim: Optional[int] = None


# ─── proveedor 1: Ollama ──────────────────────────────────────────────────────

class OllamaEmbeddingProvider(EmbeddingProvider):
    """Embeddings vía Ollama local. Modelo recomendado: nomic-embed-text (768d)."""

    def __init__(self, model: str = "nomic-embed-text") -> None:
        self._model = model
        self._dim = None

    @property
    def name(self) -> str:
        return f"ollama:{self._model}"

    def available(self) -> bool:
        """True si Ollama responde y el modelo de embeddings está descargado."""
        try:
            with httpx.Client(timeout=_HEALTH_TIMEOUT_S) as client:
                resp = client.get(f"{_OLLAMA_URL}/api/tags")
                if resp.status_code != 200:
                    return False
                names = [m.get("name", "") for m in resp.json().get("models", [])]
                # "nomic-embed-text" suele aparecer como "nomic-embed-text:latest"
                base = self._model.split(":")[0]
                return any(n.split(":")[0] == base for n in names)
        except Exception:
            return False

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # Intento 1: /api/embed (batch, Ollama reciente).
        try:
            return self._embed_batch(texts)
        except _BatchUnsupported:
            # Intento 2: /api/embeddings (uno por uno, Ollama antiguo).
            return [self._embed_single(t) for t in texts]

    # ── internos ───────────────────────────────────────────────────────────────

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        with httpx.Client(timeout=_EMBED_TIMEOUT_S) as client:
            resp = client.post(
                f"{_OLLAMA_URL}/api/embed",
                json={"model": self._model, "input": texts},
            )
            if resp.status_code == 404:
                raise _BatchUnsupported()
            resp.raise_for_status()
            data = resp.json()
            vecs = data.get("embeddings")
            if not vecs:
                raise _BatchUnsupported()
            return [list(map(float, v)) for v in vecs]

    def _embed_single(self, text: str) -> list[float]:
        with httpx.Client(timeout=_EMBED_TIMEOUT_S) as client:
            resp = client.post(
                f"{_OLLAMA_URL}/api/embeddings",
                json={"model": self._model, "prompt": text},
            )
            resp.raise_for_status()
            vec = resp.json().get("embedding", [])
            return list(map(float, vec))


class _BatchUnsupported(Exception):
    """El endpoint /api/embed no existe en esta versión de Ollama."""


# ─── proveedor 2: ONNX (bundled en ChromaDB) ──────────────────────────────────

class OnnxEmbeddingProvider(EmbeddingProvider):
    """Embeddings con all-MiniLM-L6-v2 vía ONNX runtime (incluido en chromadb).

    No necesita torch ni Ollama. La primera ejecución descarga el modelo (~80 MB)
    a la caché local; después funciona sin red.
    """

    def __init__(self) -> None:
        self._dim = None
        self._ef = None  # función de embedding de ChromaDB (carga diferida)

    @property
    def name(self) -> str:
        return "onnx:all-MiniLM-L6-v2"

    def _ensure_ef(self):
        """Carga diferida de la función ONNX de ChromaDB."""
        if self._ef is None:
            # Import diferido — chromadb es dependencia opcional.
            from chromadb.utils import embedding_functions
            self._ef = embedding_functions.DefaultEmbeddingFunction()
        return self._ef

    def available(self) -> bool:
        try:
            self._ensure_ef()
            return True
        except Exception as exc:
            log_engine.debug("ONNX embeddings no disponible: %s", exc)
            return False

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        ef = self._ensure_ef()
        # La interfaz de ChromaDB es callable: ef(input) -> embeddings.
        vectors = ef(texts)
        return [list(map(float, v)) for v in vectors]


# ─── fábrica con fallback ──────────────────────────────────────────────────────

def _build_provider(spec: str) -> Optional[EmbeddingProvider]:
    """Construye un proveedor desde un string de config.

    'ollama:nomic-embed-text' → OllamaEmbeddingProvider('nomic-embed-text')
    'onnx'                     → OnnxEmbeddingProvider()
    """
    spec = (spec or "").strip()
    if spec == "onnx" or spec == "onnx:all-MiniLM-L6-v2":
        return OnnxEmbeddingProvider()
    if spec.startswith("ollama:"):
        return OllamaEmbeddingProvider(model=spec.split(":", 1)[1])
    if spec.startswith("ollama"):
        return OllamaEmbeddingProvider()
    return None


_provider: Optional[EmbeddingProvider] = None
_provider_lock = threading.Lock()


def get_embedding_provider(force_reload: bool = False) -> EmbeddingProvider:
    """Devuelve el proveedor activo, eligiendo entre primario y fallback.

    Orden:
      1. config.rag.embed_model    (preferido, p.ej. ollama:nomic-embed-text)
      2. config.rag.embed_fallback (p.ej. onnx)

    Si el primario no está disponible (Ollama apagado / modelo no descargado),
    cae al fallback automáticamente. Lanza RuntimeError si ninguno sirve.

    Thread-safe (double-checked locking). Cacheado como singleton.
    """
    global _provider
    if _provider is not None and not force_reload:
        return _provider

    with _provider_lock:
        if _provider is not None and not force_reload:
            return _provider

        try:
            from ..config import config
            primary_spec  = config.rag.embed_model
            fallback_spec = config.rag.embed_fallback
        except Exception:
            primary_spec, fallback_spec = "ollama:nomic-embed-text", "onnx"

        # 1. Intentar el primario.
        primary = _build_provider(primary_spec)
        if primary is not None and primary.available():
            log_engine.info(
                "Embeddings RAG: usando %s (%dd)",
                primary.name, primary.dimension,
                extra={"component": "rag"},
            )
            _provider = primary
            return _provider

        # 2. Caer al fallback.
        fallback = _build_provider(fallback_spec)
        if fallback is not None and fallback.available():
            if primary is not None:
                log_engine.warning(
                    "Embeddings RAG: '%s' no disponible, usando fallback %s (%dd). "
                    "Para nomic-embed-text: ollama pull nomic-embed-text",
                    primary_spec, fallback.name, fallback.dimension,
                    extra={"component": "rag"},
                )
            _provider = fallback
            return _provider

    raise RuntimeError(
        "Ningún proveedor de embeddings disponible. "
        "Instala chromadb (fallback ONNX) o arranca Ollama con: "
        "ollama pull nomic-embed-text"
    )


def reset_embedding_provider() -> None:
    """Resetea el singleton (para tests o tras cambiar la config)."""
    global _provider
    with _provider_lock:
        _provider = None
