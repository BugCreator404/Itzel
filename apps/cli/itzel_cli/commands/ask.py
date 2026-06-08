"""Comando: itzel ask "pregunta"."""

from __future__ import annotations

import json
from typing import Optional

from rich.console import Console

console = Console()

BACKEND_URL = "http://localhost:7432"


def run(question: str, json_output: bool = False, quiet: bool = False, model_name: Optional[str] = None) -> None:
    # TODO(v2): conectar al backend FastAPI cuando esté listo
    if json_output:
        console.print_json(json.dumps({"status": "dev", "question": question, "answer": "Backend no disponible aún."}))
    elif not quiet:
        console.print(f"[#9890b8]Pregunta:[/] {question}")
        console.print("[yellow]⚠ Backend no disponible — ejecuta `itzel setup` primero.[/]")
