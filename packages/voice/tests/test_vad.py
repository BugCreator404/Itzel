"""
Tests del VoiceActivityDetector.

Cubre:
  - Configuración y defaults
  - Carga del modelo (con mock de silero_vad)
  - Normalización de chunks
  - Máquina de estados IDLE → SPEECH → IDLE
  - Buffer pre-voz
  - Callbacks on_speech_start y on_speech_end
  - Duración mínima de enunciado
  - force_end() y reset()
  - Propiedad state

Ningún test descarga modelos reales ni requiere micrófono.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from itzel_voice.vad import (
    CHUNK_SAMPLES,
    SAMPLE_RATE,
    VADConfig,
    VoiceActivityDetector,
    _normalize_chunk,
    _samples_to_ms,
)

from .conftest import make_silence, make_speech, make_chunks

# ─── helpers de test ──────────────────────────────────────────────────────────

def _one_chunk() -> np.ndarray:
    """Genera exactamente un chunk de silencio (512 muestras)."""
    return np.zeros(CHUNK_SAMPLES, dtype=np.float32)


def _feed_chunks(vad: VoiceActivityDetector, n: int) -> None:
    """Procesa n chunks en el VAD (contenido irrelevante — modelo es mock)."""
    for _ in range(n):
        vad.process_chunk(_one_chunk())


def _wait_callback(event: threading.Event, timeout: float = 2.0) -> bool:
    """Espera a que un callback async dispare el evento."""
    return event.wait(timeout=timeout)


# ─── configuración ────────────────────────────────────────────────────────────

class TestVADConfig:
    def test_defaults(self):
        cfg = VADConfig()
        assert cfg.threshold          == 0.5
        assert cfg.min_speech_ms      == 250
        assert cfg.silence_trigger_ms == 700
        assert cfg.max_speech_s       == 30
        assert cfg.sample_rate        == SAMPLE_RATE
        assert cfg.use_onnx           is True

    def test_custom_values(self):
        cfg = VADConfig(threshold=0.7, min_speech_ms=100, silence_trigger_ms=500)
        assert cfg.threshold          == 0.7
        assert cfg.min_speech_ms      == 100
        assert cfg.silence_trigger_ms == 500


# ─── carga del modelo ─────────────────────────────────────────────────────────

class TestVADLoad:
    def test_load_uses_onnx_first(self, mock_silero_model):
        """load() intenta onnx=True antes de torch."""
        with patch("itzel_voice.vad.VoiceActivityDetector.load") as mock_load:
            vad = VoiceActivityDetector()
            vad._model    = mock_silero_model
            vad._use_onnx = True
        assert mock_load.call_count == 0   # no se llamó al real load()

    def test_is_loaded_false_before_load(self):
        vad = VoiceActivityDetector()
        assert vad.is_loaded is False

    def test_is_loaded_true_after_mock_load(self, loaded_vad):
        assert loaded_vad.is_loaded is True

    def test_process_chunk_raises_if_not_loaded(self):
        vad = VoiceActivityDetector()
        with pytest.raises(RuntimeError, match="load\\(\\)"):
            vad.process_chunk(_one_chunk())

    def test_load_raises_voice_error_without_silero(self):
        """Sin silero_vad instalado, load() lanza VoiceNotAvailableError."""
        import sys
        original = sys.modules.get("silero_vad")
        sys.modules["silero_vad"] = None  # type: ignore[assignment]
        try:
            from itzel_voice import VoiceNotAvailableError
            vad = VoiceActivityDetector()
            with pytest.raises((VoiceNotAvailableError, ImportError)):
                vad.load()
        finally:
            if original is None:
                sys.modules.pop("silero_vad", None)
            else:
                sys.modules["silero_vad"] = original


# ─── normalización de chunks ──────────────────────────────────────────────────

class TestChunkNormalization:
    def test_correct_size_unchanged(self):
        chunk = np.ones(CHUNK_SAMPLES, dtype=np.float32)
        result = _normalize_chunk(chunk)
        assert len(result) == CHUNK_SAMPLES
        np.testing.assert_array_equal(result, chunk)

    def test_short_chunk_padded_with_zeros(self):
        chunk = np.ones(100, dtype=np.float32)
        result = _normalize_chunk(chunk)
        assert len(result) == CHUNK_SAMPLES
        assert np.all(result[100:] == 0.0)

    def test_long_chunk_truncated(self):
        chunk = np.ones(1024, dtype=np.float32)
        result = _normalize_chunk(chunk)
        assert len(result) == CHUNK_SAMPLES

    def test_output_always_float32(self):
        chunk = np.ones(CHUNK_SAMPLES, dtype=np.float64)
        result = _normalize_chunk(chunk)
        assert result.dtype == np.float32

    def test_samples_to_ms(self):
        assert _samples_to_ms(SAMPLE_RATE, SAMPLE_RATE) == pytest.approx(1000.0)
        assert _samples_to_ms(CHUNK_SAMPLES, SAMPLE_RATE) == pytest.approx(32.0)


# ─── máquina de estados ───────────────────────────────────────────────────────

class TestVADStateMachine:
    def test_initial_state_is_idle(self, loaded_vad):
        assert loaded_vad.state == "idle"

    def test_high_confidence_transitions_to_speech(self, loaded_vad):
        """Con modelo retornando 0.9, un solo chunk activa SPEECH."""
        loaded_vad._model.return_value = 0.9
        loaded_vad.process_chunk(_one_chunk())
        assert loaded_vad.state == "speech"

    def test_low_confidence_stays_idle(self, loaded_vad):
        """Con modelo retornando 0.05, el estado se mantiene en IDLE."""
        loaded_vad._model.return_value = 0.05
        _feed_chunks(loaded_vad, 30)
        assert loaded_vad.state == "idle"

    def test_silence_after_speech_resets_to_idle(self, loaded_vad):
        """Suficiente silencio después de voz → vuelta a IDLE."""
        # Fase voz
        loaded_vad._model.return_value = 0.9
        _feed_chunks(loaded_vad, 20)
        assert loaded_vad.state == "speech"

        # Fase silencio: silence_trigger_ms=700 → ~22 chunks de 32ms
        loaded_vad._model.return_value = 0.05
        _feed_chunks(loaded_vad, 30)   # 960ms > 700ms

        # El estado vuelve a IDLE después de _flush()
        assert loaded_vad.state == "idle"


# ─── callbacks ────────────────────────────────────────────────────────────────

class TestVADCallbacks:
    def test_on_speech_start_fires_on_transition(self, loaded_vad):
        fired = threading.Event()
        loaded_vad.on_speech_start = lambda: fired.set()

        loaded_vad._model.return_value = 0.9
        loaded_vad.process_chunk(_one_chunk())

        assert _wait_callback(fired), "on_speech_start no se disparó"

    def test_on_speech_start_fires_only_once_per_utterance(self, loaded_vad):
        count = []
        loaded_vad.on_speech_start = lambda: count.append(1)

        loaded_vad._model.return_value = 0.9
        _feed_chunks(loaded_vad, 20)
        threading.Event().wait(0.05)   # dar tiempo a threads

        assert len(count) == 1

    def test_on_speech_end_fires_after_silence(self, loaded_vad):
        ended   = threading.Event()
        received: list[np.ndarray] = []

        def _on_end(audio: np.ndarray) -> None:
            received.append(audio)
            ended.set()

        loaded_vad.on_speech_end = _on_end

        # Voz
        loaded_vad._model.return_value = 0.9
        _feed_chunks(loaded_vad, 20)

        # Silencio suficiente para trigger (>700ms)
        loaded_vad._model.return_value = 0.05
        _feed_chunks(loaded_vad, 30)

        assert _wait_callback(ended), "on_speech_end no se disparó"
        assert len(received) == 1
        assert isinstance(received[0], np.ndarray)
        assert received[0].dtype == np.float32

    def test_on_level_change_fires_per_chunk(self, loaded_vad):
        levels: list[float] = []
        loaded_vad.on_level_change = lambda rms: levels.append(rms)

        loaded_vad._model.return_value = 0.05
        _feed_chunks(loaded_vad, 5)

        assert len(levels) == 5
        for lvl in levels:
            assert isinstance(lvl, float)
            assert lvl >= 0.0


# ─── duración mínima de enunciado ─────────────────────────────────────────────

class TestMinSpeechDuration:
    def test_short_utterance_discarded(self, loaded_vad):
        """Enunciado < min_speech_ms (250ms) no dispara on_speech_end."""
        # min_speech_ms=250ms → ~7.8 chunks de 32ms → necesitamos < 8 chunks
        loaded_vad.config.min_speech_ms = 250

        fired = threading.Event()
        loaded_vad.on_speech_end = lambda a: fired.set()

        # Solo 4 chunks de voz (~128ms < 250ms)
        loaded_vad._model.return_value = 0.9
        _feed_chunks(loaded_vad, 4)

        # Silencio para trigger
        loaded_vad._model.return_value = 0.05
        _feed_chunks(loaded_vad, 30)

        # No debe dispararse
        assert not fired.wait(timeout=0.3), "on_speech_end se disparó para enunciado muy corto"

    def test_long_utterance_emitted(self, loaded_vad):
        """Enunciado >= min_speech_ms sí dispara on_speech_end."""
        loaded_vad.config.min_speech_ms = 250

        fired = threading.Event()
        loaded_vad.on_speech_end = lambda a: fired.set()

        # 20 chunks de voz (640ms > 250ms)
        loaded_vad._model.return_value = 0.9
        _feed_chunks(loaded_vad, 20)

        # Silencio
        loaded_vad._model.return_value = 0.05
        _feed_chunks(loaded_vad, 30)

        assert _wait_callback(fired), "on_speech_end no se disparó para enunciado largo"


# ─── pre-speech buffer ────────────────────────────────────────────────────────

class TestPreSpeechBuffer:
    def test_pre_speech_included_in_audio(self, loaded_vad):
        """
        El audio emitido incluye los chunks del buffer pre-voz.
        Esto captura el inicio del habla antes de que el VAD lo detecte.
        """
        received: list[np.ndarray] = []
        ended = threading.Event()

        def _on_end(audio: np.ndarray) -> None:
            received.append(audio)
            ended.set()

        loaded_vad.on_speech_end = _on_end

        # Fase silencio (pre-buffer): 5 chunks → se guardan en _pre_buf
        loaded_vad._model.return_value = 0.05
        _feed_chunks(loaded_vad, 5)

        # Transición a voz: los 5 chunks pre-voz se incluyen
        loaded_vad._model.return_value = 0.9
        _feed_chunks(loaded_vad, 20)

        # Silencio para cierre
        loaded_vad._model.return_value = 0.05
        _feed_chunks(loaded_vad, 30)

        _wait_callback(ended)

        if received:
            # El audio emitido debe tener al menos 20 chunks de voz + pre-buffer
            min_expected = 20 * CHUNK_SAMPLES
            assert len(received[0]) >= min_expected


# ─── force_end y reset ────────────────────────────────────────────────────────

class TestForceEndAndReset:
    def test_force_end_emits_speech(self, loaded_vad):
        """force_end() dispara on_speech_end aunque no haya silencio."""
        fired = threading.Event()
        loaded_vad.on_speech_end = lambda a: fired.set()

        # Activar voz
        loaded_vad._model.return_value = 0.9
        _feed_chunks(loaded_vad, 20)
        assert loaded_vad.state == "speech"

        # Forzar fin sin silencio
        loaded_vad.force_end()

        assert _wait_callback(fired), "force_end() no disparó on_speech_end"
        assert loaded_vad.state == "idle"

    def test_force_end_in_idle_does_nothing(self, loaded_vad):
        """force_end() en IDLE no lanza excepciones ni dispara callbacks."""
        loaded_vad.on_speech_end = MagicMock()
        loaded_vad.force_end()
        threading.Event().wait(0.05)
        loaded_vad.on_speech_end.assert_not_called()

    def test_reset_cancels_speech_accumulation(self, loaded_vad):
        """reset() descarta el audio acumulado y vuelve a IDLE."""
        loaded_vad.on_speech_end = MagicMock()

        loaded_vad._model.return_value = 0.9
        _feed_chunks(loaded_vad, 20)
        assert loaded_vad.state == "speech"

        loaded_vad.reset()

        assert loaded_vad.state == "idle"
        threading.Event().wait(0.05)
        # No debe haber emitido nada
        loaded_vad.on_speech_end.assert_not_called()
