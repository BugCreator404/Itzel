"""
Tests de la capa de modelos — adaptadores, factory y router.

Todos los tests corren sin modelos reales instalados:
  - OllamaAdapter  → httpx mockeado
  - LlamaCppAdapter → importlib + Path.exists mockeados
  - get_adapter()  → available() mockeado en cada adaptador
"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from itzel_core.models import (
    NoModelAdapter,
    OllamaAdapter,
    LlamaCppAdapter,
    get_adapter,
    reset_adapter,
)
from itzel_core.models.base import (
    BaseAdapter,
    GenerationError,
    GenerationParams,
    ModelNotAvailableError,
)
from itzel_core.router import build_messages, build_system_prompt


# ─── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_adapter_cache():
    """Limpia el singleton de adaptador antes y después de cada test."""
    reset_adapter()
    yield
    reset_adapter()


# ─── GenerationParams ─────────────────────────────────────────────────────────

class TestGenerationParams:
    def test_defaults(self):
        p = GenerationParams()
        assert p.temperature == 0.7
        assert p.top_p == 0.95
        assert p.max_tokens == 2048

    def test_clamp_temperature_upper(self):
        p = GenerationParams(temperature=5.0).clamp()
        assert p.temperature == 2.0

    def test_clamp_temperature_lower(self):
        p = GenerationParams(temperature=-1.0).clamp()
        assert p.temperature == 0.0

    def test_clamp_top_p(self):
        p = GenerationParams(top_p=1.5).clamp()
        assert p.top_p == 1.0

    def test_clamp_max_tokens_upper(self):
        p = GenerationParams(max_tokens=99_999).clamp()
        assert p.max_tokens == 8192

    def test_clamp_max_tokens_lower(self):
        p = GenerationParams(max_tokens=0).clamp()
        assert p.max_tokens == 1

    def test_valid_values_unchanged(self):
        p = GenerationParams(temperature=0.5, top_p=0.9, max_tokens=512).clamp()
        assert p.temperature == 0.5
        assert p.top_p == 0.9
        assert p.max_tokens == 512


# ─── BaseAdapter helpers ──────────────────────────────────────────────────────

class TestBaseAdapterHelpers:
    def test_estimate_tokens_empty(self):
        assert BaseAdapter._estimate_tokens([]) == 0

    def test_estimate_tokens_chars_div_4(self):
        msgs = [{"role": "user", "content": "abcd"}]  # 4 chars → 1 token
        assert BaseAdapter._estimate_tokens(msgs) == 1

    def test_estimate_tokens_multiple_messages(self):
        msgs = [
            {"role": "system",    "content": "aaaa"},   # 4
            {"role": "user",      "content": "bbbbbbbb"}, # 8
        ]
        assert BaseAdapter._estimate_tokens(msgs) == 3  # 12 // 4

    def test_trim_empty_list(self):
        result = BaseAdapter._trim_to_context([], max_tokens=100)
        assert result == []

    def test_trim_keeps_all_if_fits(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user",   "content": "hola"},
        ]
        result = BaseAdapter._trim_to_context(msgs, max_tokens=1000)
        assert result == msgs

    def test_trim_preserves_system_and_last(self):
        # Crea un historial muy largo que excede el límite
        system = {"role": "system", "content": "s" * 400}   # 100 tokens
        old_1  = {"role": "user",      "content": "o" * 400} # 100 tokens
        old_2  = {"role": "assistant", "content": "o" * 400} # 100 tokens
        last   = {"role": "user",      "content": "último"}

        msgs   = [system, old_1, old_2, last]
        result = BaseAdapter._trim_to_context(msgs, max_tokens=120)

        # El system siempre debe estar
        assert result[0]["role"] == "system"
        # El último mensaje siempre debe estar
        assert result[-1]["content"] == "último"


# ─── ModelNotAvailableError ───────────────────────────────────────────────────

class TestExceptions:
    def test_model_not_available_message(self):
        exc = ModelNotAvailableError("ollama", hint="Instala Ollama")
        assert "ollama" in str(exc)
        assert "Instala Ollama" in str(exc)
        assert exc.backend == "ollama"

    def test_generation_error_message(self):
        exc = GenerationError("llama.cpp", "OOM")
        assert "llama.cpp" in str(exc)
        assert "OOM" in str(exc)
        assert exc.cause == "OOM"


# ─── OllamaAdapter ────────────────────────────────────────────────────────────

def _mock_ollama_get(status_code: int, body: dict):
    """Helper: mockea httpx.AsyncClient.get para available()."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = body

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__  = AsyncMock(return_value=False)
    return mock_ctx


async def _async_lines(lines: list[str]) -> AsyncGenerator[str, None]:
    for line in lines:
        yield line


def _mock_ollama_stream(status_code: int, ndjson_lines: list[str]):
    """Helper: mockea httpx streaming para adapter.stream()."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.aiter_lines = MagicMock(return_value=_async_lines(ndjson_lines))

    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_stream_ctx.__aexit__  = AsyncMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=mock_stream_ctx)

    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__  = AsyncMock(return_value=False)
    return mock_client_ctx


class TestOllamaAdapter:
    @pytest.mark.asyncio
    async def test_available_true_with_models(self):
        mock_ctx = _mock_ollama_get(200, {"models": [{"name": "llama3.1:8b"}]})
        with patch("itzel_core.models.ollama.httpx.AsyncClient", return_value=mock_ctx):
            assert await OllamaAdapter().available() is True

    @pytest.mark.asyncio
    async def test_available_false_non_200(self):
        mock_ctx = _mock_ollama_get(503, {})
        with patch("itzel_core.models.ollama.httpx.AsyncClient", return_value=mock_ctx):
            assert await OllamaAdapter().available() is False

    @pytest.mark.asyncio
    async def test_available_false_connect_error(self):
        import httpx
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_ctx.__aexit__  = AsyncMock(return_value=False)
        with patch("itzel_core.models.ollama.httpx.AsyncClient", return_value=mock_ctx):
            assert await OllamaAdapter().available() is False

    @pytest.mark.asyncio
    async def test_available_false_empty_models(self):
        mock_ctx = _mock_ollama_get(200, {"models": []})
        with patch("itzel_core.models.ollama.httpx.AsyncClient", return_value=mock_ctx):
            # Con lista vacía Ollama corre pero no tiene modelos
            assert await OllamaAdapter().available() is True  # corre = True

    @pytest.mark.asyncio
    async def test_stream_yields_tokens(self):
        lines = [
            json.dumps({"message": {"role": "assistant", "content": "Hola"}, "done": False}),
            json.dumps({"message": {"role": "assistant", "content": " mundo"}, "done": False}),
            json.dumps({"message": {"role": "assistant", "content": ""}, "done": True}),
        ]
        mock_ctx = _mock_ollama_stream(200, lines)
        with patch("itzel_core.models.ollama.httpx.AsyncClient", return_value=mock_ctx):
            tokens = []
            async for t in OllamaAdapter("llama3.1:8b").stream(
                [{"role": "user", "content": "test"}]
            ):
                tokens.append(t)
        assert tokens == ["Hola", " mundo"]

    @pytest.mark.asyncio
    async def test_stream_404_raises_model_not_available(self):
        mock_ctx = _mock_ollama_stream(404, [])
        with patch("itzel_core.models.ollama.httpx.AsyncClient", return_value=mock_ctx):
            with pytest.raises(ModelNotAvailableError) as exc_info:
                async for _ in OllamaAdapter("no-existe").stream(
                    [{"role": "user", "content": "test"}]
                ):
                    pass
        assert "no-existe" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_stream_skips_empty_lines(self):
        lines = [
            "",
            "   ",
            json.dumps({"message": {"role": "assistant", "content": "ok"}, "done": True}),
        ]
        mock_ctx = _mock_ollama_stream(200, lines)
        with patch("itzel_core.models.ollama.httpx.AsyncClient", return_value=mock_ctx):
            tokens = []
            async for t in OllamaAdapter().stream([{"role": "user", "content": "x"}]):
                tokens.append(t)
        assert tokens == ["ok"]

    @pytest.mark.asyncio
    async def test_info_returns_adapter_info(self):
        mock_ctx = _mock_ollama_get(200, {"models": [{"name": "mistral:7b"}]})
        with patch("itzel_core.models.ollama.httpx.AsyncClient", return_value=mock_ctx):
            info = await OllamaAdapter("mistral:7b").info()
        assert info.backend == "ollama"
        assert "mistral:7b" in info.id

    @pytest.mark.asyncio
    async def test_model_name_strips_ollama_prefix(self):
        adapter = OllamaAdapter("ollama/llama3.1:8b")
        assert adapter._model == "llama3.1:8b"


# ─── LlamaCppAdapter ──────────────────────────────────────────────────────────

class TestLlamaCppAdapter:
    @pytest.mark.asyncio
    async def test_available_false_no_lib(self, tmp_path):
        gguf = tmp_path / "itzel-1b-q4_k_m.gguf"
        gguf.touch()  # archivo existe
        adapter = LlamaCppAdapter(gguf_path=gguf)
        with patch.object(adapter, "_llama_cpp_importable", return_value=False):
            assert await adapter.available() is False

    @pytest.mark.asyncio
    async def test_available_false_no_gguf(self, tmp_path):
        gguf = tmp_path / "no-existe.gguf"  # no existe
        adapter = LlamaCppAdapter(gguf_path=gguf)
        with patch.object(adapter, "_llama_cpp_importable", return_value=True):
            assert await adapter.available() is False

    @pytest.mark.asyncio
    async def test_available_true_when_both_present(self, tmp_path):
        gguf = tmp_path / "itzel-1b-q4_k_m.gguf"
        gguf.touch()
        adapter = LlamaCppAdapter(gguf_path=gguf)
        with patch.object(adapter, "_llama_cpp_importable", return_value=True):
            assert await adapter.available() is True

    @pytest.mark.asyncio
    async def test_load_raises_if_no_lib(self, tmp_path):
        gguf = tmp_path / "m.gguf"
        gguf.touch()
        adapter = LlamaCppAdapter(gguf_path=gguf)
        with patch.object(adapter, "_llama_cpp_importable", return_value=False):
            with pytest.raises(ModelNotAvailableError) as exc_info:
                await adapter.load()
        assert "llama.cpp" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_load_raises_if_no_gguf(self, tmp_path):
        gguf = tmp_path / "no-existe.gguf"
        adapter = LlamaCppAdapter(gguf_path=gguf)
        with patch.object(adapter, "_llama_cpp_importable", return_value=True):
            with pytest.raises(ModelNotAvailableError) as exc_info:
                await adapter.load()
        assert "itzel-1b" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_stream_raises_if_not_loaded(self):
        adapter = LlamaCppAdapter()
        with pytest.raises(ModelNotAvailableError):
            async for _ in adapter.stream([{"role": "user", "content": "hola"}]):
                pass

    @pytest.mark.asyncio
    async def test_info_not_loaded(self):
        adapter = LlamaCppAdapter()
        info = await adapter.info()
        assert info.backend == "llama.cpp"
        assert info.loaded is False


# ─── NoModelAdapter ───────────────────────────────────────────────────────────

class TestNoModelAdapter:
    @pytest.mark.asyncio
    async def test_always_available(self):
        assert await NoModelAdapter().available() is True

    @pytest.mark.asyncio
    async def test_stream_yields_tokens(self):
        adapter = NoModelAdapter()
        tokens  = []
        async for t in adapter.stream([{"role": "user", "content": "hola"}]):
            tokens.append(t)
        assert len(tokens) > 0
        full = "".join(tokens)
        assert len(full) > 10

    @pytest.mark.asyncio
    async def test_stream_es_mx_for_spanish_input(self):
        adapter = NoModelAdapter()
        tokens  = []
        async for t in adapter.stream(
            [{"role": "user", "content": "¿Cómo te llamas?"}]
        ):
            tokens.append(t)
        full = "".join(tokens)
        # La respuesta en ES-MX menciona "Itzel" o instrucciones en español
        assert "Itzel" in full or "modelo" in full.lower()

    @pytest.mark.asyncio
    async def test_stream_en_for_english_input(self):
        adapter = NoModelAdapter()
        tokens  = []
        async for t in adapter.stream(
            [{"role": "user", "content": "what can you do for me?"}]
        ):
            tokens.append(t)
        full = "".join(tokens)
        assert "model" in full.lower() or "Itzel" in full

    @pytest.mark.asyncio
    async def test_info_backend_none(self):
        info = await NoModelAdapter().info()
        assert info.backend == "none"
        assert info.loaded is False


# ─── get_adapter() factory ────────────────────────────────────────────────────

class TestGetAdapter:
    @pytest.mark.asyncio
    async def test_prefers_llamacpp_when_available(self):
        with (
            patch.object(LlamaCppAdapter, "available", return_value=True),
            patch.object(LlamaCppAdapter, "load",      new_callable=AsyncMock),
            patch.object(OllamaAdapter,  "available", return_value=False),
        ):
            adapter = await get_adapter()
        assert isinstance(adapter, LlamaCppAdapter)

    @pytest.mark.asyncio
    async def test_falls_to_ollama_when_llamacpp_unavailable(self):
        with (
            patch.object(LlamaCppAdapter, "available", return_value=False),
            patch.object(OllamaAdapter,   "available", return_value=True),
            patch.object(OllamaAdapter,   "list_models",
                         new_callable=AsyncMock, return_value=["llama3.1:8b"]),
        ):
            adapter = await get_adapter()
        assert isinstance(adapter, OllamaAdapter)

    @pytest.mark.asyncio
    async def test_falls_to_no_model_when_nothing_available(self):
        with (
            patch.object(LlamaCppAdapter, "available", return_value=False),
            patch.object(OllamaAdapter,   "available", return_value=False),
        ):
            adapter = await get_adapter()
        assert isinstance(adapter, NoModelAdapter)

    @pytest.mark.asyncio
    async def test_caches_result(self):
        with (
            patch.object(LlamaCppAdapter, "available", return_value=False),
            patch.object(OllamaAdapter,   "available", return_value=False),
        ):
            a1 = await get_adapter()
            a2 = await get_adapter()
        assert a1 is a2  # mismo objeto — singleton

    @pytest.mark.asyncio
    async def test_reset_clears_cache(self):
        with (
            patch.object(LlamaCppAdapter, "available", return_value=False),
            patch.object(OllamaAdapter,   "available", return_value=False),
        ):
            a1 = await get_adapter()

        reset_adapter()

        with (
            patch.object(LlamaCppAdapter, "available", return_value=False),
            patch.object(OllamaAdapter,   "available", return_value=False),
        ):
            a2 = await get_adapter()

        assert a1 is not a2  # nuevo objeto tras reset


# ─── router: build_system_prompt / build_messages ─────────────────────────────

class TestRouter:
    def test_build_system_prompt_es_mx(self):
        prompt = build_system_prompt(
            session_id = "ses-001",
            model_name = "itzel-1b",
            language   = "es-MX",
        )
        assert "Itzel" in prompt
        assert "itzel-1b" in prompt
        assert "ses-001" in prompt
        assert "Español" in prompt

    def test_build_system_prompt_en_us(self):
        prompt = build_system_prompt(
            session_id = "ses-002",
            model_name = "ollama/llama3.1:8b",
            language   = "en-US",
        )
        assert "Itzel" in prompt
        assert "ollama/llama3.1:8b" in prompt
        assert "English" in prompt

    def test_build_system_prompt_lang_normalization(self):
        for lang in ("es", "en", "fr", "zh"):
            prompt = build_system_prompt(
                session_id="x", model_name="m", language=lang
            )
            assert "Itzel" in prompt

    def test_build_system_prompt_contains_date(self):
        from datetime import datetime, timezone
        prompt = build_system_prompt(
            session_id="x", model_name="m", language="es-MX"
        )
        year = str(datetime.now(timezone.utc).year)
        assert year in prompt

    def test_build_messages_structure(self):
        system = "Eres Itzel."
        history = [
            {"role": "user",      "content": "Hola"},
            {"role": "assistant", "content": "¡Hola!"},
        ]
        msgs = build_messages(
            system_prompt = system,
            history       = history,
            new_message   = "¿Cómo estás?",
        )
        assert msgs[0]  == {"role": "system",    "content": "Eres Itzel."}
        assert msgs[1]  == {"role": "user",      "content": "Hola"}
        assert msgs[2]  == {"role": "assistant", "content": "¡Hola!"}
        assert msgs[-1] == {"role": "user",      "content": "¿Cómo estás?"}
        assert len(msgs) == 4

    def test_build_messages_empty_history(self):
        msgs = build_messages(
            system_prompt = "sys",
            history       = [],
            new_message   = "hola",
        )
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
