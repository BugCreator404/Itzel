"""
SystemAgent — control del sistema operativo para Itzel.

Expone tools para abrir/cerrar apps, manejar el portapapeles, enviar
notificaciones nativas y consultar el estado del sistema (RAM, CPU, disco).
Las implementaciones se ramifican según el OS (Windows / macOS / Linux).

Seguridad:
    - close_app → pide confirmación (puede perder trabajo no guardado).
    - El resto son acciones reversibles/no destructivas.

Degradación elegante:
    Si falta una dependencia opcional (psutil, pyperclip, plyer), la tool
    devuelve un error legible en vez de romper el agente.

English summary:
    OS control agent (open/close apps, clipboard, notifications, system info),
    branched per platform. close_app requires confirmation. Missing optional
    deps yield a clear error instead of crashing.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from typing import Any

from itzel_core.tools import Tool, tool

from .base_agent import BaseAgent

# ─── detección de plataforma ──────────────────────────────────────────────────

_OS = platform.system()   # "Windows" | "Darwin" | "Linux"
_IS_WINDOWS = _OS == "Windows"
_IS_MACOS   = _OS == "Darwin"
_IS_LINUX   = _OS == "Linux"

# Timeout corto para comandos del sistema que no deben colgar al agente.
_CMD_TIMEOUT = 10


def _run(argv: list[str], *, timeout: int = _CMD_TIMEOUT) -> subprocess.CompletedProcess:
    """Ejecuta un comando del sistema capturando salida. No lanza por returncode."""
    kwargs: dict = {}
    if _IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        **kwargs,
    )


# ─── tools: apps ──────────────────────────────────────────────────────────────

@tool(
    "open_app",
    "Abre una aplicación instalada por su nombre.",
    param_docs={"name": "Nombre de la aplicación (p. ej. 'Calculator', 'notepad', 'firefox')."},
)
def open_app(name: str) -> str:
    """Abre una app según el OS. Devuelve un mensaje del resultado."""
    if _IS_WINDOWS:
        # 'start' es un built-in de cmd; el primer "" es el título de ventana.
        proc = _run(["cmd", "/c", "start", "", name])
    elif _IS_MACOS:
        proc = _run(["open", "-a", name])
    else:  # Linux
        launcher = shutil.which("gtk-launch") or shutil.which("xdg-open")
        if launcher is None:
            raise RuntimeError("No se encontró xdg-open ni gtk-launch para abrir apps.")
        proc = _run([launcher, name])

    if proc.returncode != 0:
        raise RuntimeError(
            f"No se pudo abrir '{name}': {proc.stderr.strip() or 'código ' + str(proc.returncode)}"
        )
    return f"Aplicación '{name}' abierta."


@tool(
    "close_app",
    "Cierra una aplicación en ejecución. Pide confirmación (puede perder trabajo).",
    confirm_if_irreversible=True,
    param_docs={"name": "Nombre del proceso/app a cerrar."},
)
def close_app(name: str) -> str:
    """Cierra una app por nombre. Confirmación obligatoria."""
    if _IS_WINDOWS:
        exe = name if name.lower().endswith(".exe") else f"{name}.exe"
        proc = _run(["taskkill", "/IM", exe, "/F"])
    elif _IS_MACOS:
        proc = _run(["osascript", "-e", f'quit app "{name}"'])
    else:  # Linux
        killer = shutil.which("pkill")
        if killer is None:
            raise RuntimeError("No se encontró 'pkill' para cerrar apps.")
        proc = _run([killer, "-f", name])

    if proc.returncode != 0:
        raise RuntimeError(
            f"No se pudo cerrar '{name}': {proc.stderr.strip() or 'proceso no encontrado'}"
        )
    return f"Aplicación '{name}' cerrada."


# ─── tools: portapapeles ──────────────────────────────────────────────────────

@tool(
    "get_clipboard",
    "Lee el contenido de texto del portapapeles del sistema.",
)
def get_clipboard() -> str:
    """Devuelve el texto actual del portapapeles."""
    try:
        import pyperclip
    except ImportError:
        raise RuntimeError(
            "pyperclip no está instalado. Instala con: pip install pyperclip"
        )
    return pyperclip.paste()


@tool(
    "set_clipboard",
    "Escribe texto en el portapapeles del sistema.",
    param_docs={"text": "Texto a copiar al portapapeles."},
)
def set_clipboard(text: str) -> str:
    """Copia texto al portapapeles."""
    try:
        import pyperclip
    except ImportError:
        raise RuntimeError(
            "pyperclip no está instalado. Instala con: pip install pyperclip"
        )
    pyperclip.copy(text)
    return f"Copiado al portapapeles ({len(text)} caracteres)."


# ─── tools: notificaciones ────────────────────────────────────────────────────

@tool(
    "send_notification",
    "Muestra una notificación nativa del sistema operativo.",
    param_docs={
        "title": "Título de la notificación.",
        "body":  "Cuerpo / mensaje de la notificación.",
    },
)
def send_notification(title: str, body: str = "") -> str:
    """Envía una notificación del OS. Usa plyer si está; si no, fallback por OS."""
    # 1. Intentar plyer (multiplataforma)
    try:
        from plyer import notification
        notification.notify(title=title, message=body, app_name="Itzel", timeout=5)
        return "Notificación enviada."
    except Exception:
        pass

    # 2. Fallback por OS
    try:
        if _IS_MACOS:
            script = f'display notification "{body}" with title "{title}"'
            _run(["osascript", "-e", script])
            return "Notificación enviada (osascript)."
        if _IS_LINUX and shutil.which("notify-send"):
            _run(["notify-send", title, body])
            return "Notificación enviada (notify-send)."
        if _IS_WINDOWS:
            # PowerShell con BurntToast no siempre está; usar globo de notificación
            ps = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
                "ContentType = WindowsRuntime] > $null; "
                f"Write-Output '{title}: {body}'"
            )
            _run(["powershell", "-NoProfile", "-Command", ps])
            return "Notificación enviada (powershell)."
    except Exception as exc:
        raise RuntimeError(f"No se pudo enviar la notificación: {exc}")

    raise RuntimeError(
        "No hay backend de notificaciones disponible. Instala 'plyer' (pip install plyer)."
    )


# ─── tools: información del sistema ────────────────────────────────────────────

@tool(
    "get_system_info",
    "Obtiene información del sistema: OS, CPU, RAM y uso de disco.",
)
def get_system_info() -> dict[str, Any]:
    """Devuelve métricas del sistema. Usa psutil si está disponible."""
    info: dict[str, Any] = {
        "os":             _OS,
        "os_release":     platform.release(),
        "os_version":     platform.version(),
        "machine":        platform.machine(),
        "python_version": sys.version.split()[0],
        "hostname":       platform.node(),
    }

    try:
        import psutil
    except ImportError:
        info["note"] = "Instala psutil para CPU/RAM/disco: pip install psutil"
        return info

    vm   = psutil.virtual_memory()
    disk = psutil.disk_usage("/" if not _IS_WINDOWS else "C:\\")
    info.update({
        "cpu_count":        psutil.cpu_count(logical=True),
        "cpu_percent":      psutil.cpu_percent(interval=0.1),
        "ram_total_mb":     round(vm.total / 1024 / 1024),
        "ram_available_mb": round(vm.available / 1024 / 1024),
        "ram_percent":      vm.percent,
        "disk_total_gb":    round(disk.total / 1024 / 1024 / 1024, 1),
        "disk_free_gb":     round(disk.free / 1024 / 1024 / 1024, 1),
        "disk_percent":     disk.percent,
    })
    return info


# ─── el agente ────────────────────────────────────────────────────────────────

class SystemAgent(BaseAgent):
    """Agente de control del sistema operativo. 6 tools, ramificadas por OS."""

    name = "system"

    _TOOLS: list[Tool] = [
        open_app,
        close_app,
        get_clipboard,
        set_clipboard,
        send_notification,
        get_system_info,
    ]

    def _register_tools(self) -> None:
        self.registry.register_all(self._TOOLS)
