"""
Tests del cache L1/L2 (itzel_core.cache).

Cubre derivación de claves, hits en ambos niveles, política LRU, TTL/expiración,
purga, invalidación y el modo deshabilitado.
"""

from __future__ import annotations

import time

from itzel_core.cache import ResponseCache

# ─── claves ───────────────────────────────────────────────────────────────────

class TestMakeKey:
    def test_stable_for_same_inputs(self):
        k1 = ResponseCache.make_key("hola", model="itzel-1b", params={"t": 0.7})
        k2 = ResponseCache.make_key("hola", model="itzel-1b", params={"t": 0.7})
        assert k1 == k2

    def test_param_order_irrelevant(self):
        k1 = ResponseCache.make_key("hola", model="m", params={"a": 1, "b": 2})
        k2 = ResponseCache.make_key("hola", model="m", params={"b": 2, "a": 1})
        assert k1 == k2

    def test_differs_by_prompt_model_params(self):
        base = ResponseCache.make_key("hola", model="m", params={"t": 0.7})
        assert base != ResponseCache.make_key("adios", model="m", params={"t": 0.7})
        assert base != ResponseCache.make_key("hola", model="otro", params={"t": 0.7})
        assert base != ResponseCache.make_key("hola", model="m", params={"t": 0.8})


# ─── hits L1 / L2 ─────────────────────────────────────────────────────────────

class TestHits:
    def test_miss_then_set_then_hit(self, cache):
        k = cache.make_key("p", model="m")
        assert cache.get(k) is None
        cache.set(k, "respuesta")
        assert cache.get(k) == "respuesta"

    def test_l2_hit_promotes_to_l1(self, cache):
        k = cache.make_key("p", model="m")
        cache.set(k, "v")
        cache._l1.clear()                 # vaciar L1, queda solo en L2
        assert cache.l1_size == 0
        assert cache.get(k) == "v"        # hit desde L2
        assert cache.l1_size == 1         # promovido a L1

    def test_persists_in_l2(self, cache):
        k = cache.make_key("p", model="m")
        cache.set(k, "v")
        assert cache.l2_size() == 1


# ─── LRU ──────────────────────────────────────────────────────────────────────

class TestLRU:
    def test_evicts_least_recently_used(self, cache):
        # l1_max = 3 (fixture)
        for i in range(3):
            cache.set(f"k{i}", f"v{i}")
        # Tocar k0 para que sea el más reciente.
        cache.get("k0")
        # Insertar k3 → debe expulsar k1 (el menos usado).
        cache.set("k3", "v3")
        assert cache.l1_size == 3
        # k1 ya no está en L1 (fue expulsado); k0 sigue.
        assert "k1" not in cache._l1
        assert "k0" in cache._l1


# ─── TTL / expiración ─────────────────────────────────────────────────────────

class TestTTL:
    def test_expires_both_levels(self, cache):
        cache.set("temp", "efimero", ttl_s=1)
        assert cache.get("temp") == "efimero"
        time.sleep(1.2)
        assert cache.get("temp") is None

    def test_purge_expired(self, cache):
        cache.set("a", "1", ttl_s=1)
        cache.set("b", "2", ttl_s=100)
        time.sleep(1.2)
        purged = cache.purge_expired()
        assert purged >= 1
        assert cache.l2_size() == 1   # solo 'b' sobrevive


# ─── invalidación / clear / disabled ──────────────────────────────────────────

class TestInvalidation:
    def test_invalidate(self, cache):
        cache.set("k", "v")
        cache.invalidate("k")
        assert cache.get("k") is None
        assert cache.l2_size() == 0

    def test_clear(self, cache):
        cache.set("a", "1")
        cache.set("b", "2")
        cache.clear()
        assert cache.l1_size == 0
        assert cache.l2_size() == 0

    def test_disabled_always_miss(self, cache):
        cache.set("k", "v")
        cache.enabled = False
        assert cache.get("k") is None
