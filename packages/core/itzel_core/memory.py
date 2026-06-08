"""Memoria episódica de Itzel — SQLite cifrado con SQLCipher."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel

# SQLCipher se importa condicionalmente para no romper en entornos sin C libs
try:
    from sqlcipher3 import dbapi2 as sqlite
    _HAS_CIPHER = True
except ImportError:
    import sqlite3 as sqlite  # type: ignore[no-redef]
    _HAS_CIPHER = False

DB_PATH = Path.home() / ".itzel" / "memory.db"


class MemoryEntry(BaseModel):
    id: str
    role: str           # "user" | "assistant" | "system"
    content: str
    session_id: str
    created_at: str
    metadata: dict = {}


class MemoryStore:
    """Gestiona el historial de conversaciones en SQLite (cifrado en producción)."""

    def __init__(self, db_path: Path = DB_PATH, key: Optional[str] = None) -> None:
        self._path = db_path
        self._key = key
        self._conn = self._connect()
        self._ensure_schema()

    def _connect(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite.connect(str(self._path))
        if _HAS_CIPHER and self._key:
            conn.execute(f"PRAGMA key = '{self._key}'")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                session_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}'
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON messages(session_id)")
        self._conn.commit()

    def save(self, role: str, content: str, session_id: str, metadata: dict = {}) -> MemoryEntry:
        entry = MemoryEntry(
            id=str(uuid4()),
            role=role,
            content=content,
            session_id=session_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            metadata=metadata,
        )
        self._conn.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?,?)",
            (entry.id, entry.role, entry.content, entry.session_id, entry.created_at, json.dumps(entry.metadata)),
        )
        self._conn.commit()
        return entry

    def get_session(self, session_id: str, limit: int = 50) -> list[MemoryEntry]:
        rows = self._conn.execute(
            "SELECT id,role,content,session_id,created_at,metadata FROM messages WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [
            MemoryEntry(id=r[0], role=r[1], content=r[2], session_id=r[3], created_at=r[4], metadata=json.loads(r[5]))
            for r in reversed(rows)
        ]

    def search(self, query: str, limit: int = 20) -> list[MemoryEntry]:
        rows = self._conn.execute(
            "SELECT id,role,content,session_id,created_at,metadata FROM messages WHERE content LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        return [
            MemoryEntry(id=r[0], role=r[1], content=r[2], session_id=r[3], created_at=r[4], metadata=json.loads(r[5]))
            for r in rows
        ]

    def clear(self) -> None:
        self._conn.execute("DELETE FROM messages")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
