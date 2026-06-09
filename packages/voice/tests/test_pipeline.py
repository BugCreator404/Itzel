"""
Tests del VoicePipeline — orquestador completo.

Cubre:
  - Configuración y defaults
  - Ciclo de vida start/stop
  - Validación de modo
  - Transiciones de estado
  - Modo always: flujo completo mic → VAD → STT → callback
  - Modo hotkey: filtrado de audio con PTT
  - Bridge asyncio para FastAPI
  - list_input_devices()
  - Manejo de errores en STT

Usa los fixtures del conftest: full_pipeline, full_pipeline_hotkey.
Ningún test accede a hardware real.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from itzel_voice.pipeline import (
    PipelineConfig,
    PipelineState,
    VoicePipeline,
    list_input_devices,
)
from itzel_voice.stt import STTConfig
from itzel_voice.vad import VADConfig

from .conftest import make_silence, make_speech, make_chunks, CHUNK_SAMPLES, SAMPLE_RATE


# ─── helpers ──────────────────────────────────────────────────────────────────

def _wait(event: threading.Event, timeout: float = 3.0) -> bool:
    return event.wait(timeout=timeout)


def _inject_chunks(pipeline: VoicePipeline, audio: np.ndarray) -> None:
    """Inyecta audio chunk a chunk a través del callback del pipeline."""
    for chunk in make_chunks(audio):
        arr = chunk.reshape(-1, 1)   # shape (CHUNK_SAMPLES, 1) como sounddevice
        pipeline._audio_callback(arr, len(chunk), None, None)


# ─── PipelineConfig ───────────────────────────────────────────────────────────

class TestPipelineConfig:
    def test_defaults(self):
        cfg = PipelineConfig()
        assert cfg.mode         == "always"
        assert cfg.device_index is None
        assert cfg.max_queue    == 3
        assert isinstance(cfg.vad, VADConfig)
        assert isinstance(cfg.stt, STTConfig)

    def test_custom_mode(self):
        cfg = PipelineConfig(mode="hotkey")
        assert cfg.mode == "hotkey"

    def test_custom_vad_config(self):
        vad_cfg = VADConfig(threshold=0.8)
        cfg     = PipelineConfig(vad=vad_cfg)
        assert cfg.vad.threshold == 0.8


# ─── PipelineState ────────────────────────────────────────────────────────────

class TestPipelineStateEnum:
    def test_all_states_defined(self):
        states = {s.name for s in PipelineState}
        assert {"STOPPED", "IDLE", "LISTENING", "PROCESSING"} <= states


# ─── ciclo de vida ────────────────────────────────────────────────────────────

class TestPipelineLifecycle:
    def test_initial_state_stopped(self, full_pipeline):
        assert full_pipeline.state      == "stopped"
        assert full_pipeline.is_running is False

    def test_start_sets_idle(self, full_pipeline):
        full_pipeline.start()
        assert full_pipeline.state      == "idle"
        assert full_pipeline.is_running is True

    def test_stop_returns_to_stopped(self, full_pipeline):
        full_pipeline.start()
        full_pipeline.stop()
        assert full_pipeline.state      == "stopped"
        assert full_pipeline.is_running is False

    def test_start_when_already_running_raises(self, full_pipeline):
        full_pipeline.start()
        with pytest.raises(RuntimeError, match="ya está corriendo"):
            full_pipeline.start()

    def test_stop_when_not_running_is_safe(self, full_pipeline):
        """Llamar stop() sin start() no lanza excepción."""
        full_pipeline.stop()   # no debe lanzar

    def test_stop_twice_is_safe(self, full_pipeline):
        full_pipeline.start()
        full_pipeline.stop()
        full_pipeline.stop()   # segunda vez — no debe lanzar


# ─── validación de modo ───────────────────────────────────────────────────────

class TestPipelineMode:
    def test_invalid_mode_raises(self, loaded_vad, loaded_stt, mock_sounddevice):
        sys.modules["sounddevice"] = mock_sounddevice
        try:
            cfg      = PipelineConfig(mode="invalid")
            pipeline = VoicePipeline(cfg)
            pipeline.vad = loaded_vad
            pipeline.stt = loaded_stt
            with pytest.raises(ValueError, match="no reconocido"):
                pipeline.start()
        finally:
            sys.modules.pop("sounddevice", None)

    def test_keyword_mode_falls_back_to_always(
        self, loaded_vad, loaded_stt, mock_sounddevice
    ):
        """Modo 'keyword' no implementado → fallback a 'always' con warning."""
        sys.modules["sounddevice"] = mock_sounddevice
        try:
            cfg      = PipelineConfig(mode="keyword")
            pipeline = VoicePipeline(cfg)
            pipeline.vad = loaded_vad
            pipeline.stt = loaded_stt
            with pytest.warns(None):   # puede emitir un warning de log
                pipeline.start()
            # Debe arrancar en modo always (no lanza)
            assert pipeline.is_running is True
            pipeline.stop()
        finally:
            sys.modules.pop("sounddevice", None)


# ─── transiciones de estado ───────────────────────────────────────────────────

class TestPipelineStateTransitions:
    def test_on_state_change_fired_on_start(self, full_pipeline):
        states: list[str] = []
        full_pipeline.on_state_change = lambda s: states.append(s)
        full_pipeline.start()
        time.sleep(0.05)
        assert "idle" in states

    def test_state_becomes_listening_on_speech(self, full_pipeline):
        listening_event = threading.Event()

        def _on_state(s: str) -> None:
            if s == "listening":
                listening_event.set()

        full_pipeline.on_state_change = _on_state
        full_pipeline.start()

        # Inyectar voz (mock devuelve 0.9 → confianza alta)
        _inject_chunks(full_pipeline, make_speech(0.1))
        assert _wait(listening_event), "Estado 'listening' no alcanzado"

    def test_state_becomes_processing_after_force_end(self, full_pipeline):
        processing_event = threading.Event()

        def _on_state(s: str) -> None:
            if s == "processing":
                processing_event.set()

        full_pipeline.on_state_change = _on_state
        full_pipeline.start()

        # Activar voz
        _inject_chunks(full_pipeline, make_speech(0.1))
        time.sleep(0.05)

        # Forzar fin → estado processing
        full_pipeline.vad.force_end()
        assert _wait(processing_event), "Estado 'processing' no alcanzado"


# ─── flujo completo (always) ──────────────────────────────────────────────────

class TestPipelineFullFlow:
    def test_on_transcript_called_after_speech(self, full_pipeline):
        """
        Flujo completo: audio inyectado → VAD detecta voz → Whisper transcribe
        → on_transcript disparado con texto.
        """
        transcribed = threading.Event()
        received: list[tuple[str, str]] = []

        def _on_transcript(text: str, lang: str) -> None:
            received.append((text, lang))
            transcribed.set()

        full_pipeline.on_transcript = _on_transcript
        full_pipeline.start()

        # 1. Inyectar voz para activar VAD
        _inject_chunks(full_pipeline, make_speech(0.2))
        time.sleep(0.05)

        # 2. Forzar fin del enunciado (en lugar de esperar 700ms de silencio)
        full_pipeline.vad.force_end()

        assert _wait(transcribed, timeout=5.0), "on_transcript no se disparó"
        assert len(received) == 1
        text, lang = received[0]
        assert isinstance(text, str) and text != ""
        assert lang in ("es", "en")

    def test_on_transcript_not_called_without_speech(self, full_pipeline):
        """Sin voz inyectada, on_transcript no se llama."""
        called = threading.Event()
        full_pipeline.on_transcript = lambda t, l: called.set()
        full_pipeline.start()
        # No inyectamos audio
        assert not called.wait(timeout=0.3), "on_transcript se llamó sin voz"

    def test_on_listening_start_fires(self, full_pipeline):
        fired = threading.Event()
        full_pipeline.on_listening_start = lambda: fired.set()
        full_pipeline.start()

        _inject_chunks(full_pipeline, make_speech(0.1))
        assert _wait(fired), "on_listening_start no se disparó"

    def test_on_listening_end_fires_after_force_end(self, full_pipeline):
        ended = threading.Event()
        full_pipeline.on_listening_end = lambda: ended.set()
        full_pipeline.start()

        _inject_chunks(full_pipeline, make_speech(0.1))
        time.sleep(0.05)
        full_pipeline.vad.force_end()
        assert _wait(ended), "on_listening_end no se disparó"

    def test_on_level_fires_per_chunk(self, full_pipeline):
        levels: list[float] = []
        full_pipeline.on_level = lambda rms: levels.append(rms)
        full_pipeline.start()

        _inject_chunks(full_pipeline, make_silence(0.2))
        time.sleep(0.1)

        assert len(levels) > 0
        assert all(isinstance(l, float) for l in levels)

    def test_on_error_fired_on_stt_exception(self, full_pipeline):
        """Si Whisper lanza excepción, on_error se llama."""
        error_event = threading.Event()
        errors: list[Exception] = []

        full_pipeline.on_error = lambda e: (errors.append(e), error_event.set())
        full_pipeline.stt._model.transcribe.side_effect = RuntimeError("STT error simulado")

        full_pipeline.start()
        _inject_chunks(full_pipeline, make_speech(0.1))
        time.sleep(0.05)
        full_pipeline.vad.force_end()

        assert _wait(error_event, timeout=5.0), "on_error no se disparó"
        assert isinstance(errors[0], RuntimeError)


# ─── modo hotkey (push-to-talk) ───────────────────────────────────────────────

class TestPipelineHotkeyMode:
    def test_audio_filtered_when_ptt_inactive(self, full_pipeline_hotkey):
        """En modo hotkey, audio sin PTT activo no llega al VAD."""
        listening_event = threading.Event()
        full_pipeline_hotkey.on_listening_start = lambda: listening_event.set()
        full_pipeline_hotkey.start()

        # Inyectar voz SIN activar PTT
        _inject_chunks(full_pipeline_hotkey, make_speech(0.5))
        assert not listening_event.wait(timeout=0.3), (
            "VAD procesó audio cuando PTT estaba inactivo"
        )

    def test_ptt_start_enables_audio(self, full_pipeline_hotkey):
        """Con PTT activo, el audio llega al VAD."""
        listening_event = threading.Event()
        full_pipeline_hotkey.on_listening_start = lambda: listening_event.set()
        full_pipeline_hotkey.start()

        full_pipeline_hotkey.push_to_talk_start()
        _inject_chunks(full_pipeline_hotkey, make_speech(0.1))

        assert _wait(listening_event), "VAD no procesó audio con PTT activo"

    def test_ptt_stop_forces_end(self, full_pipeline_hotkey):
        """Soltar PTT cierra el enunciado via force_end()."""
        transcribed = threading.Event()
        full_pipeline_hotkey.on_transcript = lambda t, l: transcribed.set()

        full_pipeline_hotkey.start()
        full_pipeline_hotkey.push_to_talk_start()
        _inject_chunks(full_pipeline_hotkey, make_speech(0.2))
        time.sleep(0.05)
        full_pipeline_hotkey.push_to_talk_stop()

        assert _wait(transcribed, timeout=5.0), (
            "on_transcript no se llamó después de push_to_talk_stop"
        )

    def test_ptt_inactive_after_stop(self, full_pipeline_hotkey):
        full_pipeline_hotkey.start()
        full_pipeline_hotkey.push_to_talk_start()
        assert full_pipeline_hotkey._ptt_active is True
        full_pipeline_hotkey.push_to_talk_stop()
        assert full_pipeline_hotkey._ptt_active is False


# ─── bridge asyncio ───────────────────────────────────────────────────────────

class TestAsyncBridge:
    def test_set_async_handler_sets_on_transcript(self, full_pipeline):
        """set_async_transcript_handler() registra un on_transcript."""
        loop = asyncio.new_event_loop()
        received: list[str] = []

        async def _handler(text: str, lang: str) -> None:
            received.append(text)

        full_pipeline.set_async_transcript_handler(loop, _handler)
        assert full_pipeline.on_transcript is not None

    def test_async_handler_bridge_calls_coroutine(self, full_pipeline):
        """El bridge llama a la coroutine en el event loop correcto."""
        loop     = asyncio.new_event_loop()
        done     = threading.Event()
        received: list[str] = []

        async def _handler(text: str, lang: str) -> None:
            received.append(text)
            done.set()

        full_pipeline.set_async_transcript_handler(loop, _handler)

        # Ejecutar loop en hilo separado
        def _run_loop():
            loop.run_forever()

        t = threading.Thread(target=_run_loop, daemon=True)
        t.start()

        # Disparar on_transcript directamente
        full_pipeline.on_transcript("hola", "es")

        assert done.wait(timeout=2.0), "Coroutine async no fue llamada"
        assert "hola" in received

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=1.0)
        loop.close()


# ─── list_input_devices ───────────────────────────────────────────────────────

class TestListInputDevices:
    def test_returns_list(self, mock_sounddevice):
        sys.modules["sounddevice"] = mock_sounddevice
        try:
            devices = list_input_devices()
            assert isinstance(devices, list)
        finally:
            sys.modules.pop("sounddevice", None)

    def test_device_has_required_keys(self, mock_sounddevice):
        sys.modules["sounddevice"] = mock_sounddevice
        try:
            devices = list_input_devices()
            if devices:
                d = devices[0]
                assert "index"       in d
                assert "name"        in d
                assert "channels"    in d
                assert "sample_rate" in d
        finally:
            sys.modules.pop("sounddevice", None)

    def test_returns_empty_without_sounddevice(self):
        """Sin sounddevice instalado, retorna lista vacía (no lanza)."""
        # None en sys.modules fuerza ImportError sin re-importar el paquete real
        with patch.dict(sys.modules, {"sounddevice": None}):
            devices = list_input_devices()
            assert devices == []
