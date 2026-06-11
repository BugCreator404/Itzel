"""
Tests del puente de auditoría a la BD (itzel_core.audit).

Verifica que DBActionSink escribe a actions_log, que combined_sink reparte a
varios sinks y que get_actions consulta con filtros.
"""

from __future__ import annotations

from itzel_core.audit import DBActionSink, combined_sink, get_actions
from itzel_core.tools.base import ActionRequest, ToolResult


def _req(tool, args, agent="file"):
    return ActionRequest(tool_name=tool, summary=tool, args=args, agent=agent)


class TestDBActionSink:
    def test_writes_to_actions_log(self, mem_db):
        sink = DBActionSink(mem_db)
        sink(_req("read_file", {"path": "/a.txt"}), ToolResult.success("data"))
        sink(_req("delete_file", {"path": "/b.txt"}), ToolResult.rejected())
        sink(_req("boom", {"path": "/c"}), ToolResult.failure("err"))

        rows = mem_db.query("SELECT agent, action, target, result FROM actions_log ORDER BY created_at")
        results = [(r["agent"], r["action"], r["target"], r["result"]) for r in rows]
        assert ("file", "read_file", "/a.txt", "ok") in results
        assert ("file", "delete_file", "/b.txt", "denied") in results
        assert ("file", "boom", "/c", "error") in results

    def test_details_stores_args_json(self, mem_db):
        sink = DBActionSink(mem_db)
        sink(_req("move_file", {"src": "/a", "dst": "/b"}), ToolResult.success("ok"))
        row = mem_db.query_one("SELECT details, target FROM actions_log")
        assert '"src"' in row["details"]
        assert row["target"] == "/a"   # 'src' tiene prioridad


class TestCombinedSink:
    def test_fans_out_to_all(self, mem_db):
        seen = []
        memory_sink = lambda req, res: seen.append((req.tool_name, res.ok))
        db_sink = DBActionSink(mem_db)

        sink = combined_sink(memory_sink, db_sink)
        sink(_req("read_file", {"path": "/x"}), ToolResult.success("d"))

        assert seen == [("read_file", True)]                  # sink 1 recibió
        assert mem_db.query_one("SELECT COUNT(*) AS n FROM actions_log")["n"] == 1  # sink 2 también

    def test_one_failing_sink_does_not_block_others(self, mem_db):
        def broken(req, res):
            raise RuntimeError("boom")

        seen = []
        good = lambda req, res: seen.append(req.tool_name)
        sink = combined_sink(broken, good)
        sink(_req("x", {}), ToolResult.success(None))
        assert seen == ["x"]   # el sink bueno recibió pese al fallo del otro


class TestGetActions:
    def test_get_actions_recent_first(self, mem_db):
        sink = DBActionSink(mem_db)
        for i in range(5):
            sink(_req(f"action{i}", {"path": f"/{i}"}), ToolResult.success(i))
        actions = get_actions(mem_db, limit=3)
        assert len(actions) == 3

    def test_filter_by_agent(self, mem_db):
        sink = DBActionSink(mem_db)
        sink(_req("read_file", {"path": "/a"}, agent="file"), ToolResult.success("d"))
        sink(_req("open_app", {"name": "calc"}, agent="system"), ToolResult.success("d"))
        files = get_actions(mem_db, agent="file")
        assert len(files) == 1
        assert files[0]["agent"] == "file"
