"""
Clase base para todos los agentes de Itzel.

Un agente agrupa un conjunto de tools relacionadas (archivos, sistema, código,
documentos) y las expone al LLM a través de un ToolRegistry propio. La base
cablea el registry con la confirmación de acciones irreversibles, el sandbox y
el log de auditoría, para que cada agente concreto solo declare sus @tool.

Diseño:
    class FileAgent(BaseAgent):
        name = "file"
        def _register_tools(self):
            self.registry.register(self._read_file_tool())
            ...

    agent = FileAgent(confirm_handler=TerminalConfirmHandler("es-MX"))
    schemas = agent.schemas()                 # para entregar al LLM
    result  = agent.invoke("read_file", {...})  # ejecutar una tool

Seguridad:
    Sin confirm_handler, las acciones irreversibles SE RECHAZAN (fail-safe).
    Toda acción se audita en ~/.itzel/logs/actions.log por defecto.

English summary:
    Base class wiring a ToolRegistry (confirmation + audit) for concrete agents.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

from itzel_core.config import SecurityConfig, config
from itzel_core.tools import (
    ActionLogger,
    ConfirmHandler,
    ToolRegistry,
    ToolResult,
    action_logger,
)


class BaseAgent(ABC):
    """
    Base común de los agentes. Subclases definen `name` y `_register_tools()`.
    """

    #: Nombre corto del agente — aparece en el log de auditoría ("file", "system"…).
    name: str = "agent"

    def __init__(
        self,
        *,
        confirm_handler: Optional[ConfirmHandler] = None,
        action_sink:     Optional[ActionLogger] = None,
        security:        Optional[SecurityConfig] = None,
    ) -> None:
        self.security = security or config.security
        self.registry = ToolRegistry(
            agent_name      = self.name,
            confirm_handler = confirm_handler,
            # Por defecto, todos los agentes auditan al mismo log compartido.
            action_sink     = action_sink or action_logger,
        )
        self._register_tools()

    # ── contrato de subclases ─────────────────────────────────────────────────

    @abstractmethod
    def _register_tools(self) -> None:
        """Registra las tools del agente en self.registry."""

    # ── API uniforme ──────────────────────────────────────────────────────────

    def schemas(self) -> list[dict[str, Any]]:
        """Esquemas JSON de las tools del agente — para entregar al LLM."""
        return self.registry.all_schemas()

    def invoke(self, name: str, args: Optional[dict[str, Any]] = None) -> ToolResult:
        """Ejecuta una tool del agente (con confirmación + auditoría)."""
        return self.registry.invoke(name, args or {})

    @property
    def tools(self) -> list[str]:
        """Nombres de las tools registradas."""
        return self.registry.names

    def shutdown(self) -> None:
        """Libera el executor de threads del registry."""
        self.registry.shutdown()

    # ── helpers compartidos ───────────────────────────────────────────────────

    @staticmethod
    def _resolve_path(path: str, *, must_exist: bool = False) -> Path:
        """
        Normaliza una ruta: expande ~ y variables, y la vuelve absoluta.

        Args:
            path:       Ruta de entrada (relativa, con ~, etc.).
            must_exist: Si True, lanza FileNotFoundError si no existe.

        Returns:
            Path absoluto y normalizado.
        """
        import os

        expanded = os.path.expandvars(os.path.expanduser(path))
        p = Path(expanded).resolve()
        if must_exist and not p.exists():
            raise FileNotFoundError(f"No existe la ruta: {p}")
        return p

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} tools={len(self.registry)}>"
