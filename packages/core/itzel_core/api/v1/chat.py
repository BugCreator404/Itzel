"""Endpoint /api/v1/chat — chat con streaming SSE."""

from __future__ import annotations

from typing import AsyncGenerator, Optional
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(prefix="/chat", tags=["chat"])


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


@router.post("/", response_model=ChatResponse)
async def chat(req: ChatRequest):
    session = req.session_id or str(uuid4())

    if req.stream:
        return StreamingResponse(
            _stream_response(req.message, session),
            media_type="text/event-stream",
        )

    # Respuesta síncrona (sin streaming)
    return ChatResponse(
        id=str(uuid4()),
        session_id=session,
        content="Backend en construcción — v2 disponible próximamente.",
        model="itzel-1b",
    )


async def _stream_response(message: str, session_id: str) -> AsyncGenerator[str, None]:
    # TODO(v2): conectar al motor llama.cpp real
    placeholder = "Backend en construcción — v2 disponible próximamente."
    for char in placeholder:
        yield f"data: {char}\n\n"
