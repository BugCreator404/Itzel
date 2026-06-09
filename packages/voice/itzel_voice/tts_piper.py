"""
Motor TTS de respaldo — Piper TTS (ONNX).

Se activa automáticamente si Kokoro no puede cargarse o si
config.engine == "piper". API idéntica a TextToSpeech para
que el pipeline pueda intercambiarlos sin cambiar código.

Diferencias vs Kokoro:
  - Calidad: ligeramente inferior, más robótica en prosodía
  - Tamaño: ~63 MB (es_MX-claude-high.onnx)
  - Latencia: similar (~200–400ms first-syllable en CPU)
  - Sin PyTorch ni transformers: solo onnxruntime

Voces disponibles (rhasspy/piper-voices):
  es_MX-claude-high      ES-MX femenino, alta calidad [DEFAULT]
  es_ES-davefx-medium    ES-ES masculino, calidad media
  es_ES-carlfm-x_low     ES-ES masculino, baja calidad (muy ligero, ~9 MB)
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Optional

import numpy as np

from .tts import (
    SentenceBuffer,
    TTSConfig,
    _SUB_CHUNK_SAMPLES,
    _split_sentences,
)

log = logging.getLogger("itzel.voice.tts_piper")

# ─── ruta HuggingFace por convención del repo rhasspy/piper-voices ───────────

_PIPER_VOICES_REPO = "rhasspy/piper-voices"
# Voces de fallback en orden de preferencia si la principal falla
_FALLBACK_VOICES = [
    "es_MX-claude-high",
    "es_ES-davefx-medium",
    "es_ES-carlfm-x_low",
]


def _voice_hf_paths(voice_name: str) -> tuple[str, str]:
    """
    Calcula las rutas en rhasspy/piper-voices para un modelo Piper.

    Estructura del repo:
        {lang}/{COUNTRY}/{voice_name}/{quality}/{voice_name}.onnx
        {lang}/{COUNTRY}/{voice_name}/{quality}/{voice_name}.onnx.json

    Args:
        voice_name: p.ej. "es_MX-claude-high"

    Returns:
        (onnx_hf_path, json_hf_path)
    """
    parts    = voice_name.split("-")      # ["es_MX", "claude", "high"]
    lc       = parts[0]                   # "es_MX"
    quality  = parts[-1]                  # "high"

    if "_" in lc:
        lang, country = lc.split("_", 1)
        base = f"{lang}/{country}/{voice_name}/{quality}"
    else:
        base = f"{lc}/{voice_name}/{quality}"

    return f"{base}/{voice_name}.onnx", f"{base}/{voice_name}.onnx.json"


# ─── motor TTS ────────────────────────────────────────────────────────────────

class PiperTextToSpeech:
    """
    Motor TTS basado en Piper TTS — fallback para Kokoro.

    API idéntica a TextToSpeech:
        tts = PiperTextToSpeech()
        tts.load()
        tts.speak("Hola, soy Itzel.")
        tts.stop()   # interrupción < 50ms

    Descarga automática del modelo al llamar load() si no está en caché.
    """

    def __init__(self, config: Optional[TTSConfig] = None) -> None:
        self.config  = config or TTSConfig(engine="piper", voice="es_MX-claude-high")
        self._voice  = None   # piper.voice.PiperVoice
        self._loaded = False
        self._sr     = 22_050  # Hz — se actualiza desde el config del modelo

        # Sincronización
        self._interrupt     = threading.Event()
        self._speaking_lock = threading.Lock()

        # Callbacks públicos — misma interfaz que TextToSpeech
        self.on_start:       Optional[Callable[[], None]]          = None
        self.on_end:         Optional[Callable[[], None]]          = None
        self.on_sentence:    Optional[Callable[[str], None]]       = None
        self.on_level:       Optional[Callable[[float], None]]     = None
        self.on_interrupted: Optional[Callable[[], None]]          = None

    # ── carga del modelo ──────────────────────────────────────────────────────

    def load(self) -> None:
        """
        Descarga (si hace falta) y carga el modelo Piper ONNX.

        Intenta primero la voz configurada; si falla, prueba las voces
        de fallback en _FALLBACK_VOICES.

        Raises:
            VoiceNotAvailableError: si piper-tts no está instalado.
            RuntimeError: si ninguna voz pudo descargarse/cargarse.
        """
        try:
            from piper.voice import PiperVoice  # noqa: F401
        except ImportError as exc:
            from . import VoiceNotAvailableError
            raise VoiceNotAvailableError(
                missing="piper-tts",
                install_cmd="pip install piper-tts",
            ) from exc

        voices_to_try = [self.config.voice] + [
            v for v in _FALLBACK_VOICES if v != self.config.voice
        ]

        for voice_name in voices_to_try:
            try:
                model_path, json_path = self._ensure_downloaded(voice_name)
                from piper.voice import PiperVoice
                self._voice  = PiperVoice.load(str(model_path), config_path=str(json_path))
                self._sr     = self._voice.config.sample_rate
                self._loaded = True
                if voice_name != self.config.voice:
                    log.warning(
                        "Voz Piper '%s' no disponible; usando '%s'.",
                        self.config.voice, voice_name,
                    )
                log.info("Piper TTS listo — voz: %s @ %d Hz", voice_name, self._sr)
                return
            except Exception as exc:
                log.warning("No se pudo cargar la voz '%s': %s", voice_name, exc)

        raise RuntimeError(
            "No se pudo cargar ninguna voz Piper. "
            f"Voces intentadas: {voices_to_try}"
        )

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def is_downloaded(self) -> bool:
        """True si el modelo ONNX de la voz configurada ya existe localmente."""
        piper_dir  = self.config.models_dir / "piper"
        voice_name = self.config.voice
        return (piper_dir / f"{voice_name}.onnx").exists()

    def _ensure_downloaded(self, voice_name: str) -> tuple[Path, Path]:
        """
        Devuelve (model_path, json_path) locales.
        Descarga de HuggingFace si no existen.
        """
        piper_dir = self.config.models_dir / "piper"
        piper_dir.mkdir(parents=True, exist_ok=True)

        model_local = piper_dir / f"{voice_name}.onnx"
        json_local  = piper_dir / f"{voice_name}.onnx.json"

        if model_local.exists() and json_local.exists():
            return model_local, json_local

        log.info("Descargando voz Piper '%s'…", voice_name)
        onnx_hf, json_hf = _voice_hf_paths(voice_name)

        from huggingface_hub import hf_hub_download

        downloaded_onnx = hf_hub_download(
            repo_id   = _PIPER_VOICES_REPO,
            filename  = onnx_hf,
            local_dir = str(piper_dir),
        )
        downloaded_json = hf_hub_download(
            repo_id   = _PIPER_VOICES_REPO,
            filename  = json_hf,
            local_dir = str(piper_dir),
        )

        # Copiar a ubicación plana para facilitar is_downloaded()
        import shutil
        shutil.copy2(downloaded_onnx, model_local)
        shutil.copy2(downloaded_json, json_local)

        return model_local, json_local

    # ── API pública (idéntica a TextToSpeech) ─────────────────────────────────

    def speak(
        self,
        text:         str,
        *,
        interrupt_on: Optional[threading.Event] = None,
    ) -> None:
        """
        Sintetiza y reproduce un texto completo. Bloqueante.
        Idéntico a TextToSpeech.speak().
        """
        if not self._loaded:
            raise RuntimeError("Llama load() primero.")

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
        Sintetiza desde un stream de tokens del LLM. Bloqueante.
        Idéntico a TextToSpeech.speak_stream().
        """
        if not self._loaded:
            raise RuntimeError("Llama load() primero.")

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
                remainder = buf.flush()
                if remainder and not self._is_interrupted(interrupt_on):
                    self._generate_and_play(remainder, interrupt_on)

            if self.on_end:
                self.on_end()

    def stop(self) -> None:
        """Interrumpe la reproducción en curso (≤ 50ms)."""
        self._interrupt.set()
        if self.on_interrupted:
            threading.Thread(target=self.on_interrupted, daemon=True).start()

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
        Genera audio para una frase con Piper y la reproduce.

        Piper genera int16 PCM; convertimos a float32 antes de reproducir.
        El parámetro length_scale es el inverso de speed (1/speed).

        Returns:
            False si fue interrumpida.
        """
        if self._is_interrupted(interrupt_on):
            return False

        if self.on_sentence:
            self.on_sentence(sentence)

        # Recopilar audio de Piper (genera bloques de int16 bytes)
        audio_chunks: list[np.ndarray] = []
        try:
            length_scale = 1.0 / max(self.config.speed, 0.1)
            for raw_bytes in self._voice.synthesize_stream_raw(
                sentence,
                length_scale = length_scale,
            ):
                if self._is_interrupted(interrupt_on):
                    return False
                if not raw_bytes:
                    continue
                pcm = np.frombuffer(raw_bytes, dtype=np.int16)
                audio_chunks.append(pcm.astype(np.float32) / 32_768.0)
        except Exception as exc:
            log.error("Error en síntesis Piper: %s", exc)
            return False

        if not audio_chunks:
            return True

        full_audio = np.concatenate(audio_chunks)
        return self._play_audio(full_audio, interrupt_on)

    def _play_audio(
        self,
        audio:        np.ndarray,
        interrupt_on: Optional[threading.Event],
    ) -> bool:
        """
        Reproduce audio vía sd.OutputStream con sub-chunks de 50ms.
        Mismo mecanismo de interrupción que TextToSpeech.
        """
        try:
            import sounddevice as sd
        except ImportError:
            log.warning("sounddevice no disponible — TTS sin audio")
            return True

        # _SUB_CHUNK_SAMPLES está definido para 24kHz; ajustar para la sr de Piper
        chunk_size = int(self._sr * 0.05)  # 50ms a la sr real del modelo

        audio_scaled = (audio * self.config.volume).astype(np.float32)

        stream = sd.OutputStream(
            samplerate = self._sr,
            channels   = 1,
            dtype      = "float32",
            latency    = "low",
        )
        stream.start()

        interrupted = False
        try:
            for i in range(0, len(audio_scaled), chunk_size):
                if self._is_interrupted(interrupt_on):
                    stream.abort()
                    interrupted = True
                    break

                chunk = audio_scaled[i : i + chunk_size]

                if self.on_level:
                    rms = float(np.sqrt(np.mean(chunk ** 2)))
                    self.on_level(rms)

                stream.write(chunk.reshape(-1, 1))

        except Exception as exc:
            log.error("Error en reproducción Piper: %s", exc)
            interrupted = True

        finally:
            try:
                if not interrupted:
                    stream.stop()
                stream.close()
            except Exception:
                pass

        return not interrupted
