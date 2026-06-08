"""
Adaptador llama-cpp-python para Itzel.

Carga un archivo .gguf localmente y transmite tokens usando la API
nativa de llama-cpp-python. No requiere conexión a internet.

Instalación de llama-cpp-python:
  CPU only (Windows/Linux/macOS):
    pip install llama-cpp-python

  Con aceleración GPU (CUDA):
    CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python --upgrade --force-reinstall

Modelo Itzel-1B (cuando esté disponible en el repo):
  Copiar itzel-1b-q4_k_m.gguf a ~/.itzel/models/

Alternativa mientras tanto (HuggingFace CLI):
  pip install huggingface_hub
  huggingface-cli download Qwen/Qwen2.5-1.5B-Instruct-GGUF \\
      qwen2.5-1.5b-instruct-q4_k_m.gguf \\
      --local-dir ~/.itzel/models/
  # Luego renombrar a itzel-1b-q4_k_m.gguf
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import os
from pathlib import Path
from typing import AsyncGenerator

from ..config import config
from ..logger import log_engine
from .base import (
    AdapterInfo,
    BaseAdapter,
    GenerationError,
    GenerationParams,
    Message,
    ModelNotAvailableError,
)

# ─── constantes ───────────────────────────────────────────────────────────────

_MODELS_DIR  = Path.home() / ".itzel" / "models"
_GGUF_NAME   = "itzel-1b-q4_k_m.gguf"
_GGUF_PATH   = _MODELS_DIR / _GGUF_NAME

_DOWNLOAD_HINT = (
    "Modelo Itzel-1B no encontrado en ~/.itzel/models/. "
    "Descárgalo con:\n"
    "  pip install huggingface_hub\n"
    "  huggingface-cli download Qwen/Qwen2.5-1.5B-Instruct-GGUF "
    "qwen2.5-1.5b-instruct-q4_k_m.gguf --local-dir ~/.itzel/models/\n"
    "  # Renombrar a itzel-1b-q4_k_m.gguf"
)

_INSTALL_HINT = (
    "llama-cpp-python no está instalado. Instálalo con:\n"
    "  pip install llama-cpp-python"
)

# ─── adaptador ────────────────────────────────────────────────────────────────

class LlamaCppAdapter(BaseAdapter):
    """
    Adaptador para llama-cpp-python.

    El modelo se carga en memoria la primera vez que se llama a load().
    Después de eso, stream() reutiliza la instancia cargada.

    La carga es bloqueante (~2-10s según el hardware), por eso se hace
    en un thread pool para no bloquear el event loop de FastAPI.
    """

    def __init__(self, gguf_path: Path | None = None) -> None:
        self._gguf_path = gguf_path or _GGUF_PATH
        self._llm: object | None = None          # instancia Llama, cargada en load()
        self._loaded = False
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="itzel-llm",
        )

    # ── health ────────────────────────────────────────────────────────────────

    async def available(self) -> bool:
        """True si llama_cpp importable Y el .gguf existe en disco."""
        if not self._gguf_exists():
            return False
        return self._llama_cpp_importable()

    # ── carga del modelo ──────────────────────────────────────────────────────

    async def load(self) -> None:
        """
        Carga el modelo en memoria (bloqueante).
        Llamar desde el lifespan de FastAPI via asyncio.to_thread.
        """
        if self._loaded:
            return

        if not self._llama_cpp_importable():
            raise ModelNotAvailableError("llama.cpp", hint=_INSTALL_HINT)

        if not self._gguf_exists():
            raise ModelNotAvailableError("itzel-1b", hint=_DOWNLOAD_HINT)

        log_engine.info(
            "Cargando modelo llama.cpp: %s", self._gguf_path,
            extra={"component": "llamacpp"},
        )

        llama_cpp = importlib.import_module("llama_cpp")
        Llama     = llama_cpp.Llama

        n_threads = max(1, (os.cpu_count() or 4) // 2)

        def _load_blocking() -> object:
            return Llama(
                model_path  = str(self._gguf_path),
                n_ctx       = config.model.context_length,
                n_threads   = n_threads,
                n_gpu_layers= 0,          # CPU solo — GPU se activa en config futura
                verbose     = False,
                chat_format = "chatml",   # compatible con Qwen2.5
            )

        loop = asyncio.get_event_loop()
        self._llm    = await loop.run_in_executor(self._executor, _load_blocking)
        self._loaded = True

        log_engine.info(
            "Modelo llama.cpp cargado correctamente.",
            extra={"component": "llamacpp", "path": str(self._gguf_path)},
        )

    def unload(self) -> None:
        """Libera el modelo de memoria."""
        self._llm    = None
        self._loaded = False

    # ── inferencia ────────────────────────────────────────────────────────────

    async def stream(  # type: ignore[override]
        self,
        messages: list[Message],
        params: GenerationParams | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Transmite tokens desde llama.cpp.

        La API de llama-cpp-python es síncrona, así que se ejecuta
        en un thread dedicado y los tokens se pasan al event loop
        vía asyncio.Queue para mantener el streaming no bloqueante.
        """
        if not self._loaded or self._llm is None:
            raise ModelNotAvailableError(
                "itzel-1b",
                hint="El modelo no está cargado. Reinicia el backend.",
            )

        p       = (params or GenerationParams()).clamp()
        trimmed = self._trim_to_context(messages, config.model.context_length)

        # Cola para pasar tokens del thread al event loop
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_event_loop()
        llm  = self._llm

        def _generate() -> None:
            try:
                for chunk in llm.create_chat_completion(  # type: ignore[attr-defined]
                    messages   = trimmed,
                    stream     = True,
                    temperature= p.temperature,
                    top_p      = p.top_p,
                    max_tokens = p.max_tokens,
                    stop       = p.stop or [],
                ):
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    token = choices[0].get("delta", {}).get("content", "")
                    if token:
                        loop.call_soon_threadsafe(queue.put_nowait, token)
            except Exception as exc:
                # Envía el error como token especial para que el generador lo re-lance
                loop.call_soon_threadsafe(queue.put_nowait, f"\x00ERR\x00{exc}")
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)   # sentinel

        # Lanzar generación en el thread pool
        self._executor.submit(_generate)

        # Consumir la cola en el event loop
        while True:
            token = await queue.get()
            if token is None:
                break
            if token.startswith("\x00ERR\x00"):
                raise GenerationError("llama.cpp", token[5:])
            yield token

    # ── metadata ──────────────────────────────────────────────────────────────

    async def info(self) -> AdapterInfo:
        return AdapterInfo(
            id          = "itzel-1b",
            name        = "Itzel-1B (llama.cpp)",
            backend     = "llama.cpp",
            context_len = config.model.context_length,
            loaded      = self._loaded,
        )

    # ── helpers privados ──────────────────────────────────────────────────────

    def _gguf_exists(self) -> bool:
        return self._gguf_path.exists()

    @staticmethod
    def _llama_cpp_importable() -> bool:
        try:
            importlib.import_module("llama_cpp")
            return True
        except ImportError:
            return False
