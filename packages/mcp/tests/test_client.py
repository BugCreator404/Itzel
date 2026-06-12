"""
Tests del cliente MCP (itzel_mcp.client) con servidor mockeado.

No requieren el SDK `mcp` real ni red: se instala un módulo `mcp` falso en
sys.modules (mismo patrón que los tests de voz con sounddevice). Eso ejercita
la maquinaria REAL del cliente: thread + loop dedicado, AsyncExitStack,
handshake, cache de tools, proxy de tools y cierre limpio.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from itzel_core.config import MCPServerConfig
from itzel_core.tools import ToolRegistry

import itzel_mcp.client as client_mod
from itzel_mcp import MCPClient, MCPConnectionError, MCPProxyTool, connect_all


# ─── servidor MCP falso ───────────────────────────────────────────────────────

class FakeSession:
    """Simula mcp.ClientSession — registra llamadas para los asserts."""

    last: "FakeSession | None" = None

    def __init__(self, read, write) -> None:
        self.initialized = False
        self.calls: list[tuple[str, dict]] = []
        self.closed = False
        FakeSession.last = self

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc) -> None:
        self.closed = True

    async def initialize(self) -> None:
        self.initialized = True

    async def list_tools(self):
        return SimpleNamespace(tools=[
            SimpleNamespace(
                name="get_weather",
                description="Clima de una ciudad",
                inputSchema={
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            ),
            SimpleNamespace(
                name="send_mail",
                description="",
                inputSchema=None,
            ),
        ])

    async def call_tool(self, name: str, args: dict):
        self.calls.append((name, args))
        if name == "explota":
            return SimpleNamespace(
                isError=True,
                content=[SimpleNamespace(text="server boom")],
            )
        return SimpleNamespace(
            isError=False,
            content=[
                SimpleNamespace(text=f"{name} ok"),
                SimpleNamespace(text=str(args)),
            ],
        )


class _FakeTransport:
    """Context manager async que entrega el par (read, write)."""

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self):
        return ("fake-read", "fake-write")

    async def __aexit__(self, *exc) -> None:
        pass


def _record_factory(store: dict):
    """Crea un transport falso que registra cómo fue invocado."""
    def factory(*args, **kwargs):
        store["args"] = args
        store["kwargs"] = kwargs
        return _FakeTransport()
    return factory


@pytest.fixture
def fake_mcp(monkeypatch):
    """Instala un SDK `mcp` falso en sys.modules. Devuelve el registro de transports."""
    record: dict = {"stdio": {}, "sse": {}}

    mcp_mod = types.ModuleType("mcp")
    mcp_mod.ClientSession = FakeSession
    mcp_mod.StdioServerParameters = lambda command, args: SimpleNamespace(
        command=command, args=args
    )

    client_pkg = types.ModuleType("mcp.client")
    stdio_mod  = types.ModuleType("mcp.client.stdio")
    sse_mod    = types.ModuleType("mcp.client.sse")
    stdio_mod.stdio_client = _record_factory(record["stdio"])
    sse_mod.sse_client     = _record_factory(record["sse"])

    monkeypatch.setitem(sys.modules, "mcp", mcp_mod)
    monkeypatch.setitem(sys.modules, "mcp.client", client_pkg)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio_mod)
    monkeypatch.setitem(sys.modules, "mcp.client.sse", sse_mod)

    FakeSession.last = None
    return record


def _stdio_config(name: str = "demo") -> MCPServerConfig:
    return MCPServerConfig(
        name=name, transport="stdio", command="fake-server", args=["--x"]
    )


# ─── conexión ─────────────────────────────────────────────────────────────────

class TestConnection:
    def test_connect_does_handshake(self, fake_mcp):
        with MCPClient(_stdio_config()) as client:
            assert client.is_connected
            assert FakeSession.last.initialized is True

    def test_close_is_clean_and_idempotent(self, fake_mcp):
        client = MCPClient(_stdio_config())
        client.connect()
        thread = client._thread
        client.close()
        assert client.is_connected is False
        assert not thread.is_alive()
        client.close()  # segunda vez: no lanza

    def test_stdio_passes_command(self, fake_mcp):
        with MCPClient(_stdio_config()):
            params = fake_mcp["stdio"]["args"][0]
            assert params.command == "fake-server"
            assert params.args == ["--x"]

    def test_sse_passes_url(self, fake_mcp):
        cfg = MCPServerConfig(name="remoto", transport="sse", url="https://mcp.x/sse")
        with MCPClient(cfg):
            assert fake_mcp["sse"]["args"] == ("https://mcp.x/sse",)

    def test_stdio_without_command_fails(self, fake_mcp):
        cfg = MCPServerConfig(name="malo", transport="stdio")  # sin command
        with pytest.raises(MCPConnectionError):
            MCPClient(cfg).connect()

    def test_call_without_connect_fails(self):
        client = MCPClient(_stdio_config())
        with pytest.raises(MCPConnectionError):
            client.call_tool("x", {})


# ─── autenticación ────────────────────────────────────────────────────────────

class TestAuth:
    def test_api_key_sent_as_bearer(self, fake_mcp, monkeypatch):
        monkeypatch.setattr(client_mod, "get_mcp_api_key", lambda name: "secreta-123")
        cfg = MCPServerConfig(
            name="gmail", transport="sse", url="https://mcp.x/sse", auth="api_key"
        )
        with MCPClient(cfg):
            headers = fake_mcp["sse"]["kwargs"]["headers"]
            assert headers == {"Authorization": "Bearer secreta-123"}

    def test_missing_api_key_clear_error(self, fake_mcp, monkeypatch):
        monkeypatch.setattr(client_mod, "get_mcp_api_key", lambda name: None)
        cfg = MCPServerConfig(
            name="gmail", transport="sse", url="https://mcp.x/sse", auth="api_key"
        )
        with pytest.raises(MCPConnectionError, match="keychain"):
            MCPClient(cfg).connect()

    def test_no_auth_no_headers(self, fake_mcp):
        cfg = MCPServerConfig(name="libre", transport="sse", url="https://mcp.x/sse")
        with MCPClient(cfg):
            assert fake_mcp["sse"]["kwargs"]["headers"] is None


# ─── descubrimiento y llamadas ────────────────────────────────────────────────

class TestTools:
    def test_list_tools_discovers(self, fake_mcp):
        with MCPClient(_stdio_config()) as client:
            tools = client.list_tools()
            assert [t["name"] for t in tools] == ["get_weather", "send_mail"]
            assert tools[0]["input_schema"]["required"] == ["city"]

    def test_list_tools_cached_until_refresh(self, fake_mcp):
        with MCPClient(_stdio_config()) as client:
            first  = client.list_tools()
            second = client.list_tools()
            assert second is first              # cache
            third = client.list_tools(refresh=True)
            assert third is not first

    def test_call_tool_returns_text(self, fake_mcp):
        with MCPClient(_stdio_config()) as client:
            out = client.call_tool("get_weather", {"city": "Tijuana"})
            assert "get_weather ok" in out
            assert FakeSession.last.calls == [("get_weather", {"city": "Tijuana"})]

    def test_call_tool_server_error_raises(self, fake_mcp):
        with MCPClient(_stdio_config()) as client:
            with pytest.raises(RuntimeError, match="server boom"):
                client.call_tool("explota", {})

    def test_ping(self, fake_mcp):
        with MCPClient(_stdio_config()) as client:
            assert client.ping() is True


# ─── exposición como Tools del engine ─────────────────────────────────────────

class TestProxyTools:
    def test_as_tools_wraps_all(self, fake_mcp):
        with MCPClient(_stdio_config(name="srv")) as client:
            tools = client.as_tools()
            assert [t.name for t in tools] == [
                "mcp-srv-get_weather", "mcp-srv-send_mail",
            ]
            assert all(isinstance(t, MCPProxyTool) for t in tools)

    def test_proxy_uses_server_schema(self, fake_mcp):
        with MCPClient(_stdio_config(name="srv")) as client:
            schema = client.as_tools()[0].to_schema()
            # Schema del servidor, no inferido de la firma del proxy
            assert schema["input_schema"]["properties"] == {"city": {"type": "string"}}

    def test_proxy_invocable_via_registry(self, fake_mcp):
        """Integración: la tool MCP se invoca por el ToolRegistry real."""
        with MCPClient(_stdio_config(name="srv")) as client:
            registry = ToolRegistry(agent_name="mcp")
            registry.register_all(client.as_tools())
            result = registry.invoke("mcp-srv-get_weather", {"city": "Tijuana"})
            assert result.ok is True
            assert "get_weather ok" in result.value
            registry.shutdown()


# ─── connect_all ──────────────────────────────────────────────────────────────

class TestConnectAll:
    def test_connects_enabled_skips_disabled(self, fake_mcp):
        configs = [
            _stdio_config("uno"),
            MCPServerConfig(name="apagado", transport="stdio",
                            command="x", enabled=False),
        ]
        clients, errors = connect_all(configs)
        assert list(clients) == ["uno"]
        assert errors == {}
        clients["uno"].close()

    def test_failure_does_not_block_others(self, fake_mcp):
        configs = [
            MCPServerConfig(name="roto", transport="stdio"),   # sin command → falla
            _stdio_config("sano"),
        ]
        clients, errors = connect_all(configs)
        assert "sano" in clients
        assert "roto" in errors
        clients["sano"].close()
