"""Comando: itzel run "tarea" — Itzel elige y ejecuta una herramienta.

Flujo (SIN tocar /chat):
  1. Construye el catálogo de tools local (skills + agentes), con confirmación.
  2. Le pide al modelo que elija UNA herramienta y sus argumentos (JSON).
  3. La ejecuta; si es irreversible, confirma en la terminal antes.

Degrada con elegancia: sin core, sin modelo, o sin herramienta adecuada → lo
dice y no hace nada. La selección por modelo es best-effort: un modelo pequeño
puede equivocarse; por eso las acciones irreversibles siempre se confirman.
"""

from __future__ import annotations

import json as _json
import re
from typing import Any

from ..client import BackendError, BackendOfflineError, ItzelClient
from ..output import console, err, hint, info
from .tools import local_catalog, print_result

_SELECT_PROMPT = """Eres el enrutador de herramientas de Itzel. Dada una tarea y un \
catálogo, elige LA MEJOR herramienta y sus argumentos.

Responde ÚNICAMENTE con un objeto JSON (sin texto extra) con esta forma:
  {{"tool": "<nombre>", "args": {{...}}}}
Si ninguna herramienta sirve, responde exactamente: {{"tool": null}}

Catálogo:
{catalog}

Tarea: {task}

JSON:"""


def run(task: str, json_output: bool = False, auto_confirm: bool = False) -> None:
    catalog = local_catalog(auto_confirm=auto_confirm)
    if catalog is None:
        err("itzel-core no está instalado en este entorno.",
            hint="Instálalo con: pip install -e packages/core")
        return
    if not catalog:
        info("No hay herramientas disponibles.")
        hint("Añade skills o instala los agentes (pip install -e packages/agents).")
        return

    client = ItzelClient()
    try:
        selection = _select_tool(task, catalog, client)
    except BackendOfflineError:
        err("Backend offline — no puedo razonar la tarea.",
            hint="Inícialo con: itzel setup")
        return
    except BackendError as exc:
        err(f"Error del modelo: {exc.detail}")
        return

    if selection is None:
        info("No encontré una herramienta adecuada para esa tarea.")
        hint("Mira el catálogo con: itzel tools list")
        return

    name, args = selection
    tool = next((t for t in catalog if t.name == name), None)
    if tool is None:                       # el modelo inventó un nombre
        err(f"El modelo eligió una herramienta inexistente: '{name}'.")
        return

    if not json_output:
        console.print(
            f"[#9890b8]→ ejecutando[/] [#4ecdc4]{name}[/] "
            f"[dim]{args if args else ''}[/]"
        )
    result = tool.invoke(args)
    print_result(name, result, json_output)


# ─── selección por modelo ───────────────────────────────────────────────────────

def _select_tool(
    task: str, catalog: list, client: ItzelClient,
) -> tuple[str, dict[str, Any]] | None:
    """Pide al modelo que elija una tool del catálogo. Devuelve (name, args) o None."""
    lines = []
    for t in catalog:
        props = t.schema.get("input_schema", {}).get("properties", {})
        params = ", ".join(props) if props else "—"
        desc = t.description.splitlines()[0] if t.description else ""
        lines.append(f"- {t.name}: {desc} (args: {params})")
    prompt = _SELECT_PROMPT.format(catalog="\n".join(lines), task=task)

    raw = client.chat_sync(message=prompt)
    obj = _extract_json(raw)
    if not obj or not obj.get("tool"):
        return None

    name = str(obj["tool"])
    args = obj.get("args") or {}
    if not isinstance(args, dict):
        args = {}
    if not any(t.name == name for t in catalog):
        return None
    return name, args


def _extract_json(text: str | None) -> dict[str, Any] | None:
    """Extrae el primer objeto JSON de un texto (el modelo a veces lo envuelve)."""
    if not text:
        return None
    try:
        parsed = _json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, _json.JSONDecodeError):
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        parsed = _json.loads(m.group(0))
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, _json.JSONDecodeError):
        return None
