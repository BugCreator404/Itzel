"""
Cache de respuestas L1/L2 para Itzel.

Evita recomputar respuestas idénticas del modelo:
    L1 — diccionario LRU en memoria (rápido, vida = sesión del proceso).
    L2 — tabla en la BD cifrada (persistente, TTL configurable, default 24h).

Flujo de lectura:
    get(key) → L1 hit?  → devuelve
             → L2 hit (no expirado)? → promueve a L1 y devuelve
             → miss → None

La clave se deriva de hash(prompt + modelo + parámetros), de modo que el mismo
prompt con los mismos parámetros y modelo reusa la respuesta. Un cache hit
devuelve la respuesta al instante; la mascota no cambia de estado (no "piensa").

English summary:
    Two-level response cache: in-memory LRU (L1) + encrypted SQLite table (L2)
    with TTL. Keys are sha256(prompt + model + params).
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .config import config
from .db import Database, get_db


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ─── cache ────────────────────────────────────────────────────────────────────

class ResponseCache:
    """
    Cache de respuestas en dos niveles (L1 memoria + L2 SQLite).

    Uso:
        cache = ResponseCache()
        key = cache.make_key("¿hora?", model="itzel-1b", params={"temperature": 0.7})
        hit = cache.get(key)
        if hit is None:
            resp = generar(...)
            cache.set(key, resp)
    """

    def __init__(
        self,
        db:      Optional[Database] = None,
        *,
        l1_max:  Optional[int] = None,
        l2_ttl_s: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self._db      = db or get_db()
        self._l1_max  = l1_max if l1_max is not None else config.cache.l1_max
        self._l2_ttl  = l2_ttl_s if l2_ttl_s is not None else config.cache.l2_ttl_s
        self.enabled  = enabled if enabled is not None else config.cache.enabled

        # L1 guarda (valor, expires_at_iso) para respetar el TTL en memoria.
        self._l1: "OrderedDict[str, tuple[str, str]]" = OrderedDict()
        self._lock = threading.Lock()

    # ── claves ────────────────────────────────────────────────────────────────

    @staticmethod
    def make_key(prompt: str, *, model: str, params: Optional[dict] = None) -> str:
        """Deriva una clave estable de prompt + modelo + parámetros."""
        payload = json.dumps(
            {"prompt": prompt, "model": model, "params": params or {}},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ── lectura ───────────────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[str]:
        """Devuelve el valor cacheado (L1 o L2 no expirado), o None."""
        if not self.enabled:
            return None

        now_iso = _iso(_now())

        # L1
        with self._lock:
            if key in self._l1:
                value, expires = self._l1[key]
                if expires > now_iso:
                    self._l1.move_to_end(key)   # recién usado (LRU)
                    return value
                # Expirado en L1: descartar y seguir a L2.
                del self._l1[key]

        # L2
        row = self._db.query_one(
            "SELECT value, expires_at FROM cache_entries WHERE key = ?", (key,)
        )
        if row is None:
            return None
        if row["expires_at"] <= now_iso:
            # Expirado: eliminar y reportar miss.
            self._db.execute("DELETE FROM cache_entries WHERE key = ?", (key,))
            return None

        # Promover a L1 con su expiración original.
        self._l1_put(key, row["value"], row["expires_at"])
        return row["value"]

    # ── escritura ─────────────────────────────────────────────────────────────

    def set(self, key: str, value: str, *, ttl_s: Optional[int] = None) -> None:
        """Guarda un valor en L1 y L2 con su TTL."""
        if not self.enabled:
            return
        ttl = ttl_s if ttl_s is not None else self._l2_ttl
        now = _now()
        expires_iso = _iso(now + timedelta(seconds=ttl))

        self._l1_put(key, value, expires_iso)
        self._db.execute(
            "INSERT OR REPLACE INTO cache_entries (key, value, created_at, expires_at) "
            "VALUES (?,?,?,?)",
            (key, value, _iso(now), expires_iso),
        )

    # ── mantenimiento ─────────────────────────────────────────────────────────

    def purge_expired(self) -> int:
        """Elimina de L2 las entradas vencidas. Devuelve cuántas borró."""
        cur = self._db.execute(
            "DELETE FROM cache_entries WHERE expires_at <= ?", (_iso(_now()),)
        )
        return cur.rowcount if cur.rowcount is not None else 0

    def clear(self) -> None:
        """Vacía ambos niveles del cache."""
        with self._lock:
            self._l1.clear()
        self._db.execute("DELETE FROM cache_entries")

    def invalidate(self, key: str) -> None:
        """Elimina una entrada específica de ambos niveles."""
        with self._lock:
            self._l1.pop(key, None)
        self._db.execute("DELETE FROM cache_entries WHERE key = ?", (key,))

    # ── stats ─────────────────────────────────────────────────────────────────

    @property
    def l1_size(self) -> int:
        with self._lock:
            return len(self._l1)

    def l2_size(self) -> int:
        row = self._db.query_one("SELECT COUNT(*) AS n FROM cache_entries")
        return int(row["n"]) if row else 0

    # ── internals ─────────────────────────────────────────────────────────────

    def _l1_put(self, key: str, value: str, expires_iso: str) -> None:
        """Inserta en L1 (valor + expiración) respetando el tope LRU."""
        with self._lock:
            if key in self._l1:
                self._l1.move_to_end(key)
            self._l1[key] = (value, expires_iso)
            while len(self._l1) > self._l1_max:
                self._l1.popitem(last=False)   # descarta el LRU (más antiguo)
