"""Endpoint WebSocket /ws — bridge principal Tauri ↔ Backend."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...logger import log_ws
from ...ws_manager import ws_manager

router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """
    Punto de entrada WebSocket único.

    Protocolo entrante:  { "type": "ping|pong|status_request", "id": "<uuid>", "payload": {} }
    Protocolo saliente:  { "type": "pong|status|error",        "id": "<uuid>", "payload": {} }
    """
    session_id = await ws_manager.connect(ws)
    try:
        while True:
            raw = await ws.receive_text()
            await ws_manager.handle_message(session_id, raw)
    except WebSocketDisconnect:
        log_ws.info("Cliente desconectado normalmente", extra={"component": "ws", "session_id": session_id})
    except Exception as exc:
        log_ws.error("Error inesperado en WS session %s: %s", session_id, exc)
    finally:
        ws_manager.disconnect(session_id)
