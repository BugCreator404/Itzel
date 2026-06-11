"""
Tests del CodeAgent.

Verifican la ejecución en sandbox (Python y shell), el timeout, el aislamiento
de entorno y el fail-safe: con confirmación denegada el código NO se ejecuta.
"""

from __future__ import annotations

import sys


# ─── run_python ───────────────────────────────────────────────────────────────

class TestRunPython:
    def test_basic_execution(self, code_agent):
        r = code_agent.invoke("run_python", {"code": "print(6 * 7)"})
        assert r.ok is True
        assert r.value["stdout"].strip() == "42"
        assert r.value["returncode"] == 0
        assert r.value["ok"] is True

    def test_error_captured(self, code_agent):
        r = code_agent.invoke("run_python", {"code": "raise ValueError('boom')"})
        assert r.ok is True  # la tool corrió; el código falló dentro del sandbox
        assert r.value["returncode"] != 0
        assert "ValueError" in r.value["stderr"]
        assert r.value["ok"] is False

    def test_timeout(self, code_agent):
        r = code_agent.invoke(
            "run_python",
            {"code": "import time; time.sleep(30)", "timeout": 2},
        )
        assert r.value["timed_out"] is True
        assert r.value["ok"] is False

    def test_env_isolation(self, code_agent, monkeypatch):
        monkeypatch.setenv("ITZEL_TEST_SECRET", "leaked")
        r = code_agent.invoke(
            "run_python",
            {"code": "import os; print(os.environ.get('ITZEL_TEST_SECRET', 'CLEAN'))"},
        )
        assert r.value["stdout"].strip() == "CLEAN"

    def test_stdin_isolation_no_network_proxy(self, code_agent):
        # Los proxies del usuario no deben filtrarse al sandbox.
        r = code_agent.invoke(
            "run_python",
            {"code": "import os; print(os.environ.get('HTTP_PROXY', 'NOPROXY'))"},
        )
        assert r.value["stdout"].strip() == "NOPROXY"


# ─── run_bash ─────────────────────────────────────────────────────────────────

class TestRunBash:
    def test_echo(self, code_agent):
        # 'echo' funciona tanto en bash como en PowerShell.
        r = code_agent.invoke("run_bash", {"command": "echo itzel-ok"})
        assert r.ok is True
        assert "itzel-ok" in r.value["stdout"]
        assert "shell" in r.value

    def test_timeout(self, code_agent):
        # 'sleep 30' funciona en bash y en PowerShell (sleep = alias de Start-Sleep).
        r = code_agent.invoke("run_bash", {"command": "sleep 30", "timeout": 2})
        assert r.value["timed_out"] is True


# ─── seguridad (fail-safe) ────────────────────────────────────────────────────

class TestCodeSecurity:
    def test_run_python_denied_does_not_execute(self, code_agent_deny, tmp_path):
        """Con auto-deny, run_python NO debe ejecutar: sin efecto observable."""
        marker = tmp_path / "executed.txt"
        code = f"open(r'{marker}', 'w').write('I RAN')"
        r = code_agent_deny.invoke("run_python", {"code": code})
        assert r.denied is True
        assert not marker.exists()  # el código jamás corrió

    def test_run_bash_denied(self, code_agent_deny):
        r = code_agent_deny.invoke("run_bash", {"command": "echo nope"})
        assert r.denied is True

    def test_denied_logged(self, code_agent_deny, temp_logger):
        code_agent_deny.invoke("run_python", {"code": "print(1)"})
        history = temp_logger.read_history()
        assert any(
            e["action"] == "run_python" and e["result"] == "denied"
            for e in history
        )


# ─── límites y metadata ───────────────────────────────────────────────────────

class TestCodeLimits:
    def test_timeout_clamped_to_max(self, code_agent):
        # Pedir un timeout enorme no debe romper; se clampa internamente.
        r = code_agent.invoke("run_python", {"code": "print('ok')", "timeout": 9999})
        assert r.ok is True
        assert r.value["stdout"].strip() == "ok"

    def test_tools_registered(self, code_agent):
        assert set(code_agent.tools) == {"run_python", "run_bash"}

    def test_tools_marked_sandbox_and_confirm(self, code_agent):
        for name in ("run_python", "run_bash"):
            t = code_agent.registry.get(name)
            assert t.sandbox is True
            assert t.confirm_if_irreversible is True
