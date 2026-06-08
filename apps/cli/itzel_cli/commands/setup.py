"""Comando: itzel setup."""

from __future__ import annotations

from rich.console import Console
from rich.prompt import Confirm

console = Console()


def run() -> None:
    console.print("[bold #f9a8d4]Bienvenido a Itzel Setup 🦎[/]\n")
    console.print("Este comando:")
    console.print("  1. Descarga el modelo Itzel-1B (~900MB)")
    console.print("  2. Configura el entorno de voz")
    console.print("  3. Crea la configuración inicial\n")

    # TODO(v2): implementar descarga real del modelo y configuración completa
    console.print("[yellow]⚠ Setup completo disponible en v2.[/]")
    console.print("[#9890b8]Por ahora, revisa docs/PROGRESS.md para instrucciones manuales.[/]")
