"""
Factory de adaptadores de modelo para Itzel.

Uso desde cualquier módulo del backend:
    from itzel_core.models import get_adapter

    adapter = await get_adapter()
    async for token in adapter.stream(messages):
        ...

Prioridad de selección:
    1. LlamaCppAdapter  — si ~/.itzel/models/itzel-1b-q4_k_m.gguf existe
                          y llama-cpp-python está instalado
    2. OllamaAdapter    — si localhost:11434 responde y tiene modelos
    3. NoModelAdapter   — mensaje amigable en ES-MX con instrucciones

El resultado se cachea en `_cached_adapter` para no re-detectar en
cada request. Llama a `reset_adapter()` si quieres forzar re-detección
(útil al instalar un modelo nuevo sin reiniciar el servidor).
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from ..logger import log_engine
from .base import (
    AdapterInfo,
    BaseAdapter,
    GenerationParams,
    Message,
    ModelNotAvailableError,
)
from .llamacpp import LlamaCppAdapter
from .ollama import OllamaAdapter

# ─── singleton ────────────────────────────────────────────────────────────────

_cached_adapter: BaseAdapter | None = None
_detection_lock = asyncio.Lock()


async def get_adapter() -> BaseAdapter:
    """
    Devuelve el adaptador activo, detectando y cacheando en el primer llamado.

    Thread-safe: usa un Lock para evitar detección paralela en el arranque.
    """
    global _cached_adapter

    if _cached_adapter is not None:
        return _cached_adapter

    async with _detection_lock:
        # Doble check tras adquirir el lock
        if _cached_adapter is not None:
            return _cached_adapter

        _cached_adapter = await _detect_adapter()
        return _cached_adapter


def reset_adapter() -> None:
    """
    Descarta el adaptador cacheado.
    El próximo llamado a get_adapter() volverá a detectar.
    Útil al instalar un nuevo modelo sin reiniciar el servidor.
    """
    global _cached_adapter
    _cached_adapter = None
    log_engine.info(
        "Adaptador de modelo reseteado — se re-detectará en el próximo request.",
        extra={"component": "models"},
    )


# ─── lógica de detección ──────────────────────────────────────────────────────

async def _detect_adapter() -> BaseAdapter:
    """
    Prueba los adaptadores en orden de prioridad y retorna el primero disponible.
    Si ninguno funciona, retorna NoModelAdapter con instrucciones.
    """
    log_engine.info(
        "Detectando adaptador de modelo disponible…",
        extra={"component": "models"},
    )

    # 1. llama.cpp — modelo local (máxima privacidad, sin dependencias externas)
    llama = LlamaCppAdapter()
    if await llama.available():
        log_engine.info(
            "Adaptador seleccionado: llama.cpp (itzel-1b)",
            extra={"component": "models"},
        )
        await llama.load()
        return llama

    # 2. Ollama — servicio local pero proceso separado
    ollama = OllamaAdapter()
    if await ollama.available():
        # Selecciona el primer modelo disponible si el activo no coincide
        available_models = await ollama.list_models()
        if available_models:
            preferred = _pick_ollama_model(available_models)
            ollama = OllamaAdapter(model=preferred)
            log_engine.info(
                "Adaptador seleccionado: Ollama (%s)",
                preferred,
                extra={"component": "models"},
            )
        return ollama

    # 3. Sin modelo disponible
    log_engine.warning(
        "Ningún modelo disponible. Usando NoModelAdapter.",
        extra={"component": "models"},
    )
    return NoModelAdapter()


def _pick_ollama_model(available: list[str]) -> str:
    """
    Elige el mejor modelo entre los descargados en Ollama.
    Orden de preferencia: llama3 > mistral > phi > qwen > el primero disponible.
    """
    preference = ["llama3", "mistral", "phi3", "phi", "qwen", "gemma"]
    for pref in preference:
        for m in available:
            if pref in m.lower():
                return m
    return available[0]


# ─── adaptador "sin modelo" ───────────────────────────────────────────────────

_NO_MODEL_MSG_ES = (
    "No tengo ningún modelo de IA cargado en este momento 😔\n\n"
    "Para activarme, elige una de estas opciones:\n\n"
    "**Opción A — Itzel-1B (recomendado, sin internet luego):**\n"
    "  pip install huggingface_hub\n"
    "  huggingface-cli download Qwen/Qwen2.5-1.5B-Instruct-GGUF "
    "qwen2.5-1.5b-instruct-q4_k_m.gguf --local-dir ~/.itzel/models/\n"
    "  # Renombra el archivo a: itzel-1b-q4_k_m.gguf\n"
    "  pip install llama-cpp-python\n\n"
    "**Opción B — Ollama (más fácil de instalar):**\n"
    "  Instala Ollama desde https://ollama.com/download\n"
    "  ollama pull llama3.1:8b\n\n"
    "Reinicia el backend tras instalar el modelo."
)

_NO_MODEL_MSG_EN = (
    "No AI model is currently loaded 😔\n\n"
    "To activate me, choose one of these options:\n\n"
    "**Option A — Itzel-1B (recommended, offline after download):**\n"
    "  pip install huggingface_hub\n"
    "  huggingface-cli download Qwen/Qwen2.5-1.5B-Instruct-GGUF "
    "qwen2.5-1.5b-instruct-q4_k_m.gguf --local-dir ~/.itzel/models/\n"
    "  # Rename to: itzel-1b-q4_k_m.gguf\n"
    "  pip install llama-cpp-python\n\n"
    "**Option B — Ollama (easier to install):**\n"
    "  Install Ollama from https://ollama.com/download\n"
    "  ollama pull llama3.1:8b\n\n"
    "Restart the backend after installing the model."
)


class NoModelAdapter(BaseAdapter):
    """
    Adaptador de emergencia — siempre disponible, nunca genera tokens reales.
    Devuelve instrucciones de instalación en ES-MX (o EN si el idioma es inglés).
    """

    async def available(self) -> bool:
        return True

    async def stream(  # type: ignore[override]
        self,
        messages: list[Message],
        params: GenerationParams | None = None,
    ) -> AsyncGenerator[str, None]:
        """Emite el mensaje de "sin modelo" token a token para mantener el flujo SSE."""
        # Detecta idioma del último mensaje del usuario
        last_user = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        # Heurística simple: si el mensaje tiene más palabras en inglés, responde en EN
        msg = _NO_MODEL_MSG_EN if _looks_english(last_user) else _NO_MODEL_MSG_ES

        for word in msg.split(" "):
            yield word + " "
            await asyncio.sleep(0.01)

    async def info(self) -> AdapterInfo:
        return AdapterInfo(
            id      = "none",
            name    = "Sin modelo",
            backend = "none",
            loaded  = False,
        )


def _looks_english(text: str) -> bool:
    """Heurística muy simple: detecta palabras comunes en inglés."""
    en_words = {"the", "is", "are", "what", "how", "can", "you", "i", "do", "does"}
    words    = set(text.lower().split())
    return len(words & en_words) >= 2


# ─── exports públicos ─────────────────────────────────────────────────────────

__all__ = [
    "get_adapter",
    "reset_adapter",
    "BaseAdapter",
    "AdapterInfo",
    "GenerationParams",
    "LlamaCppAdapter",
    "OllamaAdapter",
    "NoModelAdapter",
    "ModelNotAvailableError",
]
