"""
Adaptador Ollama para Itzel.

Conecta a la instancia local de Ollama (localhost:11434) y transmite
tokens vía la API /api/chat con stream=True.

Requisito: Ollama instalado y corriendo.
  Windows/Mac: https://ollama.com/download
  Linux: curl -fsSL https://ollama.com/install.sh | sh

Modelos recomendados:
  ollama pull llama3.1:8b    ← balance calidad/velocidad (5 GB RAM)
  ollama pull mistral:7b     ← alternativa rápida (5 GB RAM)
  ollama pull phi3:mini      ← para máquinas con poca RAM (2 GB RAM)
"""

from __future__ import annotations

import json
from typing import AsyncGenerator

import httpx

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

_BASE_URL          = "http://127.0.0.1:11434"
_HEALTH_TIMEOUT_S  = 2.0    # timeout para /api/tags
_FIRST_TOKEN_S     = 30.0   # timeout hasta recibir el primer token
_READ_TIMEOUT_S    = 60.0   # timeout para seguir leyendo el stream

_INSTALL_HINT = (
    "Para usar Ollama, instálalo desde https://ollama.com/download "
    "y ejecuta: ollama pull llama3.1:8b"
)

# ─── adaptador ────────────────────────────────────────────────────────────────

class OllamaAdapter(BaseAdapter):
    """
    Adaptador para Ollama — requiere que el servicio esté corriendo localmente.

    El modelo usado se toma de config.model.active si empieza con "ollama/",
    o se usa el primero disponible en la instancia.
    """

    def __init__(self, model: str | None = None) -> None:
        # Normaliza "ollama/llama3.1:8b" → "llama3.1:8b"
        raw = model or config.model.active
        self._model = raw.removeprefix("ollama/") if raw.startswith("ollama/") else raw

    # ── health ────────────────────────────────────────────────────────────────

    async def available(self) -> bool:
        """Comprueba si Ollama responde en :11434 y tiene al menos un modelo."""
        try:
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT_S) as client:
                resp = await client.get(f"{_BASE_URL}/api/tags")
                if resp.status_code != 200:
                    return False
                data = resp.json()
                models = data.get("models", [])
                if not models:
                    log_engine.warning(
                        "Ollama está corriendo pero no tiene modelos descargados. "
                        "Ejecuta: ollama pull llama3.1:8b"
                    )
                return True
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """Devuelve los modelos instalados en la instancia local de Ollama."""
        try:
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT_S) as client:
                resp = await client.get(f"{_BASE_URL}/api/tags")
                resp.raise_for_status()
                return [m["name"] for m in resp.json().get("models", [])]
        except Exception:
            return []

    # ── inferencia ────────────────────────────────────────────────────────────

    async def stream(  # type: ignore[override]
        self,
        messages: list[Message],
        params: GenerationParams | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Transmite tokens desde Ollama vía NDJSON.

        Ollama responde con líneas JSON separadas por newline:
            {"message": {"role": "assistant", "content": "token"}, "done": false}
            ...
            {"message": {"role": "assistant", "content": ""}, "done": true}
        """
        p = (params or GenerationParams()).clamp()
        trimmed = self._trim_to_context(messages, config.model.context_length)

        payload = {
            "model":    self._model,
            "messages": trimmed,
            "stream":   True,
            "options": {
                "temperature": p.temperature,
                "top_p":       p.top_p,
                "num_predict": p.max_tokens,
                "stop":        p.stop,
            },
        }

        timeout = httpx.Timeout(
            connect=_HEALTH_TIMEOUT_S,
            read=_READ_TIMEOUT_S,
            write=10.0,
            pool=5.0,
        )

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{_BASE_URL}/api/chat",
                    json=payload,
                ) as resp:
                    if resp.status_code == 404:
                        raise ModelNotAvailableError(
                            f"ollama/{self._model}",
                            hint=f"Modelo no descargado. Ejecuta: ollama pull {self._model}",
                        )
                    if resp.status_code != 200:
                        raise ModelNotAvailableError(
                            "ollama",
                            hint=_INSTALL_HINT,
                        )

                    async for raw_line in resp.aiter_lines():
                        if not raw_line.strip():
                            continue
                        try:
                            chunk = json.loads(raw_line)
                        except json.JSONDecodeError:
                            continue

                        token = chunk.get("message", {}).get("content", "")
                        if token:
                            yield token

                        if chunk.get("done"):
                            break

        except ModelNotAvailableError:
            raise
        except httpx.ConnectError:
            raise ModelNotAvailableError("ollama", hint=_INSTALL_HINT)
        except httpx.TimeoutException as exc:
            raise GenerationError("ollama", f"Timeout: {exc}")
        except Exception as exc:
            raise GenerationError("ollama", str(exc))

    # ── metadata ──────────────────────────────────────────────────────────────

    async def info(self) -> AdapterInfo:
        loaded = await self.available()
        return AdapterInfo(
            id          = f"ollama/{self._model}",
            name        = f"Ollama — {self._model}",
            backend     = "ollama",
            context_len = 32_768,
            loaded      = loaded,
        )
