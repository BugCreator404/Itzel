"""Comando: itzel memory [search|clear|export|import]."""

from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(help="Gestiona la memoria de Itzel")
console = Console()


@app.command("search")
def search(query: str = typer.Argument(..., help="Texto a buscar en la memoria")) -> None:
    """Busca en la memoria episódica."""
    console.print(f"[#9890b8]Buscando:[/] {query}")
    console.print("[yellow]⚠ Memoria disponible en v2.[/]")


@app.command("clear")
def clear(yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    """Limpia toda la memoria episódica."""
    if not yes:
        confirm = typer.confirm("¿Borrar toda la memoria de Itzel?")
        if not confirm:
            raise typer.Abort()
    console.print("[yellow]⚠ Memoria disponible en v2.[/]")


@app.command("export")
def export(path: str = typer.Argument("itzel_memory.json", help="Ruta de salida")) -> None:
    """Exporta la memoria a un archivo JSON."""
    console.print(f"[yellow]⚠ Export a {path} disponible en v2.[/]")


@app.command("import")
def import_memory(path: str = typer.Argument(..., help="Archivo a importar")) -> None:
    """Importa memoria desde un archivo JSON."""
    console.print(f"[yellow]⚠ Import desde {path} disponible en v2.[/]")
