"""Tests de los flags del RAG vía la API (/api/v1/rag/config y /status).

Solo el toggle de configuración (enabled / auto_context): no necesita chromadb
ni índice real, solo verifica que el endpoint cambia config.rag y persiste.
`save_config` se mockea para no escribir en el disco del usuario.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

import itzel_core.api.v1.rag as rag_api
from itzel_core.config import config
from itzel_core.engine import app


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _no_disk_writes(monkeypatch):
    """El endpoint llama save_config(); lo mockeamos para no tocar el disco."""
    monkeypatch.setattr(rag_api, "save_config", lambda: None)


@pytest.mark.asyncio
async def test_config_toggles_auto_context(client, monkeypatch):
    """POST /rag/config {auto_context:true} enciende el auto-contexto y persiste."""
    monkeypatch.setattr(config.rag, "auto_context", False)

    async with client as c:
        r = await c.post("/api/v1/rag/config", json={"auto_context": True})

    assert r.status_code == 200
    assert r.json()["auto_context"] is True
    assert config.rag.auto_context is True   # el flag quedó cambiado


@pytest.mark.asyncio
async def test_config_can_enable_even_when_disabled(client, monkeypatch):
    """Se puede ENCENDER el RAG aunque estuviera apagado (sin guard 503)."""
    monkeypatch.setattr(config.rag, "enabled", False)

    async with client as c:
        r = await c.post("/api/v1/rag/config", json={"enabled": True})

    assert r.status_code == 200
    assert r.json()["enabled"] is True
    assert config.rag.enabled is True


@pytest.mark.asyncio
async def test_config_only_applies_present_fields(client, monkeypatch):
    """Un body sin auto_context no toca ese flag (solo aplica lo presente)."""
    monkeypatch.setattr(config.rag, "enabled", True)
    monkeypatch.setattr(config.rag, "auto_context", True)

    async with client as c:
        r = await c.post("/api/v1/rag/config", json={"enabled": False})

    assert r.status_code == 200
    assert config.rag.enabled is False
    assert config.rag.auto_context is True   # intacto
