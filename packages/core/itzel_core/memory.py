"""
Memoria de Itzel — historial de conversaciones + memoria episódica.

Persiste mensajes en la BD cifrada (db.py) y, cuando una conversación crece
demasiado, genera resúmenes automáticos con el modelo para usarlos como contexto
comprimido en turnos futuros (memoria episódica).

Compatibilidad:
    La API pública (save, get_session, search, clear, close) se mantiene para
    no romper a sus consumidores (api/v1/chat.py). El "session_id" del API se
    mapea a conversations.id en el nuevo schema.

English summary:
    Conversation history + episodic memory over the encrypted DB. Long
    conversations get auto-summarized; get_context() returns summary + recent
    messages as a compressed context window.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable, Optional
from uuid import uuid4

from pydantic import BaseModel

from .config import config
from .db import Database, get_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _estimate_tokens(text: str) -> int:
    """Estimación rápida: ~4 caracteres por token."""
    return len(text) // 4


# ─── modelos ──────────────────────────────────────────────────────────────────

class MemoryEntry(BaseModel):
    id: str
    role: str                 # "user" | "assistant" | "system"
    content: str
    session_id: str           # == conversation_id
    created_at: str
    metadata: dict = {}
    tokens_used: int = 0


class ConversationSummary(BaseModel):
    id: str
    conversation_id: str
    summary: str
    up_to_message_id: Optional[str]
    token_count: int
    created_at: str


# ─── store ────────────────────────────────────────────────────────────────────

class MemoryStore:
    """
    Historial de conversaciones sobre la BD cifrada, con memoria episódica.

    Uso:
        mem = MemoryStore()                       # BD compartida (cifrada)
        mem.save("user", "hola", session_id="s1")
        ctx = mem.get_context("s1")               # resumen + mensajes recientes

    Para tests:
        mem = MemoryStore(db=Database(":memory:", key=None))
    """

    def __init__(self, db: Optional[Database] = None) -> None:
        self._db = db or get_db()

    # ── conversaciones ────────────────────────────────────────────────────────

    def _ensure_conversation(
        self,
        session_id: str,
        model:      Optional[str] = None,
        language:   Optional[str] = None,
    ) -> None:
        """Crea la conversación si no existe; si existe, actualiza updated_at."""
        now = _now()
        existing = self._db.query_one(
            "SELECT id FROM conversations WHERE id = ?", (session_id,)
        )
        if existing is None:
            self._db.execute(
                "INSERT INTO conversations "
                "(id, created_at, updated_at, model, language, title) "
                "VALUES (?,?,?,?,?,?)",
                (session_id, now, now, model, language, None),
            )
        else:
            # Actualiza updated_at y rellena model/language si llegan y faltaban
            # (el primer mensaje suele ser 'user' sin model; el assistant lo trae).
            self._db.execute(
                "UPDATE conversations SET updated_at = ?, "
                "model = COALESCE(model, ?), language = COALESCE(language, ?) "
                "WHERE id = ?",
                (now, model, language, session_id),
            )

    # ── API pública (compatible con chat.py) ──────────────────────────────────

    def save(
        self,
        role:        str,
        content:     str,
        session_id:  str,
        metadata:    Optional[dict] = None,
        tokens_used: int = 0,
    ) -> MemoryEntry:
        """Guarda un mensaje, creando la conversación si hace falta."""
        metadata = metadata or {}
        entry = MemoryEntry(
            id          = str(uuid4()),
            role        = role,
            content     = content,
            session_id  = session_id,
            created_at  = _now(),
            metadata    = metadata,
            tokens_used = tokens_used or _estimate_tokens(content),
        )
        self._ensure_conversation(
            session_id,
            model    = metadata.get("model"),
            language = metadata.get("language"),
        )
        self._db.execute(
            "INSERT INTO messages "
            "(id, conversation_id, role, content, created_at, tokens_used, metadata) "
            "VALUES (?,?,?,?,?,?,?)",
            (entry.id, session_id, role, content, entry.created_at,
             entry.tokens_used, json.dumps(entry.metadata)),
        )
        return entry

    def get_session(self, session_id: str, limit: int = 50) -> list[MemoryEntry]:
        """Devuelve los últimos `limit` mensajes en orden cronológico."""
        rows = self._db.query(
            "SELECT id, role, content, conversation_id, created_at, metadata, tokens_used "
            "FROM messages WHERE conversation_id = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        )
        return [self._row_to_entry(r) for r in reversed(rows)]

    def search(self, query: str, limit: int = 20) -> list[MemoryEntry]:
        """Busca mensajes cuyo contenido contenga `query`."""
        rows = self._db.query(
            "SELECT id, role, content, conversation_id, created_at, metadata, tokens_used "
            "FROM messages WHERE content LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{query}%", limit),
        )
        return [self._row_to_entry(r) for r in rows]

    def delete_session(self, session_id: str) -> None:
        """
        Borra una conversación completa (mensajes y resúmenes via CASCADE).
        Reemplaza el acceso directo a la conexión que hacía chat.py.
        """
        self._db.execute("DELETE FROM conversations WHERE id = ?", (session_id,))

    def clear(self) -> None:
        """Borra todo el historial (conversaciones, mensajes y resúmenes)."""
        self._db.execute("DELETE FROM conversations")
        self._db.execute("DELETE FROM messages")
        self._db.execute("DELETE FROM conversation_summaries")

    def close(self) -> None:
        """No cierra la BD compartida; presente por compatibilidad de API."""
        # La conexión es compartida (get_db); cerrarla afectaría a otros módulos.

    # ── memoria episódica ─────────────────────────────────────────────────────

    def conversation_tokens(self, session_id: str) -> int:
        """Suma de tokens de los mensajes de una conversación."""
        row = self._db.query_one(
            "SELECT COALESCE(SUM(tokens_used), 0) AS total "
            "FROM messages WHERE conversation_id = ?",
            (session_id,),
        )
        return int(row["total"]) if row else 0

    def get_latest_summary(self, session_id: str) -> Optional[ConversationSummary]:
        """Devuelve el resumen más reciente de la conversación, o None."""
        row = self._db.query_one(
            "SELECT id, conversation_id, summary, up_to_message_id, token_count, created_at "
            "FROM conversation_summaries WHERE conversation_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        )
        if row is None:
            return None
        return ConversationSummary(
            id               = row["id"],
            conversation_id  = row["conversation_id"],
            summary          = row["summary"],
            up_to_message_id = row["up_to_message_id"],
            token_count      = row["token_count"],
            created_at       = row["created_at"],
        )

    def maybe_summarize(
        self,
        session_id: str,
        summarizer: Callable[[str], str],
        *,
        threshold:  Optional[int] = None,
    ) -> Optional[ConversationSummary]:
        """
        Genera un resumen si la conversación supera el umbral de tokens.

        Resume solo los mensajes NO cubiertos por el resumen anterior, de modo
        que el resumen sea incremental.

        Args:
            session_id: Conversación a resumir.
            summarizer: Función que toma texto y devuelve un resumen (modelo).
            threshold:  Umbral de tokens (default: config.cache.summary_threshold_tokens).

        Returns:
            El nuevo ConversationSummary, o None si no hizo falta resumir.
        """
        threshold = threshold or config.cache.summary_threshold_tokens

        # Mensajes aún no resumidos (posteriores al último resumen).
        previous = self.get_latest_summary(session_id)
        pending = self._messages_after(session_id, previous)

        pending_tokens = sum(m.tokens_used for m in pending)
        if pending_tokens < threshold or not pending:
            return None

        # Construir el texto a resumir (incluye el resumen previo como contexto).
        lines = []
        if previous:
            lines.append(f"[resumen previo] {previous.summary}")
        for m in pending:
            lines.append(f"{m.role}: {m.content}")
        text = "\n".join(lines)

        summary_text = summarizer(text)
        summary = ConversationSummary(
            id               = str(uuid4()),
            conversation_id  = session_id,
            summary          = summary_text,
            up_to_message_id = pending[-1].id,
            token_count      = _estimate_tokens(summary_text),
            created_at       = _now(),
        )
        self._db.execute(
            "INSERT INTO conversation_summaries "
            "(id, conversation_id, summary, up_to_message_id, token_count, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (summary.id, session_id, summary.summary, summary.up_to_message_id,
             summary.token_count, summary.created_at),
        )
        return summary

    def get_context(
        self,
        session_id:   str,
        recent_limit: int = 20,
    ) -> dict:
        """
        Devuelve el contexto comprimido de una conversación:
            {"summary": str | None, "messages": [MemoryEntry, ...]}

        Si hay un resumen, los mensajes son solo los posteriores a él;
        si no, los últimos `recent_limit` mensajes.
        """
        summary = self.get_latest_summary(session_id)
        messages = self._messages_after(session_id, summary, limit=recent_limit)
        return {
            "summary":  summary.summary if summary else None,
            "messages": messages,
        }

    # ── internals ─────────────────────────────────────────────────────────────

    def _messages_after(
        self,
        session_id: str,
        after:      Optional[ConversationSummary],
        limit:      Optional[int] = None,
    ) -> list[MemoryEntry]:
        """Mensajes posteriores al resumen `after` (o todos si es None)."""
        if after is not None and after.up_to_message_id:
            cutoff = self._db.query_one(
                "SELECT created_at FROM messages WHERE id = ?",
                (after.up_to_message_id,),
            )
            if cutoff is not None:
                sql = (
                    "SELECT id, role, content, conversation_id, created_at, metadata, tokens_used "
                    "FROM messages WHERE conversation_id = ? AND created_at > ? "
                    "ORDER BY created_at ASC"
                )
                params: tuple = (session_id, cutoff["created_at"])
                if limit:
                    sql += " LIMIT ?"
                    params = (session_id, cutoff["created_at"], limit)
                return [self._row_to_entry(r) for r in self._db.query(sql, params)]

        # Sin resumen: últimos `limit` (o todos) en orden cronológico.
        if limit:
            return self.get_session(session_id, limit=limit)
        rows = self._db.query(
            "SELECT id, role, content, conversation_id, created_at, metadata, tokens_used "
            "FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (session_id,),
        )
        return [self._row_to_entry(r) for r in rows]

    @staticmethod
    def _row_to_entry(r) -> MemoryEntry:
        return MemoryEntry(
            id          = r["id"],
            role        = r["role"],
            content     = r["content"],
            session_id  = r["conversation_id"],
            created_at  = r["created_at"],
            metadata    = json.loads(r["metadata"]),
            tokens_used = r["tokens_used"],
        )
