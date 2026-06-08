"""Comando: itzel voice."""

from __future__ import annotations

from rich.console import Console

console = Console()


def run(stt: str = "whisper-small", tts: str = "kokoro") -> None:
    # TODO(v2): integrar Whisper STT + Kokoro TTS
    console.print(f"[#4ecdc4]Modo voz[/] — STT: {stt} · TTS: {tts}")
    console.print("[yellow]⚠ Voz no disponible — ejecuta `itzel setup` primero.[/]")
