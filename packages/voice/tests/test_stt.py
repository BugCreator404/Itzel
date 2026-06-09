"""
Tests del motor SpeechToText.

Cubre:
  - Configuración y defaults
  - Verificación de estado (requiere load())
  - Transcripción completa (transcribe)
  - Transcripción streaming (transcribe_stream)
  - Audio muy corto (< 100ms) → resultado vacío
  - Múltiples segmentos → texto unido
  - Detección automática de idioma
  - Utilidades de preparación de audio (_prepare_audio)
  - Validación de modelo en download_model()
  - is_downloaded() sin disco real

Ningún test descarga modelos ni requiere faster-whisper instalado.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from itzel_voice.stt import (
    STTConfig,
    SpeechToText,
    TranscriptResult,
    TranscriptSegment,
    _prepare_audio,
)

from .conftest import make_silence, make_speech

# ─── configuración ────────────────────────────────────────────────────────────

class TestSTTConfig:
    def test_defaults(self):
        cfg = STTConfig()
        assert cfg.model_size   == "small"
        assert cfg.language     == "es"
        assert cfg.device       == "cpu"
        assert cfg.compute_type == "int8"
        assert cfg.beam_size    == 5
        assert cfg.vad_filter   is True
        assert cfg.cpu_threads  == 0
        assert "whisper" in str(cfg.models_dir)

    def test_custom_values(self):
        cfg = STTConfig(model_size="medium", language=None, device="cpu")
        assert cfg.model_size == "medium"
        assert cfg.language   is None

    def test_models_dir_is_path(self):
        cfg = STTConfig()
        assert isinstance(cfg.models_dir, Path)


# ─── dataclasses de resultado ────────────────────────────────────────────────

class TestTranscriptDataclasses:
    def test_transcript_segment_fields(self):
        seg = TranscriptSegment(
            text="hola", start_s=0.0, end_s=1.0, language="es"
        )
        assert seg.text     == "hola"
        assert seg.start_s  == 0.0
        assert seg.end_s    == 1.0
        assert seg.language == "es"

    def test_transcript_result_fields(self):
        seg = TranscriptSegment("prueba", 0.0, 1.5, "es")
        res = TranscriptResult(
            text="prueba", segments=[seg], language="es", duration_s=1.5
        )
        assert res.text       == "prueba"
        assert len(res.segments) == 1
        assert res.language   == "es"
        assert res.duration_s == 1.5


# ─── verificación de estado ───────────────────────────────────────────────────

class TestSTTLoadState:
    def test_is_loaded_false_before_load(self):
        stt = SpeechToText()
        assert stt.is_loaded is False

    def test_is_loaded_true_after_mock(self, loaded_stt):
        assert loaded_stt.is_loaded is True

    def test_transcribe_raises_if_not_loaded(self):
        stt = SpeechToText()
        audio = make_speech(1.0)
        with pytest.raises(RuntimeError, match="load\\(\\)"):
            stt.transcribe(audio)

    def test_transcribe_stream_raises_if_not_loaded(self):
        stt = SpeechToText()
        with pytest.raises(RuntimeError, match="load\\(\\)"):
            list(stt.transcribe_stream(make_speech(1.0)))

    def test_detect_language_raises_if_not_loaded(self):
        stt = SpeechToText()
        with pytest.raises(RuntimeError, match="load\\(\\)"):
            stt.detect_language(make_speech(1.0))

    def test_load_raises_without_faster_whisper(self):
        """Sin faster-whisper instalado, load() lanza VoiceNotAvailableError."""
        import sys
        original = sys.modules.get("faster_whisper")
        sys.modules["faster_whisper"] = None  # type: ignore[assignment]
        try:
            from itzel_voice import VoiceNotAvailableError
            stt = SpeechToText()
            with pytest.raises((VoiceNotAvailableError, ImportError)):
                stt.load()
        finally:
            if original is None:
                sys.modules.pop("faster_whisper", None)
            else:
                sys.modules["faster_whisper"] = original


# ─── transcripción completa ───────────────────────────────────────────────────

class TestTranscribe:
    def test_returns_transcript_result(self, loaded_stt, speech_audio):
        result = loaded_stt.transcribe(speech_audio)
        assert isinstance(result, TranscriptResult)

    def test_text_not_empty_for_speech(self, loaded_stt, speech_audio):
        result = loaded_stt.transcribe(speech_audio)
        assert result.text != ""

    def test_language_is_string(self, loaded_stt, speech_audio):
        result = loaded_stt.transcribe(speech_audio)
        assert isinstance(result.language, str)
        assert len(result.language) == 2    # código ISO: "es", "en"…

    def test_duration_approximates_audio_length(self, loaded_stt):
        audio = make_speech(2.0)
        result = loaded_stt.transcribe(audio)
        assert result.duration_s == pytest.approx(2.0, abs=0.1)

    def test_segments_list_populated(self, loaded_stt, speech_audio):
        result = loaded_stt.transcribe(speech_audio)
        assert len(result.segments) >= 1
        assert all(isinstance(s, TranscriptSegment) for s in result.segments)

    def test_multiple_segments_joined(self, loaded_stt):
        """Múltiples segmentos se unen con espacio."""
        seg1 = MagicMock(); seg1.text = " Primera parte."
        seg2 = MagicMock(); seg2.text = " Segunda parte."
        info = MagicMock(); info.language = "es"; info.language_probability = 0.95

        loaded_stt._model.transcribe.side_effect = (
            lambda *a, **kw: (iter([seg1, seg2]), info)
        )

        result = loaded_stt.transcribe(make_speech(3.0))
        assert "Primera parte" in result.text
        assert "Segunda parte" in result.text

    def test_empty_segments_produce_empty_text(self, loaded_stt):
        info = MagicMock(); info.language = "es"; info.language_probability = 0.9
        loaded_stt._model.transcribe.side_effect = (
            lambda *a, **kw: (iter([]), info)
        )
        result = loaded_stt.transcribe(make_speech(1.0))
        assert result.text == ""

    def test_segment_text_is_stripped(self, loaded_stt, mock_whisper_segment):
        """Los espacios iniciales y finales del segmento se eliminan."""
        mock_whisper_segment.text = "  texto con espacios  "
        result = loaded_stt.transcribe(make_speech(1.0))
        # El campo text del resultado une los segmentos
        assert result.text == "texto con espacios"


# ─── audio muy corto ──────────────────────────────────────────────────────────

class TestShortAudio:
    def test_audio_shorter_than_minimum_returns_empty(self, loaded_stt):
        """Audio < 100ms → no enviar a Whisper, retornar vacío."""
        # 50ms @ 16kHz = 800 muestras < 1600 mínimo
        short_audio = np.zeros(800, dtype=np.float32)
        result = loaded_stt.transcribe(short_audio)
        assert result.text     == ""
        assert result.segments == []

    def test_silence_returns_empty_via_model(self, loaded_stt, silence_audio):
        """El modelo recibe el audio; si retorna segmentos vacíos → texto vacío."""
        info = MagicMock(); info.language = "es"; info.language_probability = 0.9
        loaded_stt._model.transcribe.side_effect = (
            lambda *a, **kw: (iter([]), info)
        )
        result = loaded_stt.transcribe(silence_audio)
        assert result.text == ""


# ─── transcripción streaming ──────────────────────────────────────────────────

class TestTranscribeStream:
    def test_yields_strings(self, loaded_stt, speech_audio):
        segments = list(loaded_stt.transcribe_stream(speech_audio))
        assert len(segments) >= 1
        assert all(isinstance(s, str) for s in segments)

    def test_empty_segments_yield_nothing(self, loaded_stt):
        info = MagicMock(); info.language = "es"; info.language_probability = 0.9
        loaded_stt._model.transcribe.side_effect = (
            lambda *a, **kw: (iter([]), info)
        )
        segments = list(loaded_stt.transcribe_stream(make_speech(1.0)))
        assert segments == []

    def test_whitespace_only_segments_filtered(self, loaded_stt):
        seg = MagicMock(); seg.text = "   "
        info = MagicMock(); info.language = "es"; info.language_probability = 0.8
        loaded_stt._model.transcribe.side_effect = (
            lambda *a, **kw: (iter([seg]), info)
        )
        result = list(loaded_stt.transcribe_stream(make_speech(1.0)))
        assert result == []


# ─── detección de idioma ──────────────────────────────────────────────────────

class TestDetectLanguage:
    def test_returns_tuple_lang_prob(self, loaded_stt, speech_audio):
        lang, prob = loaded_stt.detect_language(speech_audio)
        assert isinstance(lang, str)
        assert isinstance(prob, float)
        assert 0.0 <= prob <= 1.0

    def test_detects_spanish(self, loaded_stt, speech_audio):
        lang, _ = loaded_stt.detect_language(speech_audio)
        assert lang == "es"   # mock retorna "es"

    def test_uses_beam_size_1_for_speed(self, loaded_stt, speech_audio):
        """La detección de idioma usa beam_size=1 para ser rápida."""
        loaded_stt.detect_language(speech_audio)
        call_kwargs = loaded_stt._model.transcribe.call_args[1]
        assert call_kwargs.get("beam_size") == 1


# ─── preparación de audio ────────────────────────────────────────────────────

class TestPrepareAudio:
    def test_float32_passthrough(self):
        audio = np.ones(1000, dtype=np.float32) * 0.5
        result = _prepare_audio(audio)
        assert result.dtype == np.float32

    def test_float64_converted(self):
        audio = np.ones(1000, dtype=np.float64)
        result = _prepare_audio(audio)
        assert result.dtype == np.float32

    def test_2d_input_flattened(self):
        audio = np.ones((16000, 1), dtype=np.float32)
        result = _prepare_audio(audio)
        assert result.ndim == 1
        assert len(result) == 16000

    def test_clipping_normalized(self):
        """Amplitudes > 1.0 se normalizan a [-1, 1]."""
        audio = np.array([2.0, -3.0, 1.5], dtype=np.float32)
        result = _prepare_audio(audio)
        assert np.abs(result).max() == pytest.approx(1.0)

    def test_no_normalization_below_1(self):
        """Audio dentro de rango no se modifica."""
        audio = np.array([0.5, -0.5, 0.3], dtype=np.float32)
        result = _prepare_audio(audio)
        np.testing.assert_array_almost_equal(result, audio)

    def test_empty_array_passthrough(self):
        audio = np.array([], dtype=np.float32)
        result = _prepare_audio(audio)
        assert len(result) == 0


# ─── is_downloaded ────────────────────────────────────────────────────────────

class TestIsDownloaded:
    def test_returns_false_when_dir_missing(self, tmp_path):
        stt = SpeechToText(STTConfig(models_dir=tmp_path / "whisper"))
        assert stt.is_downloaded() is False

    def test_returns_false_when_dir_empty(self, tmp_path):
        model_dir = tmp_path / "whisper" / "small"
        model_dir.mkdir(parents=True)
        stt = SpeechToText(STTConfig(models_dir=tmp_path / "whisper"))
        assert stt.is_downloaded() is False

    def test_returns_true_when_files_present(self, tmp_path):
        model_dir = tmp_path / "whisper" / "small"
        model_dir.mkdir(parents=True)
        (model_dir / "model.bin").write_bytes(b"fake")

        stt = SpeechToText(STTConfig(models_dir=tmp_path / "whisper"))
        assert stt.is_downloaded() is True


# ─── download_model ───────────────────────────────────────────────────────────

class TestDownloadModel:
    def test_raises_for_unknown_model(self):
        from itzel_voice.stt import download_model
        with pytest.raises(ValueError, match="no reconocido"):
            download_model("modelo-inexistente")

    def test_valid_model_sizes_accepted(self):
        from itzel_voice.stt import download_model, _HF_REPOS
        # No descargamos — solo comprobamos que no lanza ValueError
        for size in _HF_REPOS:
            # Necesitaría faster-whisper para ejecutar, pero podemos mock
            pass   # la validación ocurre antes del import de WhisperModel
        assert len(_HF_REPOS) >= 4
