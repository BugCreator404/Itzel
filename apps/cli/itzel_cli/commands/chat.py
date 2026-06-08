"""Comando: itzel chat."""

from __future__ import annotations

from typing import Optional

from rich.console import Console

console = Console()


def run(context: Optional[str] = None, model_name: Optional[str] = None) -> None:
    # TODO(v2): chat interactivo con historial persistente
    console.print("[#f9a8d4]Itzel Chat[/] — [yellow]Backend no disponible aún.[/]")
