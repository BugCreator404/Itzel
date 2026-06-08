"""Endpoint POST /api/v1/chat — streaming SSE de tokens.

Flujo:
  1. Recibe { message, session_id?, model?, stream }
  2. Broadcast WS → mascot:"work"
  3. Emite tokens via SSE  →  data: <token>\n\n
  4. Al terminar           →  data: [DONE]\n\n  + broadcast mascot:"idle"
  5. En error              →  data: [ERROR:<msg>]\n\n + broadcast mascot:"idle"
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ...logger import log_api
from ...rate_limiter import RateLimiter
from ...ws_manager import WsMessage, ws_manager

router = APIRouter(prefix="/chat", tags=["chat"])

# Rate limiter local al módulo (se sobreescribirá desde engine si se inyecta)
_rate_limiter = RateLimiter(model_rps=10)


# ── Schemas ────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    model: Optional[str] = None
    stream: bool = True


class ChatResponse(BaseModel):
    id: str
    session_id: str
    content: str
    model: str


# ── Helpers SSE ────────────────────────────────────────────────────────────

def _sse(data: str) -> str:
    """Formatea una línea SSE."""
    return f"data: {data}\n\n"


async def _stream_tokens(
    message: str,
    session_id: str,
    model_name: str,
) -> AsyncGenerator[str, None]:
    """
    Genera tokens como SSE.

    Ahora: simula un stream con el placeholder.
    TODO(v2): reemplazar por llamada real a llama.cpp / Ollama.
    """
    # Notificar a todos los clientes WS que la mascota está trabajando
    await ws_manager.broadcast(WsMessage.status("work", model=model_name))

    try:
        # ── Respuesta placeholder — reemplazar en v2 ──────────────
        placeholder = (
            "Hola, soy Itzel 🦎 — el backend real con llama.cpp "
            "estará disponible en la siguiente sesión. "
            f"Tu mensaje fue: «{message}»"
        )
        for token in placeholder.split(" "):
            yield _sse(token + " ")
            await asyncio.sleep(0.04)   # simula latencia de inferencia
        # ── fin placeholder ───────────────────────────────────────

        yield _sse("[DONE]")
        log_api.info(
            "Chat completado",
            extra={"component": "chat", "session_id": session_id, "model": model_name},
        )

    except asyncio.CancelledError:
        yield _sse("[ERROR:Generación cancelada]")
        log_api.warning("Stream cancelado por el cliente", extra={"session_id": session_id})
        raise

    except Exception as exc:
        yield _sse(f"[ERROR:{exc}]")
        log_api.error("Error en stream: %s", exc, extra={"session_id": session_id})

    finally:
        # Siempre restaurar mascota a idle, incluso si hubo error
        await ws_manager.broadcast(WsMessage.status("idle", model=model_name))


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.post("/", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> StreamingResponse | ChatResponse:
    session_id = req.session_id or str(uuid4())
    model_name = req.model or "itzel-1b"

    # Rate limiting
    allowed = await _rate_limiter.check("model")
    if not allowed:
        log_api.warning("Rate limit alcanzado", extra={"path": "/chat", "session_id": session_id})
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiadas solicitudes. Espera un momento antes de intentar de nuevo.",
        )

    if req.stream:
        return StreamingResponse(
            _stream_tokens(req.message, session_id, model_name),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",   # desactiva buffering en nginx si hay proxy
                "X-Session-Id": session_id,
            },
        )

    # Respuesta síncrona (sin streaming) — para clientes CLI simples
    full_response = (
        f"Hola, soy Itzel 🦎 — modelo: {model_name}. "
        f"Tu mensaje fue: «{req.message}»"
    )
    return ChatResponse(
        id=str(uuid4()),
        session_id=session_id,
        content=full_response,
        model=model_name,
    )
