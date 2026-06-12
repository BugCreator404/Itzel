"""
Cliente MCP — conecta servidores Model Context Protocol a Itzel.

Un servidor MCP expone tools (y resources) por un protocolo JSON-RPC. Este
cliente las autodescubre y las envuelve como Tools de itzel_core, de modo que
el LLM las invoca por el mismo ToolRegistry que los agentes y las skills.

Arquitectura (puente sync ↔ async):
    El SDK oficial `mcp` es asíncrono, pero el ToolRegistry de Itzel es
    síncrono. MCPClient corre un event loop propio en un thread daemon y
    mantiene la sesión MCP viva (el handshake initialize se hace una vez).
    Cada list_tools()/call_tool() se despacha a ese loop con timeout.

Transports:
    stdio — lanza el servidor como subproceso local (command + args).
    sse   — se conecta a un servidor HTTP remoto (url).

Autenticación (principio #8: credenciales en el keychain del OS):
    auth="api_key" → la key se lee del keychain (servicio "itzel",
    usuario "mcp-<name>") y se envía como header Authorization: Bearer.
    La key NUNCA vive en itzel.config.json.
    OAuth NO está implementado aún — requiere flujo de navegador + refresh
    tokens; queda documentado como limitación (la config solo admite
    none | api_key, así que no hay forma silenciosa de pedirlo).

English summary:
    Sync facade over the official async `mcp` SDK: dedicated loop thread,
    persistent session, stdio/SSE transports, keychain-backed API keys, and
    MCP tools exposed as itzel_core Tool objects (server-provided schemas).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import AsyncExitStack
from typing import Any, Optional

from itzel_core.config import MCPServerConfig
from itzel_core.tools import Tool

log = logging.getLogger("itzel.mcp")

#: Timeout por defecto para operaciones MCP (segundos).
_DEFAULT_TIMEOUT = 30.0
#: Timeout del handshake de conexión.
_CONNECT_TIMEOUT = 15.0

_KEY_SERVICE = "itzel"


# ─── errores ──────────────────────────────────────────────────────────────────

class MCPNotAvailableError(RuntimeError):
    """El SDK `mcp` no está instalado."""

    def __init__(self) -> None:
        super().__init__(
            "El SDK de MCP no está instalado. "
            "Instala con: pip install -e packages/mcp"
        )


class MCPConnectionError(RuntimeError):
    """No se pudo conectar o la sesión murió."""


# ─── API keys en el keychain ──────────────────────────────────────────────────

def get_mcp_api_key(server_name: str) -> Optional[str]:
    """Lee la API key de un servidor MCP desde el keychain del OS."""
    try:
        import keyring
        return keyring.get_password(_KEY_SERVICE, f"mcp-{server_name}")
    except Exception:
        return None


def set_mcp_api_key(server_name: str, api_key: str) -> bool:
    """Guarda la API key de un servidor MCP en el keychain. True si se guardó."""
    try:
        import keyring
        keyring.set_password(_KEY_SERVICE, f"mcp-{server_name}", api_key)
        return True
    except Exception as exc:
        log.warning("No se pudo guardar la key en el keychain: %s", exc)
        return False


# ─── tool proxy ───────────────────────────────────────────────────────────────

class MCPProxyTool(Tool):
    """
    Tool que reenvía la invocación a un servidor MCP.

    A diferencia de las tools locales (schema inferido de la firma), aquí el
    schema lo publica el servidor — to_schema() lo devuelve tal cual.
    """

    def __init__(
        self,
        client:       "MCPClient",
        remote_name:  str,
        description:  str,
        input_schema: dict[str, Any],
    ) -> None:
        def _proxy(**kwargs: Any) -> str:
            return client.call_tool(remote_name, kwargs)

        super().__init__(
            _proxy,
            name        = f"mcp-{client.config.name}-{remote_name}",
            description = description or f"Tool '{remote_name}' del servidor MCP '{client.config.name}'.",
            timeout     = int(_DEFAULT_TIMEOUT),
        )
        self.remote_name   = remote_name
        self._input_schema = input_schema or {"type": "object", "properties": {}}

    def to_schema(self) -> dict[str, Any]:
        return {
            "name":         self.name,
            "description":  self.description,
            "input_schema": self._input_schema,
        }


# ─── cliente ──────────────────────────────────────────────────────────────────

class MCPClient:
    """
    Conexión a un servidor MCP (stdio o SSE).

    Uso:
        from itzel_core.config import MCPServerConfig

        client = MCPClient(MCPServerConfig(
            name="files", transport="stdio",
            command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        ))
        client.connect()
        tools = client.as_tools()        # → registrar en el ToolRegistry
        out   = client.call_tool("read_file", {"path": "/tmp/x.txt"})
        client.close()
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._loop:    Optional[asyncio.AbstractEventLoop] = None
        self._thread:  Optional[threading.Thread] = None
        self._stack:   Optional[AsyncExitStack] = None
        self._session: Any = None
        self._tools_cache: Optional[list[dict[str, Any]]] = None

    # ── ciclo de vida ─────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    def connect(self) -> None:
        """
        Abre la conexión y hace el handshake MCP.

        Raises:
            MCPNotAvailableError: si el SDK no está instalado.
            MCPConnectionError:   si la conexión o el handshake fallan.
        """
        if self.is_connected:
            return
        try:
            import mcp  # noqa: F401 — solo verificación temprana
        except ImportError as exc:
            raise MCPNotAvailableError() from exc

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            name=f"itzel-mcp-{self.config.name}",
            daemon=True,
        )
        self._thread.start()

        try:
            self._run(self._open(), timeout=_CONNECT_TIMEOUT)
            log.info("Conectado al servidor MCP '%s' (%s).",
                     self.config.name, self.config.transport)
        except Exception as exc:
            self._teardown_loop()
            raise MCPConnectionError(
                f"No se pudo conectar al servidor MCP '{self.config.name}': {exc}"
            ) from exc

    def close(self) -> None:
        """Cierra la sesión y detiene el loop. Idempotente."""
        if self._loop is None:
            return
        try:
            if self._stack is not None:
                self._run(self._stack.aclose(), timeout=5.0)
        except Exception:
            pass
        finally:
            self._teardown_loop()
            self._session = None
            self._stack = None
            self._tools_cache = None

    # ── operaciones ───────────────────────────────────────────────────────────

    def list_tools(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        """
        Autodescubre las tools del servidor.

        Returns:
            Lista de {"name", "description", "input_schema"} por tool.
        """
        self._ensure_connected()
        if self._tools_cache is not None and not refresh:
            return self._tools_cache

        result = self._run(self._session.list_tools())
        tools: list[dict[str, Any]] = []
        for t in result.tools:
            tools.append({
                "name":         t.name,
                "description":  t.description or "",
                "input_schema": getattr(t, "inputSchema", None) or {},
            })
        self._tools_cache = tools
        return tools

    def call_tool(
        self,
        name:    str,
        args:    Optional[dict[str, Any]] = None,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> str:
        """
        Invoca una tool remota y devuelve su salida como texto.

        Raises:
            MCPConnectionError: sin conexión.
            RuntimeError:       el servidor reportó error en la tool.
        """
        self._ensure_connected()
        result = self._run(
            self._session.call_tool(name, args or {}), timeout=timeout
        )

        text = self._extract_text(result)
        if getattr(result, "isError", False):
            raise RuntimeError(
                f"El servidor MCP '{self.config.name}' reportó un error "
                f"en '{name}': {text or 'sin detalle'}"
            )
        return text

    def as_tools(self) -> list[MCPProxyTool]:
        """Envuelve cada tool remota como Tool de itzel_core (para el registry)."""
        return [
            MCPProxyTool(
                client       = self,
                remote_name  = t["name"],
                description  = t["description"],
                input_schema = t["input_schema"],
            )
            for t in self.list_tools()
        ]

    def ping(self) -> bool:
        """True si el servidor responde a list_tools (prueba de conexión)."""
        try:
            self.list_tools(refresh=True)
            return True
        except Exception:
            return False

    # ── internals ─────────────────────────────────────────────────────────────

    async def _open(self) -> None:
        """Abre transport + sesión dentro del loop dedicado."""
        from mcp import ClientSession

        self._stack = AsyncExitStack()

        if self.config.transport == "stdio":
            if not self.config.command:
                raise ValueError("transport='stdio' requiere 'command' en la config.")
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client
            params = StdioServerParameters(
                command=self.config.command,
                args=list(self.config.args),
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))
        else:  # sse
            if not self.config.url:
                raise ValueError("transport='sse' requiere 'url' en la config.")
            from mcp.client.sse import sse_client
            headers = self._auth_headers()
            read, write = await self._stack.enter_async_context(
                sse_client(self.config.url, headers=headers)
            )

        self._session = await self._stack.enter_async_context(
            ClientSession(read, write)
        )
        await self._session.initialize()

    def _auth_headers(self) -> Optional[dict[str, str]]:
        """Headers de autenticación según la config (key desde el keychain)."""
        if self.config.auth != "api_key":
            return None
        key = get_mcp_api_key(self.config.name)
        if not key:
            raise MCPConnectionError(
                f"El servidor '{self.config.name}' requiere API key y no hay "
                f"ninguna en el keychain. Guárdala con: "
                f"itzel mcp add {self.config.name} --key"
            )
        return {"Authorization": f"Bearer {key}"}

    def _run(self, coro: Any, *, timeout: float = _DEFAULT_TIMEOUT) -> Any:
        """Ejecuta una corrutina en el loop dedicado y espera el resultado."""
        if self._loop is None:
            raise MCPConnectionError("Cliente MCP no conectado.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def _ensure_connected(self) -> None:
        if not self.is_connected:
            raise MCPConnectionError(
                f"No hay conexión con '{self.config.name}'. Llama connect() primero."
            )

    def _teardown_loop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        if self._loop is not None and not self._loop.is_running():
            try:
                self._loop.close()
            except Exception:
                pass
        self._loop = None
        self._thread = None

    @staticmethod
    def _extract_text(result: Any) -> str:
        """Concatena los bloques de texto del resultado MCP."""
        parts: list[str] = []
        for item in getattr(result, "content", []) or []:
            text = getattr(item, "text", None)
            if text:
                parts.append(text)
        return "\n".join(parts)

    def __enter__(self) -> "MCPClient":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ─── conexión múltiple desde la config ────────────────────────────────────────

def connect_all(
    configs: list[MCPServerConfig],
) -> tuple[dict[str, MCPClient], dict[str, str]]:
    """
    Conecta todos los servidores MCP habilitados de la config.

    Un servidor que falle no impide conectar a los demás.

    Returns:
        (clients, errors): clientes conectados por nombre, y errores por nombre.
    """
    clients: dict[str, MCPClient] = {}
    errors:  dict[str, str] = {}
    for cfg in configs:
        if not cfg.enabled:
            continue
        client = MCPClient(cfg)
        try:
            client.connect()
            clients[cfg.name] = client
        except Exception as exc:
            errors[cfg.name] = str(exc)
            log.warning("Servidor MCP '%s' no disponible: %s", cfg.name, exc)
    return clients, errors
