"""
itzel-agents — Agentes de Itzel para operar la computadora del usuario.

Cada agente agrupa tools relacionadas y las expone al LLM con confirmación de
acciones irreversibles, sandbox para código y auditoría local. 100% local.

Agentes:
    FileAgent      — leer, escribir, mover, copiar, borrar, listar, buscar.
    SystemAgent    — abrir/cerrar apps, portapapeles, notificaciones, sysinfo.
    CodeAgent      — ejecutar Python / shell en sandbox aislado.
    DocumentAgent  — leer PDF/DOCX/XLSX, escribir DOCX, resumir con Itzel-1B.

Uso rápido:
    from itzel_agents import create_all_agents
    from itzel_core.tools import TerminalConfirmHandler

    agents = create_all_agents(
        confirm_handler=TerminalConfirmHandler("es-MX"),
    )
    result = agents["file"].invoke("read_file", {"path": "~/notas.txt"})

    # Esquemas de TODAS las tools para entregar al LLM:
    schemas = [s for a in agents.values() for s in a.schemas()]

Privacidad:
    Ninguna acción ni dato sale de la máquina. Cada acción se audita en
    ~/.itzel/logs/actions.log.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Optional

from itzel_core.tools import ActionLogger, ConfirmHandler

from .base_agent import BaseAgent
from .code_agent import CodeAgent
from .document_agent import DocumentAgent
from .file_agent import FileAgent
from .system_agent import SystemAgent

__version__ = "0.1.0"
__all__ = [
    "BaseAgent",
    "FileAgent",
    "SystemAgent",
    "CodeAgent",
    "DocumentAgent",
    "create_all_agents",
    "check_dependencies",
]


# ─── factory ──────────────────────────────────────────────────────────────────

def create_all_agents(
    *,
    confirm_handler: Optional[ConfirmHandler] = None,
    action_sink:     Optional[ActionLogger] = None,
    summarizer:      Optional[Callable[[str], str]] = None,
) -> dict[str, BaseAgent]:
    """
    Instancia los cuatro agentes con el mismo handler de confirmación y logger.

    Args:
        confirm_handler: Handler de confirmación compartido (CLI / UI).
                         Sin handler, las acciones irreversibles se rechazan.
        action_sink:     Logger de auditoría compartido (default: log global).
        summarizer:      Función que resume texto con Itzel-1B (para DocumentAgent).

    Returns:
        Dict {"file": FileAgent, "system": SystemAgent,
              "code": CodeAgent, "document": DocumentAgent}
    """
    common: dict[str, Any] = {
        "confirm_handler": confirm_handler,
        "action_sink":     action_sink,
    }
    return {
        "file":     FileAgent(**common),
        "system":   SystemAgent(**common),
        "code":     CodeAgent(**common),
        "document": DocumentAgent(summarizer=summarizer, **common),
    }


# ─── verificación de dependencias ─────────────────────────────────────────────

def check_dependencies() -> dict[str, bool]:
    """
    Reporta qué dependencias opcionales de los agentes están instaladas.

    Returns:
        Dict nombre → bool. Ejemplo:
        {
          "psutil":      True,    # SystemAgent.get_system_info
          "pyperclip":   True,    # SystemAgent clipboard
          "plyer":       False,   # SystemAgent.send_notification (opcional)
          "pdfminer":    True,    # DocumentAgent.read_pdf
          "docx":        True,    # DocumentAgent read/write docx
          "openpyxl":    False,   # DocumentAgent.read_xlsx
        }
    """
    modules = {
        "psutil":    "psutil",
        "pyperclip": "pyperclip",
        "plyer":     "plyer",
        "pdfminer":  "pdfminer.high_level",
        "docx":      "docx",
        "openpyxl":  "openpyxl",
    }
    result: dict[str, bool] = {}
    for label, module in modules.items():
        try:
            importlib.import_module(module)
            result[label] = True
        except ImportError:
            result[label] = False
    return result
