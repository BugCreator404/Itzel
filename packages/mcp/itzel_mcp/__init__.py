"""
itzel-mcp — Cliente Model Context Protocol para Itzel.

Conecta servidores MCP (stdio o SSE), autodescubre sus tools y las expone como
Tools de itzel_core para que el LLM las invoque por el mismo ToolRegistry que
los agentes y las skills.

Uso rápido:
    from itzel_core.config import config
    from itzel_mcp import connect_all

    clients, errors = connect_all(config.mcp_servers)
    for client in clients.values():
        registry.register_all(client.as_tools())

Privacidad:
    Conectar un servidor MCP remoto es una decisión EXPLÍCITA del usuario en
    itzel.config.json. Las API keys viven en el keychain del OS, nunca en el
    archivo de configuración.
"""

from __future__ import annotations

from .client import (
    MCPClient,
    MCPConnectionError,
    MCPNotAvailableError,
    MCPProxyTool,
    connect_all,
    get_mcp_api_key,
    set_mcp_api_key,
)

__version__ = "0.1.0"
__all__ = [
    "MCPClient",
    "MCPProxyTool",
    "MCPConnectionError",
    "MCPNotAvailableError",
    "connect_all",
    "get_mcp_api_key",
    "set_mcp_api_key",
]
