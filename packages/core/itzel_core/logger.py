"""Logger estructurado en JSON para Itzel.

Escribe a data/logs/itzel.log con rotación diaria.
Sin telemetría — los logs son locales y nunca salen de la máquina.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOG_DIR = Path.home() / ".itzel" / "logs"
_LOG_FILE = _LOG_DIR / "itzel.log"
_MAX_BYTES = 5 * 1024 * 1024   # 5 MB por archivo
_BACKUP_COUNT = 3               # máximo 3 archivos rotados


class _JsonFormatter(logging.Formatter):
    """Formatea cada log entry como una línea JSON."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        # Campos extra que se pueden pasar con logger.info("...", extra={...})
        for key in ("req_id", "path", "method", "status", "latency_ms", "component"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        return json.dumps(entry, ensure_ascii=False)


def _build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # ya configurado

    logger.setLevel(logging.DEBUG)

    # ── Handler archivo (rotación por tamaño) ──────────────────────
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        _LOG_FILE,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(_JsonFormatter())
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    # ── Handler consola (solo en desarrollo) ───────────────────────
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(_JsonFormatter())
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)

    return logger


# Instancias públicas — importar estas en otros módulos
log = _build_logger("itzel")
log_api = _build_logger("itzel.api")
log_ws = _build_logger("itzel.ws")
log_engine = _build_logger("itzel.engine")
