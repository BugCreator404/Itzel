"""
Handlers de confirmación para acciones irreversibles.

El ToolRegistry no sabe nada de la UI: recibe un ConfirmHandler inyectado
que decide si una acción irreversible (borrar, sobrescribir…) procede.

Implementaciones incluidas:
    TerminalConfirmHandler  — pregunta en la terminal (CLI), timeout 30s.
    AutoConfirmHandler      — siempre acepta (tests / modo no-interactivo).
    AutoDenyHandler         — siempre rechaza (máxima seguridad).

El texto se muestra bilingüe (ES-MX / EN-US) reutilizando el i18n del core.
Si no hay respuesta en 30s, la acción se CANCELA (principio #5: confirmar
antes de acciones irreversibles; el silencio nunca es un "sí").

English summary:
    Injectable confirmation handlers. The terminal one prompts y/n with a
    30s timeout and defaults to "deny" on timeout (fail-safe).
"""

from __future__ import annotations

import sys
import threading
from abc import ABC, abstractmethod
from typing import Callable, Optional

from ..i18n.i18n import T
from .base import ActionRequest

# ─── textos del prompt (bilingüe, autocontenido) ──────────────────────────────
# Solo lo específico del prompt interactivo; los mensajes por acción vienen
# de la sección "confirmations" del i18n.

_PROMPT = {
    "es-MX": {
        "ask":     "Confirmar [s/N]: ",
        "yes":     {"s", "si", "sí", "y", "yes"},
        "timeout": "Sin respuesta en {secs}s — acción cancelada por seguridad.",
        "denied":  "Acción cancelada.",
    },
    "en-US": {
        "ask":     "Confirm [y/N]: ",
        "yes":     {"y", "yes"},
        "timeout": "No response in {secs}s — action cancelled for safety.",
        "denied":  "Action cancelled.",
    },
}


def describe_action(request: ActionRequest, lang: str = "es-MX") -> str:
    """
    Genera el texto humano que describe la acción a confirmar.

    Intenta usar una clave específica `confirmations.<tool_name>` del i18n
    (formateada con los args de la acción); si no existe, cae al resumen
    genérico más `cli.confirm_action`.
    """
    t = T(lang)
    key = f"confirmations.{request.tool_name}"
    specific = t.get(key, **request.args)

    # Si la clave no existía, T.get() devuelve la clave tal cual.
    if specific != key:
        return specific

    # Fallback genérico: resumen de la acción + "¿Estás seguro?"
    return f"{request.summary}\n{t.get('cli.confirm_action')}"


# ─── interfaz ─────────────────────────────────────────────────────────────────

class ConfirmHandler(ABC):
    """Decide si una acción irreversible procede. True = permitir."""

    @abstractmethod
    def __call__(self, request: ActionRequest) -> bool:
        ...


# ─── handlers automáticos (tests / no-interactivo) ────────────────────────────

class AutoConfirmHandler(ConfirmHandler):
    """Acepta todo automáticamente. Útil en tests y modos batch confiables."""

    def __call__(self, request: ActionRequest) -> bool:  # noqa: D401
        return True


class AutoDenyHandler(ConfirmHandler):
    """Rechaza todo automáticamente. Máxima seguridad / modo solo-lectura."""

    def __call__(self, request: ActionRequest) -> bool:  # noqa: D401
        return False


# ─── handler de terminal (CLI) ────────────────────────────────────────────────

class TerminalConfirmHandler(ConfirmHandler):
    """
    Pregunta al usuario por la terminal antes de una acción irreversible.

    - Muestra la descripción bilingüe de la acción.
    - Lee s/n con timeout (default 30s).
    - Timeout o respuesta vacía → False (cancela, fail-safe).

    El `input_fn` y `print_fn` son inyectables para poder testear sin stdin.
    """

    def __init__(
        self,
        lang:     str = "es-MX",
        *,
        timeout:  int = 30,
        input_fn: Callable[[str], str] = input,
        print_fn: Callable[[str], None] = print,
    ) -> None:
        self.lang     = lang if lang in _PROMPT else "es-MX"
        self.timeout  = timeout
        self._input_fn = input_fn
        self._print_fn = print_fn

    def __call__(self, request: ActionRequest) -> bool:
        msg = describe_action(request, self.lang)
        strings = _PROMPT[self.lang]

        self._print_fn(msg)
        answer = self._read_with_timeout(strings["ask"])

        if answer is None:
            self._print_fn(strings["timeout"].format(secs=self.timeout))
            return False

        allowed = answer.strip().lower() in strings["yes"]
        if not allowed:
            self._print_fn(strings["denied"])
        return allowed

    # ── lectura con timeout (cross-platform) ──────────────────────────────────

    def _read_with_timeout(self, prompt: str) -> Optional[str]:
        """
        Lee una línea de stdin con timeout.

        Usa un thread daemon porque input() no es interrumpible y select()
        no funciona sobre stdin en Windows. Si el thread sigue bloqueado tras
        el timeout, devolvemos None (el daemon muere con el proceso).
        """
        result: list[Optional[str]] = [None]

        def _reader() -> None:
            try:
                result[0] = self._input_fn(prompt)
            except (EOFError, KeyboardInterrupt):
                result[0] = None

        thread = threading.Thread(target=_reader, daemon=True)
        thread.start()
        thread.join(timeout=self.timeout)

        if thread.is_alive():
            # Timeout: empujar un salto de línea ayuda a "soltar" el prompt visual.
            try:
                sys.stdout.write("\n")
                sys.stdout.flush()
            except Exception:
                pass
            return None
        return result[0]


# ─── handler basado en callback (UI Tauri / WebSocket) ────────────────────────

class CallbackConfirmHandler(ConfirmHandler):
    """
    Delega la confirmación a un callback arbitrario.

    Pensado para la UI de escritorio: el callback dispara el modal con la
    mascota en modo "think" y bloquea hasta que el usuario responde (o el
    modal expira). El callback debe devolver bool.
    """

    def __init__(self, callback: Callable[[ActionRequest], bool]) -> None:
        self._callback = callback

    def __call__(self, request: ActionRequest) -> bool:
        try:
            return bool(self._callback(request))
        except Exception:
            return False  # fail-safe ante cualquier error de la UI
