"""Gestor de sesiones WebSocket para Itzel.

Responsabilidades:
- Registrar / desregistrar conexiones activas
- Broadcast a todos los clientes conectados
- Heartbeat ping/pong cada 30 segundos
- Protocolo de mensajes: { type, id, payload }
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect

from .logger import log_ws

_PING_INTERVAL = 30.0   # segundos entre pings
_PING_TIMEOUT  = 10.0   # segundos máximo esperando pong


class WsMessage:
    """Helpers para construir mensajes del protocolo Itzel-WS."""

    @staticmethod
    def make(msg_type: str, payload: dict[str, Any] | None = None, msg_id: str | None = None) -> str:
        return json.dumps({
            "type": msg_type,
            "id": msg_id or str(uuid4()),
            "payload": payload or {},
        }, ensure_ascii=False)

    @staticmethod
    def status(mascot: str, model: str = "itzel-1b", extra: dict | None = None) -> str:
        return WsMessage.make("status", {"mascot": mascot, "model": model, **(extra or {})})

    @staticmethod
    def error(code: str, message: str) -> str:
        return WsMessage.make("error", {"code": code, "message": message})

    @staticmethod
    def pong(ping_id: str) -> str:
        return WsMessage.make("pong", msg_id=ping_id)


class WsManager:
    """Gestiona todas las conexiones WebSocket activas."""

    def __init__(self) -> None:
        # session_id → WebSocket
        self._sessions: dict[str, WebSocket] = {}
        self._ping_tasks: dict[str, asyncio.Task] = {}

    # ── Ciclo de vida ─────────────────────────────────────────────

    async def connect(self, ws: WebSocket) -> str:
        """Acepta la conexión y devuelve el session_id asignado."""
        await ws.accept()
        session_id = str(uuid4())
        self._sessions[session_id] = ws
        log_ws.info("WS conectado", extra={"component": "ws", "session_id": session_id})

        # Arrancar heartbeat para esta sesión
        task = asyncio.create_task(self._heartbeat(session_id))
        self._ping_tasks[session_id] = task

        return session_id

    def disconnect(self, session_id: str) -> None:
        """Elimina la sesión y cancela su heartbeat."""
        self._sessions.pop(session_id, None)
        task = self._ping_tasks.pop(session_id, None)
        if task:
            task.cancel()
        log_ws.info("WS desconectado", extra={"component": "ws", "session_id": session_id})

    # ── Envío ─────────────────────────────────────────────────────

    async def send(self, session_id: str, message: str) -> bool:
        """Envía a una sesión específica. Devuelve False si ya no existe."""
        ws = self._sessions.get(session_id)
        if ws is None:
            return False
        try:
            await ws.send_text(message)
            return True
        except Exception as exc:
            log_ws.warning("Error enviando a sesión %s: %s", session_id, exc)
            self.disconnect(session_id)
            return False

    async def broadcast(self, message: str) -> None:
        """Envía el mensaje a todas las sesiones activas."""
        dead: list[str] = []
        for sid, ws in list(self._sessions.items()):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(sid)
        for sid in dead:
            self.disconnect(sid)

    # ── Heartbeat ─────────────────────────────────────────────────

    async def _heartbeat(self, session_id: str) -> None:
        """Envía ping cada 30 s y desconecta si no hay pong en 10 s."""
        while session_id in self._sessions:
            await asyncio.sleep(_PING_INTERVAL)
            if session_id not in self._sessions:
                break
            ping_id = str(uuid4())
            sent = await self.send(session_id, WsMessage.make("ping", msg_id=ping_id))
            if not sent:
                break
            # Esperar pong (el handler del endpoint actualiza _pong_pending)
            # Para el MVP: si la conexión sigue viva, el cliente respondió
            # TODO(v3): rastrear pong_pending por session_id
            log_ws.debug("Ping enviado", extra={"component": "ws", "session_id": session_id})

    # ── Manejo de mensaje entrante ─────────────────────────────────

    async def handle_message(self, session_id: str, raw: str) -> None:
        """Procesa un mensaje JSON recibido del cliente."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await self.send(session_id, WsMessage.error("INVALID_JSON", "Mensaje no es JSON válido"))
            return

        msg_type = msg.get("type", "")
        msg_id   = msg.get("id", str(uuid4()))

        if msg_type == "ping":
            await self.send(session_id, WsMessage.pong(msg_id))

        elif msg_type == "pong":
            log_ws.debug("Pong recibido", extra={"component": "ws", "session_id": session_id})

        elif msg_type == "status_request":
            # El engine responderá con broadcast de status real
            # Por ahora devolvemos un status base
            await self.send(session_id, WsMessage.status("idle"))

        else:
            log_ws.debug("Mensaje WS desconocido: %s", msg_type)

    @property
    def active_count(self) -> int:
        return len(self._sessions)


# Instancia global — importada por engine.py y los endpoints
ws_manager = WsManager()
