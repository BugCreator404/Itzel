"""
Tests del motor TextToSpeech (Kokoro) y PiperTextToSpeech.

Cubre:
  - TTSConfig defaults y valores personalizados
  - SentenceBuffer: acumulación, emisión y flush
  - _split_sentences: segmentación de texto
  - Ciclo de vida: load() / is_loaded / errores sin modelo
  - speak(): callbacks on_start, on_end, on_sentence, on_level
  - speak_stream(): bufferización de tokens y flush final
  - Interrupción: stop() y interrupt_on Event
  - is_downloaded(): sin archivos / con archivo > 10 MB
  - PiperTextToSpeech: estructura, _voice_hf_paths, API compatible

Sin descargas reales, sin modelo real, sin hardware de audio.
"""

from __future__ import annotations

import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from itzel_voice.tts import (
    SentenceBuffer,
    TTSConfig,
    TextToSpeech,
    _split_sentences,
)
from itzel_voice.tts_piper import PiperTextToSpeech, _voice_hf_paths

# ─── fixtures locales ─────────────────────────────────────────────────────────

def _make_audio(duration_s: float = 0.1, sr: int = 24_000) -> np.ndarray:
    """Genera audio sintético de duración dada (silencio)."""
    return np.zeros(int(sr * duration_s), dtype=np.float32)


@pytest.fixture
def mock_kokoro_pipeline():
    """
    Mock de KPipeline de Kokoro.

    Cuando se llama como pipeline(text, voice=..., speed=...), devuelve
    un generador que yielda (graphemes, phonemes, audio_chunk).
    Cada llamada genera 100ms de silencio para ser rápido.
    """
    def _gen(text, voice="ef_dora", speed=1.0):
        yield ("", "", _make_audio(0.1))

    m = MagicMock(name="KPipeline")
    m.side_effect = _gen
    return m


@pytest.fixture
def loaded_tts(mock_kokoro_pipeline) -> TextToSpeech:
    """TextToSpeech con modelo mockeado, listo para usar."""
    tts = TextToSpeech(TTSConfig())
    tts._model  = mock_kokoro_pipeline
    tts._loaded = True
    return tts


@pytest.fixture
def mock_sd_for_tts():
    """
    Inyecta un módulo sounddevice falso con OutputStream funcional.

    Activo durante el test vía patch.dict(sys.modules).
    Permite acceder al último stream creado en FakeOS.last.
    """
    sd_mod = types.ModuleType("sounddevice")

    class FakeOS:
        last: "FakeOS | None" = None

        def __init__(self, *a, **kw):
            self.written: list[np.ndarray] = []
            self.aborted  = False
            self._stopped = False
            FakeOS.last   = self

        def start(self) -> None: pass
        def write(self, d: np.ndarray) -> None:
            self.written.append(np.asarray(d).copy())
        def abort(self) -> None: self.aborted = True
        def stop(self)  -> None: self._stopped = True
        def close(self) -> None: pass

    sd_mod.OutputStream = FakeOS

    with patch.dict(sys.modules, {"sounddevice": sd_mod}):
        yield sd_mod


# ─── TTSConfig ────────────────────────────────────────────────────────────────

class TestTTSConfig:
    def test_defaults(self):
        cfg = TTSConfig()
        assert cfg.engine      == "kokoro"
        assert cfg.voice       == "ef_dora"
        assert cfg.lang_code   == "e"
        assert cfg.speed       == 1.0
        assert cfg.volume      == 1.0
        assert cfg.sample_rate == 24_000
        assert "tts" in str(cfg.models_dir)

    def test_custom_speed_volume(self):
        cfg = TTSConfig(speed=1.5, volume=0.8)
        assert cfg.speed  == 1.5
        assert cfg.volume == 0.8

    def test_models_dir_is_path(self):
        cfg = TTSConfig()
        assert isinstance(cfg.models_dir, Path)


# ─── SentenceBuffer ───────────────────────────────────────────────────────────

class TestSentenceBuffer:
    def test_push_returns_none_without_boundary(self):
        buf = SentenceBuffer()
        assert buf.push("Hola") is None
        assert buf.push(" mundo") is None

    def test_push_emits_at_boundary_when_min_chars_met(self):
        buf = SentenceBuffer(min_chars=5)
        assert buf.push("Hola") is None        # 4 chars, no boundary
        result = buf.push(".")                  # now "Hola.", boundary + ≥5
        assert result == "Hola."

    def test_push_does_not_emit_if_below_min_chars(self):
        buf = SentenceBuffer(min_chars=20)
        # "Hola." es solo 5 chars < 20
        for tok in ["Ho", "la", "."]:
            result = buf.push(tok)
        assert result is None  # no emitido aún
        assert buf.buffered == "Hola."

    def test_push_clears_buffer_on_emit(self):
        buf = SentenceBuffer(min_chars=3)
        buf.push("Hola.")
        assert buf.buffered == ""

    def test_flush_returns_remaining_text(self):
        buf = SentenceBuffer()
        buf.push("Sin punto final")
        result = buf.flush()
        assert result == "Sin punto final"

    def test_flush_returns_none_when_empty(self):
        buf = SentenceBuffer()
        assert buf.flush() is None

    def test_flush_after_clear_returns_none(self):
        buf = SentenceBuffer()
        buf.push("Algo de texto")
        buf.clear()
        assert buf.flush() is None

    def test_clear_discards_buffer(self):
        buf = SentenceBuffer()
        buf.push("Texto acumulado")
        buf.clear()
        assert buf.buffered == ""

    def test_multiple_sentences_emitted_in_order(self):
        buf = SentenceBuffer(min_chars=5)
        results = []
        for token in ["Primera.", " ", "Segunda.", " ", "Tercera."]:
            s = buf.push(token)
            if s:
                results.append(s)
        assert results == ["Primera.", "Segunda.", "Tercera."]


# ─── _split_sentences ─────────────────────────────────────────────────────────

class TestSplitSentences:
    def test_single_sentence(self):
        parts = _split_sentences("Hola, soy Itzel.")
        assert parts == ["Hola, soy Itzel."]

    def test_multiple_sentences(self):
        parts = _split_sentences("Primera. Segunda. Tercera.")
        assert len(parts) == 3
        assert parts[0] == "Primera."

    def test_text_without_punctuation(self):
        parts = _split_sentences("Texto sin signos de puntuación")
        assert len(parts) == 1

    def test_empty_string(self):
        parts = _split_sentences("")
        assert parts == []

    def test_question_and_exclamation(self):
        parts = _split_sentences("¿Cómo estás? ¡Muy bien!")
        # Los signos de apertura no son límites — solo . ! ? ; :
        assert len(parts) >= 1


# ─── estado de carga ──────────────────────────────────────────────────────────

class TestTTSLoadState:
    def test_is_loaded_false_before_load(self):
        tts = TextToSpeech()
        assert tts.is_loaded is False

    def test_is_loaded_true_after_mock(self, loaded_tts):
        assert loaded_tts.is_loaded is True

    def test_speak_raises_if_not_loaded(self):
        tts = TextToSpeech()
        with pytest.raises(RuntimeError, match="load\\(\\)"):
            tts.speak("Hola.")

    def test_speak_stream_raises_if_not_loaded(self):
        tts = TextToSpeech()
        with pytest.raises(RuntimeError, match="load\\(\\)"):
            tts.speak_stream(iter(["hola"]))

    def test_load_raises_without_kokoro(self):
        """Sin kokoro instalado, load() lanza VoiceNotAvailableError."""
        with patch.dict(sys.modules, {"kokoro": None}):
            from itzel_voice import VoiceNotAvailableError
            tts = TextToSpeech()
            with pytest.raises((VoiceNotAvailableError, ImportError)):
                tts.load()


# ─── speak() — callbacks ──────────────────────────────────────────────────────

class TestTTSSpeak:
    def test_on_start_fires(self, loaded_tts, mock_sd_for_tts):
        fired = threading.Event()
        loaded_tts.on_start = lambda: fired.set()
        loaded_tts.speak("Hola, soy Itzel.")
        assert fired.is_set(), "on_start no se disparó"

    def test_on_end_fires(self, loaded_tts, mock_sd_for_tts):
        fired = threading.Event()
        loaded_tts.on_end = lambda: fired.set()
        loaded_tts.speak("Hola, soy Itzel.")
        assert fired.is_set(), "on_end no se disparó"

    def test_on_sentence_fires_per_sentence(self, loaded_tts, mock_sd_for_tts):
        sentences = []
        loaded_tts.on_sentence = lambda s: sentences.append(s)
        loaded_tts.speak("Primera oración. Segunda oración.")
        assert len(sentences) == 2

    def test_on_level_fires_with_audio(self, loaded_tts, mock_sd_for_tts):
        """on_level se llama para cada sub-chunk de audio."""
        levels: list[float] = []
        loaded_tts.on_level = lambda rms: levels.append(rms)
        loaded_tts.speak("Hola, Itzel.")
        assert len(levels) > 0
        assert all(isinstance(l, float) and l >= 0.0 for l in levels)

    def test_speak_calls_model_per_sentence(self, loaded_tts, mock_sd_for_tts):
        """El modelo Kokoro se llama una vez por frase."""
        loaded_tts.speak("Primera. Segunda.")
        assert loaded_tts._model.call_count == 2

    def test_volume_zero_produces_silence(self, loaded_tts, mock_sd_for_tts):
        """volume=0.0 silencia la salida: aunque el modelo genere audio con
        amplitud, lo que se escribe al stream es todo ceros (audio * 0)."""
        loaded_tts.config.volume = 0.0

        # El modelo emite audio NO silencioso a propósito, para probar que el
        # silencio viene del escalado por volume=0 y no de un mock que ya da
        # ceros. The model emits non-silent audio so the test proves the
        # silence comes from the volume scaling, not from a zero-filled mock.
        loud = np.ones(int(24_000 * 0.1), dtype=np.float32)
        loaded_tts._model.side_effect = (
            lambda text, voice="ef_dora", speed=1.0: iter([("", "", loud)])
        )

        loaded_tts.speak("Texto.")

        # _generate_and_play() escala por volume y escribe en sub-chunks al
        # OutputStream; capturamos lo realmente escrito al hardware (mock).
        written = mock_sd_for_tts.OutputStream.last.written
        assert written, "no se escribió nada al stream de salida"
        assert all(np.all(chunk == 0.0) for chunk in written)


# ─── speak_stream() ───────────────────────────────────────────────────────────

class TestTTSSpeakStream:
    def test_buffers_tokens_and_emits_sentence(self, loaded_tts, mock_sd_for_tts):
        """Los tokens se acumulan hasta tener una frase completa."""
        sentences = []
        loaded_tts.on_sentence = lambda s: sentences.append(s)

        tokens = [
            "Esta ", "es ", "la ", "primera ", "oración", ".",
        ]
        loaded_tts.speak_stream(iter(tokens))
        assert "Esta es la primera oración." in sentences

    def test_flush_emits_incomplete_sentence(self, loaded_tts, mock_sd_for_tts):
        """Texto sin puntuación final se emite en flush()."""
        sentences = []
        loaded_tts.on_sentence = lambda s: sentences.append(s)

        tokens = ["Texto", " sin", " puntuación"]  # sin "."
        loaded_tts.speak_stream(iter(tokens))
        assert sentences == ["Texto sin puntuación"]

    def test_on_start_and_end_fire_for_stream(self, loaded_tts, mock_sd_for_tts):
        start_fired = threading.Event()
        end_fired   = threading.Event()
        loaded_tts.on_start = lambda: start_fired.set()
        loaded_tts.on_end   = lambda: end_fired.set()
        loaded_tts.speak_stream(iter(["Hola", "."]))
        assert start_fired.is_set()
        assert end_fired.is_set()

    def test_empty_stream_fires_start_and_end(self, loaded_tts, mock_sd_for_tts):
        """Un stream vacío todavía dispara on_start y on_end."""
        calls: list[str] = []
        loaded_tts.on_start = lambda: calls.append("start")
        loaded_tts.on_end   = lambda: calls.append("end")
        loaded_tts.speak_stream(iter([]))
        assert calls == ["start", "end"]

    def test_multiple_sentences_in_order(self, loaded_tts, mock_sd_for_tts):
        """Las frases se emiten en el orden en que se acumulan."""
        sentences: list[str] = []
        loaded_tts.on_sentence = lambda s: sentences.append(s)

        # Dos frases largas (> 20 chars cada una)
        tokens = (
            list("Esta es la primera oración. ") +
            list("Y esta es la segunda oración.")
        )
        loaded_tts.speak_stream(iter(tokens))
        assert len(sentences) == 2
        assert sentences[0].startswith("Esta es la primera")
        assert sentences[1].startswith("Y esta es la segunda")


# ─── interrupción ─────────────────────────────────────────────────────────────

class TestTTSInterruption:
    def test_stop_sets_interrupt_event(self, loaded_tts):
        loaded_tts.stop()
        assert loaded_tts._interrupt.is_set()

    def test_stop_fires_on_interrupted_callback(self, loaded_tts):
        fired = threading.Event()
        loaded_tts.on_interrupted = lambda: fired.set()
        loaded_tts.stop()
        assert fired.wait(timeout=1.0), "on_interrupted no se disparó"

    def test_speak_clears_interrupt_at_start(self, loaded_tts, mock_sd_for_tts):
        """Cada llamada a speak() limpia el _interrupt del stop() anterior."""
        loaded_tts.stop()                  # pre-set interrupt
        assert loaded_tts._interrupt.is_set()

        called = []
        loaded_tts.on_sentence = lambda s: called.append(s)
        loaded_tts.speak("Hola.")
        assert len(called) == 1            # se ejecutó normalmente

    def test_speak_stops_between_sentences_on_interrupt(self, loaded_tts):
        """
        Cuando _generate_and_play devuelve False, el loop de speak() para.
        Simulamos devolviendo False manualmente.
        """
        sentences_called: list[str] = []

        def _fake_gap(sentence, interrupt_on):
            sentences_called.append(sentence)
            loaded_tts._interrupt.set()    # interrumpir después de primera frase
            return False                   # señal: fue interrumpida

        loaded_tts._generate_and_play = _fake_gap
        loaded_tts.speak("Primera. Segunda. Tercera.")
        assert sentences_called == ["Primera."]

    def test_speak_stream_stops_on_interrupt(self, loaded_tts):
        """speak_stream() para cuando _interrupt se activa mid-stream."""
        sentences_called: list[str] = []

        def _fake_gap(sentence, interrupt_on):
            sentences_called.append(sentence)
            loaded_tts._interrupt.set()
            return False

        loaded_tts._generate_and_play = _fake_gap
        # Dos frases largas
        tokens = list("Esta es la primera oración. ") + list("Esta es la segunda oración.")
        loaded_tts.speak_stream(iter(tokens))
        assert len(sentences_called) <= 1

    def test_interrupt_on_event_stops_speak(self, loaded_tts, mock_sd_for_tts):
        """interrupt_on Event externo también detiene speak()."""
        ext_event = threading.Event()
        sentences_called: list[str] = []

        def _fake_gap(sentence, interrupt_on):
            sentences_called.append(sentence)
            ext_event.set()                # activar evento externo
            return False

        loaded_tts._generate_and_play = _fake_gap
        loaded_tts.speak("Primera. Segunda.", interrupt_on=ext_event)
        assert sentences_called == ["Primera."]

    def test_is_interrupted_false_when_clear(self, loaded_tts):
        loaded_tts._interrupt.clear()
        assert loaded_tts._is_interrupted(None) is False

    def test_is_interrupted_true_when_set(self, loaded_tts):
        loaded_tts._interrupt.set()
        assert loaded_tts._is_interrupted(None) is True

    def test_is_interrupted_true_from_external_event(self, loaded_tts):
        ext = threading.Event()
        ext.set()
        assert loaded_tts._is_interrupted(ext) is True


# ─── is_downloaded ────────────────────────────────────────────────────────────

class TestTTSIsDownloaded:
    def test_false_when_dir_missing(self, tmp_path):
        tts = TextToSpeech(TTSConfig(models_dir=tmp_path / "tts"))
        assert tts.is_downloaded() is False

    def test_false_when_no_large_files(self, tmp_path):
        cache = tmp_path / "tts" / "kokoro"
        cache.mkdir(parents=True)
        (cache / "small.txt").write_bytes(b"x" * 100)  # < 10MB
        tts = TextToSpeech(TTSConfig(models_dir=tmp_path / "tts"))
        assert tts.is_downloaded() is False

    def test_true_when_large_file_present(self, tmp_path):
        cache = tmp_path / "tts" / "kokoro"
        cache.mkdir(parents=True)
        big_file = cache / "kokoro.safetensors"
        big_file.write_bytes(b"0" * (11 * 1024 * 1024))   # 11 MB > 10 MB
        tts = TextToSpeech(TTSConfig(models_dir=tmp_path / "tts"))
        assert tts.is_downloaded() is True


# ─── PiperTextToSpeech ────────────────────────────────────────────────────────

class TestPiperVoicePaths:
    def test_es_mx_high(self):
        onnx, json = _voice_hf_paths("es_MX-claude-high")
        assert onnx == "es/MX/es_MX-claude-high/high/es_MX-claude-high.onnx"
        assert json == "es/MX/es_MX-claude-high/high/es_MX-claude-high.onnx.json"

    def test_es_es_medium(self):
        onnx, _ = _voice_hf_paths("es_ES-davefx-medium")
        assert onnx == "es/ES/es_ES-davefx-medium/medium/es_ES-davefx-medium.onnx"

    def test_es_es_x_low(self):
        onnx, _ = _voice_hf_paths("es_ES-carlfm-x_low")
        assert onnx == "es/ES/es_ES-carlfm-x_low/x_low/es_ES-carlfm-x_low.onnx"


class TestPiperStructure:
    def test_has_same_public_api_as_kokoro(self):
        """PiperTextToSpeech expone la misma interfaz que TextToSpeech."""
        kokoro_api = {"load", "speak", "speak_stream", "stop", "is_loaded", "is_downloaded"}
        piper_api  = set(dir(PiperTextToSpeech))
        assert kokoro_api <= piper_api

    def test_is_loaded_false_before_load(self):
        tts = PiperTextToSpeech()
        assert tts.is_loaded is False

    def test_is_downloaded_false_without_files(self, tmp_path):
        cfg = TTSConfig(engine="piper", voice="es_MX-claude-high", models_dir=tmp_path)
        tts = PiperTextToSpeech(cfg)
        assert tts.is_downloaded() is False

    def test_is_downloaded_true_when_onnx_present(self, tmp_path):
        piper_dir = tmp_path / "piper"
        piper_dir.mkdir()
        (piper_dir / "es_MX-claude-high.onnx").write_bytes(b"fake model")
        cfg = TTSConfig(engine="piper", voice="es_MX-claude-high", models_dir=tmp_path)
        tts = PiperTextToSpeech(cfg)
        assert tts.is_downloaded() is True

    def test_speak_raises_if_not_loaded(self):
        tts = PiperTextToSpeech()
        with pytest.raises(RuntimeError, match="load\\(\\)"):
            tts.speak("Hola.")

    def test_speak_stream_raises_if_not_loaded(self):
        tts = PiperTextToSpeech()
        with pytest.raises(RuntimeError, match="load\\(\\)"):
            tts.speak_stream(iter(["hola"]))

    def test_load_raises_without_piper(self):
        """Sin piper-tts instalado, load() lanza VoiceNotAvailableError."""
        with patch.dict(sys.modules, {"piper": None, "piper.voice": None}):
            from itzel_voice import VoiceNotAvailableError
            tts = PiperTextToSpeech()
            with pytest.raises((VoiceNotAvailableError, ImportError)):
                tts.load()

    def test_accepts_tts_config(self):
        cfg = TTSConfig(engine="piper", voice="es_ES-carlfm-x_low", speed=1.2)
        tts = PiperTextToSpeech(cfg)
        assert tts.config.voice == "es_ES-carlfm-x_low"
        assert tts.config.speed == 1.2

    def test_stop_sets_interrupt(self):
        tts = PiperTextToSpeech()
        tts.stop()
        assert tts._interrupt.is_set()
