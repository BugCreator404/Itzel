"""logger.py — Logs estructurados en JSON para Itzel.

ES: Sistema de logs multi-archivo con rotación por tamaño + retención por días.
    Sin telemetría — todo queda en ~/.itzel/logs/ (principio #1).

    Archivos generados:
      itzel.log    → log general (todos los niveles ≥ DEBUG)
      errors.log   → solo ERROR y CRITICAL (facilita el troubleshooting)
      actions.log  → acciones del sistema ejecutadas por agentes

    Rotación: cada archivo rota al llegar a max_bytes (default: 100 MB).
              Los archivos más antiguos de retention_days (default: 7) se purgan
              al arrancar el logger.

    Modo silencioso: cuando monitoring.enabled=False, todos los handlers son
    NullHandler — 0 bytes escritos, 0 archivos creados (principio #6 del spec).

EN: Multi-file JSON structured logger with size-based rotation and day-based
    retention. Silent mode (NullHandler) when monitoring is disabled.

API pública / Public API (backward-compatible):
    from itzel_core.logger import log, log_api, log_ws, log_engine, log_actions

    log.info("mensaje", extra={"component": "engine"})
    log_actions.info("acción ejecutada", extra={"tool": "file_agent"})
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOG_DIR = Path.home() / ".itzel" / "logs"


# ─── configuración desde MonitoringConfig ─────────────────────────────────────

def _monitoring_cfg() -> tuple[bool, int, int]:
    """Devuelve (enabled, max_bytes, retention_days).
    Usa defaults seguros si la config aún no está disponible (inicio del proceso).
    """
    try:
        from .config import config
        mon = config.monitoring
        return mon.enabled, mon.log_max_bytes, mon.log_retention_days
    except Exception:
        return True, 100 * 1024 * 1024, 7


# ─── purga de archivos viejos ─────────────────────────────────────────────────

def _purge_old_logs(retention_days: int) -> None:
    """Elimina archivos de log más antiguos que retention_days. Silencioso."""
    try:
        cutoff = time.time() - retention_days * 86_400
        for f in _LOG_DIR.glob("*.log*"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except Exception:
        pass


# ─── formatter JSON ───────────────────────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    """Formatea cada entrada como una línea JSON. Un JSON por línea (JSONL).

    Campos extra opcionales (via logger.info("…", extra={…})):
      req_id, path, method, status, latency_ms, component, tool, action
    """

    _EXTRA_FIELDS = (
        "req_id", "path", "method", "status",
        "latency_ms", "component", "tool", "action",
    )

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts":     datetime.now(timezone.utc).isoformat(),
            "level":  record.levelname,
            "logger": record.name,
            "msg":    record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        for key in self._EXTRA_FIELDS:
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        return json.dumps(entry, ensure_ascii=False)


# ─── handler de rotación por tamaño ──────────────────────────────────────────

def _rotating_handler(path: Path, max_bytes: int, level: int) -> logging.Handler:
    """Crea un RotatingFileHandler. backupCount=7 → max 7 archivos rotados."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=7,
        encoding="utf-8",
        delay=True,     # no crea el archivo hasta el primer write
    )
    handler.setFormatter(_JsonFormatter())
    handler.setLevel(level)
    return handler


# ─── construcción del logger ──────────────────────────────────────────────────

def _build_logger(name: str, *, extra_handlers: list[logging.Handler] | None = None) -> logging.Logger:
    """Configura un logger con handlers de archivo + consola (o NullHandler si
    el monitoreo está desactivado). Idempotente: si ya tiene handlers, no añade.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False        # evitar duplicados si el root logger tiene handlers

    enabled, max_bytes, retention_days = _monitoring_cfg()

    if not enabled:
        # Modo silencioso: 0 bytes, 0 archivos.
        # Silent mode: no files written.
        logger.addHandler(logging.NullHandler())
        return logger

    # ── Handler: itzel.log (todos los niveles) ───────────────────────
    _purge_old_logs(retention_days)        # purga al primer logger que se construye
    logger.addHandler(_rotating_handler(_LOG_DIR / "itzel.log", max_bytes, logging.DEBUG))

    # ── Handler: errors.log (ERROR y CRITICAL) ───────────────────────
    logger.addHandler(_rotating_handler(_LOG_DIR / "errors.log", max_bytes, logging.ERROR))

    # ── Handlers adicionales (p.ej. actions.log) ─────────────────────
    for h in (extra_handlers or []):
        logger.addHandler(h)

    # ── Handler consola ───────────────────────────────────────────────
    # Solo en desarrollo (ITZEL_ENV != production). En producción el operador
    # lee los logs desde el archivo o el dashboard.
    if os.getenv("ITZEL_ENV", "development") != "production":
        import sys
        # Forzar UTF-8 en consolas Windows (cp1252 rompe acentos en JSON).
        # Force UTF-8 on Windows consoles to avoid cp1252 encoding errors.
        stream = sys.stderr
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass
        console = logging.StreamHandler(stream)
        console.setFormatter(_JsonFormatter())
        console.setLevel(logging.INFO)
        logger.addHandler(console)

    return logger


# ─── logger de acciones (actions.log) ────────────────────────────────────────

def _build_actions_logger() -> logging.Logger:
    """Logger dedicado para las acciones del sistema.

    Escribe en actions.log (además de itzel.log general). El dashboard lee
    actions.log directamente para mostrar el historial de acciones.
    """
    logger = logging.getLogger("itzel.actions")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = True     # también sube al log general (itzel.log)

    enabled, max_bytes, _ = _monitoring_cfg()
    if not enabled:
        logger.addHandler(logging.NullHandler())
        return logger

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.addHandler(_rotating_handler(_LOG_DIR / "actions.log", max_bytes, logging.DEBUG))
    return logger


# ─── instancias públicas ──────────────────────────────────────────────────────
# Importar estas instancias en otros módulos. API backward-compatible.
# Import these instances in other modules. Backward-compatible API.

log         = _build_logger("itzel")
log_api     = _build_logger("itzel.api")
log_ws      = _build_logger("itzel.ws")
log_engine  = _build_logger("itzel.engine")
log_actions = _build_actions_logger()


# ─── helpers de conveniencia ──────────────────────────────────────────────────

def log_action(
    action: str,
    tool: str = "",
    status: str = "ok",
    detail: str = "",
) -> None:
    """Registra una acción del sistema en actions.log con campos estructurados.

    Llamar desde agentes y tools después de ejecutar una acción:
        from itzel_core.logger import log_action
        log_action("delete_file", tool="file_agent", detail=str(path))
    """
    log_actions.info(
        "acción: %s", action,
        extra={"action": action, "tool": tool, "status": status, "component": detail},
    )


def get_log_dir() -> Path:
    """Devuelve la ruta del directorio de logs (crea si no existe)."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR
