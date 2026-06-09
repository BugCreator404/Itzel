"""Comando: itzel memory [search|list|clear|export|stats]

Accede directamente a la memoria episódica SQLite (~/.itzel/memory.db).
No requiere que el backend esté corriendo — operación local pura.

Subcomandos:
  search <query>   — busca en el historial de mensajes
  list             — lista las sesiones con conteo de mensajes
  clear            — borra toda la memoria (con confirmación)
  export [path]    — exporta a JSON
  stats            — estadísticas de uso de la memoria
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from ..client import ItzelClient
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

app = typer.Typer(help="Gestiona la memoria episódica de Itzel")

_DB_PATH = Path.home() / ".itzel" / "memory.db"


# ─── search ───────────────────────────────────────────────────────────────────

@app.command("search")
def search(
    query:   str = typer.Argument(..., help="Texto a buscar en la memoria"),
    limit:   int = typer.Option(20, "--limit", "-n", help="Máximo de resultados"),
    session: Optional[str] = typer.Option(None, "--session", "-s", help="Filtrar por session_id"),
    role:    Optional[str] = typer.Option(None, "--role", "-r", help="Filtrar por rol: user | assistant"),
) -> None:
    """Busca en la memoria episódica por texto."""
    store = _open_store()
    if store is None:
        return

    entries = store.search(query, limit=limit)

    # Filtros adicionales
    if session:
        entries = [e for e in entries if e.session_id.startswith(session)]
    if role:
        entries = [e for e in entries if e.role == role]

    if not entries:
        warn(f"Sin resultados para '{query}'.")
        return

    table = make_table(
        f"Resultados — \"{query}\"",
        ("Fecha",     "#9890b8", 19),
        ("Rol",       "#f9a8d4",  9),
        ("Sesión",    "#9890b8", 10),
        ("Contenido", "",        60),
    )

    for e in entries:
        # Resaltar el query en el contenido
        content = e.content.replace("\n", " ")[:120]
        content = _highlight(content, query)

        fecha = _fmt_date(e.created_at)
        sid   = e.session_id[:8] + "…"
        rol   = f"[bold #f9a8d4]{e.role}[/]" if e.role == "user" else f"[#9890b8]{e.role}[/]"

        table.add_row(fecha, rol, sid, content)

    console.print(table)
    console.print(f"\n[dim]{len(entries)} resultado(s)[/]")


# ─── list ─────────────────────────────────────────────────────────────────────

@app.command("list")
def list_sessions(
    limit: int = typer.Option(20, "--limit", "-n", help="Máximo de sesiones"),
) -> None:
    """Lista las sesiones almacenadas con sus estadísticas."""
    store = _open_store()
    if store is None:
        return

    try:
        rows = store._conn.execute("""
            SELECT
                session_id,
                COUNT(*) as total,
                SUM(CASE WHEN role='user' THEN 1 ELSE 0 END) as user_msgs,
                MIN(created_at) as first_msg,
                MAX(created_at) as last_msg
            FROM messages
            GROUP BY session_id
            ORDER BY last_msg DESC
            LIMIT ?
        """, (limit,)).fetchall()
    except Exception as exc:
        err(f"Error al leer la memoria: {exc}")
        return

    if not rows:
        warn("La memoria está vacía.")
        hint("Los mensajes se guardan automáticamente al usar: itzel ask / itzel chat")
        return

    table = make_table(
        "Sesiones en memoria",
        ("Sesión",   "#f9a8d4", 10),
        ("Mensajes", "#4ecdc4",  9),
        ("Usuario",  "#9890b8",  8),
        ("Primer mensaje",  "",  19),
        ("Último mensaje",  "",  19),
    )

    for sid, total, user_msgs, first, last in rows:
        table.add_row(
            sid[:8] + "…",
            str(total),
            str(user_msgs),
            _fmt_date(first),
            _fmt_date(last),
        )

    console.print(table)
    console.print(f"\n[dim]{len(rows)} sesión(es)[/]")


# ─── clear ────────────────────────────────────────────────────────────────────

@app.command("clear")
def clear(
    yes:     bool = typer.Option(False, "--yes", "-y", help="Sin confirmación"),
    session: Optional[str] = typer.Option(None, "--session", "-s", help="Borrar solo esta sesión"),
) -> None:
    """Borra la memoria episódica (toda o una sesión específica)."""
    store = _open_store()
    if store is None:
        return

    if session:
        # Borrar solo una sesión
        try:
            count = store._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session,)
            ).fetchone()[0]
        except Exception:
            count = 0

        if count == 0:
            warn(f"No se encontraron mensajes para la sesión '{session}'.")
            return

        if not yes:
            confirm(f"¿Borrar {count} mensaje(s) de la sesión '{session[:8]}…'?")

        store._conn.execute("DELETE FROM messages WHERE session_id = ?", (session,))
        store._conn.commit()
        ok(f"{count} mensaje(s) eliminados de la sesión {session[:8]}…")
        return

    # Borrar todo
    try:
        total = store._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    except Exception:
        total = 0

    if total == 0:
        warn("La memoria ya está vacía.")
        return

    if not yes:
        confirm(f"¿Borrar TODA la memoria de Itzel? ({total} mensajes en total)")

    store.clear()
    ok(f"Memoria borrada — {total} mensajes eliminados.")


# ─── export ───────────────────────────────────────────────────────────────────

@app.command("export")
def export(
    path:    str  = typer.Argument("itzel_memory.json", help="Ruta del archivo de salida"),
    session: Optional[str] = typer.Option(None, "--session", "-s", help="Exportar solo esta sesión"),
    pretty:  bool = typer.Option(True, "--pretty/--compact", help="JSON formateado o compacto"),
) -> None:
    """Exporta la memoria a un archivo JSON."""
    store = _open_store()
    if store is None:
        return

    client = ItzelClient()
    records = client.export_memory()

    if session:
        records = [r for r in records if r["session_id"].startswith(session)]

    if not records:
        warn("No hay mensajes para exportar.")
        return

    output_path = Path(path)
    indent = 2 if pretty else None

    try:
        output_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=indent),
            encoding="utf-8",
        )
        ok(f"Exportados {len(records)} mensajes a: {output_path.resolve()}")
    except OSError as exc:
        err(f"No se pudo escribir el archivo: {exc}")
        raise typer.Exit(1)


# ─── stats ────────────────────────────────────────────────────────────────────

@app.command("stats")
def stats() -> None:
    """Muestra estadísticas de uso de la memoria."""
    store = _open_store()
    if store is None:
        return

    try:
        total = store._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        user_count = store._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE role='user'"
        ).fetchone()[0]
        ai_count = store._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE role='assistant'"
        ).fetchone()[0]
        sessions = store._conn.execute(
            "SELECT COUNT(DISTINCT session_id) FROM messages"
        ).fetchone()[0]
        oldest = store._conn.execute(
            "SELECT MIN(created_at) FROM messages"
        ).fetchone()[0]
        newest = store._conn.execute(
            "SELECT MAX(created_at) FROM messages"
        ).fetchone()[0]
        db_size = _DB_PATH.stat().st_size if _DB_PATH.exists() else 0
    except Exception as exc:
        err(f"Error al leer estadísticas: {exc}")
        return

    console.print("\n[bold #f9a8d4]Memoria Episódica — Estadísticas[/]\n")
    info(f"Total mensajes:    {total}")
    info(f"  → del usuario:   {user_count}")
    info(f"  → de Itzel:      {ai_count}")
    info(f"Sesiones únicas:   {sessions}")
    info(f"Primer mensaje:    {_fmt_date(oldest) if oldest else '—'}")
    info(f"Último mensaje:    {_fmt_date(newest) if newest else '—'}")
    info(f"Tamaño en disco:   {db_size // 1024} KB  ({_DB_PATH})")
    console.print()


# ─── helpers ──────────────────────────────────────────────────────────────────

def _open_store():
    """Abre el MemoryStore o muestra error si itzel_core no está disponible."""
    try:
        from itzel_core.memory import MemoryStore
        return MemoryStore()
    except ImportError:
        err(
            "itzel-core no está instalado en este entorno.",
            hint="Instálalo con: pip install -e packages/core",
        )
        return None
    except Exception as exc:
        err(f"No se pudo abrir la memoria: {exc}")
        return None


def _fmt_date(iso: Optional[str]) -> str:
    """Formatea ISO datetime a formato legible corto."""
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        # Mostrar en hora local si es posible
        local = dt.astimezone()
        return local.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso[:16]


def _highlight(text: str, query: str) -> str:
    """Marca el query en el texto con color amarillo (Rich markup)."""
    idx = text.lower().find(query.lower())
    if idx == -1:
        return text
    before = text[:idx]
    match  = text[idx : idx + len(query)]
    after  = text[idx + len(query):]
    return f"{before}[bold #fbbf24]{match}[/]{after}"
