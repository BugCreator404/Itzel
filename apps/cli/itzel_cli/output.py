"""
Helpers de output para el CLI de Itzel.

Centraliza el uso de Rich para que todos los comandos tengan el
mismo estilo visual sin duplicar markup.

Paleta de colores Itzel:
  PINK   #f9a8d4  — acento principal (nombre, títulos)
  LILAC  #9890b8  — texto secundario, hints
  TEAL   #4ecdc4  — éxito, acciones positivas
  YELLOW #fbbf24  — advertencias
  RED    #f87171  — errores

Uso:
    from itzel_cli.output import ok, err, warn, hint, console
    ok("Modelo cargado")
    err("Backend offline")
    warn("Sin conexión")
    hint("Ejecuta: itzel setup")
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.text import Text

# ─── consolas ─────────────────────────────────────────────────────────────────

# Salida estándar — para contenido
console = Console(highlight=False)

# Salida de errores — va a stderr para no contaminar pipes
err_console = Console(stderr=True, highlight=False)


# ─── helpers de mensaje ───────────────────────────────────────────────────────

def ok(msg: str) -> None:
    """Mensaje de éxito con ✓ verde."""
    console.print(f"[bold #4ecdc4]✓[/] {msg}")


def err(msg: str, hint: str | None = None) -> None:
    """Mensaje de error en stderr. Si se pasa hint, lo imprime en lila."""
    err_console.print(f"[bold #f87171]✗ Error:[/] {msg}")
    if hint:
        err_console.print(f"  [#9890b8]{hint}[/]")


def warn(msg: str) -> None:
    """Advertencia en amarillo."""
    console.print(f"[bold #fbbf24]⚠[/] {msg}")


def hint(msg: str) -> None:
    """Texto de ayuda en lila — para instrucciones de siguiente paso."""
    console.print(f"[#9890b8]{msg}[/]")


def info(msg: str) -> None:
    """Texto informativo neutro."""
    console.print(f"[dim]{msg}[/]")


def label(key: str, value: str, *, color: str = "#4ecdc4") -> None:
    """Par clave: valor alineado."""
    console.print(f"[#9890b8]{key}:[/] [{color}]{value}[/]")


# ─── exit helpers ─────────────────────────────────────────────────────────────

def exit_offline(base_url: str = "http://127.0.0.1:7432") -> None:
    """Imprime el mensaje de backend offline y termina con código 1."""
    err(
        f"Backend no disponible en {base_url}.",
        hint="Inicia el backend con: uvicorn itzel_core.engine:app --port 7432\n"
             "  O configura todo con: itzel setup",
    )
    raise typer.Exit(1)


def exit_error(msg: str, hint_msg: str | None = None) -> None:
    """Imprime error y termina con código 1."""
    err(msg, hint=hint_msg)
    raise typer.Exit(1)


# ─── confirmación de acciones irreversibles ───────────────────────────────────

def confirm(prompt: str, *, default: bool = False, abort: bool = True) -> bool:
    """
    Pide confirmación al usuario para acciones destructivas.
    Si abort=True, lanza typer.Abort() en caso de rechazo.
    """
    answer = typer.confirm(prompt, default=default)
    if not answer and abort:
        hint("Operación cancelada.")
        raise typer.Abort()
    return answer


# ─── streaming de tokens ──────────────────────────────────────────────────────

@contextmanager
def streaming_response(
    prefix: str = "",
) -> Generator[None, None, None]:
    """
    Context manager para imprimir la respuesta de Itzel token a token.
    Imprime el prefijo en lila antes del stream y una nueva línea al cerrar.

    Uso:
        with streaming_response():
            for token in client.stream_chat(msg):
                console.print(token, end="")
    """
    if prefix:
        console.print(f"\n[bold #9890b8]{prefix}[/]", end=" ")
    try:
        yield
    finally:
        console.print()   # nueva línea al terminar el stream


def print_token(token: str) -> None:
    """Imprime un token sin nueva línea. Flush inmediato para streaming real."""
    console.print(token, end="", highlight=False)
    # Rich bufferiza; forzamos flush en el archivo subyacente
    if hasattr(console.file, "flush"):
        console.file.flush()


# ─── citas del RAG ────────────────────────────────────────────────────────────

def print_sources(sources: list[dict]) -> None:
    """Imprime el pie de 'Fuentes' con las citas del RAG ([n] → archivo).

    `sources` es la lista que deja `ItzelClient.last_sources` (vacía si el
    chat no usó tus documentos). No imprime nada si está vacía.
    """
    if not sources:
        return
    console.print()
    console.print("[dim #9890b8]Fuentes:[/]")
    for s in sources:
        n  = s.get("n", "?")
        fn = s.get("filename") or s.get("source") or "?"
        console.print(f"  [#c4b5fd][{n}][/] [#9890b8]{fn}[/]")


# ─── markdown rendering ───────────────────────────────────────────────────────

def print_markdown(text: str) -> None:
    """Renderiza texto Markdown con Rich."""
    console.print(Markdown(text))


# ─── tablas ───────────────────────────────────────────────────────────────────

def make_table(title: str, *columns: tuple[str, str, int]) -> Table:
    """
    Crea una Table con el estilo Itzel.

    Args:
        title:   Título de la tabla.
        columns: Tuplas (nombre, estilo, min_width).
    """
    table = Table(title=title, border_style="#f9a8d4", header_style="bold #e8e4f4")
    for col_name, col_style, col_min in columns:
        table.add_column(col_name, style=col_style, min_width=col_min)
    return table


# ─── panel / header ───────────────────────────────────────────────────────────

def print_header(title: str, subtitle: str = "") -> None:
    """Panel de bienvenida / sección con estilo Itzel."""
    content = Text()
    content.append(title, style="bold #f9a8d4")
    if subtitle:
        content.append(f"\n{subtitle}", style="#9890b8")
    console.print(Panel(content, border_style="#f9a8d4", padding=(0, 2)))


# ─── barras de progreso ───────────────────────────────────────────────────────

def download_progress() -> Progress:
    """Barra de progreso estilo Itzel para descargas de archivos."""
    return Progress(
        SpinnerColumn(style="#f9a8d4"),
        TextColumn("[#e8e4f4]{task.description}"),
        BarColumn(bar_width=30, style="#9890b8", complete_style="#f9a8d4"),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )


def spinner_progress(description: str = "Trabajando…") -> Progress:
    """Spinner simple para operaciones sin tamaño conocido."""
    return Progress(
        SpinnerColumn(style="#f9a8d4"),
        TextColumn(f"[#9890b8]{description}"),
        console=console,
        transient=True,
    )
