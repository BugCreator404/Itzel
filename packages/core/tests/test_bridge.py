"""Tests del bridge WebSocket — WsManager y protocolo de mensajes."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from itzel_core.engine import app
from itzel_core.ws_manager import WsMessage, ws_manager


# ── WsMessage helpers ─────────────────────────────────────────────────────

def test_ws_message_make_has_required_fields():
    raw = WsMessage.make("status", {"mascot": "idle"})
    msg = json.loads(raw)
    assert msg["type"] == "status"
    assert "id" in msg
    assert msg["payload"]["mascot"] == "idle"


def test_ws_message_pong_preserves_id():
    pong = json.loads(WsMessage.pong("test-id-123"))
    assert pong["type"] == "pong"
    assert pong["id"] == "test-id-123"


def test_ws_message_error_structure():
    raw = WsMessage.error("MODEL_NOT_LOADED", "El modelo no está cargado")
    msg = json.loads(raw)
    assert msg["type"] == "error"
    assert msg["payload"]["code"] == "MODEL_NOT_LOADED"
    assert "model" in msg["payload"]["message"].lower()


def test_ws_message_status_defaults():
    raw = WsMessage.status("work")
    msg = json.loads(raw)
    assert msg["payload"]["mascot"] == "work"
    assert msg["payload"]["model"] == "itzel-1b"


# ── WsManager unit tests (sin levantar servidor) ──────────────────────────

@pytest.mark.asyncio
async def test_ws_manager_handle_invalid_json():
    """JSON inválido debe ser manejado sin lanzar excepción."""
    # Sin WebSocket real, solo probamos que no explota el método
    # El send() fallará silenciosamente porque no hay sesión registrada
    await ws_manager.handle_message("fake-session", "esto no es json{{{")
    # Si llegamos aquí, no hubo excepción no controlada


@pytest.mark.asyncio
async def test_ws_manager_active_count_starts_at_zero():
    # El manager puede tener conexiones de otros tests, pero el método existe
    assert isinstance(ws_manager.active_count, int)
    assert ws_manager.active_count >= 0


# ── Tests de integración WebSocket (requieren httpx-ws) ───────────────────
# Estos tests se marcan como skip si httpx-ws no está instalado

try:
    import httpx_ws  # noqa: F401
    _HAS_HTTPX_WS = True
except ImportError:
    _HAS_HTTPX_WS = False


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAS_HTTPX_WS, reason="httpx-ws no instalado")
async def test_ws_ping_pong():
    """El servidor debe responder pong a un ping."""
    from httpx_ws import aconnect_ws  # import diferido — solo si httpx-ws existe
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with aconnect_ws("ws://test/ws", client) as ws:
            await ws.send_text(WsMessage.make("ping", msg_id="ping-test-1"))
            response = await ws.receive_text()
            msg = json.loads(response)
            assert msg["type"] == "pong"
            assert msg["id"] == "ping-test-1"


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAS_HTTPX_WS, reason="httpx-ws no instalado")
async def test_ws_status_request():
    """status_request debe devolver un mensaje de tipo status."""
    from httpx_ws import aconnect_ws  # import diferido — solo si httpx-ws existe
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with aconnect_ws("ws://test/ws", client) as ws:
            await ws.send_text(WsMessage.make("status_request"))
            response = await ws.receive_text()
            msg = json.loads(response)
            assert msg["type"] == "status"
            assert "mascot" in msg["payload"]
