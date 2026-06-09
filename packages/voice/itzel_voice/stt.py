"""
Speech-to-Text — faster-whisper con modelo "small" en CPU.

faster-whisper reimplementa OpenAI Whisper con CTranslate2:
  · ~4x más rápido que el Whisper original en CPU
  · int8 quantization: modelo ~460 MB (vs ~967 MB float32)
  · ~1-3s de latencia por 5s de audio en CPU moderno

Descarga del modelo:
  El modelo se descarga automáticamente la primera vez en:
    ~/.itzel/models/whisper/<model_size>/

Detección de idioma:
  Con language=None, Whisper detecta ES o EN automáticamente.
  El primer segundo de audio se usa para la detección (~100ms extra).

Uso:
    stt = SpeechToText()
    stt.load()                         # descarga/carga el modelo
    text = stt.transcribe(audio_np)    # audio float32 @ 16 kHz
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

# ─── constantes ───────────────────────────────────────────────────────────────

_MODELS_DIR = Path.home() / ".itzel" / "models" / "whisper"

# Mínimo de audio para enviar a Whisper (evita inferencia en clips vacíos)
_MIN_AUDIO_S  = 0.1   # 100ms
_MIN_AUDIO_SAMPLES = int(16_000 * _MIN_AUDIO_S)

# HuggingFace repos para cada tamaño (faster-whisper CTranslate2 format)
_HF_REPOS = {
    "tiny":     "guillaumekln/faster-whisper-tiny",
    "base":     "guillaumekln/faster-whisper-base",
    "small":    "guillaumekln/faster-whisper-small",
    "medium":   "guillaumekln/faster-whisper-medium",
    "large-v2": "guillaumekln/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
}


# ─── configuración ────────────────────────────────────────────────────────────

@dataclass
class STTConfig:
    """Parámetros del motor de reconocimiento de voz."""
    model_size:   str            = "small"   # tiny | base | small | medium | large-v3
    language:     Optional[str]  = "es"      # "es", "en", o None (auto-detect)
    device:       str            = "cpu"     # "cpu" o "cuda"
    compute_type: str            = "int8"    # int8 | float16 | float32
    beam_size:    int            = 5         # beam search width (precisión vs velocidad)
    vad_filter:   bool           = True      # VAD interno de Whisper (anti-alucinaciones)
    models_dir:   Path           = field(default_factory=lambda: _MODELS_DIR)
    cpu_threads:  int            = 0         # 0 = auto (usa os.cpu_count() // 2)


# ─── resultado de transcripción ───────────────────────────────────────────────

@dataclass
class TranscriptSegment:
    """Un segmento de texto reconocido con su marca de tiempo."""
    text:     str
    start_s:  float
    end_s:    float
    language: str


@dataclass
class TranscriptResult:
    """Resultado completo de una transcripción."""
    text:     str                        # texto completo unido
    segments: list[TranscriptSegment]
    language: str
    duration_s: float


# ─── motor STT ────────────────────────────────────────────────────────────────

class SpeechToText:
    """
    Reconocimiento de voz en español MX con faster-whisper.

    No bloquea el hilo principal — diseñado para usarse desde
    el Whisper Worker Thread del pipeline de voz.

    Ejemplo:
        stt = SpeechToText(STTConfig(model_size="small", language="es"))
        stt.load()
        result = stt.transcribe(audio_float32_16khz)
        print(result.text)
    """

    def __init__(self, config: Optional[STTConfig] = None) -> None:
        self.config  = config or STTConfig()
        self._model  = None
        self._loaded = False

    # ── carga del modelo ──────────────────────────────────────────────────────

    def load(self) -> None:
        """
        Descarga (si no existe) y carga el modelo en memoria.

        La primera descarga puede tardar varios minutos.
        Las siguientes son instantáneas (modelo en disco).

        Raises:
            VoiceNotAvailableError: si faster-whisper no está instalado.
        """
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            from . import VoiceNotAvailableError
            raise VoiceNotAvailableError(
                missing="faster-whisper",
                install_cmd="pip install faster-whisper",
            ) from exc

        self.config.models_dir.mkdir(parents=True, exist_ok=True)

        threads = self.config.cpu_threads or max(1, (os.cpu_count() or 2) // 2)

        self._model = WhisperModel(
            model_size_or_path = self.config.model_size,
            device             = self.config.device,
            compute_type       = self.config.compute_type,
            cpu_threads        = threads,
            download_root      = str(self.config.models_dir),
        )
        self._loaded = True

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def is_downloaded(self) -> bool:
        """
        True si el modelo ya está en disco (sin necesidad de cargarlo en RAM).
        Útil para mostrar estado en la UI antes de llamar load().
        """
        model_dir = self.config.models_dir / self.config.model_size
        return model_dir.exists() and any(model_dir.iterdir())

    # ── transcripción ─────────────────────────────────────────────────────────

    def transcribe(self, audio: np.ndarray) -> TranscriptResult:
        """
        Transcribe audio a texto completo.

        Args:
            audio: array float32, mono, 16 kHz.
                   No necesita normalización previa.

        Returns:
            TranscriptResult con texto y metadatos.

        Raises:
            RuntimeError: si load() no se llamó antes.
        """
        self._require_loaded()
        audio = _prepare_audio(audio)

        if len(audio) < _MIN_AUDIO_SAMPLES:
            return TranscriptResult(
                text="", segments=[], language=self.config.language or "es",
                duration_s=len(audio) / 16_000,
            )

        segments_gen, info = self._model.transcribe(
            audio,
            beam_size      = self.config.beam_size,
            language       = self.config.language,
            vad_filter     = self.config.vad_filter,
            word_timestamps= False,
        )

        segments: list[TranscriptSegment] = []
        for seg in segments_gen:
            segments.append(TranscriptSegment(
                text     = seg.text.strip(),
                start_s  = seg.start,
                end_s    = seg.end,
                language = info.language,
            ))

        full_text = " ".join(s.text for s in segments if s.text)

        return TranscriptResult(
            text       = full_text,
            segments   = segments,
            language   = info.language,
            duration_s = len(audio) / 16_000,
        )

    def transcribe_stream(self, audio: np.ndarray) -> Iterator[str]:
        """
        Versión generadora: emite segmentos de texto conforme Whisper
        los decodifica (beam search por segmentos ~30s).

        Útil para mostrar texto parcial en la UI mientras sigue procesando.

        Yields:
            Segmentos de texto (una o varias palabras por yield).
        """
        self._require_loaded()
        audio = _prepare_audio(audio)

        if len(audio) < _MIN_AUDIO_SAMPLES:
            return

        segments_gen, _ = self._model.transcribe(
            audio,
            beam_size      = self.config.beam_size,
            language       = self.config.language,
            vad_filter     = self.config.vad_filter,
            word_timestamps= False,
        )

        for seg in segments_gen:
            text = seg.text.strip()
            if text:
                yield text

    def detect_language(self, audio: np.ndarray) -> tuple[str, float]:
        """
        Detecta el idioma del audio usando el primer segundo.

        Returns:
            Tupla (código_iso, probabilidad). Ejemplo: ("es", 0.97)
        """
        self._require_loaded()
        audio = _prepare_audio(audio)

        # Whisper detecta idioma en los primeros 30s; usamos solo el inicio
        sample = audio[:16_000 * 10] if len(audio) > 16_000 * 10 else audio
        _, info = self._model.transcribe(
            sample,
            beam_size  = 1,           # rápido — solo queremos el idioma
            vad_filter = False,
        )
        prob = info.language_probability if hasattr(info, "language_probability") else 1.0
        return info.language, float(prob)

    # ── helpers privados ──────────────────────────────────────────────────────

    def _require_loaded(self) -> None:
        if not self._loaded or self._model is None:
            raise RuntimeError(
                "SpeechToText no inicializado — llama load() primero."
            )


# ─── utilidades ───────────────────────────────────────────────────────────────

def _prepare_audio(audio: np.ndarray) -> np.ndarray:
    """
    Normaliza el audio al formato que espera faster-whisper:
      · dtype: float32
      · shape: (n_samples,) — mono, 1D
      · rango: [-1.0, 1.0]  — normalizado si hay clipping
    """
    audio = np.asarray(audio, dtype=np.float32).ravel()

    # Normalizar si hay clipping (amplitud > 1.0)
    max_val = np.abs(audio).max()
    if max_val > 1.0:
        audio = audio / max_val

    return audio


def download_model(model_size: str = "small", models_dir: Optional[Path] = None) -> Path:
    """
    Descarga el modelo faster-whisper sin cargarlo en RAM.
    Útil para el comando `itzel setup` y `itzel model pull`.

    Args:
        model_size:  "tiny" | "base" | "small" | "medium" | "large-v3"
        models_dir:  directorio destino (default: ~/.itzel/models/whisper/)

    Returns:
        Path al directorio del modelo descargado.

    Raises:
        ValueError: si model_size no es reconocido.
        VoiceNotAvailableError: si faster-whisper no está instalado.
    """
    if model_size not in _HF_REPOS:
        raise ValueError(
            f"Modelo '{model_size}' no reconocido. "
            f"Opciones: {', '.join(_HF_REPOS)}"
        )

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        from . import VoiceNotAvailableError
        raise VoiceNotAvailableError(
            missing="faster-whisper",
            install_cmd="pip install faster-whisper",
        ) from exc

    dest = (models_dir or _MODELS_DIR) / model_size
    dest.mkdir(parents=True, exist_ok=True)

    # WhisperModel con num_workers=0 solo descarga sin cargar en GPU/CPU
    WhisperModel(
        model_size_or_path = model_size,
        device             = "cpu",
        compute_type       = "int8",
        download_root      = str(dest.parent),
    )
    return dest
