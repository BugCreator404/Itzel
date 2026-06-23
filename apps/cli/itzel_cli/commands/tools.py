"""Comando: itzel tools — descubre el catálogo de herramientas de Itzel.

Read-only: muestra qué puede invocar Itzel (skills + agentes + MCP), agrupado
por fuente. La ejecución vive en el flujo de agente (`itzel run`).
"""

from __future__ import annotations

import json as _json
from typing import Any

import typer

from ..client import BackendError, BackendOfflineError, ItzelClient
from ..output import console, err, hint, make_table, ok, warn

app = typer.Typer(help="Catálogo de herramientas (tools) que Itzel puede usar.")


# ─── ejecución local (con confirmación de terminal) ─────────────────────────────

def local_catalog(*, auto_confirm: bool) -> list | None:
    """Construye el catálogo de tools EN EL CLI, con confirmación de terminal.

    La ejecución corre aquí (no en el backend) porque confirmar una acción
    irreversible necesita preguntarte en la terminal. Devuelve None si
    itzel-core no está instalado en este entorno.
    """
    try:
        from itzel_core.config import config
        from itzel_core.tools.catalog import build_catalog
        from itzel_core.tools.confirm import AutoConfirmHandler, TerminalConfirmHandler
    except ImportError:
        return None
    handler = (
        AutoConfirmHandler()
        if auto_confirm
        else TerminalConfirmHandler(lang=getattr(config, "language", "es-MX"))
    )
    return build_catalog(confirm_handler=handler)


def print_result(name: str, result: Any, json_output: bool) -> None:
    """Imprime el ToolResult de una invocación (texto o JSON)."""
    if json_output:
        import json as __json
        console.print_json(__json.dumps(
            {
                "tool":   name,
                "ok":     result.ok,
                "value":  result.value if result.ok else None,
                "error":  result.error,
                "denied": result.denied,
            },
            ensure_ascii=False, default=str, indent=2,
        ))
        return
    if result.ok:
        ok(f"{name} ✓")
        if result.value is not None:
            console.print(result.value)
    elif result.denied:
        warn(f"{name}: acción cancelada (no confirmada).")
    else:
        err(f"{name}: {result.error}")


@app.command("list")
def list_cmd(
    mcp: bool = typer.Option(
        False, "--mcp", help="Incluir tools de servidores MCP (se conecta a ellos)"
    ),
    json_output: bool = typer.Option(False, "--json", help="Salida en JSON"),
) -> None:
    """Lista las herramientas disponibles, agrupadas por fuente."""
    client = ItzelClient()
    try:
        data = client.list_tools(mcp=mcp)
    except BackendOfflineError:
        err("Backend offline.", hint="Inícialo con: itzel setup")
        raise typer.Exit(1) from None
    except BackendError as exc:
        err(f"No se pudo obtener el catálogo: {exc.detail}")
        raise typer.Exit(1) from None

    if json_output:
        console.print_json(_json.dumps(data, ensure_ascii=False, indent=2))
        return

    tools = data.get("tools", [])
    if not tools:
        console.print("[#9890b8]No hay herramientas disponibles.[/]")
        hint("Instala los agentes (pip install -e packages/agents) o añade skills.")
        return

    table = make_table(
        f"Herramientas disponibles ({data.get('total', len(tools))})",
        ("Tool",        "#f9a8d4", 24),
        ("Fuente",      "#4ecdc4", 14),
        ("Descripción", "#9890b8", 52),
    )
    for t in sorted(tools, key=lambda x: (x["source"], x["name"])):
        desc = (t.get("description") or "").split("\n")[0][:80]
        table.add_row(t["name"], t["source"], desc)
    console.print(table)

    by_source = data.get("by_source", {})
    if by_source:
        summary = " · ".join(f"{k}: {v}" for k, v in sorted(by_source.items()))
        console.print(f"[dim]{summary}[/]")


@app.command("call")
def call_cmd(
    name: str = typer.Argument(..., help="Nombre de la tool (ver: itzel tools list)"),
    args: str = typer.Option(
        "{}", "--args", "-a", help='Argumentos en JSON, ej: \'{"path":"/tmp/x.txt"}\''
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Auto-confirmar acciones irreversibles (¡cuidado!)"
    ),
    json_output: bool = typer.Option(False, "--json", help="Salida en JSON"),
) -> None:
    """Ejecuta una herramienta del catálogo. Confirma si la acción es irreversible."""
    import json as __json

    try:
        parsed = __json.loads(args)
        if not isinstance(parsed, dict):
            raise ValueError("debe ser un objeto JSON, ej: '{\"path\": \"...\"}'")
    except (ValueError, __json.JSONDecodeError) as exc:
        err(f"Argumentos JSON inválidos: {exc}")
        raise typer.Exit(1) from None

    catalog = local_catalog(auto_confirm=yes)
    if catalog is None:
        err("itzel-core no está instalado en este entorno.",
            hint="Instálalo con: pip install -e packages/core")
        raise typer.Exit(1)

    tool = next((t for t in catalog if t.name == name), None)
    if tool is None:
        err(f"No existe la herramienta '{name}'.")
        hint("Lista las disponibles con: itzel tools list")
        raise typer.Exit(1)

    result = tool.invoke(parsed)
    print_result(name, result, json_output)
    if not result.ok:
        raise typer.Exit(1)
