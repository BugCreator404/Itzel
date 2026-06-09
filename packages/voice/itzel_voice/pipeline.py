"""
Pipeline de voz — orquestador principal.

Conecta micrófono → VAD → Whisper con un modelo de concurrencia
basado en colas thread-safe. Nunca bloquea el hilo del llamante.

Modelo de concurrencia:
    ┌──────────────────────────────────────────────────────────────┐
    │ sounddevice callback (C thread, PortAudio)                   │
    │   └─ put_nowait(chunk) ──→ audio_queue: SimpleQueue          │
    │                                                              │
    │ VAD Worker Thread (daemon)                                   │
    │   └─ get(chunk) ←── audio_queue                             │
    │   └─ vad.process_chunk(chunk)                                │
    │   └─ on_speech_end: put(audio) ──→ speech_queue: Queue       │
    │                                                              │
    │ Whisper Worker Thread (daemon)                               │
    │   └─ get(audio) ←── speech_queue                            │
    │   └─ stt.transcribe(audio) → resultado                       │
    │   └─ on_transcript(text, language)                           │
    └──────────────────────────────────────────────────────────────┘

Modos:
    "always"  → escucha continua, VAD activo
    "hotkey"  → push-to-talk, audio solo cuando PTT activo
    "keyword" → por implementar (placeholder)

Uso:
    pipeline = VoicePipeline(PipelineConfig(mode="always"))
    pipeline.on_transcript = lambda text, lang: print(f"[{lang}] {text}")
    pipeline.start()
    ...
    pipeline.stop()
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional

import numpy as np

from .stt import STTConfig, SpeechToText
from .vad import CHUNK_SAMPLES, SAMPLE_RATE, VADConfig, VoiceActivityDetector

log = logging.getLogger("itzel.voice.pipeline")


# ─── estado del pipeline ──────────────────────────────────────────────────────

class PipelineState(Enum):
    STOPPED    = auto()   # sin iniciar / detenido
    IDLE       = auto()   # escuchando, sin voz detectada
    LISTENING  = auto()   # VAD detectó voz, acumulando audio
    PROCESSING = auto()   # Whisper transcribiendo
    SPEAKING   = auto()   # TTS reproduciendo respuesta


# ─── configuración ────────────────────────────────────────────────────────────

@dataclass
class PipelineConfig:
    """Configuración del pipeline de voz completo."""
    mode:         str            = "always"     # "always" | "hotkey" | "keyword"
    vad:          VADConfig      = field(default_factory=VADConfig)
    stt:          STTConfig      = field(default_factory=STTConfig)
    device_index: Optional[int]  = None         # None = micrófono por defecto
    # Cola máxima: si Whisper va muy lento, descarta utterances antiguas
    max_queue:    int            = 3


# ─── pipeline ─────────────────────────────────────────────────────────────────

class VoicePipeline:
    """
    Pipeline completo: micrófono → VAD → Whisper → texto.

    Callbacks públicos (asignar antes de start()):
        on_transcript(text, language)   → texto reconocido + idioma ("es"/"en")
        on_listening_start()            → VAD detectó inicio de voz
        on_listening_end()              → VAD detectó fin de voz
        on_level(rms)                   → nivel de volumen del mic (0.0-1.0)
        on_error(exc)                   → error en el pipeline
        on_state_change(state_str)      → cambio de estado para la UI
        on_speaking_start()             → TTS comenzó a reproducir
        on_speaking_end()               → TTS terminó de reproducir
        on_speaking_level(rms)          → nivel de volumen del TTS (animación)
        on_interrupted()                → TTS fue interrumpido por el usuario

    TTS (opcional):
        pipeline.tts = TextToSpeech()   → asignar antes de start()
        Si tts es None, el pipeline funciona en modo solo-texto (sin voz de salida).
    """

    def __init__(self, config: Optional[PipelineConfig] = None) -> None:
        self.config = config or PipelineConfig()
        self.vad    = VoiceActivityDetector(self.config.vad)
        self.stt    = SpeechToText(self.config.stt)
        self.tts    = None   # TextToSpeech | PiperTextToSpeech — opcional

        # Callbacks públicos — voz de entrada
        self.on_transcript:    Optional[Callable[[str, str], None]]  = None
        self.on_listening_start: Optional[Callable[[], None]]        = None
        self.on_listening_end:   Optional[Callable[[], None]]        = None
        self.on_level:           Optional[Callable[[float], None]]   = None
        self.on_error:           Optional[Callable[[Exception], None]]= None
        self.on_state_change:    Optional[Callable[[str], None]]     = None

        # Callbacks públicos — voz de salida (TTS)
        self.on_speaking_start: Optional[Callable[[], None]]        = None
        self.on_speaking_end:   Optional[Callable[[], None]]        = None
        self.on_speaking_level: Optional[Callable[[float], None]]   = None
        self.on_interrupted:    Optional[Callable[[], None]]        = None

        # Estado interno
        self._state    = PipelineState.STOPPED
        self._running  = False
        self._ptt_active = False        # solo relevante en modo "hotkey"

        # Colas inter-hilo
        self._audio_queue:  queue.SimpleQueue = queue.SimpleQueue()
        self._speech_queue: queue.Queue       = queue.Queue(maxsize=self.config.max_queue)

        # Hilos worker
        self._vad_thread: Optional[threading.Thread] = None
        self._stt_thread: Optional[threading.Thread] = None
        self._stream     = None          # sounddevice.InputStream

    # ── ciclo de vida ─────────────────────────────────────────────────────────

    def start(self, *, use_mic: bool = True) -> None:
        """
        Carga modelos (si no están cargados) e inicia el pipeline.

        Args:
            use_mic: True (default) abre el stream de sounddevice.
                     False omite el micrófono — el audio se inyecta
                     externamente vía _audio_queue (p. ej. WebSocket).

        Bloquea brevemente mientras carga Silero VAD.
        Whisper se carga en el primer uso para no bloquear start().

        Raises:
            VoiceNotAvailableError: si faltan dependencias.
            RuntimeError: si el pipeline ya está corriendo.
        """
        if self._running:
            raise RuntimeError("El pipeline ya está corriendo. Llama stop() primero.")

        # Validar modo
        if self.config.mode not in ("always", "hotkey", "keyword"):
            raise ValueError(
                f"Modo '{self.config.mode}' no reconocido. "
                "Opciones: always | hotkey | keyword"
            )

        if self.config.mode == "keyword":
            log.warning(
                "Modo 'keyword' aún no está implementado. "
                "Usando 'always' como fallback."
            )
            self.config = PipelineConfig(**{**self.config.__dict__, "mode": "always"})

        # Cargar VAD (rápido, ~100ms)
        if not self.vad.is_loaded:
            log.info("Cargando Silero VAD…")
            self.vad.load()

        # Registrar callbacks del VAD
        self.vad.on_speech_start = self._on_vad_speech_start
        self.vad.on_speech_end   = self._on_vad_speech_end
        self.vad.on_level_change = self._on_level

        # Conectar callbacks de TTS si hay motor configurado
        if self.tts is not None:
            self._setup_tts_callbacks()

        self._running = True
        self._set_state(PipelineState.IDLE)

        # Iniciar hilos worker
        self._vad_thread = threading.Thread(
            target=self._vad_worker, name="itzel-vad", daemon=True
        )
        self._stt_thread = threading.Thread(
            target=self._stt_worker, name="itzel-stt", daemon=True
        )
        self._vad_thread.start()
        self._stt_thread.start()

        if use_mic:
            self._open_stream()

        log.info(
            "Pipeline de voz iniciado — modo: %s, fuente: %s",
            self.config.mode,
            f"mic:{self.config.device_index or 'default'}" if use_mic else "cliente",
        )

    def stop(self) -> None:
        """
        Detiene el pipeline y libera todos los recursos.

        Espera a que Whisper termine la utterance en progreso (max 10s).
        """
        if not self._running:
            return

        # Interrumpir TTS antes de cerrar el stream de audio
        if self.tts is not None and self._state is PipelineState.SPEAKING:
            self.tts.stop()

        self._running = False

        # 1. Cerrar stream (para el callback de sounddevice)
        self._close_stream()

        # 2. Forzar fin del enunciado actual en VAD
        self.vad.force_end()

        # 3. Esperar VAD worker
        if self._vad_thread and self._vad_thread.is_alive():
            self._vad_thread.join(timeout=2.0)

        # 4. Enviar sentinel al STT worker y esperar
        try:
            self._speech_queue.put_nowait(None)
        except queue.Full:
            pass
        if self._stt_thread and self._stt_thread.is_alive():
            self._stt_thread.join(timeout=10.0)

        self.vad.reset()
        self._set_state(PipelineState.STOPPED)
        log.info("Pipeline de voz detenido.")

    # ── push-to-talk ──────────────────────────────────────────────────────────

    def push_to_talk_start(self) -> None:
        """
        Inicia grabación push-to-talk (llamar al presionar la tecla).
        Solo tiene efecto en modo "hotkey".
        """
        if not self._running:
            self.start()

        self._ptt_active = True
        self.vad.reset()   # ignorar audio anterior al press
        if self.on_listening_start:
            threading.Thread(target=self.on_listening_start, daemon=True).start()

    def push_to_talk_stop(self) -> None:
        """
        Termina grabación push-to-talk (llamar al soltar la tecla).
        Fuerza el cierre del enunciado aunque no haya silencio.
        """
        if self._ptt_active:
            self._ptt_active = False
            self.vad.force_end()

    # ── integración asyncio (para FastAPI/WebSocket) ──────────────────────────

    def set_async_transcript_handler(
        self,
        loop: asyncio.AbstractEventLoop,
        coro_factory: Callable[[str, str], Coroutine[Any, Any, None]],
    ) -> None:
        """
        Conecta el pipeline a un event loop de asyncio.

        Permite usar el pipeline desde FastAPI (que corre en asyncio)
        sin bloquear el event loop — usa run_coroutine_threadsafe().

        Args:
            loop:          el event loop de FastAPI
            coro_factory:  función async(text, language) → coroutine
        """
        def _bridge(text: str, language: str) -> None:
            future = asyncio.run_coroutine_threadsafe(
                coro_factory(text, language), loop
            )
            try:
                future.result(timeout=5.0)
            except Exception as exc:
                log.error("Error en bridge asyncio: %s", exc)

        self.on_transcript = _bridge

    # ── propiedades ───────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def state(self) -> str:
        """Estado actual: 'stopped' | 'idle' | 'listening' | 'processing' | 'speaking'."""
        return self._state.name.lower()

    # ── configuración del TTS ─────────────────────────────────────────────────

    def _setup_tts_callbacks(self) -> None:
        """Conecta los callbacks internos del TTS al pipeline."""
        self.tts.on_start       = self._on_tts_start
        self.tts.on_end         = self._on_tts_end
        self.tts.on_level       = self._on_tts_level
        self.tts.on_interrupted = self._on_tts_interrupted

    def _on_tts_start(self) -> None:
        """TTS comenzó a reproducir — pasar a estado SPEAKING."""
        self._set_state(PipelineState.SPEAKING)
        if self.on_speaking_start:
            self.on_speaking_start()

    def _on_tts_end(self) -> None:
        """TTS terminó de reproducir — volver a IDLE solo si nadie cambió el estado."""
        if self._state is PipelineState.SPEAKING:
            self._set_state(PipelineState.IDLE)
        if self.on_speaking_end:
            self.on_speaking_end()

    def _on_tts_level(self, rms: float) -> None:
        """Nivel de volumen del TTS para animación de la mascota."""
        if self.on_speaking_level:
            self.on_speaking_level(rms)

    def _on_tts_interrupted(self) -> None:
        """TTS fue interrumpido (por voz del usuario o stop())."""
        if self.on_interrupted:
            threading.Thread(
                target=self.on_interrupted, daemon=True
            ).start()

    # ── callbacks internos del VAD ────────────────────────────────────────────

    def _on_vad_speech_start(self) -> None:
        """VAD detectó inicio de voz. Si Itzel estaba hablando, la interrumpe."""
        if self.tts is not None and self._state is PipelineState.SPEAKING:
            self.tts.stop()

        self._set_state(PipelineState.LISTENING)
        if self.on_listening_start:
            self.on_listening_start()

    def _on_vad_speech_end(self, audio: np.ndarray) -> None:
        """VAD terminó de capturar un enunciado — encolar para Whisper."""
        self._set_state(PipelineState.PROCESSING)
        if self.on_listening_end:
            self.on_listening_end()

        try:
            # Si la cola está llena, descartamos el más antiguo (política LIFO suave)
            if self._speech_queue.full():
                try:
                    self._speech_queue.get_nowait()
                    log.warning("Speech queue llena — descartando utterance antigua.")
                except queue.Empty:
                    pass
            self._speech_queue.put_nowait(audio)
        except Exception as exc:
            log.error("Error al encolar audio para STT: %s", exc)

    def _on_level(self, rms: float) -> None:
        """Nivel de volumen del micrófono."""
        if self.on_level:
            self.on_level(rms)

    # ── worker threads ────────────────────────────────────────────────────────

    def _vad_worker(self) -> None:
        """
        Consume chunks del audio_queue y los pasa al VAD.
        Corre como daemon thread — muere con el proceso principal.
        """
        log.debug("VAD worker thread iniciado.")
        while self._running:
            try:
                chunk = self._audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            try:
                self.vad.process_chunk(chunk)
            except Exception as exc:
                log.error("Error en VAD: %s", exc)
                if self.on_error:
                    threading.Thread(
                        target=self.on_error, args=(exc,), daemon=True
                    ).start()

        log.debug("VAD worker thread terminado.")

    def _stt_worker(self) -> None:
        """
        Consume utterances del speech_queue y llama a Whisper.
        Puede bloquearse por 1-3s durante la transcripción — es intencional.
        """
        log.debug("STT worker thread iniciado.")

        # Carga diferida del modelo Whisper en el primer uso
        model_loaded = False

        while True:
            try:
                audio = self._speech_queue.get(timeout=0.1)
            except queue.Empty:
                if not self._running:
                    break
                continue

            # Sentinel de parada
            if audio is None:
                break

            # Cargar modelo la primera vez (puede tardar 2-5s)
            if not model_loaded:
                try:
                    if not self.stt.is_loaded:
                        log.info(
                            "Cargando faster-whisper '%s'… (primera vez ~5s)",
                            self.config.stt.model_size,
                        )
                        self.stt.load()
                    model_loaded = True
                except Exception as exc:
                    log.error("No se pudo cargar el modelo STT: %s", exc)
                    if self.on_error:
                        self.on_error(exc)
                    self._speech_queue.task_done()
                    continue

            # Transcribir
            try:
                result = self.stt.transcribe(audio)
                if result.text:
                    if self.on_transcript:
                        self.on_transcript(result.text, result.language)

                    # TTS: Itzel responde en voz — bloqueante en este hilo.
                    # El guard _state PROCESSING→IDLE en finally NO ejecuta
                    # porque _on_tts_start lo cambia a SPEAKING antes de retornar.
                    if self.tts is not None:
                        try:
                            self.tts.speak(result.text)
                        except Exception as exc:
                            log.error("Error en TTS al hablar: %s", exc)

                log.debug(
                    "Transcripción [%s, %.1fs]: %s",
                    result.language, result.duration_s, result.text[:60],
                )
            except Exception as exc:
                log.error("Error en STT: %s", exc)
                if self.on_error:
                    threading.Thread(
                        target=self.on_error, args=(exc,), daemon=True
                    ).start()
            finally:
                self._speech_queue.task_done()
                # Solo volver a IDLE si nadie más cambió el estado
                # (p.ej. TTS ya lo llevó a SPEAKING→IDLE, o VAD lo llevó a LISTENING)
                if self._state is PipelineState.PROCESSING:
                    self._set_state(PipelineState.IDLE)

        log.debug("STT worker thread terminado.")

    # ── callback de sounddevice ───────────────────────────────────────────────

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: object,
    ) -> None:
        """
        Callback de PortAudio — corre en hilo C, nunca puede bloquear.

        indata shape: (blocksize, channels), dtype: float32
        """
        if status:
            log.warning("sounddevice status: %s", status)

        # En modo hotkey, ignorar audio cuando PTT no está activo
        if self.config.mode == "hotkey" and not self._ptt_active:
            return

        # Tomar canal mono y copiar (indata es una vista temporal)
        chunk = indata[:, 0].copy()
        self._audio_queue.put_nowait(chunk)

    # ── apertura / cierre del stream ──────────────────────────────────────────

    def _open_stream(self) -> None:
        """Abre el stream de sounddevice."""
        try:
            import sounddevice as sd
        except ImportError as exc:
            from . import VoiceNotAvailableError
            raise VoiceNotAvailableError(
                missing="sounddevice",
                install_cmd="pip install sounddevice",
            ) from exc

        self._stream = sd.InputStream(
            samplerate   = SAMPLE_RATE,
            blocksize    = CHUNK_SAMPLES,
            dtype        = "float32",
            channels     = 1,
            device       = self.config.device_index,
            callback     = self._audio_callback,
        )
        self._stream.start()

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                log.warning("Error cerrando stream de audio: %s", exc)
            self._stream = None

    # ── helpers de estado ──────────────────────────────────────────────────────

    def _set_state(self, new_state: PipelineState) -> None:
        if self._state is not new_state:
            self._state = new_state
            if self.on_state_change:
                threading.Thread(
                    target=self.on_state_change,
                    args=(new_state.name.lower(),),
                    daemon=True,
                ).start()


# ─── utilidades públicas ──────────────────────────────────────────────────────

def list_input_devices() -> list[dict]:
    """
    Lista los dispositivos de entrada de audio disponibles.

    Returns:
        Lista de dicts con keys: index, name, channels, sample_rate.
    """
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        return [
            {
                "index":       i,
                "name":        d["name"],
                "channels":    d["max_input_channels"],
                "sample_rate": int(d["default_samplerate"]),
                "is_default":  (
                    i == sd.default.device[0] if isinstance(sd.default.device, (list, tuple))
                    else i == sd.default.device
                ),
            }
            for i, d in enumerate(devices)
            if d["max_input_channels"] > 0
        ]
    except ImportError:
        return []
