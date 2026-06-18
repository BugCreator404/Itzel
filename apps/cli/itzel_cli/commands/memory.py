"""Comando: itzel memory [search|list|clear|export|backup|restore|stats|actions]

Accede a la memoria de Itzel en la BD cifrada (~/.itzel/memory.db).
No requiere que el backend esté corriendo — operación local pura.

Subcomandos:
  search <query>     — busca en el historial (o en tus documentos con --docs)
  list               — lista las conversaciones con conteo de mensajes
  clear              — borra la memoria de chat (o el índice con --vectors)
  export [path]      — exporta a JSON legible (vía backend)
  backup [path]      — backup cifrado portable (.db.enc, con passphrase)
  restore <archivo>  — restaura un backup .db.enc (con confirmación)
  stats              — estadísticas de uso de la memoria
  actions            — historial de acciones de los agentes (auditoría)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

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

app = typer.Typer(help="Gestiona la memoria de Itzel")

_DB_PATH = Path.home() / ".itzel" / "memory.db"


# ─── search ───────────────────────────────────────────────────────────────────

@app.command("search")
def search(
    query:   str = typer.Argument(..., help="Texto a buscar en la memoria"),
    limit:   int = typer.Option(20, "--limit", "-n", help="Máximo de resultados"),
    session: str | None = typer.Option(None, "--session", "-s", help="Filtrar por session_id"),
    role:    str | None = typer.Option(None, "--role", "-r", help="Filtrar por rol: user | assistant"),
    docs:    bool = typer.Option(False, "--docs", "-d", help="Buscar en tus documentos indexados (memoria semántica)"),
    file_types: str | None = typer.Option(None, "--type", "-t", help="Con --docs: filtra por extensión, ej: .md,.pdf"),
    folder:  str | None = typer.Option(None, "--folder", "-f", help="Con --docs: acota a una carpeta"),
) -> None:
    """Busca en el historial de chat, o en tus documentos con --docs (semántica)."""
    if docs:
        _search_docs(query, limit, file_types, folder)
        return

    store = _open_store()
    if store is None:
        return

    entries = store.search(query, limit=limit)
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
        content = _highlight(e.content.replace("\n", " ")[:120], query)
        rol = f"[bold #f9a8d4]{e.role}[/]" if e.role == "user" else f"[#9890b8]{e.role}[/]"
        table.add_row(_fmt_date(e.created_at), rol, e.session_id[:8] + "…", content)

    console.print(table)
    console.print(f"\n[dim]{len(entries)} resultado(s)[/]")


# ─── list ─────────────────────────────────────────────────────────────────────

@app.command("list")
def list_sessions(
    limit: int = typer.Option(20, "--limit", "-n", help="Máximo de conversaciones"),
) -> None:
    """Lista las conversaciones almacenadas con sus estadísticas."""
    store = _open_store()
    if store is None:
        return

    try:
        rows = store._db.query("""
            SELECT
                c.id            AS id,
                c.title         AS title,
                c.model         AS model,
                COUNT(m.id)     AS total,
                SUM(CASE WHEN m.role='user' THEN 1 ELSE 0 END) AS user_msgs,
                MIN(m.created_at) AS first_msg,
                MAX(m.created_at) AS last_msg
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            GROUP BY c.id
            ORDER BY c.updated_at DESC
            LIMIT ?
        """, (limit,))
    except Exception as exc:
        err(f"Error al leer la memoria: {exc}")
        return

    if not rows:
        warn("La memoria está vacía.")
        hint("Los mensajes se guardan automáticamente al usar: itzel ask / itzel chat")
        return

    table = make_table(
        "Conversaciones en memoria",
        ("Sesión",   "#f9a8d4", 10),
        ("Mensajes", "#4ecdc4",  9),
        ("Usuario",  "#9890b8",  8),
        ("Modelo",   "#9890b8", 12),
        ("Último mensaje", "",  19),
    )
    for r in rows:
        table.add_row(
            (r["id"] or "")[:8] + "…",
            str(r["total"] or 0),
            str(r["user_msgs"] or 0),
            r["model"] or "—",
            _fmt_date(r["last_msg"]),
        )

    console.print(table)
    console.print(f"\n[dim]{len(rows)} conversación(es)[/]")


# ─── clear ────────────────────────────────────────────────────────────────────

@app.command("clear")
def clear(
    yes:     bool = typer.Option(False, "--yes", "-y", help="Sin confirmación"),
    session: str | None = typer.Option(None, "--session", "-s", help="Borrar solo esta sesión"),
    vectors: bool = typer.Option(False, "--vectors", help="Borra el índice vectorial (documentos), no el chat"),
) -> None:
    """Borra la memoria de chat, una sesión, o el índice vectorial con --vectors."""
    if vectors:
        _clear_vectors(yes)
        return

    store = _open_store()
    if store is None:
        return

    if session:
        try:
            row = store._db.query_one(
                "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?", (session,)
            )
            count = row["n"] if row else 0
        except Exception:
            count = 0

        if count == 0:
            warn(f"No se encontraron mensajes para la sesión '{session}'.")
            return
        if not yes:
            confirm(f"¿Borrar {count} mensaje(s) de la sesión '{session[:8]}…'?")
        store.delete_session(session)
        ok(f"{count} mensaje(s) eliminados de la sesión {session[:8]}…")
        return

    try:
        row = store._db.query_one("SELECT COUNT(*) AS n FROM messages")
        total = row["n"] if row else 0
    except Exception:
        total = 0

    if total == 0:
        warn("La memoria ya está vacía.")
        return
    if not yes:
        confirm(f"¿Borrar TODA la memoria de Itzel? ({total} mensajes en total)")
    store.clear()
    ok(f"Memoria borrada — {total} mensajes eliminados.")


# ─── export (JSON legible) ────────────────────────────────────────────────────

@app.command("export")
def export(
    path:    str  = typer.Argument("itzel_memory.json", help="Ruta del archivo de salida"),
    session: str | None = typer.Option(None, "--session", "-s", help="Exportar solo esta sesión"),
    pretty:  bool = typer.Option(True, "--pretty/--compact", help="JSON formateado o compacto"),
) -> None:
    """Exporta la memoria a un archivo JSON legible (requiere backend)."""
    client = ItzelClient()
    try:
        records = client.export_memory()
    except Exception as exc:
        err(f"No se pudo exportar (¿backend activo?): {exc}")
        hint("Para un backup local cifrado usa: itzel memory backup")
        return

    if session:
        records = [r for r in records if r.get("session_id", "").startswith(session)]
    if not records:
        warn("No hay mensajes para exportar.")
        return

    output_path = Path(path)
    try:
        output_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2 if pretty else None),
            encoding="utf-8",
        )
        ok(f"Exportados {len(records)} mensajes a: {output_path.resolve()}")
    except OSError as exc:
        err(f"No se pudo escribir el archivo: {exc}")
        raise typer.Exit(1) from None


# ─── backup (cifrado portable .db.enc) ────────────────────────────────────────

@app.command("backup")
def backup(
    path: str | None = typer.Argument(None, help="Ruta del backup (.db.enc). Default: data/backups/"),
    passphrase: str | None = typer.Option(
        None, "--passphrase", "-p",
        help="Passphrase para cifrar el backup portable (recomendado)",
        prompt="Passphrase para el backup (vacío = clave local, NO portable)",
        hide_input=True, confirmation_prompt=True,
    ),
) -> None:
    """Crea un backup cifrado y portable de la memoria (.db.enc)."""
    try:
        from itzel_core.backup import export_backup
    except ImportError:
        err("itzel-core no está instalado.", hint="pip install -e packages/core")
        return

    dest = Path(path) if path else None
    pw = passphrase or None
    try:
        out = export_backup(dest, passphrase=pw)
    except Exception as exc:
        err(f"No se pudo crear el backup: {exc}")
        raise typer.Exit(1) from None

    portable = "portable (passphrase)" if pw else "local (clave del keychain)"
    ok(f"Backup creado: {out}")
    info(f"Tipo: {portable}")
    if not pw:
        hint("Sin passphrase el backup solo se restaura en ESTA máquina.")


# ─── restore ──────────────────────────────────────────────────────────────────

@app.command("restore")
def restore(
    archivo: str = typer.Argument(..., help="Archivo .db.enc a restaurar"),
    passphrase: str | None = typer.Option(
        None, "--passphrase", "-p",
        help="Passphrase con que se cifró el backup",
        prompt="Passphrase del backup (vacío = clave local)",
        hide_input=True,
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Sin confirmación"),
) -> None:
    """Restaura un backup .db.enc, reemplazando la memoria actual."""
    try:
        from itzel_core.backup import import_backup
    except ImportError:
        err("itzel-core no está instalado.", hint="pip install -e packages/core")
        return

    src = Path(archivo)
    if not src.exists():
        err(f"No existe el archivo: {src}")
        raise typer.Exit(1)

    if not yes:
        confirm(
            "¿Restaurar este backup? La memoria actual se respaldará "
            "(.pre-import-bak) y será reemplazada."
        )

    try:
        import_backup(src, passphrase=passphrase or None)
    except ValueError as exc:
        err(str(exc))
        raise typer.Exit(1) from None
    except Exception as exc:
        err(f"No se pudo restaurar: {exc}")
        raise typer.Exit(1) from None

    ok("Memoria restaurada desde el backup.")
    hint("La memoria anterior quedó en ~/.itzel/memory.db.pre-import-bak")


# ─── stats ────────────────────────────────────────────────────────────────────

@app.command("stats")
def stats() -> None:
    """Muestra estadísticas de uso de la memoria."""
    store = _open_store()
    if store is None:
        return

    try:
        db = store._db
        total    = db.query_one("SELECT COUNT(*) AS n FROM messages")["n"]
        user_c   = db.query_one("SELECT COUNT(*) AS n FROM messages WHERE role='user'")["n"]
        ai_c     = db.query_one("SELECT COUNT(*) AS n FROM messages WHERE role='assistant'")["n"]
        convs    = db.query_one("SELECT COUNT(*) AS n FROM conversations")["n"]
        summaries = db.query_one("SELECT COUNT(*) AS n FROM conversation_summaries")["n"]
        oldest   = db.query_one("SELECT MIN(created_at) AS d FROM messages")["d"]
        newest   = db.query_one("SELECT MAX(created_at) AS d FROM messages")["d"]
        encrypted = db.is_encrypted
        db_size = _DB_PATH.stat().st_size if _DB_PATH.exists() else 0
    except Exception as exc:
        err(f"Error al leer estadísticas: {exc}")
        return

    console.print("\n[bold #f9a8d4]Memoria de Itzel — Estadísticas[/]\n")
    info(f"Total mensajes:    {total}")
    info(f"  → del usuario:   {user_c}")
    info(f"  → de Itzel:      {ai_c}")
    info(f"Conversaciones:    {convs}")
    info(f"Resúmenes:         {summaries}")
    info(f"Primer mensaje:    {_fmt_date(oldest) if oldest else '—'}")
    info(f"Último mensaje:    {_fmt_date(newest) if newest else '—'}")
    info(f"Cifrado:           {'sí (AES-256)' if encrypted else 'NO'}")
    info(f"Tamaño en disco:   {db_size // 1024} KB  ({_DB_PATH})")
    console.print()


# ─── actions (auditoría) ──────────────────────────────────────────────────────

@app.command("actions")
def actions(
    limit: int = typer.Option(30, "--limit", "-n", help="Máximo de acciones"),
    agent: str | None = typer.Option(None, "--agent", "-a", help="Filtrar por agente"),
) -> None:
    """Muestra el historial de acciones de los agentes (auditoría)."""
    try:
        from itzel_core.audit import get_actions
    except ImportError:
        err("itzel-core no está instalado.", hint="pip install -e packages/core")
        return

    try:
        rows = get_actions(limit=limit, agent=agent)
    except Exception as exc:
        err(f"Error al leer la auditoría: {exc}")
        return

    if not rows:
        warn("No hay acciones registradas.")
        return

    table = make_table(
        "Auditoría de acciones",
        ("Fecha",  "#9890b8", 19),
        ("Agente", "#4ecdc4", 10),
        ("Acción", "#f9a8d4", 16),
        ("Objetivo", "",      30),
        ("Resultado", "",     10),
    )
    for r in rows:
        res = r["result"]
        color = {"ok": "#4ecdc4", "denied": "#fbbf24", "error": "#f87171"}.get(res, "")
        table.add_row(
            _fmt_date(r["created_at"]),
            r["agent"] or "—",
            r["action"] or "—",
            (r["target"] or "—")[:30],
            f"[{color}]{res}[/]" if color else res,
        )
    console.print(table)
    console.print(f"\n[dim]{len(rows)} acción(es)[/]")


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


# ─── memoria semántica (RAG) ──────────────────────────────────────────────────

def _rag_available() -> bool:
    """True si el RAG tiene sus dependencias; si no, imprime cómo instalarlas."""
    try:
        from itzel_core.rag import check_rag_available
    except ImportError:
        err("itzel-core no está instalado.", hint="pip install -e packages/core")
        return False
    try:
        ok_, missing = check_rag_available()
    except Exception as exc:
        err(f"No se pudo cargar el RAG: {exc}")
        return False
    if not ok_:
        err("Faltan dependencias de la memoria semántica: " + ", ".join(missing))
        hint("Instálalas con: pip install 'itzel-core[rag]'")
        return False
    return True


def _search_docs(
    query: str, limit: int, file_types: str | None, folder: str | None,
) -> None:
    """Búsqueda semántica sobre los documentos indexados (todo local)."""
    try:
        from itzel_core.config import config
    except ImportError:
        err("itzel-core no está instalado.", hint="pip install -e packages/core")
        return
    if not config.rag.enabled:
        warn("La memoria semántica (RAG) está desactivada.")
        hint("Actívala con: itzel config rag.enabled true")
        return
    if not _rag_available():
        return

    from itzel_core.rag.retriever import get_retriever
    types = [t.strip() for t in file_types.split(",")] if file_types else None
    try:
        hits = get_retriever().search(query, top_k=limit, file_types=types, folder=folder)
    except Exception as exc:
        err(f"No se pudo buscar en los documentos: {exc}")
        return

    if not hits:
        warn(f"Sin documentos relevantes para '{query}'.")
        hint("¿Ya indexaste tus carpetas? Revisa rag.index_dirs y re-indexa.")
        return

    console.print(f"\n[bold #f9a8d4]Documentos relevantes — \"{query}\"[/]\n")
    for i, h in enumerate(hits, 1):
        score = f"{h.score * 100:.0f}%"
        console.print(
            f"[bold #4ecdc4]{i}.[/] [#f9a8d4]{h.filename}[/]  "
            f"[dim]{score} · {h.source}[/]"
        )
        snippet = _highlight(h.snippet.replace("\n", " "), query)
        console.print(f"   {snippet}\n")
    console.print(f"[dim]{len(hits)} fragmento(s) — los vectores nunca salen de tu equipo.[/]")


def _clear_vectors(yes: bool) -> None:
    """Borra el índice vectorial. NO toca los documentos originales."""
    if not _rag_available():
        return

    from itzel_core.rag.store import get_store
    try:
        store = get_store()
        count = store.count()
    except Exception as exc:
        err(f"No se pudo abrir el índice: {exc}")
        return

    if count == 0:
        warn("El índice vectorial ya está vacío.")
        return
    if not yes:
        confirm(
            f"¿Borrar el índice vectorial? ({count} fragmento(s) de tus documentos). "
            "Tus archivos originales NO se tocan."
        )
    try:
        removed = store.reset()
    except Exception as exc:
        err(f"No se pudo borrar el índice: {exc}")
        return
    ok(f"Índice vectorial borrado — {removed} fragmento(s) eliminados.")
    hint("Tus documentos originales siguen intactos. Re-indexa cuando quieras.")


def _fmt_date(iso: str | None) -> str:
    """Formatea ISO datetime a formato legible corto en hora local."""
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso[:16]


def _highlight(text: str, query: str) -> str:
    """Marca el query en el texto con color amarillo (Rich markup)."""
    idx = text.lower().find(query.lower())
    if idx == -1:
        return text
    before, match, after = text[:idx], text[idx:idx+len(query)], text[idx+len(query):]
    return f"{before}[bold #fbbf24]{match}[/]{after}"
