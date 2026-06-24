"""catalog.py — Catálogo unificado de herramientas (tools) para Itzel.

ES: Junta en un solo lugar TODO lo que el modelo puede invocar:
      • Skills (core)          — siempre disponibles (calculator, clipboard…).
      • Agentes (itzel-agents) — File/System/Code/Document, si el paquete está.
      • MCP (itzel-mcp)        — servidores configurados, bajo demanda (opt-in).

    Importa agents y MCP de forma OPCIONAL (try/except): si esos paquetes no
    están instalados, el catálogo degrada a lo que haya, sin romper. Así el
    core NUNCA depende de ellos (evita dependencia circular).

    Cada entrada trae su `schema` (formato tool-use de Anthropic/OpenAI), su
    `source` (de dónde viene) y un `invoke(args)` que despacha a la fuente
    correcta aplicando SU confirmación y SU auditoría (vía ToolRegistry).

EN: Unified, source-tagged tool catalog (skills + optional agents + optional
    MCP). Discovery (schemas) + dispatch (invoke), with graceful degradation.

Uso / Usage:
    from itzel_core.tools.catalog import build_catalog
    for t in build_catalog():
        print(t.source, t.name)
        # result = t.invoke({"path": "/x"})
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..logger import log_engine
from .base import ActionSink, ConfirmHandler, Tool, ToolRegistry, ToolResult


@dataclass
class CatalogTool:
    """Una herramienta del catálogo, lista para descubrir e invocar."""
    name:        str
    description: str
    schema:      dict[str, Any]
    source:      str                 # "skill" | "agent:file" | "mcp:<server>"
    _invoke:     Callable[[dict[str, Any]], ToolResult]

    def invoke(self, args: dict[str, Any] | None = None) -> ToolResult:
        """Ejecuta la tool en su fuente (con confirmación + auditoría)."""
        return self._invoke(args or {})

    def to_dict(self) -> dict[str, Any]:
        """Vista serializable para la API / el CLI (sin la función invoke)."""
        return {
            "name":         self.name,
            "description":  self.description,
            "source":       self.source,
            "input_schema": self.schema.get("input_schema", {}),
        }


def build_catalog(
    *,
    confirm_handler: ConfirmHandler | None = None,
    action_sink:     ActionSink | None = None,
    include_agents:  bool = True,
    include_mcp:     bool = False,
) -> list[CatalogTool]:
    """Ensambla el catálogo de tools disponibles.

    Skills siempre; agentes si `include_agents` y el paquete está; MCP si
    `include_mcp` y hay servidores configurados. Nada lanza: cada fuente que
    falle se omite con un aviso en el log.
    """
    catalog: list[CatalogTool] = []
    catalog += _skill_tools(confirm_handler, action_sink)
    if include_agents:
        catalog += _agent_tools(confirm_handler, action_sink)
    if include_mcp:
        catalog += _mcp_tools(confirm_handler, action_sink)
    return catalog


# ─── fuentes ────────────────────────────────────────────────────────────────────

def _registry_entry(t: Tool, source: str, reg: ToolRegistry) -> CatalogTool:
    """Envuelve una Tool para invocarla vía su ToolRegistry (confirma + audita)."""
    def _invoke(args: dict[str, Any]) -> ToolResult:
        return reg.invoke(t.name, args)

    return CatalogTool(
        name        = t.name,
        description = t.description,
        schema      = t.to_schema(),
        source      = source,
        _invoke     = _invoke,
    )


def _agent_entry(agent: Any, aname: str, sch: dict[str, Any]) -> CatalogTool:
    """Envuelve una tool de agente; despacha invoke al propio agente."""
    name = sch["name"]

    def _invoke(args: dict[str, Any]) -> ToolResult:
        return agent.invoke(name, args)   # type: ignore[no-any-return]

    return CatalogTool(
        name        = name,
        description = sch.get("description", ""),
        schema      = sch,
        source      = f"agent:{aname}",
        _invoke     = _invoke,
    )


def _skill_tools(
    confirm_handler: ConfirmHandler | None,
    action_sink:     ActionSink | None,
) -> list[CatalogTool]:
    """Tools de las skills cargadas (core) — siempre intentadas."""
    try:
        from ..skills.loader import SkillLoader
        loader = SkillLoader()
        loader.load_all()
        reg = ToolRegistry(
            agent_name="skill",
            confirm_handler=confirm_handler,
            action_sink=action_sink,
        )
        out: list[CatalogTool] = []
        for t in loader.all_tools():
            try:
                reg.register(t)
            except ValueError:
                continue  # nombre duplicado — saltar
            out.append(_registry_entry(t, "skill", reg))
        return out
    except Exception as exc:
        log_engine.warning("Catálogo: skills no disponibles: %s", exc,
                           extra={"component": "tools"})
        return []


def _agent_tools(
    confirm_handler: ConfirmHandler | None,
    action_sink:     ActionSink | None,
) -> list[CatalogTool]:
    """Tools de los agentes (paquete opcional itzel-agents)."""
    try:
        from itzel_agents import create_all_agents
    except ImportError:
        return []  # paquete no instalado — sin agentes, sin drama
    try:
        agents = create_all_agents(
            confirm_handler=confirm_handler,
            action_sink=action_sink,
        )
    except Exception as exc:
        log_engine.warning("Catálogo: agentes no disponibles: %s", exc,
                           extra={"component": "tools"})
        return []

    out: list[CatalogTool] = []
    for aname, agent in agents.items():
        for sch in agent.schemas():
            out.append(_agent_entry(agent, aname, sch))
    return out


def _mcp_tools(
    confirm_handler: ConfirmHandler | None,
    action_sink:     ActionSink | None,
) -> list[CatalogTool]:
    """Tools de los servidores MCP configurados (paquete opcional itzel-mcp).

    Conectar a un servidor MCP cuesta (subproceso/red), por eso es opt-in.
    """
    try:
        from itzel_mcp.client import connect_all
    except ImportError:
        return []
    try:
        from ..config import config
        servers = list(getattr(config, "mcp_servers", []) or [])
        if not servers:
            return []
        clients, errors = connect_all(servers)
        for name, msg in errors.items():
            log_engine.warning("Catálogo: MCP '%s' no disponible: %s", name, msg,
                               extra={"component": "tools"})
        out: list[CatalogTool] = []
        for sname, client in clients.items():
            reg = ToolRegistry(
                agent_name=f"mcp:{sname}",
                confirm_handler=confirm_handler,
                action_sink=action_sink,
            )
            for t in client.as_tools():
                try:
                    reg.register(t)
                except ValueError:
                    continue
                out.append(_registry_entry(t, f"mcp:{sname}", reg))
        return out
    except Exception as exc:
        log_engine.warning("Catálogo: MCP falló: %s", exc, extra={"component": "tools"})
        return []
