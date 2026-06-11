"""
Puente de auditoría: acciones de los agentes → BD cifrada.

En Sesión 9, el ActionLogger (itzel_core.tools) escribe cada acción a un archivo
JSON (~/.itzel/logs/actions.log). Este módulo añade el segundo sink: escribir
también a la tabla `actions_log` de la BD cifrada, para poder consultarla con
filtros desde la UI/CLI.

Diseño (desacoplado):
    El tool system (tools/) NO depende de la BD. Este módulo, que vive en el
    core, importa de ambos y los une. El integrador combina los sinks:

        from itzel_core.tools import ActionLogger
        from itzel_core.audit import DBActionSink, combined_sink

        sink = combined_sink(ActionLogger(), DBActionSink())
        registry = ToolRegistry(action_sink=sink)

English summary:
    Bridge that mirrors agent actions into the encrypted `actions_log` table,
    complementing the file-based ActionLogger from Session 9 (dual sink).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import uuid4

from .db import Database, get_db
from .tools.base import ActionRequest, ActionSink, ToolResult

log = logging.getLogger("itzel.audit")

_TARGET_KEYS = ("path", "src", "source", "dst", "dest", "name", "app", "query")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_target(args: dict[str, Any]) -> str:
    for key in _TARGET_KEYS:
        if key in args and args[key] is not None:
            return str(args[key])
    return ""


def _result_label(result: ToolResult) -> str:
    if result.denied:
        return "denied"
    return "ok" if result.ok else "error"


# ─── sink a la BD ─────────────────────────────────────────────────────────────

class DBActionSink:
    """
    Sink que escribe cada acción a la tabla `actions_log` de la BD cifrada.

    Usable directamente como action_sink del ToolRegistry:
        registry = ToolRegistry(action_sink=DBActionSink())
    """

    def __init__(self, db: Optional[Database] = None) -> None:
        self._db = db or get_db()

    def __call__(self, request: ActionRequest, result: ToolResult) -> None:
        details = json.dumps(request.args, ensure_ascii=False)
        try:
            self._db.execute(
                "INSERT INTO actions_log "
                "(id, created_at, agent, action, target, details, result) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    _now(),
                    request.agent or "unknown",
                    request.tool_name,
                    _extract_target(request.args),
                    details,
                    _result_label(result),
                ),
            )
        except Exception as exc:
            # La auditoría nunca debe romper la ejecución del agente.
            log.warning("No se pudo registrar la acción en la BD: %s", exc)


# ─── sink combinado ───────────────────────────────────────────────────────────

def combined_sink(*sinks: ActionSink) -> ActionSink:
    """
    Combina varios sinks en uno. Cada acción se envía a todos (dual o más).
    Un fallo en un sink no impide que los demás reciban la acción.
    """
    def _fanout(request: ActionRequest, result: ToolResult) -> None:
        for sink in sinks:
            try:
                sink(request, result)
            except Exception:
                pass
    return _fanout


# ─── consulta ─────────────────────────────────────────────────────────────────

def get_actions(
    db:    Optional[Database] = None,
    *,
    limit: int = 50,
    agent: Optional[str] = None,
) -> list[dict]:
    """
    Devuelve las últimas acciones registradas en la BD, más recientes primero.

    Args:
        db:    BD a consultar (default: la compartida).
        limit: máximo de filas.
        agent: si se da, filtra por agente ("file", "system", …).
    """
    db = db or get_db()
    if agent:
        rows = db.query(
            "SELECT id, created_at, agent, action, target, details, result "
            "FROM actions_log WHERE agent = ? ORDER BY created_at DESC LIMIT ?",
            (agent, limit),
        )
    else:
        rows = db.query(
            "SELECT id, created_at, agent, action, target, details, result "
            "FROM actions_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    return [dict(r) for r in rows]
