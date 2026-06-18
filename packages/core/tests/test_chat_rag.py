"""Tests del aumento RAG en /chat (Sesión 17).

Cubre los tres comportamientos del cableado RAG ↔ chat:

  1. _maybe_augment_with_rag — degrada solo si el RAG no está / falla, e
     inyecta contexto + fuentes cuando sí está disponible.
  2. auto_context=True  → el stream SSE emite el frame [SOURCES] antes de [DONE].
  3. auto_context=False → el RAG ni se toca (comportamiento de chat intacto).

Todo mockeado: ni chromadb real, ni modelo, ni BD del usuario.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

import itzel_core.api.v1.chat as chat_mod
from itzel_core.config import config
from itzel_core.engine import app

# ─── 1. helper _maybe_augment_with_rag ──────────────────────────────────────

def test_augment_skips_when_rag_unavailable(monkeypatch):
    """Sin deps [rag], el helper devuelve los mensajes intactos y sin fuentes."""
    import itzel_core.rag as rag_mod
    monkeypatch.setattr(rag_mod, "check_rag_available", lambda: (False, ["chromadb"]))

    msgs = [{"role": "user", "content": "hola"}]
    out, sources = chat_mod._maybe_augment_with_rag(msgs, "es-MX")

    assert out is msgs
    assert sources == []


def test_augment_degrades_on_exception(monkeypatch):
    """Si el pipeline RAG lanza, el chat sigue normal (mensajes intactos, [] fuentes)."""
    import itzel_core.rag as rag_mod
    import itzel_core.rag.pipeline as pipe_mod
    monkeypatch.setattr(rag_mod, "check_rag_available", lambda: (True, []))

    def _boom():
        raise RuntimeError("retriever caído")

    monkeypatch.setattr(pipe_mod, "get_pipeline", _boom)

    msgs = [{"role": "user", "content": "hola"}]
    out, sources = chat_mod._maybe_augment_with_rag(msgs, "es-MX")

    assert out is msgs       # nunca rompe el chat
    assert sources == []


def test_augment_injects_context_and_sources(monkeypatch):
    """Con RAG disponible, inyecta los mensajes aumentados y devuelve las fuentes."""
    import itzel_core.rag as rag_mod
    import itzel_core.rag.pipeline as pipe_mod
    monkeypatch.setattr(rag_mod, "check_rag_available", lambda: (True, []))

    augmented = [{"role": "user", "content": "Contexto…\n\nPregunta: hola"}]
    fake_ctx = SimpleNamespace(
        sources=[{"n": 1, "source": "/docs/a.md", "filename": "a.md"}]
    )
    fake_pipeline = MagicMock()
    fake_pipeline.augment_messages.return_value = (augmented, fake_ctx)
    monkeypatch.setattr(pipe_mod, "get_pipeline", lambda: fake_pipeline)

    msgs = [{"role": "user", "content": "hola"}]
    out, sources = chat_mod._maybe_augment_with_rag(msgs, "es-MX")

    assert out == augmented
    assert sources == [{"n": 1, "source": "/docs/a.md", "filename": "a.md"}]
    fake_pipeline.augment_messages.assert_called_once()


# ─── 2 y 3. emisión SSE [SOURCES] en /chat ──────────────────────────────────

class _FakeAdapter:
    """Adaptador determinista: stream con éxito (la rama donde se emite [SOURCES])."""

    async def info(self):
        return SimpleNamespace(id="test-model")

    async def stream(self, messages, params):
        for t in ["Hola", " mundo"]:
            yield t


@pytest.fixture
def fake_chat(monkeypatch):
    """Aísla el chat: adaptador falso + memoria mockeada (sin modelo ni BD real)."""
    async def _get_adapter():
        return _FakeAdapter()

    monkeypatch.setattr(chat_mod, "get_adapter", _get_adapter)

    mem = MagicMock()
    mem.get_session.return_value = []
    monkeypatch.setattr(chat_mod, "_memory", mem)
    return mem


async def _collect_sse(message: str) -> str:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    lines: list[str] = []
    async with client as c:
        async with c.stream(
            "POST", "/api/v1/chat/", json={"message": message, "stream": True},
        ) as r:
            assert r.status_code == 200
            async for line in r.aiter_lines():
                lines.append(line)
    return "\n".join(lines)


@pytest.mark.asyncio
async def test_chat_emits_sources_when_auto_context(monkeypatch, fake_chat):
    """Con auto_context=True, el stream emite [SOURCES] (con el archivo) antes de [DONE]."""
    monkeypatch.setattr(config.rag, "enabled", True)
    monkeypatch.setattr(config.rag, "auto_context", True)

    sources = [{"n": 1, "source": "/docs/agenda.md", "filename": "agenda.md"}]
    monkeypatch.setattr(
        chat_mod, "_maybe_augment_with_rag",
        lambda messages, language: (messages, sources),
    )

    full = await _collect_sse("¿qué tengo el lunes?")

    assert "data: [SOURCES]" in full
    assert "agenda.md" in full
    assert full.index("[SOURCES]") < full.index("[DONE]")   # va antes de cerrar


@pytest.mark.asyncio
async def test_chat_no_rag_when_auto_context_off(monkeypatch, fake_chat):
    """Con auto_context=False, el RAG no se invoca y no hay frame [SOURCES]."""
    monkeypatch.setattr(config.rag, "auto_context", False)

    spy = MagicMock()
    monkeypatch.setattr(chat_mod, "_maybe_augment_with_rag", spy)

    full = await _collect_sse("hola")

    spy.assert_not_called()            # el RAG ni se tocó
    assert "[SOURCES]" not in full     # sin fuentes
    assert "data: [DONE]" in full      # pero el chat completó normal
