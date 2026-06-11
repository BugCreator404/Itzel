-- ─────────────────────────────────────────────────────────────────────────────
-- Itzel — Schema de la base de datos cifrada (SQLCipher)
--
-- Todas las fechas se guardan como TEXT en formato ISO-8601 UTC. SQLite no tiene
-- un tipo DATETIME real (solo afinidad), y TEXT ISO ordena cronológicamente con
-- comparación lexicográfica. Consistente con el código existente del core.
--
-- Versión del schema: se controla con PRAGMA user_version (ver db.py).
-- Este archivo representa el schema en su versión más reciente (v1).
-- ─────────────────────────────────────────────────────────────────────────────

-- ── Conversaciones ───────────────────────────────────────────────────────────
-- Una conversación agrupa mensajes. El "session_id" de la API == conversations.id
CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    model      TEXT,
    language   TEXT,
    title      TEXT
);

CREATE INDEX IF NOT EXISTS idx_conversations_updated
    ON conversations(updated_at);

-- ── Mensajes ─────────────────────────────────────────────────────────────────
-- Borrar una conversación borra sus mensajes (ON DELETE CASCADE).
CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL
                    REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    tokens_used     INTEGER NOT NULL DEFAULT 0,
    metadata        TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, created_at);

-- ── Resúmenes episódicos ─────────────────────────────────────────────────────
-- Cuando una conversación supera el umbral de tokens, se genera un resumen
-- con el modelo y se usa como contexto comprimido en turnos futuros.
CREATE TABLE IF NOT EXISTS conversation_summaries (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL
                     REFERENCES conversations(id) ON DELETE CASCADE,
    summary          TEXT NOT NULL,
    up_to_message_id TEXT,           -- último mensaje incluido en el resumen
    token_count      INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_summaries_conversation
    ON conversation_summaries(conversation_id, created_at);

-- ── Log de acciones de los agentes (auditoría cifrada) ───────────────────────
-- Sink dual: el ActionLogger de itzel-agents también escribe aquí.
CREATE TABLE IF NOT EXISTS actions_log (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    agent      TEXT,
    action     TEXT,
    target     TEXT,        -- ruta / app / objetivo de la acción
    details    TEXT,        -- JSON con args adicionales
    result     TEXT         -- 'ok' | 'denied' | 'error'
);

CREATE INDEX IF NOT EXISTS idx_actions_created
    ON actions_log(created_at);

-- ── Preferencias del usuario ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_preferences (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT NOT NULL
);

-- ── Cache L2 (persistente) ───────────────────────────────────────────────────
-- key = hash(prompt + modelo + parámetros). expires_at controla el TTL.
CREATE TABLE IF NOT EXISTS cache_entries (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cache_expires
    ON cache_entries(expires_at);
