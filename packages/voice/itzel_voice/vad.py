"""
Voice Activity Detection — Silero VAD con backend ONNX.

Detecta cuándo hay voz humana real en el stream de audio del micrófono.
Filtra silencio y ruido antes de enviar audio a Whisper, reduciendo
llamadas al STT en ~80%.

Arquitectura interna:
    ┌─ Chunk 32ms ─────────────────────────────────────────────────┐
    │  sounddevice (C thread) → process_chunk() → máquina de estados│
    │                                                                │
    │  IDLE ──[p≥umbral]──→ SPEECH ──[silencio≥700ms]──→ IDLE      │
    │                          │                                     │
    │                     acumula audio                              │
    │                          │                                     │
    │                    on_speech_end(audio_np)                     │
    └────────────────────────────────────────────────────────────────┘

Uso mínimo:
    vad = VoiceActivityDetector()
    vad.load()
    vad.on_speech_end = lambda audio: print(f"Captué {len(audio)/16000:.1f}s")
    # En callback de sounddevice:
    vad.process_chunk(chunk)
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Optional

import numpy as np

# ─── constantes ───────────────────────────────────────────────────────────────

SAMPLE_RATE   = 16_000
CHUNK_SAMPLES = 512          # 32 ms @ 16 kHz — mínimo aceptado por Silero VAD
CHUNK_MS      = 32           # ms por chunk

# Chunks de pre-relleno para no perder el inicio del habla (~320 ms)
_PRE_SPEECH_CHUNKS = 10


# ─── configuración ────────────────────────────────────────────────────────────

@dataclass
class VADConfig:
    """Parámetros ajustables del detector de actividad vocal."""
    threshold:           float = 0.5    # confianza mínima para considerar voz
    min_speech_ms:       int   = 250    # descartar sonidos < 250 ms (tos, click)
    silence_trigger_ms:  int   = 700    # silencio que cierra el enunciado
    max_speech_s:        int   = 30     # corte duro para enunciados muy largos
    sample_rate:         int   = SAMPLE_RATE
    use_onnx:            bool  = True   # preferir onnxruntime sobre torch


# ─── estados ──────────────────────────────────────────────────────────────────

class _State(Enum):
    IDLE   = auto()   # esperando voz
    SPEECH = auto()   # acumulando audio de voz


# ─── detector ─────────────────────────────────────────────────────────────────

class VoiceActivityDetector:
    """
    Detecta actividad vocal en tiempo real con Silero VAD.

    Thread-safe: process_chunk() puede llamarse desde el callback
    de sounddevice (hilo C de PortAudio) sin bloquear.

    Callbacks disponibles:
        on_speech_start()              → mascota a modo "escucha"
        on_speech_end(audio: ndarray)  → enviar audio a Whisper
        on_level_change(rms: float)    → actualizar indicador de volumen en UI
    """

    def __init__(self, config: Optional[VADConfig] = None) -> None:
        self.config = config or VADConfig()

        self._model      = None
        self._use_onnx   = False     # se decide en load()
        self._lock       = threading.Lock()
        self._state      = _State.IDLE

        # Buffer circular pre-voz (captura el inicio del habla)
        self._pre_buf: deque[np.ndarray] = deque(maxlen=_PRE_SPEECH_CHUNKS)

        # Buffer de voz activa
        self._speech_buf: list[np.ndarray] = []
        self._speech_samples  = 0
        self._silence_samples = 0

        # Callbacks — el usuario los asigna directamente
        self.on_speech_start: Optional[Callable[[], None]]             = None
        self.on_speech_end:   Optional[Callable[[np.ndarray], None]]   = None
        self.on_level_change: Optional[Callable[[float], None]]        = None

    # ── inicialización ────────────────────────────────────────────────────────

    def load(self) -> None:
        """
        Carga el modelo Silero VAD.

        Intenta ONNX primero (sin PyTorch); cae en torch si no está onnxruntime.

        Raises:
            VoiceNotAvailableError: si silero-vad no está instalado.
        """
        try:
            from silero_vad import load_silero_vad
        except ImportError as exc:
            from . import VoiceNotAvailableError
            raise VoiceNotAvailableError(
                missing="silero-vad",
                install_cmd="pip install silero-vad onnxruntime",
            ) from exc

        # Intentar backend ONNX (liviano, sin PyTorch)
        if self.config.use_onnx:
            try:
                self._model    = load_silero_vad(onnx=True)
                self._use_onnx = True
                return
            except Exception:
                pass   # fallback a torch

        # Fallback: backend torch
        try:
            self._model    = load_silero_vad(onnx=False)
            self._use_onnx = False
        except Exception as exc:
            from . import VoiceNotAvailableError
            raise VoiceNotAvailableError(
                missing="silero-vad (torch backend)",
                install_cmd=(
                    "pip install silero-vad onnxruntime  "
                    "# ONNX recomendado\n"
                    "  # O para torch: pip install torch --index-url "
                    "https://download.pytorch.org/whl/cpu"
                ),
            ) from exc

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    # ── procesamiento de chunks ───────────────────────────────────────────────

    def process_chunk(self, chunk: np.ndarray) -> bool:
        """
        Procesa un chunk de 32 ms y actualiza el estado interno.

        Diseñado para llamarse desde el callback de sounddevice —
        nunca bloquea más de ~1 ms.

        Args:
            chunk: array float32 de longitud CHUNK_SAMPLES (512 muestras).
                   Si llega con tamaño diferente se normaliza automáticamente.

        Returns:
            True si este chunk contiene voz activa.

        Raises:
            RuntimeError: si load() no se llamó antes.
        """
        if self._model is None:
            raise RuntimeError(
                "VoiceActivityDetector no inicializado — llama load() primero."
            )

        chunk = _normalize_chunk(chunk)

        # Notificar nivel de volumen (RMS) para la UI
        if self.on_level_change is not None:
            rms = float(np.sqrt(np.mean(chunk ** 2)))
            self.on_level_change(rms)

        # Calcular probabilidad de voz
        prob     = self._infer(chunk)
        is_voice = prob >= self.config.threshold

        with self._lock:
            self._tick(chunk, is_voice)

        return is_voice

    # ── máquina de estados (protegida por _lock) ──────────────────────────────

    def _tick(self, chunk: np.ndarray, is_voice: bool) -> None:
        """Actualiza el estado. Llamar solo con _lock adquirido."""
        if self._state is _State.IDLE:
            self._pre_buf.append(chunk)
            if is_voice:
                # Transición IDLE → SPEECH
                # Incluir el buffer pre-voz para no perder el inicio
                self._speech_buf = list(self._pre_buf)
                self._speech_samples  = len(self._speech_buf) * CHUNK_SAMPLES
                self._silence_samples = 0
                self._state = _State.SPEECH
                self._pre_buf.clear()
                if self.on_speech_start is not None:
                    threading.Thread(
                        target=self.on_speech_start, daemon=True
                    ).start()

        elif self._state is _State.SPEECH:
            self._speech_buf.append(chunk)
            self._speech_samples += CHUNK_SAMPLES

            if is_voice:
                self._silence_samples = 0
            else:
                self._silence_samples += CHUNK_SAMPLES

            silence_ms = _samples_to_ms(
                self._silence_samples, self.config.sample_rate
            )
            speech_s   = self._speech_samples / self.config.sample_rate

            if (
                silence_ms >= self.config.silence_trigger_ms
                or speech_s >= self.config.max_speech_s
            ):
                self._flush()

    def _flush(self) -> None:
        """Emite on_speech_end con el audio acumulado y resetea. Llamar con _lock."""
        if not self._speech_buf:
            self._reset_state()
            return

        audio = np.concatenate(self._speech_buf).astype(np.float32)
        self._reset_state()

        # Descartar enunciados muy cortos (tos, ruido puntual)
        duration_ms = len(audio) / self.config.sample_rate * 1000
        if duration_ms < self.config.min_speech_ms:
            return

        if self.on_speech_end is not None:
            # Copiar audio para no retener el buffer en memoria
            audio_copy = audio.copy()
            threading.Thread(
                target=self.on_speech_end,
                args=(audio_copy,),
                daemon=True,
            ).start()

    def _reset_state(self) -> None:
        """Vuelve a IDLE y limpia buffers. Llamar con _lock."""
        self._state           = _State.IDLE
        self._speech_buf      = []
        self._speech_samples  = 0
        self._silence_samples = 0
        if self._model is not None:
            try:
                self._model.reset_states()
            except AttributeError:
                pass   # algunos backends no exponen reset_states()

    # ── API pública de control ────────────────────────────────────────────────

    def force_end(self) -> None:
        """
        Fuerza el cierre del enunciado actual aunque no haya silencio.
        Útil en modo push-to-talk al soltar la tecla.
        """
        with self._lock:
            if self._state is _State.SPEECH:
                self._flush()

    def reset(self) -> None:
        """Cancela la captura actual y vuelve a IDLE (descarta el audio)."""
        with self._lock:
            self._reset_state()
            self._pre_buf.clear()

    @property
    def state(self) -> str:
        """Estado actual como string para la UI: 'idle' | 'speech'."""
        return self._state.name.lower()

    # ── inferencia ────────────────────────────────────────────────────────────

    def _infer(self, chunk: np.ndarray) -> float:
        """
        Llama al modelo Silero VAD y devuelve la probabilidad de voz (0-1).

        Maneja la diferencia de API entre backend ONNX (numpy) y torch.
        """
        if self._use_onnx:
            # ONNX: acepta numpy float32, shape (1, n) o (n,)
            return float(
                self._model(chunk.reshape(1, -1), self.config.sample_rate)
            )
        else:
            # Torch: necesita FloatTensor
            import torch
            tensor = torch.from_numpy(chunk).unsqueeze(0)
            return float(self._model(tensor, self.config.sample_rate))


# ─── utilidades ───────────────────────────────────────────────────────────────

def _normalize_chunk(chunk: np.ndarray) -> np.ndarray:
    """
    Asegura que el chunk tenga exactamente CHUNK_SAMPLES muestras float32.
    Silero VAD falla con tamaños distintos a 512/1024/1536.
    """
    chunk = np.asarray(chunk, dtype=np.float32).ravel()
    if len(chunk) == CHUNK_SAMPLES:
        return chunk
    if len(chunk) < CHUNK_SAMPLES:
        return np.pad(chunk, (0, CHUNK_SAMPLES - len(chunk)))
    return chunk[:CHUNK_SAMPLES]


def _samples_to_ms(samples: int, sample_rate: int) -> float:
    """Convierte número de muestras a milisegundos."""
    return samples / sample_rate * 1000.0
