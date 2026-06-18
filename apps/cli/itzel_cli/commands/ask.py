"""Comando: itzel ask "pregunta"

Conecta al backend FastAPI vía SSE y transmite la respuesta token a token.
Soporta --json para scripts y --quiet para pipelines silenciosos.
"""

from __future__ import annotations

import json
import sys

from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from ..client import BackendError, BackendOfflineError, ItzelClient
from ..output import (
    console,
    err,
    print_token,
    warn,
)


def run(
    question:   str,
    json_output: bool = False,
    quiet:      bool = False,
    model_name: str | None = None,
) -> None:
    """
    Envía una pregunta al backend y transmite la respuesta en tiempo real.

    Args:
        question:    El texto de la pregunta.
        json_output: Si True, imprime JSON al final en lugar de streaming.
        quiet:       Si True, imprime solo la respuesta sin decoración.
        model_name:  Si se especifica, cambia el modelo activo antes de preguntar.
    """
    client = ItzelClient()

    # ── Cambiar modelo si se especificó ──────────────────────────────────────
    if model_name:
        try:
            result = client.switch_model(model_name)
            if not quiet:
                console.print(
                    f"[#9890b8]Modelo:[/] [#4ecdc4]{result.get('model_id', model_name)}[/]"
                )
        except BackendOfflineError:
            _handle_offline(quiet)
            return
        except BackendError as exc:
            if not quiet:
                warn(f"No se pudo cambiar el modelo: {exc.detail}")

    # ── Modo JSON: buffer completo ────────────────────────────────────────────
    if json_output:
        try:
            answer = client.chat_sync(
                message    = question,
                session_id = ItzelClient.load_session(),
            )
            console.print_json(
                json.dumps(
                    {"status": "ok", "question": question, "answer": answer},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        except BackendOfflineError:
            console.print_json(
                json.dumps({"status": "offline", "question": question, "answer": None})
            )
        except BackendError as exc:
            console.print_json(
                json.dumps(
                    {"status": "error", "code": exc.status_code, "detail": exc.detail}
                )
            )
        return

    # ── Modo streaming ────────────────────────────────────────────────────────
    if not quiet:
        console.print("\n[bold #f9a8d4]Itzel[/] [#9890b8]→[/] ", end="")

    tokens: list[str] = []
    first_token = True

    try:
        # Spinner "Pensando…" hasta que llega el primer token
        spinner_live: Live | None = None
        if not quiet and sys.stdout.isatty():
            spinner_live = Live(
                Spinner("dots", text=Text("Pensando…", style="#9890b8")),
                console=console,
                transient=True,
                refresh_per_second=10,
            )
            spinner_live.start()

        session_id = ItzelClient.load_session()

        for token in client.stream_chat(message=question, session_id=session_id):
            if first_token:
                # Apagar spinner en cuanto llega el primer token
                if spinner_live:
                    spinner_live.stop()
                    spinner_live = None
                first_token = False

            tokens.append(token)
            if not quiet:
                print_token(token)

        # Apagar spinner si no llegó ningún token
        if spinner_live:
            spinner_live.stop()

        if not quiet:
            console.print()   # nueva línea al terminar

        # En modo quiet imprimimos la respuesta completa de golpe
        if quiet:
            sys.stdout.write("".join(tokens))
            sys.stdout.flush()

    except BackendOfflineError:
        if spinner_live:
            spinner_live.stop()
        _handle_offline(quiet)

    except BackendError as exc:
        if spinner_live:
            spinner_live.stop()
        if not quiet:
            err(f"Error del backend: {exc.detail}")
        else:
            sys.stderr.write(f"Error: {exc.detail}\n")


# ─── helpers ──────────────────────────────────────────────────────────────────

def _handle_offline(quiet: bool) -> None:
    if quiet:
        sys.stderr.write(
            "Error: Backend offline. Ejecuta: uvicorn itzel_core.engine:app --port 7432\n"
        )
    else:
        err(
            "Backend no disponible.",
            hint="Inicia el backend con: uvicorn itzel_core.engine:app --port 7432\n"
                 "  O configura todo con:  itzel setup",
        )
