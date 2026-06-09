"""
itzel-voice — Pipeline de voz 100% local para Itzel.

Módulos:
    vad       — Voice Activity Detection (Silero VAD + ONNX)
    stt       — Speech-to-Text (faster-whisper, modelo "small")
    pipeline  — Orquestador: mic → VAD → Whisper → texto

Uso rápido:
    from itzel_voice import VoicePipeline, PipelineConfig

    config = PipelineConfig(mode="always", language="es")
    pipeline = VoicePipeline(config)
    pipeline.on_transcript = lambda text: print(f"Dijiste: {text}")

    pipeline.start()   # inicia captura de micrófono
    input("Enter para detener...")
    pipeline.stop()

Privacidad:
    Todo el audio se procesa localmente.
    Ningún byte de audio o transcripción sale de la máquina.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

__version__ = "0.1.0"
__all__ = [
    "VoiceActivityDetector",
    "VADConfig",
    "SpeechToText",
    "STTConfig",
    "VoicePipeline",
    "PipelineConfig",
    "VoiceNotAvailableError",
    "check_dependencies",
]

# ─── error base ───────────────────────────────────────────────────────────────

class VoiceNotAvailableError(RuntimeError):
    """
    Se lanza cuando falta alguna dependencia de voz.

    Incluye instrucciones de instalación específicas.
    """
    def __init__(self, missing: str, install_cmd: str) -> None:
        super().__init__(
            f"Dependencia de voz no encontrada: {missing}\n"
            f"  Instala con: {install_cmd}"
        )
        self.missing     = missing
        self.install_cmd = install_cmd


# ─── imports diferidos ────────────────────────────────────────────────────────
# Se importan aquí solo para exponer en el namespace del paquete.
# Los módulos internos manejan sus propios ImportError.

def _lazy_import(module_path: str, name: str):
    """Importa un símbolo solo cuando se necesita."""
    mod = importlib.import_module(module_path, package=__name__)
    return getattr(mod, name)


# Importaciones reales — se ejecutan al importar el paquete
from .vad      import VoiceActivityDetector, VADConfig          # noqa: E402
from .stt      import SpeechToText, STTConfig                   # noqa: E402
from .pipeline import VoicePipeline, PipelineConfig             # noqa: E402


# ─── verificación de dependencias ─────────────────────────────────────────────

def check_dependencies() -> dict[str, bool]:
    """
    Verifica qué dependencias opcionales están instaladas.

    Returns:
        Dict con nombre → bool. Ejemplo:
        {
          "sounddevice":    True,
          "silero_vad":     True,
          "onnxruntime":    True,
          "faster_whisper": False,
        }
    """
    deps = {
        "sounddevice":    False,
        "silero_vad":     False,
        "onnxruntime":    False,
        "faster_whisper": False,
    }
    for dep in deps:
        try:
            importlib.import_module(dep)
            deps[dep] = True
        except ImportError:
            pass
    return deps


def require_full_install() -> None:
    """
    Lanza VoiceNotAvailableError si alguna dependencia clave falta.
    Llama esto al inicio de cualquier función que necesite micrófono o modelos.
    """
    missing = [k for k, v in check_dependencies().items() if not v]
    if missing:
        raise VoiceNotAvailableError(
            missing=", ".join(missing),
            install_cmd="pip install -e packages/voice[full]",
        )
