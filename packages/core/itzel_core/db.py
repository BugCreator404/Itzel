"""
Capa central de base de datos — SQLite cifrado con SQLCipher.

Responsabilidades:
    - Abrir la conexión cifrada (PRAGMA key) con WAL y foreign_keys.
    - Obtener/crear la clave de cifrado en el keychain del OS (keyring);
      si keyring no está disponible, cae a un archivo con permisos 0600.
    - Aplicar el schema y ejecutar migraciones versionadas (PRAGMA user_version).

Seguridad (principio #8: credenciales en el keychain del OS):
    La clave de la BD NUNCA se guarda junto a los datos. Vive en el keychain
    (Windows Credential Manager / macOS Keychain / Secret Service en Linux).
    El fallback a archivo 0600 es solo para entornos sin keyring, y se avisa.

English summary:
    Central SQLCipher-encrypted DB layer: keychain-backed key, WAL mode, and
    versioned migrations via PRAGMA user_version.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
from pathlib import Path
from typing import Any, Optional

# SQLCipher si está disponible; si no, sqlite3 estándar (sin cifrado real).
try:
    from sqlcipher3 import dbapi2 as sqlite  # type: ignore
    _HAS_CIPHER = True
except ImportError:
    import sqlite3 as sqlite  # type: ignore[no-redef]
    _HAS_CIPHER = False

log = logging.getLogger("itzel.db")

# ─── constantes ───────────────────────────────────────────────────────────────

DB_PATH        = Path.home() / ".itzel" / "memory.db"
_SCHEMA_PATH   = Path(__file__).parent / "schema.sql"
_KEY_FILE      = Path.home() / ".itzel" / "db.key"
_KEY_SERVICE   = "itzel"
_KEY_USERNAME  = "db-key"
_SCHEMA_VERSION = 1   # versión actual del schema (ver schema.sql)


# ─── gestión de la clave de cifrado ───────────────────────────────────────────

def _generate_key() -> str:
    """Genera una clave hexadecimal de 256 bits."""
    return secrets.token_hex(32)


def get_or_create_key() -> Optional[str]:
    """
    Obtiene la clave de cifrado de la BD, creándola en el primer arranque.

    Orden de preferencia:
        1. keyring (keychain del OS) — recomendado.
        2. Archivo ~/.itzel/db.key con permisos 0600 — fallback con aviso.

    Returns:
        La clave (hex string), o None si SQLCipher no está disponible
        (en cuyo caso la BD no se cifra y se trabaja en claro).
    """
    if not _HAS_CIPHER:
        log.warning(
            "SQLCipher no disponible — la BD NO estará cifrada. "
            "Instala sqlcipher3 para cifrado AES-256."
        )
        return None

    # 1. keyring (keychain del OS)
    try:
        import keyring
        existing = keyring.get_password(_KEY_SERVICE, _KEY_USERNAME)
        if existing:
            return existing
        # Transición: si antes se usó el fallback de archivo, importar esa clave
        # al keychain en vez de generar una nueva (evita perder acceso a la BD).
        if _KEY_FILE.exists():
            file_key = _KEY_FILE.read_text(encoding="utf-8").strip()
            keyring.set_password(_KEY_SERVICE, _KEY_USERNAME, file_key)
            log.info("Clave de BD migrada del archivo fallback al keychain del OS.")
            return file_key
        new_key = _generate_key()
        keyring.set_password(_KEY_SERVICE, _KEY_USERNAME, new_key)
        log.info("Clave de BD creada y guardada en el keychain del OS.")
        return new_key
    except Exception as exc:
        log.warning(
            "keyring no disponible (%s) — usando fallback de archivo 0600. "
            "Instala 'keyring' para guardar la clave en el keychain del OS.",
            exc,
        )

    # 2. Fallback: archivo con permisos restringidos
    return _key_from_file()


def _key_from_file() -> str:
    """Lee o crea la clave en ~/.itzel/db.key con permisos 0600."""
    if _KEY_FILE.exists():
        return _KEY_FILE.read_text(encoding="utf-8").strip()

    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    key = _generate_key()
    # Crear el archivo y restringir permisos antes de escribir el contenido.
    _KEY_FILE.touch(mode=0o600, exist_ok=True)
    try:
        os.chmod(_KEY_FILE, 0o600)   # no-op funcional en Windows, pero no falla
    except OSError:
        pass
    _KEY_FILE.write_text(key, encoding="utf-8")
    log.warning(
        "Clave de BD guardada en %s (permisos 0600). "
        "Para mayor seguridad, instala 'keyring'.", _KEY_FILE,
    )
    return key


# ─── la base de datos ─────────────────────────────────────────────────────────

class Database:
    """
    Conexión cifrada a la BD de Itzel, con schema y migraciones.

    Uso:
        db = Database()                 # ~/.itzel/memory.db, clave del keychain
        rows = db.query("SELECT * FROM conversations")
        db.execute("INSERT INTO user_preferences VALUES (?,?,?)", (...))

    Para tests:
        db = Database(path=":memory:", key=None)   # en memoria, sin cifrado
    """

    def __init__(
        self,
        path: Path | str = DB_PATH,
        key:  Optional[str] = "__auto__",
    ) -> None:
        self._path = path
        self._is_memory = str(path) == ":memory:"
        # key="__auto__" → resolver del keychain; key=None → sin cifrado; key=str → usar esa.
        if key == "__auto__":
            self._key = None if self._is_memory else get_or_create_key()
        else:
            self._key = key
        self._lock = threading.RLock()
        self._conn = self._open()
        self._init_schema()

    # ── conexión ──────────────────────────────────────────────────────────────

    def _open(self):
        """
        Abre la conexión cifrada. Si encuentra una BD heredada EN CLARO (creada
        antes de activar el cifrado), la migra a cifrada preservando un backup.
        """
        conn = self._connect()
        if self._verify(conn):
            self._apply_pragmas(conn)
            return conn

        # No se pudo leer con la clave. ¿Es una BD legacy en claro y migrable?
        conn.close()
        if self._key and not self._is_memory and self._is_plaintext_sqlite():
            log.warning(
                "BD heredada en CLARO detectada — migrando a cifrada "
                "(se conserva un backup .plaintext-bak)."
            )
            self._migrate_plaintext_to_encrypted()
            conn = self._connect()
            if self._verify(conn):
                self._apply_pragmas(conn)
                log.info("BD migrada a cifrada correctamente.")
                return conn
            conn.close()

        raise sqlite.DatabaseError(
            "No se pudo abrir la BD: clave incorrecta o archivo corrupto."
        )

    def _connect(self):
        """Abre la conexión y aplica SOLO PRAGMA key (sin tocar la BD aún)."""
        if not self._is_memory:
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite.connect(
            str(self._path),
            check_same_thread=False,   # acceso desde threads de FastAPI (serializado por _lock)
        )
        # Cifrado: PRAGMA key debe ir ANTES de cualquier lectura de la BD.
        if _HAS_CIPHER and self._key:
            safe_key = self._key.replace("'", "''")
            conn.execute(f"PRAGMA key = '{safe_key}'")
        conn.row_factory = sqlite.Row
        return conn

    def _apply_pragmas(self, conn) -> None:
        """Aplica WAL y foreign_keys — solo tras verificar que la BD se lee."""
        if not self._is_memory:
            conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _verify(conn) -> bool:
        """True si la conexión puede leer el catálogo (clave correcta / sin cifrar)."""
        try:
            conn.execute("SELECT count(*) FROM sqlite_master")
            return True
        except Exception:
            return False

    def _is_plaintext_sqlite(self) -> bool:
        """True si el archivo existe y tiene la cabecera de SQLite SIN cifrar."""
        p = Path(self._path)
        if not p.exists() or p.stat().st_size < 16:
            return False
        try:
            with p.open("rb") as f:
                return f.read(16) == b"SQLite format 3\x00"
        except OSError:
            return False

    def _migrate_plaintext_to_encrypted(self) -> None:
        """
        Migra la BD en claro a una cifrada con la clave actual (sqlcipher_export).
        El original se conserva como <db>.plaintext-bak (reversible).
        """
        src = Path(self._path)
        tmp_enc = src.with_suffix(src.suffix + ".enc-tmp")
        tmp_enc.unlink(missing_ok=True)

        plain = sqlite.connect(str(src))
        try:
            safe_key = self._key.replace("'", "''")
            safe_path = str(tmp_enc).replace("'", "''")
            plain.execute(f"ATTACH DATABASE '{safe_path}' AS enc KEY '{safe_key}'")
            plain.execute("SELECT sqlcipher_export('enc')")
            # Conservar la versión de schema para que la migración v0→v1 siga su curso.
            user_version = plain.execute("PRAGMA user_version").fetchone()[0]
            plain.execute(f"PRAGMA enc.user_version = {user_version}")
            plain.execute("DETACH DATABASE enc")
        finally:
            plain.close()

        # Respaldar el original en claro y reemplazarlo por el cifrado.
        backup = src.with_suffix(src.suffix + ".plaintext-bak")
        backup.unlink(missing_ok=True)
        src.replace(backup)
        tmp_enc.replace(src)
        # Limpiar WAL/SHM colaterales del archivo en claro, si quedaron.
        for ext in ("-wal", "-shm"):
            Path(str(src) + ext).unlink(missing_ok=True)

    # ── schema + migraciones ──────────────────────────────────────────────────

    def _init_schema(self) -> None:
        """Aplica el schema y ejecuta migraciones hasta la versión actual."""
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version >= _SCHEMA_VERSION:
                return
            log.info("Migrando BD de schema v%d → v%d", version, _SCHEMA_VERSION)
            if version < 1:
                self._migrate_v0_to_v1()
            self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self._conn.commit()

    def _migrate_v0_to_v1(self) -> None:
        """
        v0 → v1: crea el schema completo.

        Si existe una tabla `messages` legacy (con columna session_id pero sin
        conversation_id), migra sus datos a las nuevas tablas conversations +
        messages sin pérdida.
        """
        legacy = self._is_legacy_messages()

        if legacy:
            # Preservar los mensajes viejos antes de crear el nuevo schema.
            self._conn.execute("ALTER TABLE messages RENAME TO messages_legacy")

        # Aplicar schema completo (IF NOT EXISTS respeta lo ya creado).
        self._conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))

        if legacy:
            self._migrate_legacy_messages()
            self._conn.execute("DROP TABLE messages_legacy")
            log.info("Datos legacy de 'messages' migrados a conversations + messages.")

    def _is_legacy_messages(self) -> bool:
        """True si existe `messages` con session_id y sin conversation_id."""
        cols = self._column_names("messages")
        return bool(cols) and "session_id" in cols and "conversation_id" not in cols

    def _migrate_legacy_messages(self) -> None:
        """Convierte messages_legacy (session_id) → conversations + messages."""
        cols = self._column_names("messages_legacy")
        has_metadata = "metadata" in cols

        # Crear una conversación por cada session_id distinto.
        sessions = self._conn.execute(
            "SELECT session_id, MIN(created_at) AS first, MAX(created_at) AS last "
            "FROM messages_legacy GROUP BY session_id"
        ).fetchall()
        for s in sessions:
            self._conn.execute(
                "INSERT OR IGNORE INTO conversations "
                "(id, created_at, updated_at, model, language, title) "
                "VALUES (?,?,?,?,?,?)",
                (s["session_id"], s["first"], s["last"], None, None, None),
            )

        # Copiar los mensajes con conversation_id = session_id.
        meta_expr = "metadata" if has_metadata else "'{}'"
        self._conn.execute(
            f"INSERT INTO messages "
            f"(id, conversation_id, role, content, created_at, tokens_used, metadata) "
            f"SELECT id, session_id, role, content, created_at, 0, {meta_expr} "
            f"FROM messages_legacy"
        )

    def _column_names(self, table: str) -> list[str]:
        """Nombres de columnas de una tabla (vacío si no existe)."""
        try:
            return [r[1] for r in self._conn.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()]
        except Exception:
            return []

    # ── API de consultas ──────────────────────────────────────────────────────

    def execute(self, sql: str, params: tuple = ()) -> Any:
        """Ejecuta una sentencia de escritura y hace commit. Devuelve el cursor."""
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def executemany(self, sql: str, seq: list[tuple]) -> None:
        with self._lock:
            self._conn.executemany(sql, seq)
            self._conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list:
        """Ejecuta un SELECT y devuelve todas las filas (sqlite.Row)."""
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()):
        """Ejecuta un SELECT y devuelve la primera fila o None."""
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    # ── metadata / utilidades ─────────────────────────────────────────────────

    @property
    def is_encrypted(self) -> bool:
        """True si la BD está realmente cifrada (SQLCipher + clave)."""
        return _HAS_CIPHER and self._key is not None

    @property
    def schema_version(self) -> int:
        return self._conn.execute("PRAGMA user_version").fetchone()[0]

    @property
    def connection(self):
        """Conexión cruda — para módulos que comparten la misma BD (memory, cache)."""
        return self._conn

    @property
    def lock(self) -> threading.RLock:
        """Lock compartido para serializar accesos entre módulos."""
        return self._lock

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ─── instancia compartida (lazy) ──────────────────────────────────────────────

_shared: Optional[Database] = None
_shared_lock = threading.Lock()


def get_db() -> Database:
    """Devuelve la instancia compartida de la BD (la crea en el primer uso)."""
    global _shared
    if _shared is None:
        with _shared_lock:
            if _shared is None:
                _shared = Database()
    return _shared
