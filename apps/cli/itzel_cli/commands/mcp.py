"""Comando: itzel mcp [add|remove|list|test]

Gestiona los servidores MCP (Model Context Protocol) de Itzel.

La configuración vive en itzel.config.json → "mcp_servers". Las API keys NUNCA
se guardan en ese archivo: van al keychain del OS (principio #8).

Subcomandos:
  add <name>     — agrega un servidor (SSE con --url, stdio con --command)
  list           — tabla de servidores configurados
  test <name>    — conecta de verdad y muestra las tools descubiertas
  remove <name>  — quita el servidor (con confirmación) y su key del keychain
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from ..output import (
    confirm,
    console,
    err,
    hint,
    info,
    make_table,
    ok,
    warn,
)

app = typer.Typer(help="Gestiona servidores MCP")

# Misma prioridad de rutas que itzel_core.config.load_config
_CONFIG_PATHS = [
    Path.cwd() / "itzel.config.json",
    Path.home() / ".itzel" / "itzel.config.json",
]


# ─── helpers de config ────────────────────────────────────────────────────────

def _config_file() -> Path:
    """Primer config existente; si no hay ninguno, el de ~/.itzel."""
    for p in _CONFIG_PATHS:
        if p.exists():
            return p
    return _CONFIG_PATHS[1]


def _read_config() -> dict:
    path = _config_file()
    if not path.exists():
        return {"version": "0.1.0", "language": "es-MX"}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_config(data: dict) -> Path:
    path = _config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def _servers(data: dict) -> list[dict]:
    return data.setdefault("mcp_servers", [])


def _find(servers: list[dict], name: str) -> Optional[dict]:
    return next((s for s in servers if s.get("name") == name), None)


# ─── add ──────────────────────────────────────────────────────────────────────

@app.command("add")
def add(
    name:    str = typer.Argument(..., help="Nombre del servidor MCP"),
    url:     Optional[str] = typer.Option(None, "--url",     "-u", help="URL del servidor (transport SSE)"),
    command: Optional[str] = typer.Option(None, "--command", "-c", help="Comando local (transport stdio)"),
    args:    Optional[str] = typer.Option(None, "--args",          help="Argumentos del comando, separados por coma"),
    key:     bool = typer.Option(False, "--key", "-k", help="Pedir y guardar una API key en el keychain"),
) -> None:
    """Agrega un servidor MCP a itzel.config.json."""
    if bool(url) == bool(command):
        err("Indica exactamente uno: --url (SSE) o --command (stdio).")
        raise typer.Exit(1)

    entry = {
        "name":      name,
        "transport": "sse" if url else "stdio",
        "url":       url,
        "command":   command,
        "args":      [a.strip() for a in args.split(",")] if args else [],
        "auth":      "none",
        "enabled":   True,
    }

    # Validar con el modelo real antes de tocar el archivo
    try:
        from itzel_core.config import MCPServerConfig
        MCPServerConfig.model_validate(entry)
    except ImportError:
        pass  # sin core instalado, guardamos igual (el engine validará)
    except Exception as exc:
        err(f"Configuración inválida: {exc}")
        raise typer.Exit(1)

    data = _read_config()
    servers = _servers(data)
    if _find(servers, name):
        err(f"Ya existe un servidor MCP llamado '{name}'.")
        hint(f"Quítalo primero con:  itzel mcp remove {name}")
        raise typer.Exit(1)

    # API key → keychain, nunca al JSON (principio #8)
    if key:
        api_key = typer.prompt("API key", hide_input=True)
        try:
            from itzel_mcp import set_mcp_api_key
            if set_mcp_api_key(name, api_key):
                entry["auth"] = "api_key"
                ok("API key guardada en el keychain del OS.")
            else:
                warn("No se pudo guardar en el keychain — el servidor queda sin auth.")
        except ImportError:
            warn("itzel-mcp no está instalado — no se pudo guardar la key.")
            hint("Instálalo con: pip install -e packages/mcp")

    servers.append(entry)
    path = _write_config(data)
    ok(f"Servidor MCP '{name}' agregado ({entry['transport']}).")
    info(f"Config: {path}")
    hint(f"Pruébalo con:  itzel mcp test {name}")


# ─── list ─────────────────────────────────────────────────────────────────────

@app.command("list")
def list_servers() -> None:
    """Lista los servidores MCP configurados."""
    servers = _servers(_read_config())
    if not servers:
        warn("No hay servidores MCP configurados.")
        hint("Agrega uno con:  itzel mcp add <nombre> --url <url>")
        return

    table = make_table(
        "Servidores MCP",
        ("Nombre",    "#f9a8d4", 14),
        ("Transport", "#4ecdc4", 10),
        ("Destino",   "",        40),
        ("Auth",      "#9890b8",  8),
        ("Estado",    "",        12),
    )
    for s in servers:
        dest = s.get("url") or " ".join([s.get("command") or "", *s.get("args", [])]).strip()
        estado = "[#4ecdc4]● activo[/]" if s.get("enabled", True) else "[dim]○ apagado[/]"
        table.add_row(
            s.get("name", "?"),
            s.get("transport", "sse"),
            dest[:40],
            s.get("auth", "none"),
            estado,
        )
    console.print(table)
    console.print(f"\n[dim]{len(servers)} servidor(es)[/]")


# ─── test ─────────────────────────────────────────────────────────────────────

@app.command("test")
def test(name: str = typer.Argument(..., help="Servidor a probar")) -> None:
    """Conecta con el servidor y muestra las tools que ofrece."""
    entry = _find(_servers(_read_config()), name)
    if entry is None:
        err(f"Servidor MCP '{name}' no encontrado.")
        hint("Lista los configurados con:  itzel mcp list")
        raise typer.Exit(1)

    try:
        from itzel_core.config import MCPServerConfig
        from itzel_mcp import MCPClient, MCPNotAvailableError
    except ImportError:
        err("itzel-mcp no está instalado.", hint="pip install -e packages/mcp")
        raise typer.Exit(1)

    cfg = MCPServerConfig.model_validate(entry)
    info(f"Conectando a '{name}' ({cfg.transport})…")

    client = MCPClient(cfg)
    try:
        client.connect()
        tools = client.list_tools()
    except MCPNotAvailableError as exc:
        err(str(exc))
        raise typer.Exit(1)
    except Exception as exc:
        err(f"Conexión fallida: {exc}")
        raise typer.Exit(1)
    finally:
        client.close()

    ok(f"Conexión exitosa — {len(tools)} tool(s) disponibles:")
    table = make_table(
        f"Tools de '{name}'",
        ("Tool",        "#4ecdc4", 24),
        ("Descripción", "",        56),
    )
    for t in tools:
        table.add_row(t["name"], (t["description"] or "—")[:80])
    console.print(table)


# ─── remove ───────────────────────────────────────────────────────────────────

@app.command("remove")
def remove(
    name: str  = typer.Argument(..., help="Nombre del servidor"),
    yes:  bool = typer.Option(False, "--yes", "-y", help="Sin confirmación"),
) -> None:
    """Quita un servidor MCP del config (y su API key del keychain)."""
    data = _read_config()
    servers = _servers(data)
    entry = _find(servers, name)
    if entry is None:
        err(f"Servidor MCP '{name}' no encontrado.")
        raise typer.Exit(1)

    if not yes:
        confirm(f"¿Quitar el servidor MCP '{name}' de la configuración?")

    servers.remove(entry)
    _write_config(data)

    # Limpiar la key del keychain si la había
    if entry.get("auth") == "api_key":
        try:
            import keyring
            keyring.delete_password("itzel", f"mcp-{name}")
            info("API key eliminada del keychain.")
        except Exception:
            warn(f"No se pudo borrar la key del keychain (itzel / mcp-{name}).")

    ok(f"Servidor MCP '{name}' eliminado.")
