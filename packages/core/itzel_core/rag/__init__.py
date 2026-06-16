"""itzel_core.rag — Memoria semántica local (RAG) de Itzel.

ES: Indexa tus documentos en una base vectorial local (ChromaDB) y permite
    buscar por significado. Todo corre en tu máquina:
      - Los embeddings se generan localmente (Ollama o el modelo ONNX de ChromaDB).
      - Los vectores viven en ~/.itzel/data/vectors/ — nunca salen del equipo.
      - La telemetría de ChromaDB se desactiva explícitamente (principio #2).

EN: Local semantic memory over your own documents. Embeddings are generated
    on-device and vectors stay on disk; nothing is sent to external APIs.

Disponibilidad / Availability:
    Las dependencias pesadas (chromadb, watchdog, pypdf, python-docx) son
    OPCIONALES. Importar este paquete NUNCA falla aunque no estén instaladas:
    los submódulos difieren sus imports. Usa check_rag_available() para saber
    qué falta antes de operar.

Uso / Usage:
    from itzel_core.rag import check_rag_available, get_retriever

    ok, missing = check_rag_available()
    if not ok:
        print("Instala con: pip install 'itzel-core[rag]'  (faltan: %s)" % missing)
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING

# ─── dependencias opcionales ───────────────────────────────────────────────────
# Mapeo: nombre del módulo Python → para qué se usa (mensaje al usuario).
_OPTIONAL_DEPS: dict[str, str] = {
    "chromadb": "base vectorial local",     # imprescindible para indexar/buscar
    "watchdog": "vigilancia de carpetas",   # opcional: indexado en vivo
    "pypdf":    "lectura de PDFs",           # opcional: solo si indexas .pdf
    "docx":     "lectura de .docx",          # opcional: solo si indexas .docx
}

# La única dependencia IMPRESCINDIBLE para que el RAG funcione es chromadb.
_REQUIRED_FOR_RAG = ("chromadb",)


class RAGUnavailableError(RuntimeError):
    """Se lanza cuando se intenta usar el RAG sin las dependencias mínimas.

    Lleva la lista de módulos faltantes y un hint de instalación, para que
    el CLI/API puedan mostrar un mensaje accionable en vez de un traceback.
    """

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(
            "RAG no disponible — faltan dependencias: "
            + ", ".join(missing)
            + ". Instala con: pip install 'itzel-core[rag]'"
        )


def _module_present(name: str) -> bool:
    """True si el módulo se puede importar (sin importarlo de verdad)."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def check_rag_available() -> tuple[bool, list[str]]:
    """¿Están las dependencias MÍNIMAS del RAG? Devuelve (ok, faltantes).

    Solo considera lo imprescindible (chromadb). watchdog/pypdf/docx son
    extras que se degradan elegantemente (ver missing_optional()).
    """
    missing = [m for m in _REQUIRED_FOR_RAG if not _module_present(m)]
    return (len(missing) == 0, missing)


def missing_optional() -> list[str]:
    """Lista de dependencias OPCIONALES ausentes (para avisos no bloqueantes)."""
    return [m for m in _OPTIONAL_DEPS if not _module_present(m)]


def require_rag() -> None:
    """Lanza RAGUnavailableError si falta lo mínimo. Úsalo antes de operar."""
    ok, missing = check_rag_available()
    if not ok:
        raise RAGUnavailableError(missing)


# ─── re-exports perezosos ───────────────────────────────────────────────────────
# Importamos las clases bajo demanda para que `import itzel_core.rag` no arrastre
# chromadb. Cada submódulo difiere internamente sus imports pesados.

if TYPE_CHECKING:  # solo para type-checkers / IDEs, no en runtime
    from .embeddings import EmbeddingProvider, get_embedding_provider
    from .indexer import Indexer, get_indexer
    from .pipeline import RAGPipeline, get_pipeline
    from .retriever import Retriever, get_retriever
    from .store import VectorStore, get_store

_LAZY = {
    "EmbeddingProvider":       ("embeddings", "EmbeddingProvider"),
    "get_embedding_provider":  ("embeddings", "get_embedding_provider"),
    "VectorStore":             ("store",      "VectorStore"),
    "get_store":               ("store",      "get_store"),
    "Indexer":                 ("indexer",    "Indexer"),
    "get_indexer":             ("indexer",    "get_indexer"),
    "Retriever":               ("retriever",  "Retriever"),
    "get_retriever":           ("retriever",  "get_retriever"),
    "RAGPipeline":             ("pipeline",   "RAGPipeline"),
    "get_pipeline":            ("pipeline",   "get_pipeline"),
}


def __getattr__(name: str):  # PEP 562 — import perezoso a nivel de módulo
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module 'itzel_core.rag' has no attribute '{name}'")
    module_name, attr = target
    from importlib import import_module

    mod = import_module(f".{module_name}", __name__)
    return getattr(mod, attr)


__all__ = [
    "check_rag_available",
    "missing_optional",
    "require_rag",
    "RAGUnavailableError",
    # perezosos
    "EmbeddingProvider",
    "get_embedding_provider",
    "VectorStore",
    "get_store",
    "Indexer",
    "get_indexer",
    "Retriever",
    "get_retriever",
    "RAGPipeline",
    "get_pipeline",
]
