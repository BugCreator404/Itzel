"""
Contrato base para todos los adaptadores de modelo en Itzel.

Cada adaptador (Ollama, llama.cpp, etc.) implementa esta interfaz.
El endpoint de chat solo conoce BaseAdapter — nunca importa los
adaptadores concretos directamente.

Formato de mensajes (compatible con OpenAI / Ollama / llama-cpp-python):
    [
        {"role": "system",    "content": "..."},
        {"role": "user",      "content": "..."},
        {"role": "assistant", "content": "..."},
        ...
    ]
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

# ─── tipos públicos ───────────────────────────────────────────────────────────

@dataclass
class GenerationParams:
    """Parámetros de inferencia con defaults seguros para ES-MX."""
    temperature:  float = 0.7
    top_p:        float = 0.95
    max_tokens:   int   = 2048
    stop:         list[str] = field(default_factory=list)

    def clamp(self) -> GenerationParams:
        """Asegura que los valores estén en rangos válidos."""
        return GenerationParams(
            temperature = max(0.0, min(2.0, self.temperature)),
            top_p       = max(0.0, min(1.0, self.top_p)),
            max_tokens  = max(1,   min(8192, self.max_tokens)),
            stop        = self.stop,
        )


@dataclass(frozen=True)
class AdapterInfo:
    """Metadatos de un adaptador para exponer en /api/v1/models."""
    id:          str          # "ollama/llama3.1:8b", "itzel-1b", etc.
    name:        str          # Nombre legible
    backend:     str          # "ollama" | "llama.cpp" | "none"
    context_len: int = 32_768 # Tokens de contexto soportados
    loaded:      bool = False # True si el modelo está en memoria ahora


Message = dict[str, str]  # {"role": "user"|"assistant"|"system", "content": str}


# ─── clase base abstracta ─────────────────────────────────────────────────────

class BaseAdapter(ABC):
    """
    Interfaz que todo adaptador de modelo debe implementar.

    Diseño de streaming:
        stream() es un async generator que emite tokens uno a uno.
        El endpoint SSE los reenvía al cliente conforme llegan.
        Esto mantiene la UI responsiva sin buffering.
    """

    # ── health ────────────────────────────────────────────────────────────────

    @abstractmethod
    async def available(self) -> bool:
        """
        Comprueba si el backend está disponible.
        Debe retornar en < 3 segundos (timeout recomendado: 2s).
        No lanza excepciones — retorna False si hay cualquier fallo.
        """

    # ── inferencia ────────────────────────────────────────────────────────────

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        params: GenerationParams | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Genera tokens de forma asíncrona.

        Args:
            messages: Historial completo incluyendo system prompt al inicio.
            params:   Parámetros de inferencia. Usa defaults si es None.

        Yields:
            Tokens de texto uno a uno (strings, nunca vacíos).

        Raises:
            ModelNotAvailableError: Si el backend no responde al inicio del stream.
            GenerationError:        Si ocurre un error durante la generación.
        """

    # ── metadata ──────────────────────────────────────────────────────────────

    @abstractmethod
    async def info(self) -> AdapterInfo:
        """Devuelve los metadatos del adaptador/modelo activo."""

    # ── helpers protegidos ────────────────────────────────────────────────────

    @staticmethod
    def _estimate_tokens(messages: list[Message]) -> int:
        """
        Estimación rápida de tokens: caracteres ÷ 4.
        Suficiente para decidir si el contexto cabe sin llamar al tokenizador.
        """
        total_chars = sum(len(m.get("content", "")) for m in messages)
        return total_chars // 4

    @staticmethod
    def _trim_to_context(
        messages: list[Message],
        max_tokens: int,
        keep_system: bool = True,
    ) -> list[Message]:
        """
        Recorta el historial para que quepa en max_tokens.

        Siempre conserva:
          - El system prompt (primer mensaje si role=="system")
          - El último mensaje del usuario

        Elimina mensajes intermedios del más antiguo al más reciente.
        """
        if not messages:
            return messages

        system = messages[0] if messages and messages[0]["role"] == "system" else None
        rest   = messages[1:] if system else messages[:]

        # Siempre mantener al menos el último mensaje
        if not rest:
            return messages

        result = list(rest)
        while result and BaseAdapter._estimate_tokens(
            ([system] if system else []) + result
        ) > max_tokens and len(result) > 1:
            # Eliminar el mensaje más antiguo (índice 0) que no sea el último
            result.pop(0)

        return ([system] if system else []) + result


# ─── excepciones del dominio ──────────────────────────────────────────────────

class ModelNotAvailableError(RuntimeError):
    """El backend de modelo no está disponible en este momento."""

    def __init__(self, backend: str, hint: str = "") -> None:
        msg = f"Modelo '{backend}' no disponible."
        if hint:
            msg += f" {hint}"
        super().__init__(msg)
        self.backend = backend
        self.hint    = hint


class GenerationError(RuntimeError):
    """Error durante la generación de tokens."""

    def __init__(self, backend: str, cause: str) -> None:
        super().__init__(f"Error de generación en '{backend}': {cause}")
        self.backend = backend
        self.cause   = cause
