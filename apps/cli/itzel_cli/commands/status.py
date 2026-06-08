"""Comando: itzel status."""

from __future__ import annotations

import json

from rich.console import Console
from rich.table import Table

console = Console()


def run(json_output: bool = False) -> None:
    state = {
        "version": "0.1.0",
        "backend": "offline",
        "model": "not_loaded",
        "voice": {"stt": "not_loaded", "tts": "not_loaded"},
        "agents": [],
        "memory": "not_initialized",
        "mcp_servers": [],
    }

    if json_output:
        console.print_json(json.dumps(state))
        return

    table = Table(title="Estado de Itzel", border_style="#f9a8d4")
    table.add_column("Componente", style="#e8e4f4", min_width=16)
    table.add_column("Estado", min_width=20)

    table.add_row("Backend", "[yellow]offline[/]")
    table.add_row("Modelo", "[yellow]no cargado[/]")
    table.add_row("Voz STT", "[yellow]no cargado[/]")
    table.add_row("Voz TTS", "[yellow]no cargado[/]")
    table.add_row("Memoria", "[yellow]no inicializada[/]")

    console.print(table)
    console.print("\n[#9890b8]Ejecuta [bold]itzel setup[/] para inicializar todo.[/]")
