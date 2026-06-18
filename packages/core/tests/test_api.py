"""Tests de integración para los endpoints REST de Itzel."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from itzel_core.engine import app


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── /api/v1/health ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_ok(client):
    async with client as c:
        r = await c.get("/api/v1/health/")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "uptime_s" in data
    assert "ws_connections" in data
    assert data["uptime_s"] >= 0


# ── /api/v1/status ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_structure(client):
    async with client as c:
        r = await c.get("/api/v1/status/")
    assert r.status_code == 200
    data = r.json()
    for key in ("backend", "model", "voice_stt", "voice_tts", "memory", "mcp"):
        assert key in data, f"Falta campo: {key}"
        assert "ok" in data[key]
        assert "detail" in data[key]
    assert data["backend"]["ok"] is True


# ── /api/v1/models ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_models_list(client):
    async with client as c:
        r = await c.get("/api/v1/models/")
    assert r.status_code == 200
    models = r.json()
    assert isinstance(models, list)
    assert len(models) >= 1
    ids = [m["id"] for m in models]
    assert "itzel-1b" in ids


@pytest.mark.asyncio
async def test_models_switch_valid(client):
    async with client as c:
        r = await c.post("/api/v1/models/switch", json={"model_id": "itzel-1b"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["model_id"] == "itzel-1b"


@pytest.mark.asyncio
async def test_models_switch_unknown(client):
    async with client as c:
        r = await c.post("/api/v1/models/switch", json={"model_id": "modelo-fantasma"})
    assert r.status_code == 404


# ── /api/v1/chat ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_sync_response(client):
    async with client as c:
        r = await c.post("/api/v1/chat/", json={
            "message": "Hola Itzel",
            "stream": False,
        })
    assert r.status_code == 200
    data = r.json()
    assert "content" in data
    assert "session_id" in data
    assert "model" in data


@pytest.mark.asyncio
async def test_chat_stream_contains_done(client):
    """El stream SSE debe terminar con la señal [DONE]."""
    chunks: list[str] = []
    async with client as c:
        async with c.stream("POST", "/api/v1/chat/", json={
            "message": "test",
            "stream": True,
        }) as r:
            assert r.status_code == 200
            async for line in r.aiter_lines():
                chunks.append(line)

    full = "\n".join(chunks)
    assert "data: [DONE]" in full


@pytest.mark.asyncio
async def test_chat_stream_has_x_session_header(client):
    """El header X-Session-Id debe estar presente en respuestas SSE."""
    async with client as c:
        r = await c.post("/api/v1/chat/", json={"message": "ping", "stream": True})
    assert "x-session-id" in r.headers
