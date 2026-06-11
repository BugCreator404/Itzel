"""
Tests de backup/restauración (itzel_core.backup).

export/import se prueban con claves inyectadas y rutas temporales (nunca tocan
la BD real). auto_backup_if_due se prueba mockeando export_backup para verificar
solo la lógica de programación semanal.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from itzel_core import backup as backup_mod
from itzel_core.backup import auto_backup_if_due, export_backup, import_backup
from itzel_core.db import Database, _HAS_CIPHER

TEST_KEY   = "0123456789abcdef" * 4   # clave "origen"
LOCAL_KEY  = "fedcba9876543210" * 4   # clave "local" de la máquina destino


def _seed(path: Path, key) -> None:
    db = Database(path=path, key=key)
    db.execute("INSERT INTO conversations VALUES('c1','t','t','itzel-1b','es-MX','Charla')")
    db.execute("INSERT INTO messages VALUES('m1','c1','user','dato secreto','t',3,'{}')")
    db.close()


# ─── export / import ──────────────────────────────────────────────────────────

@pytest.mark.skipif(not _HAS_CIPHER, reason="SQLCipher no disponible")
class TestExportImport:
    def test_export_creates_encrypted_file(self, tmp_path):
        src = tmp_path / "memory.db"
        _seed(src, TEST_KEY)
        dest = tmp_path / "backup.db.enc"
        out = export_backup(dest, passphrase="portable", db_path=src, src_key=TEST_KEY)
        assert out.exists()
        assert out.read_bytes()[:16] != b"SQLite format 3\x00"

    def test_roundtrip_to_other_machine(self, tmp_path):
        """Backup portable: restaurar con una clave local DISTINTA preserva datos."""
        src = tmp_path / "memory.db"
        _seed(src, TEST_KEY)
        backup_file = tmp_path / "backup.db.enc"
        export_backup(backup_file, passphrase="portable", db_path=src, src_key=TEST_KEY)

        dest = tmp_path / "restaurada.db"
        import_backup(backup_file, passphrase="portable", db_path=dest, local_key=LOCAL_KEY)

        db = Database(path=dest, key=LOCAL_KEY)
        assert db.query_one("SELECT content FROM messages")["content"] == "dato secreto"
        db.close()

    def test_wrong_passphrase_raises(self, tmp_path):
        src = tmp_path / "memory.db"
        _seed(src, TEST_KEY)
        backup_file = tmp_path / "backup.db.enc"
        export_backup(backup_file, passphrase="correcta", db_path=src, src_key=TEST_KEY)

        with pytest.raises(ValueError):
            import_backup(backup_file, passphrase="incorrecta",
                          db_path=tmp_path / "x.db", local_key=LOCAL_KEY)

    def test_import_backs_up_current_db(self, tmp_path):
        src = tmp_path / "memory.db"
        _seed(src, TEST_KEY)
        backup_file = tmp_path / "backup.db.enc"
        export_backup(backup_file, passphrase="p", db_path=src, src_key=TEST_KEY)

        # BD destino existente (se debe respaldar antes de restaurar).
        dest = tmp_path / "actual.db"
        _seed(dest, LOCAL_KEY)
        import_backup(backup_file, passphrase="p", db_path=dest, local_key=LOCAL_KEY)

        pre = Path(str(dest) + ".pre-import-bak")
        assert pre.exists()

    def test_missing_backup_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            import_backup(tmp_path / "noexiste.db.enc", passphrase="p",
                          db_path=tmp_path / "x.db", local_key=LOCAL_KEY)


# ─── auto backup semanal ──────────────────────────────────────────────────────

class TestAutoBackup:
    def test_first_backup_is_due(self, mem_db, monkeypatch):
        calls = []
        monkeypatch.setattr(backup_mod, "export_backup",
                            lambda *a, **k: calls.append(1) or Path("/fake/bak.db.enc"))
        result = auto_backup_if_due(db=mem_db)
        assert result is not None
        assert len(calls) == 1
        # Quedó registrada la fecha del último backup.
        row = mem_db.query_one("SELECT value FROM user_preferences WHERE key='last_auto_backup'")
        assert row is not None

    def test_not_due_within_week(self, mem_db, monkeypatch):
        calls = []
        monkeypatch.setattr(backup_mod, "export_backup",
                            lambda *a, **k: calls.append(1) or Path("/fake/bak.db.enc"))
        # Registrar un backup reciente (ayer).
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        mem_db.execute(
            "INSERT INTO user_preferences VALUES('last_auto_backup', ?, ?)",
            (yesterday, yesterday),
        )
        result = auto_backup_if_due(db=mem_db)
        assert result is None
        assert calls == []

    def test_due_after_week(self, mem_db, monkeypatch):
        calls = []
        monkeypatch.setattr(backup_mod, "export_backup",
                            lambda *a, **k: calls.append(1) or Path("/fake/bak.db.enc"))
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        mem_db.execute(
            "INSERT INTO user_preferences VALUES('last_auto_backup', ?, ?)",
            (old, old),
        )
        result = auto_backup_if_due(db=mem_db)
        assert result is not None
        assert len(calls) == 1

    def test_disabled_by_config(self, mem_db, monkeypatch):
        from itzel_core.config import config
        monkeypatch.setattr(config.db, "backup_weekly", False)
        calls = []
        monkeypatch.setattr(backup_mod, "export_backup",
                            lambda *a, **k: calls.append(1) or Path("/fake/bak.db.enc"))
        assert auto_backup_if_due(db=mem_db) is None
        assert calls == []
