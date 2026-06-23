"""Endpoint /api/v1/tools — catálogo de herramientas disponibles (read-only).

ES: Expone el catálogo unificado (skills + agentes + MCP) para que la UI y el
    CLI muestren qué puede hacer Itzel. Solo DESCUBRIMIENTO: no ejecuta nada
    (la ejecución vive en el flujo de agente / `itzel run`, con confirmación).

    MCP es opt-in (?mcp=true) porque conectar a un servidor MCP cuesta
    (subproceso/red); por defecto solo skills + agentes locales.

EN: Read-only unified tool catalog (skills + agents + MCP) for discovery in the
    UI and CLI. Execution lives elsewhere (the agent flow), with confirmation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ...tools.catalog import build_catalog

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("")
def list_tools(
    mcp: bool = Query(False, description="Incluir tools de servidores MCP (conecta)"),
) -> dict[str, Any]:
    """Lista las herramientas que Itzel puede invocar, agrupadas por fuente."""
    catalog = build_catalog(include_mcp=mcp)
    tools = [t.to_dict() for t in catalog]

    by_source: dict[str, int] = {}
    for t in tools:
        kind = t["source"].split(":", 1)[0]   # "agent:file" → "agent"
        by_source[kind] = by_source.get(kind, 0) + 1

    return {"tools": tools, "total": len(tools), "by_source": by_source}
