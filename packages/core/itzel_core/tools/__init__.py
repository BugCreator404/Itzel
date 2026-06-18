"""
itzel_core.tools — Sistema de herramientas (tools) para los agentes de Itzel.

Una tool es una función Python invocable por el LLM (tool calling). El paquete
provee el decorador, el registro/orquestador, la confirmación de acciones
irreversibles, el sandbox de ejecución y el log de auditoría.

Componentes:
    base        — @tool, Tool, ToolRegistry, ToolResult, ActionRequest, errores
    confirm     — ConfirmHandler (Terminal / Auto / Callback)
    sandbox     — SandboxRunner + SandboxResult
    action_log  — ActionLogger (auditoría en ~/.itzel/logs/actions.log)

Uso típico (un agente arma su registry):
    from itzel_core.tools import (
        tool, ToolRegistry, ActionLogger, TerminalConfirmHandler,
    )

    registry = ToolRegistry(
        agent_name="file",
        confirm_handler=TerminalConfirmHandler("es-MX"),
        action_sink=ActionLogger(),
    )
    registry.register(read_file)
    result = registry.invoke("read_file", {"path": "/home/edwin/x.txt"})
"""

from __future__ import annotations

from .action_log import ActionLogger, action_logger
from .base import (
    ActionRequest,
    ActionSink,
    ConfirmationDenied,
    SandboxViolation,
    Tool,
    ToolError,
    ToolParam,
    ToolRegistry,
    ToolResult,
    tool,
)
from .base import (
    ConfirmHandler as ConfirmHandlerType,
)
from .confirm import (
    AutoConfirmHandler,
    AutoDenyHandler,
    CallbackConfirmHandler,
    ConfirmHandler,
    TerminalConfirmHandler,
    describe_action,
)
from .sandbox import SandboxResult, SandboxRunner

__all__ = [
    # base
    "tool",
    "Tool",
    "ToolParam",
    "ToolRegistry",
    "ToolResult",
    "ActionRequest",
    "ActionSink",
    "ToolError",
    "ConfirmationDenied",
    "SandboxViolation",
    "ConfirmHandlerType",
    # confirm
    "ConfirmHandler",
    "TerminalConfirmHandler",
    "AutoConfirmHandler",
    "AutoDenyHandler",
    "CallbackConfirmHandler",
    "describe_action",
    # sandbox
    "SandboxRunner",
    "SandboxResult",
    # action log
    "ActionLogger",
    "action_logger",
]
