"""Comando: itzel chat

REPL interactivo con historial persistente de sesión.

Comandos especiales dentro del chat:
  /salir  /exit  /quit  — terminar
  /nuevo  /new         — nueva sesión (borra contexto)
  /limpiar /clear      — limpiar pantalla
  /modelo /model <id>  — cambiar modelo activo
  /ayuda  /help        — mostrar comandos
  Ctrl+C / Ctrl+D      — salir sin confirmación
"""

from __future__ import annotations

from ..client import BackendError, BackendOfflineError, ItzelClient
from ..output import (
    console,
    err,
    info,
    ok,
    print_header,
    print_token,
    warn,
)

# ─── constantes ───────────────────────────────────────────────────────────────

_PROMPT      = "[bold #f9a8d4]tú[/]  → "
_ITZEL_LABEL = "[bold #9890b8]itzel[/] → "

_HELP_TEXT = """\
[bold #f9a8d4]Comandos disponibles:[/]
  [#4ecdc4]/nuevo[/]  o  [#4ecdc4]/new[/]       Inicia una nueva sesión (borra el contexto)
  [#4ecdc4]/limpiar[/] o [#4ecdc4]/clear[/]     Limpia la pantalla
  [#4ecdc4]/modelo[/]  [dim]<id>[/]             Cambia el modelo activo
  [#4ecdc4]/ayuda[/]  o  [#4ecdc4]/help[/]      Muestra este mensaje
  [#4ecdc4]/salir[/]  o  [#4ecdc4]/exit[/]      Sale del chat
  [dim]Ctrl+C / Ctrl+D[/]           Sale sin confirmación
"""

# ─── punto de entrada ─────────────────────────────────────────────────────────

def run(
    context:    str | None = None,
    model_name: str | None = None,
    new_session: bool = False,
) -> None:
    """
    Abre el REPL interactivo de Itzel.

    Args:
        context:     Archivo de contexto inicial (ruta). No implementado aún.
        model_name:  Modelo a usar (cambia el activo si se especifica).
        new_session: Si True, descarta la sesión anterior.
    """
    client = ItzelClient()

    # ── Verificar backend ─────────────────────────────────────────────────────
    if not client.is_alive():
        err(
            "Backend no disponible.",
            hint="Inicia el backend con: uvicorn itzel_core.engine:app --port 7432\n"
                 "  O configura todo con:  itzel setup",
        )
        return

    # ── Sesión ────────────────────────────────────────────────────────────────
    session_id = (
        ItzelClient.new_session() if new_session
        else ItzelClient.load_session()
    )

    # ── Cambiar modelo si se pidió ────────────────────────────────────────────
    if model_name:
        try:
            client.switch_model(model_name)
        except BackendError as exc:
            warn(f"No se pudo cambiar el modelo: {exc.detail}")

    # ── Header ────────────────────────────────────────────────────────────────
    print_header(
        "Itzel Chat 🦎",
        "Escribe tu mensaje y presiona Enter · /ayuda para comandos · Ctrl+C para salir",
    )
    info(f"Sesión: {session_id[:8]}…")
    console.print()

    # ── Activar readline para historial de flechas en Unix ────────────────────
    _enable_readline()

    # ── REPL ──────────────────────────────────────────────────────────────────
    _repl_loop(client, session_id)


# ─── loop principal ───────────────────────────────────────────────────────────

def _repl_loop(client: ItzelClient, session_id: str) -> None:
    while True:
        # Leer input del usuario
        try:
            console.print(_PROMPT, end="")
            user_input = input()
        except (KeyboardInterrupt, EOFError):
            console.print()
            ok("¡Hasta luego! 🦎")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # Comandos especiales
        if _handle_command(user_input, client, session_id):
            # _handle_command devuelve True si debe terminar el loop
            result = _handle_command_result(user_input, client, session_id)
            if result == "exit":
                break
            if isinstance(result, str) and result.startswith("new_session:"):
                session_id = result.split(":", 1)[1]
            continue

        # Enviar mensaje al backend
        _send_message(client, user_input, session_id)


def _send_message(client: ItzelClient, message: str, session_id: str) -> None:
    """Envía el mensaje y transmite la respuesta token a token."""
    console.print(f"\n{_ITZEL_LABEL}", end="")

    first_token = True
    tokens: list[str] = []

    try:
        for token in client.stream_chat(message=message, session_id=session_id):
            if first_token:
                first_token = False
            tokens.append(token)
            print_token(token)

        if first_token:
            # No llegó ningún token — backend no respondió nada
            warn("Sin respuesta del modelo.")
        else:
            console.print("\n")   # separación visual

    except BackendOfflineError:
        console.print()
        err(
            "Conexión perdida con el backend.",
            hint="El backend puede haberse detenido. Revisa el proceso uvicorn.",
        )
    except BackendError as exc:
        console.print()
        err(f"Error del backend [{exc.status_code}]: {exc.detail}")


# ─── comandos especiales ──────────────────────────────────────────────────────

def _handle_command(text: str, client: ItzelClient, session_id: str) -> bool:
    """Devuelve True si el texto es un comando especial (no enviar al modelo)."""
    cmd = text.lstrip("/").lower().split()[0] if text.startswith("/") else ""
    return cmd in {
        "salir", "exit", "quit",
        "nuevo", "new",
        "limpiar", "clear",
        "modelo", "model",
        "ayuda", "help",
    }


def _handle_command_result(
    text: str, client: ItzelClient, session_id: str
) -> str:
    """
    Ejecuta un comando especial.
    Devuelve:
      "exit"            → terminar el REPL
      "new_session:<id>"→ continuar con nueva sesión
      ""                → continuar normalmente
    """
    parts = text.lstrip("/").split()
    cmd   = parts[0].lower() if parts else ""
    args  = parts[1:] if len(parts) > 1 else []

    if cmd in ("salir", "exit", "quit"):
        ok("¡Hasta luego! 🦎")
        return "exit"

    if cmd in ("nuevo", "new"):
        new_sid = ItzelClient.new_session()
        ok(f"Nueva sesión iniciada: {new_sid[:8]}…")
        return f"new_session:{new_sid}"

    if cmd in ("limpiar", "clear"):
        console.clear()
        return ""

    if cmd in ("modelo", "model"):
        if not args:
            warn("Uso: /modelo <id>   Ejemplo: /modelo ollama/llama3.1:8b")
            return ""
        model_id = args[0]
        try:
            result = client.switch_model(model_id)
            ok(f"Modelo activo: {result.get('model_id', model_id)}")
        except BackendError as exc:
            err(f"No se pudo cambiar el modelo: {exc.detail}")
        return ""

    if cmd in ("ayuda", "help"):
        console.print(_HELP_TEXT)
        return ""

    return ""


# ─── readline ─────────────────────────────────────────────────────────────────

def _enable_readline() -> None:
    """
    Activa historial de flechas ↑↓ en el input del REPL.
    Solo funciona en Unix/macOS (readline nativo).
    En Windows es silenciosamente ignorado.
    """
    try:
        import readline  # noqa: F401 — solo importar activa el historial
        readline.parse_and_bind("tab: complete")
    except ImportError:
        pass  # Windows sin pyreadline — funciona igual sin historial de flechas
