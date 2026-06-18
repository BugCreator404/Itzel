"""Comando: itzel status [--json]

Muestra el estado real de todos los componentes de Itzel:
  - Backend FastAPI (online/offline + versión + uptime)
  - Modelo activo (nombre, backend, cargado)
  - Memoria SQLite (número de mensajes guardados)
  - Voz STT/TTS (placeholder hasta Sesión 7)
  - Sesión CLI activa
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from .. import __version__
from ..client import ItzelClient
from ..output import console, hint, make_table

# ─── constantes ───────────────────────────────────────────────────────────────

_MODELS_DIR = Path.home() / ".itzel" / "models"


# ─── punto de entrada ─────────────────────────────────────────────────────────

def run(json_output: bool = False) -> None:
    """Consulta el estado de todos los componentes y los muestra."""
    state = _collect_state()

    if json_output:
        console.print_json(json.dumps(state, ensure_ascii=False, indent=2))
        return

    _print_table(state)


# ─── recolección de estado ────────────────────────────────────────────────────

def _collect_state() -> dict:
    """Consulta cada componente y construye el dict de estado."""
    client  = ItzelClient()
    alive   = client.is_alive()

    # ── Backend ───────────────────────────────────────────────────────────────
    backend_info: dict = {"alive": alive, "version": "?", "uptime_s": 0.0}
    model_info:   dict = {"id": "none", "name": "—", "backend": "—", "loaded": False, "active": False}
    models_list:  list = []

    if alive:
        # /api/v1/status/
        try:
            with httpx.Client(timeout=3.0) as c:
                r = c.get(f"{client.base_url}/api/v1/status/")
                if r.status_code == 200:
                    d = r.json()
                    backend_info["version"]  = d.get("version", "?")
                    backend_info["uptime_s"] = d.get("uptime_s", 0.0)
        except Exception:
            pass

        # /api/v1/models/
        try:
            models_list = client.list_models()
            active = next((m for m in models_list if m.active), None)
            if active:
                model_info = {
                    "id":      active.id,
                    "name":    active.name,
                    "backend": active.backend,
                    "loaded":  active.loaded,
                    "active":  True,
                }
        except Exception:
            pass

    # ── Memoria ───────────────────────────────────────────────────────────────
    memory_count = _count_memory_entries()

    # ── Modelos descargados ────────────────────────────────────────────────────
    downloaded_gguf = list(_MODELS_DIR.glob("*.gguf")) if _MODELS_DIR.exists() else []

    # ── Sesión CLI ────────────────────────────────────────────────────────────
    try:
        session_id = ItzelClient.load_session()
    except Exception:
        session_id = "—"

    return {
        "cli_version":  __version__,
        "backend": {
            "alive":    alive,
            "url":      client.base_url,
            "version":  backend_info["version"],
            "uptime_s": backend_info["uptime_s"],
        },
        "model": model_info,
        "models_downloaded": [str(p.name) for p in downloaded_gguf],
        "memory": {
            "entries": memory_count,
            "path":    str(Path.home() / ".itzel" / "memory.db"),
        },
        "voice": {
            "stt": "no disponible (Sesión 7)",
            "tts": "no disponible (Sesión 7)",
        },
        "session_id": session_id,
    }


def _count_memory_entries() -> int:
    """Cuenta las entradas de memoria directamente desde SQLite."""
    try:
        from itzel_core.memory import MemoryStore
        store = MemoryStore()
        result = store._db.query_one("SELECT COUNT(*) FROM messages")
        return int(result[0]) if result else 0
    except Exception:
        return -1   # -1 = no disponible


# ─── tabla Rich ───────────────────────────────────────────────────────────────

def _print_table(state: dict) -> None:
    backend = state["backend"]
    model   = state["model"]
    memory  = state["memory"]
    voice   = state["voice"]

    table = make_table(
        "Estado de Itzel",
        ("Componente",  "#e8e4f4", 18),
        ("Estado",      "",        28),
        ("Detalle",     "#9890b8", 30),
    )

    # ── Backend ───────────────────────────────────────────────────────────────
    if backend["alive"]:
        uptime = _fmt_uptime(backend["uptime_s"])
        table.add_row(
            "Backend",
            "[bold #4ecdc4]online[/]",
            f"v{backend['version']} · {uptime} · {backend['url']}",
        )
    else:
        table.add_row(
            "Backend",
            "[bold #f87171]offline[/]",
            f"[dim]{backend['url']}[/]",
        )

    # ── Modelo ────────────────────────────────────────────────────────────────
    if model["id"] != "none":
        loaded_label = "[#4ecdc4]en memoria[/]" if model["loaded"] else "[#fbbf24]descargado[/]"
        table.add_row(
            "Modelo activo",
            f"[bold]{model['name']}[/]",
            f"{model['backend']} · {loaded_label}",
        )
    else:
        table.add_row("Modelo activo", "[#fbbf24]ninguno[/]", "")

    # ── Modelos en disco ──────────────────────────────────────────────────────
    gguf_names = state["models_downloaded"]
    if gguf_names:
        table.add_row(
            "Modelos en disco",
            f"[#4ecdc4]{len(gguf_names)} archivo(s)[/]",
            ", ".join(gguf_names[:3]) + ("…" if len(gguf_names) > 3 else ""),
        )
    else:
        table.add_row(
            "Modelos en disco",
            "[#fbbf24]ninguno[/]",
            "itzel model pull itzel-1b",
        )

    # ── Memoria ───────────────────────────────────────────────────────────────
    if memory["entries"] >= 0:
        table.add_row(
            "Memoria",
            f"[#4ecdc4]{memory['entries']} mensajes[/]",
            f"[dim]{memory['path']}[/]",
        )
    else:
        table.add_row("Memoria", "[dim]no disponible[/]", "")

    # ── Voz ───────────────────────────────────────────────────────────────────
    table.add_row("Voz STT", f"[dim]{voice['stt']}[/]", "")
    table.add_row("Voz TTS", f"[dim]{voice['tts']}[/]", "")

    # ── CLI ───────────────────────────────────────────────────────────────────
    sid = state["session_id"]
    table.add_row(
        "Sesión CLI",
        f"[#9890b8]{sid[:8]}…[/]" if len(sid) > 8 else f"[#9890b8]{sid}[/]",
        f"v{state['cli_version']}",
    )

    console.print(table)

    # Hint si el backend está offline
    if not backend["alive"]:
        console.print()
        hint(
            "El backend no está corriendo. Inícialo con:\n"
            "  [bold]uvicorn itzel_core.engine:app --port 7432[/]\n"
            "  O configura todo de una vez con: [bold]itzel setup[/]"
        )


def _fmt_uptime(seconds: float) -> str:
    """Formatea segundos de uptime en formato legible."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    h = s // 3600
    m = (s % 3600) // 60
    return f"{h}h {m}m"
