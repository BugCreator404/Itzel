"""
Tests de la capa de base de datos (itzel_core.db).

Cubre schema, versionado, cifrado real (SQLCipher), migración del schema legacy
(messages.session_id) y migración de una BD heredada en claro a cifrada.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from itzel_core.db import Database, _HAS_CIPHER
from sqlcipher3 import dbapi2 as sqlcipher

# Clave de prueba fija (igual que en conftest.py).
TEST_KEY = "0123456789abcdef" * 4

pytestmark = pytest.mark.filterwarnings("ignore")


# ─── schema ───────────────────────────────────────────────────────────────────

class TestSchema:
    def test_tables_created(self, mem_db):
        rows = mem_db.query(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        names = {r[0] for r in rows}
        assert names == {
            "conversations", "messages", "conversation_summaries",
            "actions_log", "user_preferences", "cache_entries",
        }

    def test_schema_version(self, mem_db):
        assert mem_db.schema_version == 1

    def test_role_check_constraint(self, mem_db):
        mem_db.execute("INSERT INTO conversations VALUES('c1','t','t','m','es','t')")
        with pytest.raises(Exception):
            mem_db.execute(
                "INSERT INTO messages VALUES('m1','c1','INVALID','x','t',0,'{}')"
            )

    def test_query_helpers(self, mem_db):
        mem_db.execute("INSERT INTO user_preferences VALUES('k','v','t')")
        assert mem_db.query_one("SELECT value FROM user_preferences WHERE key='k'")["value"] == "v"
        assert mem_db.query_one("SELECT * FROM user_preferences WHERE key='none'") is None
        assert len(mem_db.query("SELECT * FROM user_preferences")) == 1


# ─── FK cascade ───────────────────────────────────────────────────────────────

class TestCascade:
    def test_delete_conversation_cascades(self, mem_db):
        mem_db.execute("INSERT INTO conversations VALUES('c1','t','t','m','es','t')")
        mem_db.execute("INSERT INTO messages VALUES('m1','c1','user','hola','t',0,'{}')")
        mem_db.execute(
            "INSERT INTO conversation_summaries VALUES('s1','c1','resumen','m1',5,'t')"
        )
        mem_db.execute("DELETE FROM conversations WHERE id='c1'")
        assert mem_db.query("SELECT * FROM messages WHERE conversation_id='c1'") == []
        assert mem_db.query("SELECT * FROM conversation_summaries WHERE conversation_id='c1'") == []


# ─── cifrado ──────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not _HAS_CIPHER, reason="SQLCipher no disponible")
class TestEncryption:
    def test_encrypted_roundtrip(self, tmp_path):
        path = tmp_path / "enc.db"
        db = Database(path=path, key=TEST_KEY)
        assert db.is_encrypted is True
        db.execute("INSERT INTO user_preferences VALUES('lang','es-MX','t')")
        db.close()

        # Reabrir con la clave correcta
        db2 = Database(path=path, key=TEST_KEY)
        assert db2.query_one("SELECT value FROM user_preferences WHERE key='lang'")["value"] == "es-MX"
        db2.close()

    def test_wrong_key_rejected(self, tmp_path):
        path = tmp_path / "enc.db"
        db = Database(path=path, key=TEST_KEY)
        db.execute("INSERT INTO user_preferences VALUES('x','y','t')")
        db.close()

        with pytest.raises(Exception):
            bad = Database(path=path, key="f" * 64)
            bad.query("SELECT * FROM user_preferences")

    def test_file_is_actually_encrypted(self, tmp_path):
        path = tmp_path / "enc.db"
        db = Database(path=path, key=TEST_KEY)
        db.execute("INSERT INTO user_preferences VALUES('secreto','plaintext-marker','t')")
        db.close()
        raw = path.read_bytes()
        # El archivo cifrado no empieza con la cabecera SQLite y no contiene el marcador.
        assert not raw.startswith(b"SQLite format 3\x00")
        assert b"plaintext-marker" not in raw


# ─── migración del schema legacy ──────────────────────────────────────────────

class TestLegacyMigration:
    def test_legacy_messages_migrated(self, tmp_path):
        path = tmp_path / "legacy.db"
        # Crear una BD con el schema viejo (messages.session_id), sin cifrar.
        conn = sqlcipher.connect(str(path))
        conn.execute(
            "CREATE TABLE messages (id TEXT PRIMARY KEY, role TEXT, content TEXT, "
            "session_id TEXT, created_at TEXT, metadata TEXT DEFAULT '{}')"
        )
        conn.execute("INSERT INTO messages VALUES('m1','user','hola','sA','2026-01-01T00:00:00','{}')")
        conn.execute("INSERT INTO messages VALUES('m2','assistant','hi','sA','2026-01-01T00:01:00','{}')")
        conn.execute("INSERT INTO messages VALUES('m3','user','otra','sB','2026-01-02T00:00:00','{}')")
        conn.commit()
        conn.close()

        # Abrir con Database (sin clave) → migra a v1.
        db = Database(path=path, key=None)
        assert db.schema_version == 1
        convs = {r[0] for r in db.query("SELECT id FROM conversations")}
        assert convs == {"sA", "sB"}
        msgs = db.query("SELECT id, conversation_id FROM messages ORDER BY id")
        assert [(m[0], m[1]) for m in msgs] == [("m1", "sA"), ("m2", "sA"), ("m3", "sB")]
        # La tabla legacy fue eliminada.
        assert db.query("SELECT name FROM sqlite_master WHERE name='messages_legacy'") == []
        db.close()


# ─── migración plaintext → cifrada ────────────────────────────────────────────

@pytest.mark.skipif(not _HAS_CIPHER, reason="SQLCipher no disponible")
class TestPlaintextMigration:
    def test_plaintext_db_migrated_to_encrypted(self, tmp_path):
        path = tmp_path / "memory.db"
        # BD en claro con datos.
        conn = sqlcipher.connect(str(path))
        conn.execute(
            "CREATE TABLE messages (id TEXT PRIMARY KEY, role TEXT, content TEXT, "
            "session_id TEXT, created_at TEXT, metadata TEXT DEFAULT '{}')"
        )
        conn.execute("INSERT INTO messages VALUES('m1','user','dato','sA','2026-01-01T00:00:00','{}')")
        conn.commit()
        conn.close()
        assert path.read_bytes()[:16] == b"SQLite format 3\x00"

        # Abrir con clave → debe migrar a cifrada con backup.
        db = Database(path=path, key=TEST_KEY)
        assert db.is_encrypted is True
        assert db.query_one("SELECT content FROM messages")["content"] == "dato"
        db.close()

        # El archivo quedó cifrado y existe el backup en claro.
        assert path.read_bytes()[:16] != b"SQLite format 3\x00"
        backup = Path(str(path) + ".plaintext-bak")
        assert backup.exists()
