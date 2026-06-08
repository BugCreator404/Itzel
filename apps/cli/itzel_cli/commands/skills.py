"""Comando: itzel skills [install|list|remove|create]."""

from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(help="Gestiona skills de Itzel")
console = Console()


@app.command("install")
def install(name: str = typer.Argument(..., help="Nombre de la skill")) -> None:
    """Instala una skill del marketplace."""
    console.print(f"[yellow]⚠ Marketplace disponible en v2. Skill: {name}[/]")


@app.command("list")
def list_skills() -> None:
    """Lista las skills instaladas."""
    console.print("[#9890b8]No hay skills instaladas.[/]")


@app.command("remove")
def remove(name: str = typer.Argument(..., help="Skill a desinstalar")) -> None:
    """Desinstala una skill."""
    console.print(f"[yellow]Skill {name} no encontrada.[/]")


@app.command("create")
def create(name: str = typer.Argument(..., help="Nombre de la nueva skill")) -> None:
    """Crea el scaffold de una skill nueva en skills/community/."""
    console.print(f"[#4ecdc4]Creando skill:[/] {name}")
    console.print("[yellow]⚠ Scaffold automático disponible en v2.[/]")
    console.print("[#9890b8]Por ahora, copia skills/official/ejemplo/ y edítala.[/]")
