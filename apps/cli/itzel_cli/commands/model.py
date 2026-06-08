"""Comando: itzel model [pull|list|remove|use|info]."""

from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(help="Gestiona modelos locales de Itzel")
console = Console()


@app.command("pull")
def pull(name: str = typer.Argument(..., help="Nombre del modelo: itzel-1b | itzel-7b")) -> None:
    """Descarga un modelo del registry."""
    console.print(f"[#4ecdc4]Descargando[/] {name}...")
    console.print("[yellow]⚠ Registry disponible en v2.[/]")


@app.command("list")
def list_models() -> None:
    """Lista los modelos instalados."""
    console.print("[#9890b8]No hay modelos instalados. Ejecuta: itzel model pull itzel-1b[/]")


@app.command("remove")
def remove(name: str = typer.Argument(..., help="Nombre del modelo")) -> None:
    """Elimina un modelo local."""
    console.print(f"[yellow]Modelo {name} no encontrado.[/]")


@app.command("use")
def use(name: str = typer.Argument(..., help="Modelo a activar")) -> None:
    """Selecciona el modelo activo."""
    console.print(f"[#4ecdc4]Modelo activo:[/] {name} — [yellow]requiere backend activo.[/]")


@app.command("info")
def info(name: str = typer.Argument(..., help="Modelo a inspeccionar")) -> None:
    """Muestra información de un modelo."""
    console.print(f"[yellow]Modelo {name} no encontrado localmente.[/]")
