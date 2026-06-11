"""
Fixtures compartidas para los tests del core.

Las fixtures de BD usan SQLite en memoria (:memory:) o un archivo temporal,
nunca la BD real del usuario (~/.itzel/memory.db). Las pruebas de cifrado usan
una clave fija sobre un archivo temporal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from itzel_core.cache import ResponseCache
from itzel_core.db import Database
from itzel_core.memory import MemoryStore

# Clave de prueba fija (hex de 256 bits) para los tests de cifrado.
TEST_KEY = "0123456789abcdef" * 4


@pytest.fixture
def mem_db() -> Iterator[Database]:
    """Database en memoria, sin cifrado — rápida y aislada."""
    db = Database(path=":memory:", key=None)
    yield db
    db.close()


@pytest.fixture
def enc_db(tmp_path: Path) -> Iterator[Database]:
    """Database cifrada (SQLCipher) sobre un archivo temporal."""
    db = Database(path=tmp_path / "enc.db", key=TEST_KEY)
    yield db
    db.close()


@pytest.fixture
def mem_store(mem_db: Database) -> MemoryStore:
    """MemoryStore sobre la BD en memoria."""
    return MemoryStore(db=mem_db)


@pytest.fixture
def cache(mem_db: Database) -> ResponseCache:
    """ResponseCache sobre la BD en memoria, con límites pequeños para test."""
    return ResponseCache(mem_db, l1_max=3, l2_ttl_s=2, enabled=True)
