"""indexer.py — Indexado incremental de documentos para el RAG de Itzel.

ES: Recorre SOLO las carpetas que el usuario eligió (config.rag.index_dirs),
    extrae texto, lo trocea y lo guarda en la base vectorial. Garantías:

      🔒 AISLAMIENTO: nunca indexa nada fuera de index_dirs. Cada archivo se
         valida contra las rutas permitidas antes de tocarlo (principio #5).
         Se respetan exclude_globs (.git, node_modules, .env*, secretos…) y el
         límite de tamaño (max_file_mb).

      ♻️ INCREMENTAL: salta archivos cuya firma (tamaño:mtime) no cambió. Solo
         re-procesa lo nuevo o modificado. También poda del índice los archivos
         que se borraron o que salieron de index_dirs.

      📊 PROGRESO: expone IndexProgress (total / hechos / archivo actual) para
         que la UI muestre "Indexando 247 archivos…" en vivo.

      👁️ WATCHER: si watchdog está instalado y config.rag.watch=True, vigila las
         carpetas y re-indexa con debounce. Si no, el indexado es bajo demanda.

EN: Incremental, isolated document indexer with live progress and an optional
    watchdog-based file watcher. Only indexes inside configured directories.

Uso / Usage:
    from itzel_core.rag.indexer import get_indexer

    idx = get_indexer()
    result = idx.index_all()          # escaneo incremental completo
    print(result.indexed, result.skipped, result.removed)
    idx.start_watching()              # vigilancia en vivo (opcional)
"""

from __future__ import annotations

import fnmatch
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from ..logger import log_engine
from .chunking import (
    SUPPORTED_EXTENSIONS,
    file_signature,
    read_and_chunk,
)
from .store import VectorStore, get_store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── progreso para la UI ───────────────────────────────────────────────────────

@dataclass
class IndexProgress:
    """Estado del indexado, leíble por la UI/API en cualquier momento."""
    running:      bool = False
    total:        int = 0
    done:         int = 0
    current_file: str = ""
    indexed:      int = 0     # archivos (re)indexados
    skipped:      int = 0     # sin cambios
    removed:      int = 0     # podados (borrados / fuera de index_dirs)
    errors:       int = 0
    started_at:   str = ""
    finished_at:  str = ""

    def to_dict(self) -> dict:
        pct = (self.done / self.total * 100.0) if self.total else 0.0
        label = (
            f"Indexando {self.total} archivos… ({self.done}/{self.total})"
            if self.running else
            ("Índice al día" if self.total or self.indexed else "Sin documentos")
        )
        return {
            "running":      self.running,
            "total":        self.total,
            "done":         self.done,
            "percent":      round(pct, 1),
            "current_file": self.current_file,
            "indexed":      self.indexed,
            "skipped":      self.skipped,
            "removed":      self.removed,
            "errors":       self.errors,
            "label":        label,
            "started_at":   self.started_at,
            "finished_at":  self.finished_at,
        }


@dataclass
class IndexResult:
    """Resumen devuelto por index_all()."""
    indexed: int = 0
    skipped: int = 0
    removed: int = 0
    errors:  int = 0
    total:   int = 0


# ─── indexador ─────────────────────────────────────────────────────────────────

class Indexer:
    """Indexa documentos de index_dirs en la base vectorial, de forma incremental."""

    def __init__(self, store: Optional[VectorStore] = None) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._progress = IndexProgress()
        self._observer = None           # watchdog Observer (carga diferida)
        self._debouncer: Optional[_Debouncer] = None

    # ── configuración (se re-lee en cada operación) ─────────────────────────────

    def _cfg(self):
        from ..config import config
        return config.rag

    @property
    def store(self) -> VectorStore:
        if self._store is None:
            self._store = get_store()
        return self._store

    @property
    def progress(self) -> IndexProgress:
        return self._progress

    def _index_dirs(self) -> list[Path]:
        dirs = []
        for d in self._cfg().index_dirs:
            try:
                p = Path(d).expanduser().resolve()
                if p.is_dir():
                    dirs.append(p)
                else:
                    log_engine.warning(
                        "index_dir no existe o no es carpeta: %s", d,
                        extra={"component": "rag"},
                    )
            except Exception:
                continue
        return dirs

    # ── aislamiento y filtros ───────────────────────────────────────────────────

    def _within_index_dirs(self, path: Path, dirs: list[Path]) -> Optional[Path]:
        """Devuelve la carpeta-raíz que contiene `path`, o None si está fuera.

        Esta es la barrera de aislamiento: nada fuera de index_dirs se indexa.
        """
        try:
            rp = path.resolve()
        except Exception:
            return None
        for d in dirs:
            try:
                rp.relative_to(d)
                return d
            except ValueError:
                continue
        return None

    def _is_excluded(self, path: Path) -> bool:
        cfg = self._cfg()
        sp = str(path).replace("\\", "/")
        for pattern in cfg.exclude_globs:
            if fnmatch.fnmatch(sp, pattern) or fnmatch.fnmatch(sp, "**/" + pattern):
                return True
        return False

    def _is_indexable(self, path: Path, cfg) -> bool:
        """Filtra por extensión, exclusiones y tamaño. No comprueba aislamiento."""
        ext = path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS or ext not in set(cfg.file_types):
            return False
        if self._is_excluded(path):
            return False
        try:
            size_mb = path.stat().st_size / (1024 * 1024)
        except OSError:
            return False
        if size_mb > cfg.max_file_mb:
            log_engine.debug("Archivo demasiado grande, omitido: %s (%.1f MB)", path, size_mb)
            return False
        return True

    def _iter_files(self, dirs: list[Path]):
        """Genera los archivos indexables dentro de index_dirs."""
        cfg = self._cfg()
        seen: set[str] = set()
        for d in dirs:
            for path in d.rglob("*"):
                if not path.is_file():
                    continue
                if self._is_excluded(path):
                    continue
                if not self._is_indexable(path, cfg):
                    continue
                key = str(path.resolve())
                if key in seen:
                    continue
                seen.add(key)
                yield path

    # ── indexado de un archivo ───────────────────────────────────────────────────

    def index_file(self, path: Path, force: bool = False) -> bool:
        """(Re)indexa un único archivo si cambió. Devuelve True si lo (re)indexó.

        Respeta el aislamiento: si el archivo está fuera de index_dirs, NO lo toca.
        """
        cfg = self._cfg()
        dirs = self._index_dirs()
        root = self._within_index_dirs(path, dirs)
        if root is None:
            log_engine.warning(
                "Ignorado por aislamiento (fuera de index_dirs): %s", path,
                extra={"component": "rag"},
            )
            return False
        if not self._is_indexable(path, cfg):
            return False

        src = str(path.resolve())
        sig = file_signature(path)

        # Incremental: si la firma coincide con lo guardado, no hay nada que hacer.
        if not force:
            stored = self.store.source_signatures().get(src)
            if stored and stored == sig:
                return False

        file_hash, chunks = read_and_chunk(
            path, cfg.chunk_size_tokens, cfg.chunk_overlap_tokens
        )
        if not chunks:
            # Sin texto extraíble: limpiar lo que hubiera antes.
            self.store.delete_by_source(src)
            return False

        # Estrategia de reemplazo a nivel de archivo (incremental por archivo).
        self.store.delete_by_source(src)

        modified_iso = _iso_mtime(path)
        modified_ts  = _epoch_mtime(path)   # numérico → permite filtrar por fecha
        ids       = [f"{src}::{c.index}" for c in chunks]
        documents = [c.text for c in chunks]
        metadatas = [
            {
                "source":       src,
                "dir":          str(root),
                "file_type":    path.suffix.lower(),
                "file_sig":     sig,
                "file_hash":    file_hash or "",
                "modified":     modified_iso,
                "modified_ts":  modified_ts,
                "chunk_index":  c.index,
                "content_hash": c.content_hash,
                "indexed_at":   _now(),
            }
            for c in chunks
        ]
        self.store.add(ids=ids, documents=documents, metadatas=metadatas)
        log_engine.info(
            "Indexado: %s (%d fragmentos)", path.name, len(chunks),
            extra={"component": "rag"},
        )
        return True

    def remove_file(self, path: Path) -> int:
        """Quita del índice los fragmentos de un archivo (borrado)."""
        src = str(Path(path).resolve())
        n = self.store.delete_by_source(src)
        if n:
            log_engine.info(
                "Quitado del índice: %s (%d fragmentos)", Path(path).name, n,
                extra={"component": "rag"},
            )
        return n

    # ── indexado completo (incremental) ──────────────────────────────────────────

    def index_all(
        self,
        force:       bool = False,
        prune:       bool = True,
        progress_cb: Optional[Callable[[IndexProgress], None]] = None,
    ) -> IndexResult:
        """Escanea index_dirs y sincroniza el índice de forma incremental.

        - force=True  → reindexa todo aunque no haya cambiado.
        - prune=True  → poda archivos borrados o que salieron de index_dirs.
        """
        with self._lock:
            dirs = self._index_dirs()
            files = list(self._iter_files(dirs)) if dirs else []

            self._progress = IndexProgress(
                running=True, total=len(files), started_at=_now()
            )
            result = IndexResult(total=len(files))

            current_sources: set[str] = set()
            for path in files:
                src = str(path.resolve())
                current_sources.add(src)
                self._progress.current_file = path.name
                try:
                    if self.index_file(path, force=force):
                        result.indexed += 1
                        self._progress.indexed += 1
                    else:
                        result.skipped += 1
                        self._progress.skipped += 1
                except Exception as exc:
                    result.errors += 1
                    self._progress.errors += 1
                    log_engine.error(
                        "Error indexando %s: %s", path, exc,
                        extra={"component": "rag"},
                    )
                self._progress.done += 1
                if progress_cb:
                    progress_cb(self._progress)

            # Poda: quitar del índice fuentes que ya no están bajo index_dirs.
            if prune:
                indexed_sources = set(self.store.source_signatures().keys())
                stale = indexed_sources - current_sources
                for src in stale:
                    removed = self.store.delete_by_source(src)
                    if removed:
                        result.removed += 1
                        self._progress.removed += 1

            self._progress.running = False
            self._progress.finished_at = _now()
            log_engine.info(
                "Indexado completo: %d nuevos, %d sin cambios, %d podados, %d errores",
                result.indexed, result.skipped, result.removed, result.errors,
                extra={"component": "rag"},
            )
            return result

    # ── watcher (watchdog opcional) ──────────────────────────────────────────────

    def start_watching(self) -> bool:
        """Inicia la vigilancia de index_dirs con watchdog. No-op si falta la dep."""
        cfg = self._cfg()
        if not cfg.watch:
            return False
        if self._observer is not None:
            return True  # ya está corriendo

        try:
            from watchdog.observers import Observer
        except ImportError:
            log_engine.info(
                "watchdog no instalado — sin vigilancia en vivo. "
                "Instala con: pip install 'itzel-core[rag]'",
                extra={"component": "rag"},
            )
            return False

        dirs = self._index_dirs()
        if not dirs:
            return False

        self._debouncer = _Debouncer(cfg.watch_debounce_s, self._on_change)
        handler = _RAGEventHandler(self._debouncer)
        self._observer = Observer()
        for d in dirs:
            self._observer.schedule(handler, str(d), recursive=True)
        self._observer.daemon = True
        self._observer.start()
        log_engine.info(
            "Vigilando %d carpeta(s) para indexado en vivo", len(dirs),
            extra={"component": "rag"},
        )
        return True

    def stop_watching(self) -> None:
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2.0)
            except Exception:
                pass
            self._observer = None
        if self._debouncer is not None:
            self._debouncer.cancel()
            self._debouncer = None

    def _on_change(self, path_str: str, event_type: str) -> None:
        """Callback del debouncer: re-indexa o quita según el evento."""
        path = Path(path_str)
        try:
            if event_type == "deleted":
                self.remove_file(path)
            else:  # created / modified / moved
                if path.exists():
                    self.index_file(path)
        except Exception as exc:
            log_engine.error(
                "Error procesando cambio en %s: %s", path, exc,
                extra={"component": "rag"},
            )


# ─── debouncer y handler de watchdog ───────────────────────────────────────────

class _Debouncer:
    """Agrupa eventos de archivo y los procesa tras N segundos de calma.

    Evita re-indexar 20 veces mientras se guarda un archivo grande.
    """

    def __init__(self, delay_s: float, callback: Callable[[str, str], None]) -> None:
        self._delay = delay_s
        self._callback = callback
        self._pending: dict[str, str] = {}   # path → último tipo de evento
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def submit(self, path: str, event_type: str) -> None:
        with self._lock:
            self._pending[path] = event_type
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            pending = dict(self._pending)
            self._pending.clear()
            self._timer = None
        for path, event_type in pending.items():
            self._callback(path, event_type)

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending.clear()


def _make_event_handler_base():
    """Crea la clase handler heredando de watchdog (import diferido)."""
    from watchdog.events import FileSystemEventHandler

    class _Handler(FileSystemEventHandler):
        def __init__(self, debouncer: _Debouncer) -> None:
            self._debouncer = debouncer

        def on_created(self, event):
            if not event.is_directory:
                self._debouncer.submit(event.src_path, "created")

        def on_modified(self, event):
            if not event.is_directory:
                self._debouncer.submit(event.src_path, "modified")

        def on_deleted(self, event):
            if not event.is_directory:
                self._debouncer.submit(event.src_path, "deleted")

        def on_moved(self, event):
            if not event.is_directory:
                self._debouncer.submit(getattr(event, "dest_path", event.src_path), "moved")

    return _Handler


def _RAGEventHandler(debouncer: _Debouncer):
    """Instancia el handler de watchdog (la clase se construye al vuelo)."""
    handler_cls = _make_event_handler_base()
    return handler_cls(debouncer)


# ─── helpers ───────────────────────────────────────────────────────────────────

def _iso_mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc
        ).isoformat()
    except OSError:
        return ""


def _epoch_mtime(path: Path) -> int:
    """mtime como epoch (int). ChromaDB filtra por número, no por string ISO."""
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return 0


# ─── singleton ─────────────────────────────────────────────────────────────────

_indexer: Optional[Indexer] = None
_indexer_lock = threading.Lock()


def get_indexer() -> Indexer:
    """Devuelve el indexador global (singleton thread-safe)."""
    global _indexer
    if _indexer is None:
        with _indexer_lock:
            if _indexer is None:
                _indexer = Indexer()
    return _indexer
