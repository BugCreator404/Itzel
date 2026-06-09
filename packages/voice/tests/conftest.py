"""
Fixtures compartidas para los tests del pipeline de voz.

IMPORTANTE: ningún test descarga modelos reales.
Todo se mockea con numpy y MagicMock para que los tests corran en CI
sin GPU, sin internet y sin ~500 MB de modelos.

Tipos de audio sintético disponibles:
    silence_audio     — zeros: 1s de silencio puro
    speech_audio      — sinusoide multiarmónico ~ voz humana masculina
    noise_audio       — ruido blanco uniforme (sin voz)
    utterance_audio   — silencio + voz + silencio (enunciado realista)

Mocks de modelos:
    mock_silero_model  — devuelve confianza alta (0.9) para cualquier input
    mock_whisper_model — devuelve segmento de texto de prueba
    loaded_vad         — VoiceActivityDetector con Silero mockeado
    loaded_stt         — SpeechToText con WhisperModel mockeado
    mock_sounddevice   — módulo sounddevice completamente mockeado
    full_pipeline      — VoicePipeline lista para usar, sin hardware real
"""

from __future__ import annotations

import sys
import threading
import types
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from itzel_voice.pipeline import PipelineConfig, VoicePipeline
from itzel_voice.stt import STTConfig, SpeechToText
from itzel_voice.tts import TTSConfig, TextToSpeech
from itzel_voice.tts_piper import PiperTextToSpeech
from itzel_voice.vad import CHUNK_SAMPLES, SAMPLE_RATE, VADConfig, VoiceActivityDetector

# ─── constantes de test ───────────────────────────────────────────────────────

_RNG = np.random.default_rng(42)   # semilla fija para reproducibilidad


# ─── helpers de generación de audio ──────────────────────────────────────────

def make_silence(duration_s: float = 1.0) -> np.ndarray:
    """Genera `duration_s` segundos de silencio (zeros float32)."""
    return np.zeros(int(SAMPLE_RATE * duration_s), dtype=np.float32)


def make_speech(
    duration_s: float = 1.0,
    fundamental_hz: float = 300.0,
) -> np.ndarray:
    """
    Genera audio sintético que simula voz humana.

    Usa una señal multiarmónica (fundamental + 2 armónicos + ruido leve)
    similar al espectro de una vocal sostenida. No es voz real, pero tiene
    la energía espectral necesaria para activar el VAD con umbral ≤ 0.5.

    Args:
        duration_s:      duración en segundos
        fundamental_hz:  frecuencia fundamental (~300 Hz voz masculina)
    """
    n = int(SAMPLE_RATE * duration_s)
    t = np.linspace(0, duration_s, n, dtype=np.float32)

    audio = (
        0.40 * np.sin(2 * np.pi * fundamental_hz * t)
        + 0.25 * np.sin(2 * np.pi * fundamental_hz * 2 * t)
        + 0.20 * np.sin(2 * np.pi * fundamental_hz * 3 * t)
        + 0.10 * np.sin(2 * np.pi * fundamental_hz * 4 * t)
        + 0.05 * _RNG.uniform(-1.0, 1.0, n).astype(np.float32)
    )
    # Normalizar a ±0.7 (deja headroom, no saturado)
    audio = audio / np.abs(audio).max() * 0.7
    return audio.astype(np.float32)


def make_noise(duration_s: float = 1.0, amplitude: float = 0.05) -> np.ndarray:
    """Ruido blanco uniforme (sin estructura armónica — no activa VAD)."""
    n = int(SAMPLE_RATE * duration_s)
    return (_RNG.uniform(-amplitude, amplitude, n)).astype(np.float32)


def make_utterance(
    speech_s: float = 1.5,
    pre_silence_s: float = 0.5,
    post_silence_s: float = 0.8,
) -> np.ndarray:
    """
    Enunciado realista: silencio_pre + voz + silencio_post.

    El post-silencio supera el silence_trigger_ms default (700ms) para
    que el VAD cierre el enunciado automáticamente en los tests.
    """
    return np.concatenate([
        make_silence(pre_silence_s),
        make_speech(speech_s),
        make_silence(post_silence_s),
    ])


def make_chunks(audio: np.ndarray) -> list[np.ndarray]:
    """
    Divide audio en chunks de CHUNK_SAMPLES.
    Útil para simular el flujo del callback de sounddevice.
    """
    chunks = []
    for i in range(0, len(audio), CHUNK_SAMPLES):
        chunk = audio[i : i + CHUNK_SAMPLES]
        if len(chunk) < CHUNK_SAMPLES:
            chunk = np.pad(chunk, (0, CHUNK_SAMPLES - len(chunk)))
        chunks.append(chunk.astype(np.float32))
    return chunks


# ─── fixtures de audio ────────────────────────────────────────────────────────

@pytest.fixture
def silence_audio() -> np.ndarray:
    return make_silence(1.0)


@pytest.fixture
def speech_audio() -> np.ndarray:
    return make_speech(1.5)


@pytest.fixture
def noise_audio() -> np.ndarray:
    return make_noise(1.0)


@pytest.fixture
def utterance_audio() -> np.ndarray:
    return make_utterance(speech_s=1.5, pre_silence_s=0.3, post_silence_s=0.8)


@pytest.fixture
def speech_chunks(speech_audio) -> list[np.ndarray]:
    return make_chunks(speech_audio)


@pytest.fixture
def silence_chunks(silence_audio) -> list[np.ndarray]:
    return make_chunks(silence_audio)


# ─── mocks de modelos ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_silero_model() -> MagicMock:
    """
    Mock del modelo Silero VAD.

    Por defecto devuelve 0.9 (alta confianza de voz) para cualquier chunk.
    En tests específicos se puede cambiar el return_value:

        mock_silero_model.return_value = 0.05  # silencio
    """
    model = MagicMock(name="SileroModel")
    model.return_value = 0.9          # float — probabilidad de voz
    model.reset_states = MagicMock()
    return model


@pytest.fixture
def mock_whisper_segment() -> MagicMock:
    """Un segmento de transcripción de faster-whisper."""
    seg = MagicMock(name="WhisperSegment")
    seg.text  = " Hola, esto es una prueba de transcripción."
    seg.start = 0.0
    seg.end   = 2.1
    return seg


@pytest.fixture
def mock_whisper_info() -> MagicMock:
    """Metadatos de transcripción de faster-whisper."""
    info = MagicMock(name="TranscriptionInfo")
    info.language             = "es"
    info.language_probability = 0.97
    return info


@pytest.fixture
def mock_whisper_model(mock_whisper_segment, mock_whisper_info) -> MagicMock:
    """
    Mock completo de WhisperModel (faster-whisper).

    model.transcribe() devuelve (iter([segment]), info).
    """
    model = MagicMock(name="WhisperModel")

    def _transcribe(*args, **kwargs) -> tuple[Iterator, Any]:
        return iter([mock_whisper_segment]), mock_whisper_info

    model.transcribe.side_effect = _transcribe
    return model


# ─── fixtures de componentes pre-cargados ────────────────────────────────────

@pytest.fixture
def loaded_vad(mock_silero_model) -> VoiceActivityDetector:
    """
    VoiceActivityDetector listo para usar, con Silero mockeado.
    No requiere onnxruntime ni descarga de modelos.
    """
    vad = VoiceActivityDetector(VADConfig())
    vad._model    = mock_silero_model
    vad._use_onnx = True              # rama ONNX (acepta numpy directamente)
    return vad


@pytest.fixture
def loaded_vad_silent(mock_silero_model) -> VoiceActivityDetector:
    """VoiceActivityDetector donde el modelo nunca detecta voz."""
    mock_silero_model.return_value = 0.05
    vad = VoiceActivityDetector(VADConfig())
    vad._model    = mock_silero_model
    vad._use_onnx = True
    return vad


@pytest.fixture
def loaded_stt(mock_whisper_model) -> SpeechToText:
    """
    SpeechToText listo para usar, con WhisperModel mockeado.
    No requiere faster-whisper ni descarga del modelo de 460 MB.
    """
    stt = SpeechToText(STTConfig())
    stt._model  = mock_whisper_model
    stt._loaded = True
    return stt


# ─── mock de sounddevice ──────────────────────────────────────────────────────

class _MockInputStream:
    """
    Simula sd.InputStream sin acceder a hardware de audio.

    En los tests, para simular chunks de audio hay que llamar
    directamente al pipeline._audio_callback().
    """
    def __init__(self, *args, **kwargs):
        self.active   = False
        self.callback = kwargs.get("callback")

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False

    def close(self) -> None:
        self.active = False

    def inject(self, audio: np.ndarray) -> None:
        """
        Inyecta audio como si viniera del callback de PortAudio.
        Útil para tests de integración.
        """
        if self.callback and self.active:
            self.callback(audio.reshape(-1, 1), len(audio), None, None)


class _MockOutputStream:
    """
    Simula sd.OutputStream sin acceder a hardware de audio.

    Registra lo que se escribe y si fue abortado — útil para assertions
    en tests de TTS: ¿se escribió audio? ¿se interrumpió correctamente?
    """
    _last_instance: "_MockOutputStream | None" = None

    def __init__(self, *args, **kwargs) -> None:
        self.written:  list[np.ndarray] = []
        self.aborted:  bool             = False
        self.started:  bool             = False
        self.closed:   bool             = False
        _MockOutputStream._last_instance = self

    def start(self) -> None:
        self.started = True

    def write(self, data) -> None:
        self.written.append(np.asarray(data).copy())

    def abort(self) -> None:
        self.aborted = True

    def stop(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    @property
    def total_samples(self) -> int:
        """Número total de samples escritos (para assertions de duración)."""
        return sum(len(w) for w in self.written)


@pytest.fixture
def mock_sounddevice() -> types.ModuleType:
    """
    Módulo sounddevice completamente mockeado.
    Registra el mock en sys.modules para que pipeline.py lo importe.
    """
    sd_mock               = types.ModuleType("sounddevice")
    sd_mock.InputStream   = _MockInputStream
    sd_mock.OutputStream  = _MockOutputStream   # para tests de TTS en el pipeline
    sd_mock.query_devices = MagicMock(return_value=[
        {
            "name": "Mock Microphone",
            "max_input_channels": 1,
            "default_samplerate": 16_000.0,
        }
    ])
    sd_mock.default       = MagicMock()
    sd_mock.default.device = 0
    return sd_mock


# ─── fixture de pipeline completo ────────────────────────────────────────────

@pytest.fixture
def full_pipeline(
    loaded_vad,
    loaded_stt,
    mock_sounddevice,
) -> Iterator[VoicePipeline]:
    """
    VoicePipeline lista para usar, sin hardware real.

    - VAD mockeado (Silero, alta confianza)
    - STT mockeado (WhisperModel, texto fijo)
    - sounddevice mockeado (sin micrófono real)

    Hace teardown automático al finalizar el test.
    """
    # Registrar mock de sounddevice en sys.modules
    sys.modules["sounddevice"] = mock_sounddevice

    config   = PipelineConfig(mode="always")
    pipeline = VoicePipeline(config)
    pipeline.vad = loaded_vad
    pipeline.stt = loaded_stt

    yield pipeline

    # Teardown: detener si sigue corriendo
    if pipeline.is_running:
        pipeline.stop()

    # Limpiar sys.modules (no contaminar otros tests)
    sys.modules.pop("sounddevice", None)


@pytest.fixture
def full_pipeline_hotkey(
    loaded_vad,
    loaded_stt,
    mock_sounddevice,
) -> Iterator[VoicePipeline]:
    """Igual que full_pipeline pero en modo push-to-talk."""
    sys.modules["sounddevice"] = mock_sounddevice

    config   = PipelineConfig(mode="hotkey")
    pipeline = VoicePipeline(config)
    pipeline.vad = loaded_vad
    pipeline.stt = loaded_stt

    yield pipeline

    if pipeline.is_running:
        pipeline.stop()
    sys.modules.pop("sounddevice", None)


# ─── fixtures de TTS ──────────────────────────────────────────────────────────

def _make_kokoro_gen():
    """Generador que simula KPipeline: devuelve 100ms de audio silencioso."""
    def _gen(text, voice="ef_dora", speed=1.0):
        yield ("", "", np.zeros(int(24_000 * 0.1), dtype=np.float32))
    m = MagicMock(name="KPipeline")
    m.side_effect = _gen
    return m


@pytest.fixture
def mock_tts() -> TextToSpeech:
    """
    TextToSpeech pre-cargado con modelo Kokoro mockeado.

    No requiere kokoro instalado ni ~82 MB de modelo descargado.
    sounddevice debe estar ya en sys.modules (usa mock_sounddevice).
    """
    tts         = TextToSpeech(TTSConfig())
    tts._model  = _make_kokoro_gen()
    tts._loaded = True
    return tts


@pytest.fixture
def mock_piper_tts() -> PiperTextToSpeech:
    """
    PiperTextToSpeech pre-cargado con voz mockeada.

    La voz mock devuelve 100ms de int16 zeros cuando se llama
    synthesize_stream_raw(), reproduciendo la misma interfaz de Piper.
    """
    # Crear mock de PiperVoice
    mock_voice = MagicMock(name="PiperVoice")
    mock_voice.config.sample_rate = 22_050

    # synthesize_stream_raw devuelve bytes de int16 (100ms @ 22050 Hz)
    raw = np.zeros(int(22_050 * 0.1), dtype=np.int16).tobytes()
    mock_voice.synthesize_stream_raw.return_value = iter([raw])

    tts          = PiperTextToSpeech(TTSConfig(engine="piper", voice="es_MX-claude-high"))
    tts._voice   = mock_voice
    tts._sr      = 22_050
    tts._loaded  = True
    return tts


# ─── pipeline completo con TTS ────────────────────────────────────────────────

@pytest.fixture
def full_pipeline_with_tts(
    loaded_vad,
    loaded_stt,
    mock_tts,
    mock_sounddevice,
) -> Iterator[VoicePipeline]:
    """
    VoicePipeline con TTS activado — sin hardware real.

    Extiende full_pipeline con un TextToSpeech mockeado asignado antes
    de start(), de modo que _setup_tts_callbacks() se ejecuta en start().

    Útil para tests de integración STT → TTS y de interrupción por voz.
    """
    sys.modules["sounddevice"] = mock_sounddevice

    config   = PipelineConfig(mode="always")
    pipeline = VoicePipeline(config)
    pipeline.vad = loaded_vad
    pipeline.stt = loaded_stt
    pipeline.tts = mock_tts

    yield pipeline

    if pipeline.is_running:
        pipeline.stop()
    sys.modules.pop("sounddevice", None)
