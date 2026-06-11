"""
CodeAgent — ejecución de código en sandbox para Itzel.

Permite al LLM ejecutar fragmentos de Python o comandos de shell de forma
aislada, usando el SandboxRunner de itzel_core.tools: entorno limpio, timeout
estricto y límite de memoria (best-effort en POSIX).

Seguridad:
    - run_python y run_bash SIEMPRE piden confirmación (ejecutar código es
      sensible). El texto usa la clave i18n 'confirmations.run_code/run_bash'.
    - Se ejecuta en sandbox; ver itzel_core/tools/sandbox.py para los límites
      y la limitación conocida de aislamiento de red.

English summary:
    Sandboxed code execution agent (Python / shell). Both tools always require
    confirmation and run inside the isolated SandboxRunner.
"""

from __future__ import annotations

import shutil
import sys
from typing import Any

from itzel_core.config import config
from itzel_core.tools import SandboxRunner, Tool, tool

from .base_agent import BaseAgent

# Tope duro al timeout que el LLM puede pedir (evita colgar el agente).
_MAX_TIMEOUT = 120


def _runner(timeout: int) -> SandboxRunner:
    """Construye un SandboxRunner con los límites de config.security."""
    sec = config.security
    safe_timeout = max(1, min(timeout, _MAX_TIMEOUT))
    return SandboxRunner(
        timeout       = safe_timeout,
        ram_mb        = sec.sandbox_ram_mb,
        allow_network = False,
    )


def _result_dict(res: Any) -> dict[str, Any]:
    """Convierte un SandboxResult en el dict de retorno de la tool."""
    return {
        "stdout":     res.stdout,
        "stderr":     res.stderr,
        "returncode": res.returncode,
        "timed_out":  res.timed_out,
        "duration_s": res.duration_s,
        "ok":         res.ok,
    }


# ─── tools ────────────────────────────────────────────────────────────────────

@tool(
    "run_python",
    "Ejecuta un fragmento de código Python en un sandbox aislado. Pide confirmación.",
    confirm_if_irreversible=True,
    sandbox=True,
    timeout=_MAX_TIMEOUT,
    param_docs={
        "code":    "Código Python a ejecutar.",
        "timeout": "Tiempo máximo de ejecución en segundos (default: 30).",
    },
)
def run_python(code: str, timeout: int = 30) -> dict[str, Any]:
    """Ejecuta `code` con el intérprete de Python en sandbox."""
    res = _runner(timeout).run_python(code, timeout=timeout)
    return _result_dict(res)


@tool(
    "run_bash",
    "Ejecuta un comando de shell en un sandbox aislado. Pide confirmación.",
    confirm_if_irreversible=True,
    sandbox=True,
    timeout=_MAX_TIMEOUT,
    param_docs={
        "command": "Comando de shell a ejecutar.",
        "timeout": "Tiempo máximo de ejecución en segundos (default: 30).",
    },
)
def run_bash(command: str, timeout: int = 30) -> dict[str, Any]:
    """
    Ejecuta un comando de shell en sandbox.

    En Windows no siempre hay bash: si existe (Git Bash / WSL) se usa, y si no
    se cae a PowerShell. En POSIX se usa /bin/bash o /bin/sh.
    """
    bash = shutil.which("bash")

    if bash is not None:
        argv = [bash, "-c", command]
    elif sys.platform.startswith("win"):
        # Fallback a PowerShell en Windows sin bash.
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    else:
        sh = shutil.which("sh") or "/bin/sh"
        argv = [sh, "-c", command]

    res = _runner(timeout).run_argv(argv, timeout=timeout)
    out = _result_dict(res)
    out["shell"] = argv[0]
    return out


# ─── el agente ────────────────────────────────────────────────────────────────

class CodeAgent(BaseAgent):
    """Agente de ejecución de código en sandbox. 2 tools, confirmación obligatoria."""

    name = "code"

    _TOOLS: list[Tool] = [
        run_python,
        run_bash,
    ]

    def _register_tools(self) -> None:
        self.registry.register_all(self._TOOLS)
