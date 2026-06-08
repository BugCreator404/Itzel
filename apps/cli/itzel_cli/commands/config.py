"""Comando: itzel config [key] [value]."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

app = typer.Typer(help="Ver y editar la configuración de Itzel")
console = Console()

CONFIG_PATH = Path.home() / ".itzel" / "itzel.config.json"


@app.callback(invoke_without_command=True)
def config_main(
    ctx: typer.Context,
    key: Optional[str] = typer.Argument(None, help="Clave de configuración (ej: voice.enabled)"),
    value: Optional[str] = typer.Argument(None, help="Nuevo valor"),
) -> None:
    """Ver y editar la configuración sin tocar el JSON directamente."""
    if ctx.invoked_subcommand:
        return

    if not CONFIG_PATH.exists():
        console.print("[yellow]⚠ Config no encontrada. Ejecuta `itzel setup` primero.[/]")
        return

    with CONFIG_PATH.open() as f:
        cfg: dict = json.load(f)

    if key is None:
        console.print_json(json.dumps(cfg))
        return

    # Navegar por claves anidadas tipo "voice.enabled"
    parts = key.split(".")
    node = cfg
    for part in parts[:-1]:
        node = node.get(part, {})

    if value is None:
        console.print_json(json.dumps({key: node.get(parts[-1], None)}))
    else:
        node[parts[-1]] = value
        with CONFIG_PATH.open("w") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        console.print(f"[#4ecdc4]✓[/] {key} = {value}")
