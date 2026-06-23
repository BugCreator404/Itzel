"""Tests del catálogo unificado de tools (itzel_core.tools.catalog).

Verifica el ensamblado y el dispatch SIN depender de que itzel-agents/itzel-mcp
estén instalados (no lo están en el entorno de CI): se comprueba la degradación
elegante y el mecanismo de invoke con una tool construida a mano.
"""

from __future__ import annotations

from itzel_core.tools.base import ToolRegistry, tool
from itzel_core.tools.catalog import CatalogTool, _registry_entry, build_catalog


def test_build_catalog_returns_list():
    cat = build_catalog()
    assert isinstance(cat, list)
    # agents/mcp no instalados en el entorno de test → esas fuentes ausentes.
    assert not any(t.source.startswith("agent:") for t in cat)
    assert not any(t.source.startswith("mcp:") for t in cat)


def test_catalog_entries_have_valid_shape():
    for t in build_catalog():
        assert isinstance(t, CatalogTool)
        assert t.name and t.source
        assert "input_schema" in t.schema
        d = t.to_dict()
        assert d["name"] == t.name and d["source"] == t.source
        assert callable(t.invoke)


def test_registry_entry_dispatches_invoke():
    """Una CatalogTool despacha invoke a su ToolRegistry (confirma + audita)."""
    @tool("echo", "Devuelve lo que recibe.")
    def echo(text: str) -> str:
        return text

    reg = ToolRegistry(agent_name="test")
    reg.register(echo)
    entry = _registry_entry(echo, "skill", reg)

    assert entry.name == "echo"
    assert entry.source == "skill"
    assert entry.schema["name"] == "echo"

    result = entry.invoke({"text": "hola"})
    assert result.ok
    assert result.value == "hola"


def test_mcp_off_by_default():
    """include_mcp=False (default) no intenta conectar a servidores MCP."""
    cat = build_catalog(include_mcp=False)
    assert not any(t.source.startswith("mcp:") for t in cat)
