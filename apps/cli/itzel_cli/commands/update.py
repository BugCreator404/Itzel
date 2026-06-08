"""Comando: itzel update."""

from __future__ import annotations

from rich.console import Console

console = Console()


def run(channel: str = "stable") -> None:
    console.print(f"[#4ecdc4]Actualización[/] — canal: {channel}")
    console.print("[yellow]⚠ Auto-update disponible en v2.[/]")
    console.print("[#9890b8]Actualiza manualmente desde: github.com/BugCreator404/itzel/releases[/]")
