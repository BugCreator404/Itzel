"""Comando: itzel run "tarea"."""

from __future__ import annotations

from rich.console import Console

console = Console()


def run(task: str, json_output: bool = False, auto_confirm: bool = False) -> None:
    # TODO(v2): conectar al router de agentes
    console.print(f"[#9890b8]Tarea:[/] {task}")
    console.print("[yellow]⚠ Agentes no disponibles — ejecuta `itzel setup` primero.[/]")
