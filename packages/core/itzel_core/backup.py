"""
Backup y restauración de la memoria de Itzel.

El export produce un archivo .db.enc cifrado y PORTABLE: se cifra con una
passphrase que el usuario elige, no con la clave del keychain. Así el backup
puede restaurarse en otra máquina (donde no está la clave original).

Operaciones:
    export_backup(dest, passphrase)  → escribe un .db.enc portable.
    import_backup(src, passphrase)   → restaura, respaldando antes la BD actual.
    auto_backup_if_due()             → backup semanal automático a data/backups/.

Seguridad:
    - Antes de sobrescribir la BD en import, se respalda la actual (reversible).
    - El backup va cifrado; sin la passphrase no se puede leer.

English summary:
    Portable encrypted export/import of the memory DB (passphrase-based, not the
    keychain key) + weekly auto-backup. Import backs up the current DB first.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

try:
    from sqlcipher3 import dbapi2 as sqlite  # type: ignore
    _HAS_CIPHER = True
except ImportError:
    import sqlite3 as sqlite  # type: ignore[no-redef]
    _HAS_CIPHER = False

from .config import config
from .db import DB_PATH, Database, get_or_create_key

log = logging.getLogger("itzel.backup")

_BACKUP_PREF_KEY = "last_auto_backup"   # clave en user_preferences


def _now() -> datetime:
    return datetime.now(UTC)


def _backup_dir() -> Path:
    """Directorio de backups (config.db.backup_dir, relativo a ~/.itzel)."""
    base = config.db.backup_dir
    p = Path(base)
    if not p.is_absolute():
        p = Path.home() / ".itzel" / base
    p.mkdir(parents=True, exist_ok=True)
    return p


# ─── export ───────────────────────────────────────────────────────────────────

def export_backup(
    dest:       Path | None = None,
    *,
    passphrase: str | None = None,
    db_path:    Path = DB_PATH,
    src_key:    str | None = "__auto__",
) -> Path:
    """
    Exporta la BD a un archivo .db.enc portable.

    Args:
        dest:       Ruta del archivo de salida (default: data/backups/itzel-<fecha>.db.enc).
        passphrase: Passphrase para cifrar el backup. Si es None, se usa la clave
                    del keychain (backup NO portable, solo restaurable aquí).
        db_path:    BD de origen.
        src_key:    Clave de la BD origen. "__auto__" usa la del keychain;
                    None abre sin cifrado (tests / BD en claro).

    Returns:
        La ruta del backup creado.
    """
    if dest is None:
        stamp = _now().strftime("%Y%m%d-%H%M%S")
        dest = _backup_dir() / f"itzel-{stamp}.db.enc"
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if src_key == "__auto__":
        src_key = get_or_create_key()
    out_key = passphrase or src_key

    if not _HAS_CIPHER:
        # Sin SQLCipher no hay cifrado real: copia directa con aviso.
        log.warning("SQLCipher no disponible — backup SIN cifrar.")
        import shutil
        shutil.copy2(db_path, dest)
        return dest

    # Abrir la BD origen (con su clave) y exportar a una nueva cifrada con out_key.
    conn = sqlite.connect(str(db_path))
    try:
        if src_key:
            conn.execute(f"PRAGMA key = '{src_key.replace(chr(39), chr(39)*2)}'")
            conn.execute("SELECT count(*) FROM sqlite_master")  # fuerza descifrado
        dest.unlink(missing_ok=True)
        safe_out = (out_key or "").replace("'", "''")
        safe_dest = str(dest).replace("'", "''")
        conn.execute(f"ATTACH DATABASE '{safe_dest}' AS bak KEY '{safe_out}'")
        conn.execute("SELECT sqlcipher_export('bak')")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.execute(f"PRAGMA bak.user_version = {version}")
        conn.execute("DETACH DATABASE bak")
    finally:
        conn.close()

    log.info("Backup exportado a %s", dest)
    return dest


# ─── import ───────────────────────────────────────────────────────────────────

def import_backup(
    src:        Path,
    *,
    passphrase: str | None = None,
    db_path:    Path = DB_PATH,
    local_key:  str | None = "__auto__",
) -> Path:
    """
    Restaura un backup .db.enc, reemplazando la BD actual.

    La BD actual se respalda antes (db_path + .pre-import-bak) para poder
    deshacer (principio #5: no perder datos irreversiblemente).

    Args:
        src:        Archivo .db.enc a restaurar.
        passphrase: Passphrase con que se cifró el backup (None = clave keychain).
        db_path:    BD destino.

    Returns:
        La ruta de la BD restaurada.

    Raises:
        FileNotFoundError: si el backup no existe.
        ValueError:        si el backup no se puede descifrar (passphrase mala).
    """
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(f"Backup no encontrado: {src}")

    if local_key == "__auto__":
        local_key = get_or_create_key()
    bak_key = passphrase or local_key
    new_key = local_key             # clave local con la que quedará la BD restaurada

    if not _HAS_CIPHER:
        import shutil
        if db_path.exists():
            shutil.copy2(db_path, db_path.with_suffix(db_path.suffix + ".pre-import-bak"))
        shutil.copy2(src, db_path)
        return db_path

    # Verificar que el backup se puede descifrar con la passphrase dada.
    test = sqlite.connect(str(src))
    try:
        if bak_key:
            test.execute(f"PRAGMA key = '{bak_key.replace(chr(39), chr(39)*2)}'")
        test.execute("SELECT count(*) FROM sqlite_master")
    except Exception as exc:
        test.close()
        raise ValueError(
            "No se pudo descifrar el backup — passphrase incorrecta o archivo dañado."
        ) from exc

    # Respaldar la BD actual antes de reemplazarla.
    if db_path.exists():
        pre = db_path.with_suffix(db_path.suffix + ".pre-import-bak")
        pre.unlink(missing_ok=True)
        db_path.replace(pre)
        log.info("BD actual respaldada en %s antes de restaurar.", pre)
    for ext in ("-wal", "-shm"):
        Path(str(db_path) + ext).unlink(missing_ok=True)

    # Exportar el backup (con su passphrase) a la BD destino cifrada con la clave local.
    try:
        safe_new = new_key.replace("'", "''") if new_key else ""
        safe_dest = str(db_path).replace("'", "''")
        if new_key:
            test.execute(f"ATTACH DATABASE '{safe_dest}' AS dst KEY '{safe_new}'")
        else:
            test.execute(f"ATTACH DATABASE '{safe_dest}' AS dst KEY ''")
        test.execute("SELECT sqlcipher_export('dst')")
        version = test.execute("PRAGMA user_version").fetchone()[0]
        test.execute(f"PRAGMA dst.user_version = {version}")
        test.execute("DETACH DATABASE dst")
    finally:
        test.close()

    log.info("Backup restaurado desde %s", src)
    return db_path


# ─── backup automático semanal ────────────────────────────────────────────────

def auto_backup_if_due(db: Database | None = None) -> Path | None:
    """
    Crea un backup si pasó una semana desde el último (o si nunca hubo).

    Lee/escribe la fecha del último backup en user_preferences. Respeta
    config.db.backup_weekly: si está desactivado, no hace nada.

    Returns:
        La ruta del backup creado, o None si no tocaba.
    """
    if not config.db.backup_weekly:
        return None

    from .db import get_db
    db = db or get_db()

    row = db.query_one(
        "SELECT value FROM user_preferences WHERE key = ?", (_BACKUP_PREF_KEY,)
    )
    last = row["value"] if row else None
    due = True
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            due = _now() - last_dt >= timedelta(days=7)
        except ValueError:
            due = True

    if not due:
        return None

    dest = export_backup()
    now_iso = _now().isoformat()
    db.execute(
        "INSERT OR REPLACE INTO user_preferences (key, value, updated_at) "
        "VALUES (?,?,?)",
        (_BACKUP_PREF_KEY, now_iso, now_iso),
    )
    log.info("Backup automático semanal creado: %s", dest)
    return dest
