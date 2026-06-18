"""Endpoints /api/v1/rag — memoria semántica para la UI "Mis documentos".

ES: Expone el RAG a la interfaz Tauri y a clientes locales. Todo es local
    (principios #1 y #2). Si rag.enabled=False, TODOS los endpoints devuelven
    503 — 0 vectores, 0 indexado. Si faltan dependencias ([rag]), devuelven 501
    con instrucciones de instalación en vez de un traceback.

    Endpoints:
      GET  /api/v1/rag/status      → disponibilidad + stats del índice + progreso
      GET  /api/v1/rag/documents   → lista de documentos indexados (con fragmentos)
      GET  /api/v1/rag/dirs        → carpetas indexadas + nº de documentos por carpeta
      POST /api/v1/rag/dirs        → añadir/quitar una carpeta (persistente)
      GET  /api/v1/rag/progress    → progreso del indexado en vivo (polling)
      GET  /api/v1/rag/search      → búsqueda semántica con filtros
      POST /api/v1/rag/reindex     → re-indexar (en segundo plano)
      POST /api/v1/rag/clear       → limpiar el índice vectorial

EN: Local RAG API for the desktop "My documents" panel. Returns 503 when
    disabled, 501 when optional deps are missing.

Las operaciones que tocan ChromaDB son síncronas (def): FastAPI las corre en su
threadpool, así no bloquean el event loop.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from ...config import config, save_config
from ...logger import log_api
from ...rag import RAGUnavailableError, check_rag_available

router = APIRouter(prefix="/rag", tags=["rag"])


# ─── guardas ───────────────────────────────────────────────────────────────────

def _require_enabled() -> None:
    """503 si la memoria semántica está apagada (principio #2)."""
    if not config.rag.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La memoria semántica (RAG) está desactivada. "
                   "Actívala con: itzel config rag.enabled true",
        )


def _require_deps() -> None:
    """501 si faltan dependencias opcionales, con hint de instalación."""
    ok, missing = check_rag_available()
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Faltan dependencias del RAG: " + ", ".join(missing)
                   + ". Instala con: pip install 'itzel-core[rag]'",
        )


def _guard() -> None:
    _require_enabled()
    _require_deps()


# ─── schemas ───────────────────────────────────────────────────────────────────

class IndexStatus(BaseModel):
    enabled:      bool
    available:    bool
    missing_deps: list[str]
    embed_model:  str | None = None
    embed_dim:    int | None = None
    documents:    int = 0
    chunks:       int = 0
    store_path:   str | None = None
    telemetry:    str = "off"
    progress:     dict[str, Any] = {}


class DirEntry(BaseModel):
    path:      str
    exists:    bool
    documents: int


class DirAction(BaseModel):
    path:   str
    action: str   # "add" | "remove"


class SearchHit(BaseModel):
    source:    str
    filename:  str
    file_type: str
    score:     float
    snippet:   str
    modified:  str


# ─── estado del índice ──────────────────────────────────────────────────────────

@router.get("/status", response_model=IndexStatus)
def rag_status() -> IndexStatus:
    """Disponibilidad + estadísticas del índice + progreso. No exige deps."""
    _require_enabled()
    ok, missing = check_rag_available()

    base = IndexStatus(
        enabled=config.rag.enabled,
        available=ok,
        missing_deps=missing,
    )
    if not ok:
        return base

    try:
        from ...rag.indexer import get_indexer
        from ...rag.store import get_store
        store = get_store()
        stats = store.stats()
        base.embed_model = stats["embed_model"]
        base.embed_dim   = stats["embed_dim"]
        base.documents   = stats["documents"]
        base.chunks      = stats["chunks"]
        base.store_path  = stats["path"]
        base.progress    = get_indexer().progress.to_dict()
    except RAGUnavailableError:
        base.available = False
    except Exception as exc:
        log_api.warning("rag_status parcial: %s", exc, extra={"component": "rag"})
    return base


# ─── documentos indexados ───────────────────────────────────────────────────────

@router.get("/documents")
def rag_documents() -> dict[str, Any]:
    """Lista de documentos indexados con su nº de fragmentos (Mis documentos)."""
    _guard()
    from ...rag.store import get_store
    sources = get_store().sources()
    return {"documents": sources, "total": len(sources)}


# ─── carpetas indexadas ─────────────────────────────────────────────────────────

@router.get("/dirs", response_model=list[DirEntry])
def rag_dirs() -> list[DirEntry]:
    """Carpetas configuradas en index_dirs + nº de documentos por carpeta."""
    _require_enabled()
    ok, _ = check_rag_available()

    counts: dict[str, int] = {}
    if ok:
        try:
            from ...rag.store import get_store
            for s in get_store().sources():
                d = s.get("dir") or str(Path(s["source"]).parent)
                counts[d] = counts.get(d, 0) + 1
        except Exception:
            pass

    entries: list[DirEntry] = []
    for d in config.rag.index_dirs:
        rp = str(Path(d).expanduser())
        # contar por prefijo de carpeta-raíz
        n = sum(c for k, c in counts.items() if k.startswith(rp) or _same_dir(k, d))
        entries.append(DirEntry(path=d, exists=Path(d).expanduser().is_dir(), documents=n))
    return entries


@router.post("/dirs", response_model=list[DirEntry])
def rag_dirs_update(action: DirAction) -> list[DirEntry]:
    """Añade o quita una carpeta de index_dirs y persiste la config.

    AISLAMIENTO: solo se aceptan rutas existentes; nada se indexa hasta el
    siguiente reindex. El usuario decide explícitamente qué exponer.
    """
    _require_enabled()
    target = str(Path(action.path).expanduser())

    dirs = list(config.rag.index_dirs)
    if action.action == "add":
        if not Path(target).is_dir():
            raise HTTPException(status_code=400, detail=f"No es una carpeta: {action.path}")
        if target not in dirs and action.path not in dirs:
            dirs.append(target)
    elif action.action == "remove":
        dirs = [d for d in dirs if d not in (target, action.path)]
    else:
        raise HTTPException(status_code=400, detail="action debe ser 'add' o 'remove'")

    config.rag.index_dirs = dirs
    save_config()
    log_api.info("index_dirs actualizado: %s %s", action.action, target,
                 extra={"component": "rag"})
    return rag_dirs()


# ─── progreso ───────────────────────────────────────────────────────────────────

@router.get("/progress")
def rag_progress() -> dict[str, Any]:
    """Progreso del indexado en vivo (para la barra 'Indexando N archivos…')."""
    _guard()
    from ...rag.indexer import get_indexer
    return get_indexer().progress.to_dict()


# ─── búsqueda semántica ─────────────────────────────────────────────────────────

@router.get("/search")
def rag_search(
    q:          str = Query(..., description="Consulta en lenguaje natural"),
    top_k:      int = Query(5, ge=1, le=50),
    file_types: str | None = Query(None, description="Extensiones separadas por coma, ej: .md,.pdf"),
    folder:     str | None = Query(None),
) -> dict[str, Any]:
    """Búsqueda semántica en los documentos. Devuelve fragmentos con fuente."""
    _guard()
    from ...rag.retriever import get_retriever
    types = [t.strip() for t in file_types.split(",")] if file_types else None
    hits = get_retriever().search(q, top_k=top_k, file_types=types, folder=folder)
    return {
        "query": q,
        "results": [h.to_dict() for h in hits],
        "total": len(hits),
    }


# ─── re-indexar (segundo plano) ─────────────────────────────────────────────────

_reindex_lock = threading.Lock()
_reindex_thread: threading.Thread | None = None


@router.post("/reindex", status_code=status.HTTP_202_ACCEPTED)
def rag_reindex(force: bool = Query(False, description="Reindexar todo aunque no cambió")) -> dict[str, Any]:
    """Lanza el indexado en segundo plano. La UI sigue el progreso con /progress."""
    _guard()
    global _reindex_thread

    from ...rag.indexer import get_indexer
    indexer = get_indexer()

    if indexer.progress.running:
        return {"started": False, "detail": "Ya hay un indexado en curso.",
                "progress": indexer.progress.to_dict()}

    if not config.rag.index_dirs:
        raise HTTPException(
            status_code=400,
            detail="No hay carpetas configuradas. Añade una con POST /api/v1/rag/dirs "
                   "o: itzel config rag.index_dirs",
        )

    def _run() -> None:
        try:
            indexer.index_all(force=force)
        except Exception as exc:
            log_api.error("Reindex falló: %s", exc, extra={"component": "rag"})

    with _reindex_lock:
        _reindex_thread = threading.Thread(target=_run, daemon=True)
        _reindex_thread.start()

    return {"started": True, "force": force, "dirs": config.rag.index_dirs}


# ─── limpiar índice ─────────────────────────────────────────────────────────────

@router.post("/clear")
def rag_clear() -> dict[str, Any]:
    """Borra TODO el índice vectorial (no toca tus archivos originales)."""
    _guard()
    from ...rag.store import get_store
    removed = get_store().reset()
    log_api.info("Índice RAG limpiado: %d fragmentos", removed, extra={"component": "rag"})
    return {"cleared": True, "removed_chunks": removed}


# ─── helpers ───────────────────────────────────────────────────────────────────

def _same_dir(a: str, b: str) -> bool:
    try:
        return Path(a).expanduser().resolve() == Path(b).expanduser().resolve()
    except Exception:
        return a == b
