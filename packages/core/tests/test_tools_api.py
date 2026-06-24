"""Tests del endpoint /api/v1/tools (catálogo de herramientas, read-only)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from itzel_core.engine import app


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_tools_list_shape(client):
    """GET /tools devuelve el catálogo con conteo por fuente y schemas válidos."""
    async with client as c:
        r = await c.get("/api/v1/tools")

    assert r.status_code == 200
    data = r.json()
    assert {"tools", "total", "by_source"} <= data.keys()
    assert data["total"] == len(data["tools"])

    for t in data["tools"]:
        assert t["name"]
        assert "source" in t
        assert "input_schema" in t

    # itzel-agents / itzel-mcp no instalados en el entorno de test:
    assert all(not t["source"].startswith("agent:") for t in data["tools"])
    assert all(not t["source"].startswith("mcp:") for t in data["tools"])


@pytest.mark.asyncio
async def test_tools_by_source_counts_match(client):
    """by_source suma exactamente el total de tools."""
    async with client as c:
        r = await c.get("/api/v1/tools")
    data = r.json()
    assert sum(data["by_source"].values()) == data["total"]
