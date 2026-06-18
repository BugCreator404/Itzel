"""
Tests del MemoryStore (itzel_core.memory).

Cubre historial (save/get_session/search/delete/clear), auto-creación de
conversaciones y la memoria episódica (resúmenes automáticos + contexto).
"""

from __future__ import annotations

# ─── historial ────────────────────────────────────────────────────────────────

class TestHistory:
    def test_save_and_get_chronological(self, mem_store):
        mem_store.save("user", "hola", "s1")
        mem_store.save("assistant", "qué tal", "s1")
        sess = mem_store.get_session("s1")
        assert [(e.role, e.content) for e in sess] == [
            ("user", "hola"), ("assistant", "qué tal")
        ]

    def test_save_autocreates_conversation(self, mem_store, mem_db):
        mem_store.save("user", "hola", "nueva")
        conv = mem_db.query_one("SELECT id FROM conversations WHERE id='nueva'")
        assert conv is not None

    def test_model_language_filled_from_metadata(self, mem_store, mem_db):
        mem_store.save("user", "hola", "s1")  # primer mensaje sin model
        mem_store.save("assistant", "hi", "s1",
                       metadata={"model": "itzel-1b", "language": "es-MX"})
        conv = mem_db.query_one("SELECT model, language FROM conversations WHERE id='s1'")
        assert conv["model"] == "itzel-1b"
        assert conv["language"] == "es-MX"

    def test_search(self, mem_store):
        mem_store.save("user", "me gusta el ajolote", "s1")
        mem_store.save("user", "otra cosa", "s1")
        hits = mem_store.search("ajolote")
        assert len(hits) == 1
        assert "ajolote" in hits[0].content

    def test_get_session_limit(self, mem_store):
        for i in range(10):
            mem_store.save("user", f"msg {i}", "s1")
        assert len(mem_store.get_session("s1", limit=3)) == 3

    def test_tokens_estimated_when_not_given(self, mem_store):
        entry = mem_store.save("user", "x" * 40, "s1")
        assert entry.tokens_used == 10   # 40 chars // 4


# ─── borrado ──────────────────────────────────────────────────────────────────

class TestDelete:
    def test_delete_session_cascade(self, mem_store):
        mem_store.save("user", "a", "s1")
        mem_store.save("user", "b", "s2")
        mem_store.delete_session("s1")
        assert mem_store.get_session("s1") == []
        assert len(mem_store.get_session("s2")) == 1

    def test_clear(self, mem_store):
        mem_store.save("user", "a", "s1")
        mem_store.save("user", "b", "s2")
        mem_store.clear()
        assert mem_store.get_session("s1") == []
        assert mem_store.get_session("s2") == []


# ─── memoria episódica ────────────────────────────────────────────────────────

class TestEpisodicMemory:
    def test_conversation_tokens(self, mem_store):
        mem_store.save("user", "x" * 40, "s1")     # 10 tokens
        mem_store.save("assistant", "y" * 80, "s1")  # 20 tokens
        assert mem_store.conversation_tokens("s1") == 30

    def test_no_summary_below_threshold(self, mem_store):
        mem_store.save("user", "corto", "s1")
        result = mem_store.maybe_summarize("s1", lambda t: "RESUMEN", threshold=1000)
        assert result is None
        assert mem_store.get_latest_summary("s1") is None

    def test_summary_above_threshold(self, mem_store):
        for _ in range(10):
            mem_store.save("user", "x" * 400, "s1")   # ~100 tokens c/u → 1000
        summary = mem_store.maybe_summarize("s1", lambda t: "RESUMEN del chat", threshold=500)
        assert summary is not None
        assert summary.summary == "RESUMEN del chat"
        assert mem_store.get_latest_summary("s1").summary == "RESUMEN del chat"

    def test_summary_is_incremental(self, mem_store):
        for _ in range(6):
            mem_store.save("user", "x" * 400, "s1")
        captured = []

        def summarizer(text):
            captured.append(text)
            return "R1"

        mem_store.maybe_summarize("s1", summarizer, threshold=500)
        # Nuevos mensajes tras el primer resumen.
        for _ in range(6):
            mem_store.save("user", "y" * 400, "s1")
        mem_store.maybe_summarize("s1", summarizer, threshold=500)

        # El segundo resumen incluye el resumen previo como contexto.
        assert len(captured) == 2
        assert "[resumen previo]" in captured[1]

    def test_get_context_after_summary(self, mem_store):
        for _ in range(10):
            mem_store.save("user", "x" * 400, "s1")
        mem_store.maybe_summarize("s1", lambda t: "RESUMEN", threshold=500)
        # Mensajes nuevos tras el resumen.
        mem_store.save("user", "mensaje nuevo", "s1")

        ctx = mem_store.get_context("s1")
        assert ctx["summary"] == "RESUMEN"
        # Solo los mensajes posteriores al resumen.
        assert [m.content for m in ctx["messages"]] == ["mensaje nuevo"]

    def test_get_context_without_summary(self, mem_store):
        mem_store.save("user", "uno", "s1")
        mem_store.save("user", "dos", "s1")
        ctx = mem_store.get_context("s1", recent_limit=5)
        assert ctx["summary"] is None
        assert [m.content for m in ctx["messages"]] == ["uno", "dos"]
