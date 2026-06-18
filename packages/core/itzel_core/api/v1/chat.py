"""Endpoint POST /api/v1/chat — streaming SSE de tokens con modelo real.

Flujo completo:
  1. Recibe { message, session_id?, language?, stream }
  2. Guarda mensaje del usuario en SQLite
  3. Carga historial de la sesión desde SQLite
  4. Construye system prompt localizado (ES-MX / EN-US)
  5. Ensambla [system, ...history, user_msg]
  6. Llama al adaptador activo (llama.cpp → Ollama → NoModel)
  7. Emite tokens vía SSE  →  data: <token>\\n\\n
  8. Al terminar           →  data: [DONE]\\n\\n  + mascot:"happy"
  9. Guarda respuesta completa en SQLite
  10. En error             →  data: [ERROR]\\n\\n  + mascot:"idle"
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ...config import config
from ...logger import log_api
from ...memory import MemoryStore
from ...models.base import GenerationError, GenerationParams, ModelNotAvailableError
from ...rate_limiter import RateLimiter
from ...router import build_messages, build_system_prompt, get_adapter
from ...ws_manager import WsMessage, ws_manager

router = APIRouter(prefix="/chat", tags=["chat"])

_rate_limiter = RateLimiter(model_rps=10)

# Una sola conexión SQLite por proceso
_memory = MemoryStore()


# ── Schemas ────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message:    str
    session_id: str | None = None
    language:   str | None = None   # "es-MX" | "en-US"; default de config
    stream:     bool = True


class ChatResponse(BaseModel):
    id:         str
    session_id: str
    content:    str
    model:      str


# ── Helpers SSE ────────────────────────────────────────────────────────────

def _sse(data: str) -> str:
    return f"data: {data}\n\n"


# ── RAG: contexto de los documentos del usuario ──────────────────────────────

def _maybe_augment_with_rag(
    messages: list[dict],
    language: str,
) -> tuple[list[dict], list[dict]]:
    """Inyecta contexto de los documentos del usuario en el último turno.

    ES: Opt-in doble (rag.enabled + rag.auto_context, ya verificado por el
        caller). Degrada en SILENCIO ante cualquier fallo — deps faltantes,
        índice vacío o error de búsqueda → devuelve los mensajes sin tocar y
        sin fuentes. El chat nunca se rompe por culpa del RAG.

    EN: Augments the last user turn with document context. Fails silently:
        on any error the chat continues normally without context.

    Devuelve (mensajes, fuentes). `fuentes` es [] si no se inyectó contexto.
    """
    try:
        from ...rag import check_rag_available
        available, _missing = check_rag_available()
        if not available:
            return messages, []

        from ...rag.pipeline import get_pipeline
        augmented, ctx = get_pipeline().augment_messages(messages, language=language)
        return augmented, ctx.sources
    except Exception as exc:
        log_api.warning(
            "RAG auto_context falló; sigo sin contexto: %s", exc,
            extra={"component": "chat"},
        )
        return messages, []


# ── Stream principal ───────────────────────────────────────────────────────

async def _stream_tokens(
    message:    str,
    session_id: str,
    language:   str,
) -> AsyncGenerator[str, None]:
    """
    Genera tokens reales desde el adaptador activo y los emite como SSE.

    Pasos:
      1. Guarda el mensaje del usuario en SQLite.
      2. Recupera el historial previo de la sesión.
      3. Construye el system prompt + lista de mensajes.
      4. Llama al adaptador para hacer streaming.
      5. Guarda la respuesta completa al finalizar.
    """
    adapter      = await get_adapter()
    adapter_info = await adapter.info()
    model_name   = adapter_info.id

    await ws_manager.broadcast(WsMessage.status("work", model=model_name))

    accumulated: list[str] = []

    try:
        # 1. Guardar mensaje del usuario
        _memory.save(role="user", content=message, session_id=session_id)

        # 2. Historial previo (excluimos el último entry = el que acabamos de guardar)
        history_entries = _memory.get_session(session_id, limit=60)
        prior_entries   = history_entries[:-1]
        history_msgs    = [
            {"role": e.role, "content": e.content}
            for e in prior_entries
            if e.role in ("user", "assistant")
        ]

        # 3. System prompt + mensajes
        system_prompt = build_system_prompt(
            session_id = session_id,
            model_name = model_name,
            language   = language,
        )
        messages = build_messages(
            system_prompt = system_prompt,
            history       = history_msgs,
            new_message   = message,
        )

        # 3.5 RAG (opt-in doble): inyecta contexto de los documentos del usuario.
        #     Si está apagado o falla, `messages` queda intacto y rag_sources=[].
        rag_sources: list[dict] = []
        if config.rag.enabled and config.rag.auto_context:
            messages, rag_sources = _maybe_augment_with_rag(messages, language)

        # 4. Inferencia
        params = GenerationParams(
            temperature = config.model.temperature,
            top_p       = config.model.top_p,
            max_tokens  = config.model.max_tokens,
        )
        async for token in adapter.stream(messages, params):
            accumulated.append(token)
            yield _sse(token)

        # Fuentes citadas (solo si el RAG inyectó contexto). Frame de control
        # antes de [DONE]; el frontend mapea cada [n] a su archivo.
        if rag_sources:
            yield _sse("[SOURCES]" + json.dumps(
                {"sources": rag_sources}, ensure_ascii=False,
            ))

        yield _sse("[DONE]")

        # 5. Guardar respuesta completa
        full_response = "".join(accumulated)
        if full_response:
            _memory.save(
                role       = "assistant",
                content    = full_response,
                session_id = session_id,
                metadata   = {"model": model_name, "language": language},
            )

        log_api.info(
            "Chat completado — %d tokens",
            len(accumulated),
            extra={"component": "chat", "session_id": session_id, "model": model_name},
        )
        await ws_manager.broadcast(WsMessage.status("happy", model=model_name))

    except ModelNotAvailableError as exc:
        yield _sse(str(exc))
        yield _sse("[DONE]")
        log_api.warning("Modelo no disponible: %s", exc, extra={"session_id": session_id})
        await ws_manager.broadcast(WsMessage.status("idle", model=model_name))

    except GenerationError as exc:
        yield _sse(f"\n\n⚠ Error de generación: {exc.cause}")
        yield _sse("[ERROR]")
        log_api.error("Error de generación: %s", exc, extra={"session_id": session_id})
        await ws_manager.broadcast(WsMessage.status("idle", model=model_name))

    except asyncio.CancelledError:
        if accumulated:
            _memory.save(
                role       = "assistant",
                content    = "".join(accumulated) + " [cancelado]",
                session_id = session_id,
                metadata   = {"model": model_name, "cancelled": True},
            )
        yield _sse("[CANCELLED]")
        log_api.warning("Stream cancelado por el cliente", extra={"session_id": session_id})
        await ws_manager.broadcast(WsMessage.status("idle", model=model_name))
        raise

    except Exception as exc:
        yield _sse(f"\n\n⚠ Error inesperado: {exc}")
        yield _sse("[ERROR]")
        log_api.error("Error inesperado en stream: %s", exc, extra={"session_id": session_id})
        await ws_manager.broadcast(WsMessage.status("idle", model=model_name))


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.post("/", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> StreamingResponse | ChatResponse:
    session_id = req.session_id or str(uuid4())
    language   = req.language or config.language  # "es-MX" por defecto

    allowed = await _rate_limiter.check("model")
    if not allowed:
        log_api.warning("Rate limit alcanzado", extra={"path": "/chat", "session_id": session_id})
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiadas solicitudes. Espera un momento.",
        )

    if req.stream:
        return StreamingResponse(
            _stream_tokens(req.message, session_id, language),
            media_type="text/event-stream",
            headers={
                "Cache-Control":     "no-cache",
                "X-Accel-Buffering": "no",
                "X-Session-Id":      session_id,
            },
        )

    # ── Respuesta síncrona (para CLI sin streaming) ────────────────
    tokens: list[str] = []
    async for raw in _stream_tokens(req.message, session_id, language):
        if not raw.startswith("data: "):
            continue
        payload = raw[6:].strip()
        if payload in ("[DONE]", "[ERROR]", "[CANCELLED]"):
            break
        if payload.startswith("[SOURCES]"):
            continue   # marco de control RAG, no es texto de la respuesta
        tokens.append(payload)

    adapter_info = await (await get_adapter()).info()
    return ChatResponse(
        id         = str(uuid4()),
        session_id = session_id,
        content    = "".join(tokens),
        model      = adapter_info.id,
    )


@router.delete("/{session_id}", status_code=204)
async def clear_session(session_id: str) -> None:
    """Borra el historial de una sesión de la memoria."""
    _memory.delete_session(session_id)
    log_api.info(
        "Sesión borrada: %s", session_id,
        extra={"component": "chat", "session_id": session_id},
    )
