"""Comando: itzel mcp [add|remove|list|test]."""

from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(help="Gestiona servidores MCP")
console = Console()


@app.command("add")
def add(
    name: str = typer.Argument(..., help="Nombre del servidor MCP"),
    url: str = typer.Argument(..., help="URL del servidor MCP"),
) -> None:
    """Conecta un servidor MCP a Itzel."""
    console.print(f"[#4ecdc4]MCP:[/] {name} @ {url}")
    console.print("[yellow]⚠ MCP disponible en v2.[/]")


@app.command("remove")
def remove(name: str = typer.Argument(..., help="Nombre del servidor")) -> None:
    """Desconecta un servidor MCP."""
    console.print(f"[yellow]MCP {name} no encontrado.[/]")


@app.command("list")
def list_servers() -> None:
    """Lista los servidores MCP conectados."""
    console.print("[#9890b8]No hay servidores MCP configurados.[/]")


@app.command("test")
def test(name: str = typer.Argument(..., help="Servidor a probar")) -> None:
    """Prueba la conexión con un servidor MCP."""
    console.print(f"[yellow]⚠ Test MCP para {name} disponible en v2.[/]")
