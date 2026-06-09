"""
Motor de síntesis de voz (TTS) — Kokoro-82M.

Genera voz en español ES-MX con baja latencia usando Kokoro-82M (ONNX).
El pipeline genera y reproduce audio frase por frase sin esperar el texto
completo: la primera sílaba llega en < 500ms en CPU.

Arquitectura de streaming:
    texto / tokens LLM
        ↓
    SentenceBuffer  (acumula hasta punto o fin de oración)
        ↓ frase completa
    KPipeline.generate()  (Kokoro genera chunks de audio progresivamente)
        ↓ numpy float32 @ 24kHz
    sd.OutputStream.write()  (50ms sub-chunks con verificación de interrupción)

Interrupción:
    _interrupt.set()  → abandona el sub-chunk actual (≤ 50ms latencia)
    stream.abort()    → descarta el buffer de PortAudio al instante
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger("itzel.voice.tts")

# ─── constantes ───────────────────────────────────────────────────────────────

_MODELS_DIR = Path.home() / ".itzel" / "models" / "tts"
_SUB_CHUNK_SAMPLES = 1_200   # 50ms @ 24kHz — granularidad de interrupción
_SENTENCE_END_RE   = re.compile(r'(?<=[.!?;:\n])\s+|(?<=\n\n)')
_SENTENCE_END_CHAR = re.compile(r'[.!?;:\n]\s*$')


# ─── helpers de texto ─────────────────────────────────────────────────────────

def _split_sentences(text: str) -> list[str]:
    """
    Divide un texto en frases para síntesis fragmentada.
    Conserva la puntuación final de cada frase.
    """
    parts = _SENTENCE_END_RE.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


# ─── SentenceBuffer ───────────────────────────────────────────────────────────

class SentenceBuffer:
    """
    Acumula tokens del LLM y emite frases completas para el TTS.

    Una frase se emite cuando:
      - Contiene ≥ min_chars caracteres Y
      - Termina en . ! ? ; : o salto de línea

    Uso:
        buf = SentenceBuffer()
        for token in llm_stream():
            sentence = buf.push(token)
            if sentence:
                tts.speak(sentence)
        remainder = buf.flush()   # emite lo que quede al final
        if remainder:
            tts.speak(remainder)
    """

    def __init__(self, min_chars: int = 20) -> None:
        self._buf: list[str] = []
        self._min_chars = min_chars

    def push(self, token: str) -> Optional[str]:
        """
        Añade un token al buffer.
        Devuelve una frase lista para sintetizar si está completa, None si no.
        """
        self._buf.append(token)
        text = "".join(self._buf)
        if len(text) >= self._min_chars and _SENTENCE_END_CHAR.search(text):
            self._buf.clear()
            return text.strip()
        return None

    def flush(self) -> Optional[str]:
        """Fuerza la emisión del buffer restante al final del stream del LLM."""
        if self._buf:
            text = "".join(self._buf).strip()
            self._buf.clear()
            return text or None
        return None

    def clear(self) -> None:
        """Descarta el buffer sin emitir (en caso de interrupción)."""
        self._buf.clear()

    @property
    def buffered(self) -> str:
        """Texto actualmente en el buffer (para debugging)."""
        return "".join(self._buf)


# ─── configuración ────────────────────────────────────────────────────────────

@dataclass
class TTSConfig:
    """Configuración del motor TextToSpeech."""

    engine:      str   = "kokoro"    # "kokoro" | "piper"
    voice:       str   = "ef_dora"   # Kokoro: ef_dora (ES-MX femenino)
    lang_code:   str   = "e"         # Kokoro lang_code: 'e' = español
    speed:       float = 1.0         # Velocidad: 0.5 (lento) – 2.0 (rápido)
    volume:      float = 1.0         # Amplitud: 0.0 – 1.0
    sample_rate: int   = 24_000      # Hz — frecuencia nativa de Kokoro
    models_dir:  Path  = field(
        default_factory=lambda: _MODELS_DIR
    )


# ─── motor TTS ────────────────────────────────────────────────────────────────

class TextToSpeech:
    """
    Motor TTS basado en Kokoro-82M (ONNX).

    Características:
      - Voz española ES-MX de alta calidad (ef_dora)
      - Streaming frase por frase: primera sílaba < 500ms
      - Interrupción < 50ms sin ruido de corte
      - Callbacks para animación de mascota (on_level) y UI

    Uso básico:
        tts = TextToSpeech()
        tts.load()
        tts.on_level = lambda rms: ui.update_mouth(rms)
        tts.speak("Hola, soy Itzel.")

    Uso streaming (desde tokens LLM):
        tts.speak_stream(llm.generate("cuéntame algo"))

    Interrupción desde otro hilo:
        tts.stop()   # detiene la reproducción en ≤ 50ms
    """

    SAMPLE_RATE = 24_000   # Hz — nativo de Kokoro

    def __init__(self, config: Optional[TTSConfig] = None) -> None:
        self.config  = config or TTSConfig()
        self._model  = None
        self._loaded = False

        # Sincronización
        self._interrupt     = threading.Event()
        self._speaking_lock = threading.Lock()   # evita speak() concurrentes

        # Callbacks públicos (asignar antes de llamar speak)
        self.on_start:       Optional[Callable[[], None]]          = None
        self.on_end:         Optional[Callable[[], None]]          = None
        self.on_sentence:    Optional[Callable[[str], None]]       = None
        self.on_level:       Optional[Callable[[float], None]]     = None
        self.on_interrupted: Optional[Callable[[], None]]          = None

    # ── carga del modelo ──────────────────────────────────────────────────────

    def load(self) -> None:
        """
        Carga el modelo Kokoro-82M.

        Si el modelo no está en caché, lo descarga (~82 MB) de HuggingFace.
        El modelo se almacena en config.models_dir/kokoro/.

        Raises:
            VoiceNotAvailableError: si kokoro no está instalado.
        """
        # Redirigir caché de HuggingFace a nuestro directorio
        hf_cache = str(self.config.models_dir / "kokoro")
        os.makedirs(hf_cache, exist_ok=True)
        os.environ.setdefault("HF_HUB_CACHE", hf_cache)

        try:
            from kokoro import KPipeline
        except ImportError as exc:
            from . import VoiceNotAvailableError
            raise VoiceNotAvailableError(
                missing="kokoro",
                install_cmd="pip install kokoro>=0.9.4",
            ) from exc

        log.info("Cargando Kokoro-82M (voz: %s)…", self.config.voice)
        self._model  = KPipeline(lang_code=self.config.lang_code)
        self._loaded = True
        log.info("Kokoro listo.")

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def is_downloaded(self) -> bool:
        """
        True si el modelo ya está en el caché local.
        Comprueba si hay archivos de modelo > 10 MB en el directorio.
        """
        cache_dir = self.config.models_dir / "kokoro"
        if not cache_dir.exists():
            return False
        return any(
            f.stat().st_size > 10 * 1024 * 1024
            for f in cache_dir.rglob("*")
            if f.is_file()
        )

    # ── API pública ───────────────────────────────────────────────────────────

    def speak(
        self,
        text:         str,
        *,
        interrupt_on: Optional[threading.Event] = None,
    ) -> None:
        """
        Sintetiza y reproduce un texto completo. Bloqueante.

        Divide el texto en frases y las genera/reproduce en secuencia.
        Se interrumpe inmediatamente si interrupt_on o stop() se activan.

        Args:
            text:         Texto a sintetizar.
            interrupt_on: Event externo que interrumpe la reproducción.

        Raises:
            RuntimeError: si no se llamó load() primero.
        """
        if not self._loaded:
            raise RuntimeError(
                "El modelo TTS no está cargado. Llama load() primero."
            )

        with self._speaking_lock:
            self._interrupt.clear()

            if self.on_start:
                self.on_start()

            for sentence in _split_sentences(text):
                if self._is_interrupted(interrupt_on):
                    break
                self._generate_and_play(sentence, interrupt_on)

            if self.on_end:
                self.on_end()

    def speak_stream(
        self,
        token_iter:   Iterable[str],
        *,
        interrupt_on: Optional[threading.Event] = None,
    ) -> None:
        """
        Sintetiza desde un stream de tokens (LLM).

        Acumula tokens hasta tener una frase completa, luego la reproduce.
        Empieza a sonar sin esperar el texto completo.

        Args:
            token_iter:   Iterable de strings (tokens del LLM).
            interrupt_on: Event externo que interrumpe la reproducción.

        Raises:
            RuntimeError: si no se llamó load() primero.
        """
        if not self._loaded:
            raise RuntimeError(
                "El modelo TTS no está cargado. Llama load() primero."
            )

        with self._speaking_lock:
            self._interrupt.clear()
            buf = SentenceBuffer()

            if self.on_start:
                self.on_start()

            for token in token_iter:
                if self._is_interrupted(interrupt_on):
                    buf.clear()
                    break

                sentence = buf.push(token)
                if sentence:
                    if not self._generate_and_play(sentence, interrupt_on):
                        buf.clear()
                        break
            else:
                # Emitir lo que quede al final del stream
                remainder = buf.flush()
                if remainder and not self._is_interrupted(interrupt_on):
                    self._generate_and_play(remainder, interrupt_on)

            if self.on_end:
                self.on_end()

    def stop(self) -> None:
        """
        Interrumpe la reproducción en curso.
        La detención efectiva ocurre en ≤ 50ms (un sub-chunk).
        """
        self._interrupt.set()
        if self.on_interrupted:
            threading.Thread(
                target=self.on_interrupted, daemon=True
            ).start()

    # ── internals ─────────────────────────────────────────────────────────────

    def _is_interrupted(
        self, external: Optional[threading.Event]
    ) -> bool:
        return self._interrupt.is_set() or (
            external is not None and external.is_set()
        )

    def _generate_and_play(
        self,
        sentence:     str,
        interrupt_on: Optional[threading.Event],
    ) -> bool:
        """
        Genera audio para una frase (Kokoro) y la reproduce.

        El pipeline es: Kokoro genera chunk N → stream.write(chunk N)
        → Kokoro genera chunk N+1 … (paralelismo natural).

        Returns:
            False si la reproducción fue interrumpida.
        """
        if self._is_interrupted(interrupt_on):
            return False

        if self.on_sentence:
            self.on_sentence(sentence)

        try:
            import sounddevice as sd
        except ImportError:
            log.warning("sounddevice no disponible — TTS sin audio")
            return True

        stream = sd.OutputStream(
            samplerate = self.SAMPLE_RATE,
            channels   = 1,
            dtype      = "float32",
            latency    = "low",
        )
        stream.start()

        interrupted = False
        try:
            for _gs, _ps, audio in self._model(
                sentence,
                voice = self.config.voice,
                speed = self.config.speed,
            ):
                if audio is None or len(audio) == 0:
                    continue

                if self._is_interrupted(interrupt_on):
                    stream.abort()
                    interrupted = True
                    break

                audio_scaled = (audio * self.config.volume).astype(np.float32)

                # Escribir en sub-chunks de 50ms para interrupción granular
                for i in range(0, len(audio_scaled), _SUB_CHUNK_SAMPLES):
                    if self._is_interrupted(interrupt_on):
                        stream.abort()
                        interrupted = True
                        break

                    chunk = audio_scaled[i : i + _SUB_CHUNK_SAMPLES]

                    # RMS para animación de la mascota
                    if self.on_level:
                        rms = float(np.sqrt(np.mean(chunk ** 2)))
                        self.on_level(rms)

                    stream.write(chunk.reshape(-1, 1))

                if interrupted:
                    break

        except Exception as exc:
            log.error("Error en síntesis/reproducción TTS: %s", exc)
            interrupted = True

        finally:
            try:
                if not interrupted:
                    stream.stop()
                stream.close()
            except Exception:
                pass

        return not interrupted
