"""Punto de entrada del CLI de Itzel."""

from __future__ import annotations

import sys

import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from . import __version__
from .commands import (
    ask,
    chat,
    config,
    doctor,
    mcp,
    memory,
    model,
    run,
    skills,
    status,
    tools,
    uninstall,
    update,
    voice,
)

console = Console()
app = typer.Typer(
    name="itzel",
    help="Itzel — tu IA personal local y open source 🦎",
    add_completion=True,
    rich_markup_mode="rich",
    no_args_is_help=False,
)

# Registrar sub-comandos
app.add_typer(model.app, name="model")
app.add_typer(skills.app, name="skills")
app.add_typer(config.app, name="config")
app.add_typer(memory.app, name="memory")
app.add_typer(mcp.app, name="mcp")
app.add_typer(tools.app, name="tools")


@app.command(name="ask")
def ask_cmd(
    question: str = typer.Argument(..., help="Pregunta para Itzel"),
    json_output: bool = typer.Option(False, "--json", help="Output en JSON"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Sin output extra"),
    model_name: str | None = typer.Option(None, "--model", "-m", help="Modelo a usar"),
) -> None:
    """Hace una pregunta y obtiene respuesta directa. Soporta stdin."""
    ask.run(question=question, json_output=json_output, quiet=quiet, model_name=model_name)


@app.command(name="run")
def run_cmd(
    task: str = typer.Argument(..., help="Tarea a ejecutar con un agente"),
    json_output: bool = typer.Option(False, "--json", help="Output en JSON"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirmar plan automáticamente"),
) -> None:
    """Ejecuta una tarea con el agente más adecuado. Muestra el plan antes de ejecutar."""
    run.run(task=task, json_output=json_output, auto_confirm=yes)


@app.command(name="chat")
def chat_cmd(
    context: str | None = typer.Option(None, "--context", "-c", help="Ruta de contexto"),
    model_name: str | None = typer.Option(None, "--model", "-m", help="Modelo a usar"),
) -> None:
    """Abre un chat interactivo en la terminal con historial persistente."""
    chat.run(context=context, model_name=model_name)


@app.command(name="voice")
def voice_cmd(
    model: str = typer.Option(
        "small", "--model", "-m",
        help="Modelo Whisper STT: tiny | base | small | medium | large-v3",
    ),
    mode: str = typer.Option(
        "always", "--mode", help="Modo de escucha: always (VAD) | hotkey (Enter)",
    ),
    language: str = typer.Option("es", "--language", "-l", help="Idioma: es | en"),
    download: bool = typer.Option(
        False, "--download", help="Descarga los modelos de voz antes de empezar",
    ),
) -> None:
    """Activa el modo de voz. Habla y Itzel responde con audio. Ctrl+C para salir."""
    voice.run(model=model, mode=mode, language=language, download=download)


@app.command(name="setup")
def setup_cmd() -> None:
    """Configuración inicial interactiva: descarga modelo, configura voz y entorno."""
    from .commands import setup
    setup.run()


@app.command(name="doctor")
def doctor_cmd(
    json_output: bool = typer.Option(False, "--json", help="Salida en JSON"),
    quiet:       bool = typer.Option(False, "--quiet", "-q", help="Sin instrucciones correctivas"),
) -> None:
    """Verifica el estado completo del sistema: Python, modelo, voz, BD, disco, RAM."""
    doctor.run(json_output=json_output, quiet=quiet)


@app.command(name="status")
def status_cmd(
    json_output: bool = typer.Option(False, "--json", help="Output en JSON"),
) -> None:
    """Muestra el estado del sistema: modelo, voz, agentes, memoria, MCP."""
    status.run(json_output=json_output)


@app.command(name="update")
def update_cmd(
    channel: str = typer.Option("stable", "--channel", help="Canal: stable | beta | nightly"),
) -> None:
    """Actualiza Itzel a la última versión. Verifica la firma antes de instalar."""
    update.run(channel=channel)


@app.command(name="uninstall")
def uninstall_cmd(
    data_only:   bool = typer.Option(False, "--data-only",   help="Solo borrar datos, conservar el CLI"),
    keep_models: bool = typer.Option(False, "--keep-models", help="Conservar modelos descargados (~1 GB)"),
    yes:         bool = typer.Option(False, "--yes", "-y",   help="Sin confirmación interactiva"),
) -> None:
    """Desinstala Itzel: CLI, datos y credenciales del keychain."""
    uninstall.run(data_only=data_only, keep_models=keep_models, yes=yes)


@app.command(name="pipe")
def pipe_cmd(
    prompt: str = typer.Argument(..., help="Instrucción sobre el contenido de stdin"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Lee desde stdin y procesa con Itzel. Perfecto para pipelines."""
    if sys.stdin.isatty():
        console.print("[red]Error:[/red] pipe requiere datos desde stdin. Ejemplo: cat file.txt | itzel pipe 'resumen'")
        raise typer.Exit(1)
    content = sys.stdin.read()
    ask.run(question=f"{prompt}\n\n{content}", json_output=json_output)


def version_callback(value: bool) -> None:
    if value:
        console.print(
            Panel(
                Text.from_markup(
                    f"[bold #f9a8d4]ITZEL[/] v{__version__}\n"
                    "[#9890b8]Local · Open Source · MIT License[/]\n"
                    "[#4ecdc4]github.com/BugCreator404/itzel[/]"
                ),
                border_style="#f9a8d4",
            )
        )
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-v", callback=version_callback, is_eager=True, help="Mostrar versión"
    ),
) -> None:
    """
    [bold #f9a8d4]Itzel[/] — tu IA personal local y open source 🦎

    Corre 100% en tu máquina. Sin internet. Sin suscripciones. Sin telemetría.
    """


if __name__ == "__main__":
    app()
