"""
Auditoría de acciones de los agentes.

Cada acción que un agente ejecuta (leer, escribir, borrar, abrir app, correr
código…) se registra en ~/.itzel/logs/actions.log como una línea JSON. El
usuario puede revisar el historial completo de lo que Itzel ha hecho en su
máquina — transparencia total, sin telemetría (los logs nunca salen del equipo).

Formato de cada línea (JSON):
    {
      "ts": "2026-06-11T20:15:03.123+00:00",
      "agent": "file",
      "action": "delete_file",
      "target": "/home/edwin/notas.txt",
      "result": "ok" | "denied" | "error",
      "error": null,
      "irreversible": true,
      "sandboxed": false
    }

Vista legible (format_human):
    2026-06-11T20:15:03 | file | delete_file | /home/edwin/notas.txt | ok

English summary:
    Append-only JSON audit log of every agent action, stored locally at
    ~/.itzel/logs/actions.log. Plugs into ToolRegistry as its action_sink.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .base import ActionRequest, ToolResult

# Misma convención de ubicación que itzel_core/logger.py
_LOG_DIR     = Path.home() / ".itzel" / "logs"
_LOG_FILE    = _LOG_DIR / "actions.log"
_MAX_TARGET  = 300   # truncar rutas/targets muy largos en el log

# Claves de args que típicamente representan el "objetivo" de la acción,
# en orden de prioridad para la columna "target".
_TARGET_KEYS = ("path", "src", "source", "dst", "dest", "name", "app", "query")


# ─── helpers ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _extract_target(args: dict[str, Any]) -> str:
    """Elige un 'target' representativo de los args para la columna del log."""
    for key in _TARGET_KEYS:
        if key in args and args[key] is not None:
            val = str(args[key])
            return val if len(val) <= _MAX_TARGET else val[:_MAX_TARGET] + "…"
    return ""


def _result_label(result: ToolResult) -> str:
    """Etiqueta corta del resultado: ok | denied | error."""
    if result.denied:
        return "denied"
    return "ok" if result.ok else "error"


# ─── logger ───────────────────────────────────────────────────────────────────

class ActionLogger:
    """
    Registra acciones de agentes en un log JSON local (append-only).

    Se conecta al ToolRegistry como action_sink:
        logger   = ActionLogger()
        registry = ToolRegistry(agent_name="file", action_sink=logger)
    """

    def __init__(self, log_file: Path | None = None) -> None:
        self.log_file = log_file or _LOG_FILE
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ── escritura (interfaz action_sink) ──────────────────────────────────────

    def __call__(self, request: ActionRequest, result: ToolResult) -> None:
        """Permite usar la instancia directamente como action_sink."""
        self.log(request, result)

    def log(self, request: ActionRequest, result: ToolResult) -> None:
        """Añade una entrada al log de auditoría. Nunca lanza excepción."""
        entry = {
            "ts":           _now_iso(),
            "agent":        request.agent or "unknown",
            "action":       request.tool_name,
            "target":       _extract_target(request.args),
            "result":       _result_label(result),
            "error":        result.error,
            "irreversible": request.irreversible,
            "sandboxed":    request.sandboxed,
        }
        line = json.dumps(entry, ensure_ascii=False)
        try:
            with self._lock:
                with self.log_file.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            # La auditoría nunca debe romper la ejecución del agente.
            pass

    # ── lectura ───────────────────────────────────────────────────────────────

    def read_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """
        Devuelve las últimas `limit` acciones (más recientes al final).

        Líneas corruptas se ignoran silenciosamente.
        """
        if not self.log_file.exists():
            return []
        try:
            with self.log_file.open(encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            return []

        entries: list[dict[str, Any]] = []
        for raw in lines[-limit:]:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entries.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return entries

    def clear(self) -> None:
        """Borra el log de acciones (acción irreversible — usar con cuidado)."""
        try:
            if self.log_file.exists():
                self.log_file.unlink()
        except Exception:
            pass

    # ── formato legible ───────────────────────────────────────────────────────

    @staticmethod
    def format_human(entry: dict[str, Any]) -> str:
        """
        Formatea una entrada como línea legible:
            timestamp | agent | action | target | result
        """
        ts = entry.get("ts", "")
        # Recortar al segundo para legibilidad (quita ms y zona).
        if "T" in ts:
            ts = ts.split(".")[0].replace("T", " ")
        return " | ".join([
            ts,
            entry.get("agent", "?"),
            entry.get("action", "?"),
            entry.get("target", "") or "—",
            entry.get("result", "?"),
        ])


# ─── instancia compartida (opcional) ──────────────────────────────────────────
# Los agentes pueden importar este singleton o crear el suyo propio.

action_logger = ActionLogger()
