"""Comando: itzel voice

Sesión de voz en tiempo real — 100% local, sin servidores externos.

Modos:
  always  — escucha continua con VAD (Silero); el modelo detecta cuándo hablas
  hotkey  — push-to-talk: Enter para empezar a grabar, Enter para soltar

STT:  faster-whisper (CTranslate2, int8, ~4× más rápido que Whisper original)
VAD:  Silero VAD con backend ONNX (~12 MB, sin PyTorch)

Archivos descargados en ~/.itzel/models/whisper/<tamaño>/
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Optional

from rich.columns import Columns
from rich.text import Text

from ..output import (
    console,
    err,
    hint,
    info,
    ok,
    print_header,
    spinner_progress,
    warn,
)

# ─── colores del VU-meter de estado ──────────────────────────────────────────

_ST_IDLE       = "[dim #9890b8]● escuchando[/]"
_ST_LISTEN     = "[bold #f87171]◉ grabando[/]"
_ST_PROCESS    = "[bold #fbbf24]◌ procesando[/]"
_ST_STOPPED    = "[dim]■ detenido[/]"

_ITZEL_LABEL   = "[bold #9890b8]itzel[/]"


# ─── punto de entrada ─────────────────────────────────────────────────────────

def run(
    model:    str           = "small",
    mode:     str           = "always",
    device:   Optional[int] = None,
    language: str           = "es",
    download: bool          = False,
) -> None:
    """
    Inicia una sesión de voz interactiva.

    Args:
        model:    Tamaño del modelo Whisper: tiny | base | small | medium | large-v3
        mode:     Modo de escucha: always (VAD) | hotkey (Enter)
        device:   Índice del dispositivo de entrada (None = micrófono por defecto)
        language: Idioma esperado: es | en | None (detección automática)
        download: Si True, descarga el modelo antes de empezar (sin preguntar)
    """
    # ── Verificar dependencias ────────────────────────────────────────────────
    try:
        from itzel_voice import check_dependencies, require_full_install
        from itzel_voice.pipeline import PipelineConfig, VoicePipeline
        from itzel_voice.stt     import STTConfig, SpeechToText, download_model
        from itzel_voice.vad     import VADConfig
    except ImportError:
        err(
            "El paquete itzel-voice no está instalado.",
            hint="Instálalo con:  pip install -e packages/voice[full]",
        )
        return

    deps = check_dependencies()
    missing = [k for k, v in deps.items() if not v]
    if missing:
        err(
            f"Faltan dependencias de voz: {', '.join(missing)}",
            hint="Ejecuta:  pip install -e packages/voice[full]",
        )
        return

    # Validar modo
    if mode not in ("always", "hotkey"):
        err(
            f"Modo '{mode}' no reconocido.",
            hint="Opciones: always (VAD continuo) · hotkey (push-to-talk con Enter)",
        )
        return

    # ── Listar dispositivos si se pide (device == -1 convención) ─────────────
    if device == -1:
        _print_devices()
        return

    # ── Verificar / descargar modelo ──────────────────────────────────────────
    stt_cfg = STTConfig(
        model_size   = model,
        language     = language if language != "auto" else None,
        device       = "cpu",
        compute_type = "int8",
    )
    stt = SpeechToText(stt_cfg)

    if not stt.is_downloaded():
        if download:
            _do_download(model)
        else:
            warn(
                f"El modelo Whisper '{model}' no está descargado (~460 MB para 'small')."
            )
            hint(
                "Descárgalo ahora con:  [bold]itzel voice --download[/]\n"
                "O continúa y se descargará automáticamente en el primer uso (~5s)."
            )
            console.print()

    # ── Header ────────────────────────────────────────────────────────────────
    mode_label = "siempre activo" if mode == "always" else "push-to-talk (Enter)"
    print_header(
        "Itzel Voice",
        f"Modo: {mode_label} · Modelo: Whisper {model} · Idioma: {language}",
    )
    console.print()

    if mode == "hotkey":
        info("Presiona Enter para empezar a grabar, Enter de nuevo para soltar.")
    else:
        info("Habla cuando quieras. Ctrl+C para salir.")
    console.print()

    # ── Configurar pipeline ───────────────────────────────────────────────────
    cfg = PipelineConfig(
        mode         = mode,
        vad          = VADConfig(),
        stt          = stt_cfg,
        device_index = device,
    )
    pipeline = VoicePipeline(cfg)

    # Sustituir el STT ya construido para reutilizar el objeto
    pipeline.stt = stt

    # Evento para sincronizar estado entre threads
    _stop_event = threading.Event()
    _state_lock = threading.Lock()
    _cur_state  = ["stopped"]

    def _set_state(s: str) -> None:
        with _state_lock:
            _cur_state[0] = s

    # Callbacks del pipeline → imprimir en consola
    pipeline.on_listening_start = lambda: (
        _set_state("listening"),
        console.print(f"\n{_ST_LISTEN}", end="\r"),
    )

    pipeline.on_listening_end = lambda: (
        _set_state("processing"),
        console.print(f"{_ST_PROCESS}          ", end="\r"),
    )

    def _on_transcript(text: str, lang: str) -> None:
        _set_state("idle")
        # Borrar la línea de estado antes de imprimir el texto
        console.print(f"\r{' ' * 30}\r", end="")
        console.print(f"{_ITZEL_LABEL}: [#e8e4f4]{text}[/]")
        # Volver a mostrar estado idle
        console.print(_ST_IDLE, end="\r")

    pipeline.on_transcript = _on_transcript

    pipeline.on_state_change = lambda s: (
        None if s == "listening" else
        console.print(_ST_IDLE, end="\r") if s == "idle" else None
    )

    def _on_error(exc: Exception) -> None:
        console.print()
        warn(f"Error en el pipeline: {exc}")

    pipeline.on_error = _on_error

    # ── Cargar VAD e iniciar ──────────────────────────────────────────────────
    try:
        with spinner_progress("Cargando Silero VAD…") as sp:
            _task = sp.add_task("", total=None)
            pipeline.start()   # use_mic=True (default) — micrófono local

    except Exception as exc:
        err(f"No se pudo iniciar el pipeline de voz: {exc}")
        hint("Verifica que tu micrófono esté conectado y los permisos sean correctos.")
        return

    _set_state("idle")
    console.print(_ST_IDLE, end="\r")

    if mode == "always":
        _run_always_loop(pipeline, _stop_event)
    else:
        _run_hotkey_loop(pipeline, _stop_event)

    # ── Teardown ──────────────────────────────────────────────────────────────
    if pipeline.is_running:
        pipeline.stop()

    console.print()
    ok("Sesión de voz terminada.")


# ─── loops de interacción ─────────────────────────────────────────────────────

def _run_always_loop(pipeline, stop_event: threading.Event) -> None:
    """Escucha continua — Ctrl+C para salir."""
    try:
        while pipeline.is_running:
            time.sleep(0.05)
    except KeyboardInterrupt:
        console.print()
        stop_event.set()


def _run_hotkey_loop(pipeline, stop_event: threading.Event) -> None:
    """
    Push-to-talk con Enter.

    Primer Enter → empieza a grabar (push_to_talk_start).
    Segundo Enter → suelta (push_to_talk_stop) y transcribe.
    Ctrl+C         → salir.
    """
    recording = False

    try:
        while pipeline.is_running:
            try:
                input()   # espera Enter
            except EOFError:
                break

            if not recording:
                pipeline.push_to_talk_start()
                recording = True
                console.print(f"\n{_ST_LISTEN}  (Enter para soltar)", end="\r")
            else:
                pipeline.push_to_talk_stop()
                recording = False
                console.print(f"\r{' ' * 40}\r", end="")

    except KeyboardInterrupt:
        console.print()
        stop_event.set()

    finally:
        if recording:
            pipeline.push_to_talk_stop()


# ─── helpers ──────────────────────────────────────────────────────────────────

def _do_download(model_size: str) -> None:
    """Descarga el modelo Whisper con barra de progreso."""
    from itzel_voice.stt import download_model

    with spinner_progress(f"Descargando Whisper '{model_size}'…") as sp:
        _task = sp.add_task("", total=None)
        try:
            path = download_model(model_size)
            ok(f"Modelo descargado en {path}")
        except Exception as exc:
            err(f"Error al descargar el modelo: {exc}")
            raise SystemExit(1) from exc


def _print_devices() -> None:
    """Lista los micrófonos disponibles en el sistema."""
    try:
        from itzel_voice.pipeline import list_input_devices
        devices = list_input_devices()
    except ImportError:
        err("itzel-voice no disponible.")
        return

    if not devices:
        warn("No se encontraron dispositivos de entrada de audio.")
        return

    from rich.table import Table
    table = Table(title="Micrófonos disponibles", border_style="#f9a8d4", header_style="bold #e8e4f4")
    table.add_column("Índice",      style="#4ecdc4",  min_width=6)
    table.add_column("Nombre",      style="#e8e4f4",  min_width=28)
    table.add_column("Canales",     style="#9890b8",  min_width=8)
    table.add_column("Muestra/s",   style="#9890b8",  min_width=10)
    table.add_column("Por defecto", min_width=10)

    for d in devices:
        default = "[bold #4ecdc4]✓[/]" if d.get("is_default") else ""
        table.add_row(
            str(d["index"]),
            d["name"],
            str(d["channels"]),
            str(d["sample_rate"]),
            default,
        )

    console.print(table)
    hint("Usa: itzel voice --device <índice>  para seleccionar un micrófono.")
