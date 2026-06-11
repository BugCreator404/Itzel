"""
Tests del sistema de herramientas (itzel_core.tools).

Cubre: decorador @tool y esquemas JSON, ToolRegistry (invoke, timeout, errores),
confirmación (estática, dinámica, fail-safe), SandboxRunner y ActionLogger.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

import pytest

from itzel_core.tools import (
    ActionLogger,
    ActionRequest,
    AutoConfirmHandler,
    AutoDenyHandler,
    CallbackConfirmHandler,
    SandboxRunner,
    TerminalConfirmHandler,
    Tool,
    ToolRegistry,
    ToolResult,
    describe_action,
    tool,
)


# ─── @tool y esquemas ─────────────────────────────────────────────────────────

class TestToolSchema:
    def test_infers_required_and_optional_params(self):
        @tool("demo", "Una demo.", param_docs={"a": "param a"})
        def demo(a: str, b: int = 5) -> str:
            return f"{a}{b}"

        schema = demo.to_schema()
        assert schema["name"] == "demo"
        assert schema["description"] == "Una demo."
        props = schema["input_schema"]["properties"]
        assert props["a"] == {"type": "string", "description": "param a"}
        assert props["b"]["type"] == "integer"
        assert schema["input_schema"]["required"] == ["a"]

    def test_maps_python_types_to_json(self):
        @tool("t", "t")
        def t(s: str, i: int, f: float, b: bool, lst: list, d: dict) -> None:
            ...

        props = t.to_schema()["input_schema"]["properties"]
        assert props["s"]["type"] == "string"
        assert props["i"]["type"] == "integer"
        assert props["f"]["type"] == "number"
        assert props["b"]["type"] == "boolean"
        assert props["lst"]["type"] == "array"
        assert props["d"]["type"] == "object"

    def test_optional_type_unwraps_to_inner(self):
        @tool("t", "t")
        def t(x: Optional[int] = None) -> None:
            ...

        props = t.to_schema()["input_schema"]["properties"]
        assert props["x"]["type"] == "integer"
        assert t.to_schema()["input_schema"]["required"] == []

    def test_name_defaults_to_function_name(self):
        @tool(description="sin nombre explícito")
        def my_func(x: str) -> str:
            return x

        assert my_func.name == "my_func"

    def test_tool_is_callable_directly(self):
        @tool("add", "suma")
        def add(a: int, b: int) -> int:
            return a + b

        assert add(2, 3) == 5
        assert isinstance(add, Tool)


# ─── ToolRegistry.invoke ──────────────────────────────────────────────────────

class TestRegistryInvoke:
    def test_success_returns_value(self):
        reg = ToolRegistry()

        @tool("echo", "echo")
        def echo(text: str) -> str:
            return text.upper()

        reg.register(echo)
        r = reg.invoke("echo", {"text": "hola"})
        assert r.ok is True
        assert r.value == "HOLA"
        reg.shutdown()

    def test_unknown_tool(self):
        reg = ToolRegistry()
        r = reg.invoke("nope", {})
        assert r.ok is False
        assert "desconocida" in r.error.lower()
        reg.shutdown()

    def test_exception_is_captured(self):
        reg = ToolRegistry()

        @tool("boom", "explota")
        def boom() -> None:
            raise RuntimeError("kapow")

        reg.register(boom)
        r = reg.invoke("boom", {})
        assert r.ok is False
        assert "kapow" in r.error
        reg.shutdown()

    def test_invalid_args_captured(self):
        reg = ToolRegistry()

        @tool("needs_x", "x")
        def needs_x(x: str) -> str:
            return x

        reg.register(needs_x)
        r = reg.invoke("needs_x", {})  # falta x
        assert r.ok is False
        reg.shutdown()

    def test_timeout(self):
        reg = ToolRegistry()

        @tool("slow", "lento", timeout=1)
        def slow() -> str:
            time.sleep(5)
            return "tarde"

        reg.register(slow)
        r = reg.invoke("slow", {})
        assert r.ok is False
        assert "1s" in r.error or "límite" in r.error.lower()
        reg.shutdown()

    def test_duplicate_registration_raises(self):
        reg = ToolRegistry()

        @tool("dup", "d")
        def dup() -> None:
            ...

        reg.register(dup)
        with pytest.raises(ValueError):
            reg.register(dup)
        reg.shutdown()

    def test_all_schemas(self):
        reg = ToolRegistry()

        @tool("a", "a")
        def a() -> None: ...

        @tool("b", "b")
        def b() -> None: ...

        reg.register_all([a, b])
        schemas = reg.all_schemas()
        assert {s["name"] for s in schemas} == {"a", "b"}
        assert reg.names == ["a", "b"]
        reg.shutdown()


# ─── confirmación ─────────────────────────────────────────────────────────────

class TestConfirmation:
    def test_static_irreversible_denied_without_handler(self):
        """Fail-safe: sin handler, una acción irreversible se rechaza."""
        reg = ToolRegistry()  # sin confirm_handler

        @tool("del", "borra", confirm_if_irreversible=True)
        def _del(path: str) -> bool:
            return True

        reg.register(_del)
        r = reg.invoke("del", {"path": "/x"})
        assert r.denied is True
        assert r.ok is False
        reg.shutdown()

    def test_auto_confirm_allows(self):
        reg = ToolRegistry(confirm_handler=AutoConfirmHandler())

        @tool("del", "borra", confirm_if_irreversible=True)
        def _del(path: str) -> bool:
            return True

        reg.register(_del)
        r = reg.invoke("del", {"path": "/x"})
        assert r.ok is True
        assert r.value is True
        reg.shutdown()

    def test_auto_deny_blocks(self):
        reg = ToolRegistry(confirm_handler=AutoDenyHandler())
        ran = []

        @tool("del", "borra", confirm_if_irreversible=True)
        def _del(path: str) -> bool:
            ran.append(path)
            return True

        reg.register(_del)
        r = reg.invoke("del", {"path": "/x"})
        assert r.denied is True
        assert ran == []  # la función NUNCA se ejecutó
        reg.shutdown()

    def test_dynamic_confirm_when_true(self):
        reg = ToolRegistry(confirm_handler=AutoDenyHandler())

        @tool("mv", "mueve", confirm_when=lambda args: args.get("dst") == "exists")
        def mv(src: str, dst: str) -> str:
            return "moved"

        reg.register(mv)
        # dst == "exists" → confirma → AutoDeny rechaza
        assert reg.invoke("mv", {"src": "a", "dst": "exists"}).denied is True
        # dst != "exists" → no confirma → procede
        assert reg.invoke("mv", {"src": "a", "dst": "new"}).ok is True
        reg.shutdown()

    def test_confirm_when_error_is_failsafe(self):
        """Si confirm_when lanza, se confirma (fail-safe)."""
        reg = ToolRegistry(confirm_handler=AutoDenyHandler())

        def _boom(args):
            raise RuntimeError("predicate failed")

        @tool("risky", "riesgo", confirm_when=_boom)
        def risky() -> str:
            return "ran"

        reg.register(risky)
        r = reg.invoke("risky", {})
        assert r.denied is True  # confirmó por seguridad y AutoDeny rechazó
        reg.shutdown()

    def test_callback_handler(self):
        seen = []

        def cb(req: ActionRequest) -> bool:
            seen.append(req.tool_name)
            return True

        reg = ToolRegistry(confirm_handler=CallbackConfirmHandler(cb))

        @tool("del", "borra", confirm_if_irreversible=True)
        def _del(path: str) -> bool:
            return True

        reg.register(_del)
        assert reg.invoke("del", {"path": "/x"}).ok is True
        assert seen == ["del"]
        reg.shutdown()


class TestTerminalConfirm:
    def test_yes_answer(self):
        req = ActionRequest("delete_file", "del", {"path": "/x"}, irreversible=True)
        h = TerminalConfirmHandler("es-MX", timeout=5,
                                   input_fn=lambda p: "si", print_fn=lambda s: None)
        assert h(req) is True

    def test_no_answer(self):
        req = ActionRequest("delete_file", "del", {"path": "/x"}, irreversible=True)
        h = TerminalConfirmHandler("es-MX", timeout=5,
                                   input_fn=lambda p: "n", print_fn=lambda s: None)
        assert h(req) is False

    def test_empty_answer_denies(self):
        req = ActionRequest("delete_file", "del", {"path": "/x"}, irreversible=True)
        h = TerminalConfirmHandler("es-MX", timeout=5,
                                   input_fn=lambda p: "", print_fn=lambda s: None)
        assert h(req) is False

    def test_describe_action_uses_i18n(self):
        req = ActionRequest("delete_file", "del", {"path": "/home/x.txt"}, irreversible=True)
        es = describe_action(req, "es-MX")
        en = describe_action(req, "en-US")
        assert "/home/x.txt" in es
        assert "deshacer" in es.lower()
        assert "undone" in en.lower()

    def test_describe_action_generic_fallback(self):
        req = ActionRequest("unknown_tool", "unknown_tool(a=1)", {"a": 1})
        text = describe_action(req, "es-MX")
        assert "unknown_tool" in text


# ─── SandboxRunner ────────────────────────────────────────────────────────────

class TestSandbox:
    def test_python_basic(self):
        sb = SandboxRunner(timeout=10)
        r = sb.run_python("print(2 + 2)")
        assert r.ok is True
        assert r.stdout.strip() == "4"
        assert r.returncode == 0

    def test_python_error(self):
        sb = SandboxRunner(timeout=10)
        r = sb.run_python("raise ValueError('x')")
        assert r.ok is False
        assert r.returncode != 0
        assert "ValueError" in r.stderr

    def test_timeout(self):
        sb = SandboxRunner(timeout=10)
        r = sb.run_python("import time; time.sleep(30)", timeout=2)
        assert r.timed_out is True
        assert r.ok is False

    def test_env_isolation(self, monkeypatch):
        monkeypatch.setenv("ITZEL_SECRET", "topsecret")
        sb = SandboxRunner(timeout=10)
        r = sb.run_python(
            "import os; print(os.environ.get('ITZEL_SECRET', 'CLEAN'))"
        )
        assert r.stdout.strip() == "CLEAN"

    def test_run_argv(self):
        sb = SandboxRunner(timeout=10)
        r = sb.run_argv([sys.executable, "-c", "print('argv ok')"])
        assert r.ok is True
        assert "argv ok" in r.stdout

    def test_nonexistent_executable(self):
        sb = SandboxRunner(timeout=10)
        r = sb.run_argv(["__definitely_not_a_real_cmd__"])
        assert r.ok is False


# ─── ActionLogger ─────────────────────────────────────────────────────────────

class TestActionLogger:
    def test_logs_ok_denied_error(self, tmp_path: Path):
        logf = tmp_path / "actions.log"
        logger = ActionLogger(log_file=logf)

        logger(
            ActionRequest("read_file", "r", {"path": "/a"}, agent="file"),
            ToolResult.success("data"),
        )
        logger(
            ActionRequest("delete_file", "d", {"path": "/b"}, irreversible=True, agent="file"),
            ToolResult.rejected(),
        )
        logger(
            ActionRequest("boom", "b", {"path": "/c"}, agent="file"),
            ToolResult.failure("kaboom"),
        )

        history = logger.read_history()
        assert len(history) == 3
        assert history[0]["result"] == "ok"
        assert history[1]["result"] == "denied"
        assert history[2]["result"] == "error"
        assert history[2]["error"] == "kaboom"
        assert history[0]["agent"] == "file"

    def test_extracts_target_from_args(self, tmp_path: Path):
        logger = ActionLogger(log_file=tmp_path / "a.log")
        logger(
            ActionRequest("move_file", "m", {"src": "/a", "dst": "/b"}, agent="file"),
            ToolResult.success("ok"),
        )
        entry = logger.read_history()[0]
        assert entry["target"] == "/a"  # 'src' tiene prioridad sobre 'dst'

    def test_read_history_limit(self, tmp_path: Path):
        logger = ActionLogger(log_file=tmp_path / "a.log")
        for i in range(10):
            logger(
                ActionRequest("t", "t", {"path": f"/{i}"}, agent="x"),
                ToolResult.success(i),
            )
        assert len(logger.read_history(limit=3)) == 3

    def test_format_human(self):
        entry = {
            "ts": "2026-06-11T20:15:03.123+00:00",
            "agent": "file",
            "action": "delete_file",
            "target": "/home/x.txt",
            "result": "ok",
        }
        line = ActionLogger.format_human(entry)
        assert "file" in line
        assert "delete_file" in line
        assert "/home/x.txt" in line
        assert "ok" in line

    def test_empty_history_when_no_file(self, tmp_path: Path):
        logger = ActionLogger(log_file=tmp_path / "missing.log")
        assert logger.read_history() == []

    def test_corrupt_lines_ignored(self, tmp_path: Path):
        logf = tmp_path / "a.log"
        logf.write_text('{"valid": 1}\nNOT JSON\n{"valid": 2}\n', encoding="utf-8")
        logger = ActionLogger(log_file=logf)
        assert len(logger.read_history()) == 2
