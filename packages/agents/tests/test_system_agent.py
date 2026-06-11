"""
Tests del SystemAgent.

Ningún test abre ni cierra apps reales: se mockea `_run` (el wrapper de
subprocess) para verificar la lógica sin efectos en el sistema. El clipboard
se prueba solo si pyperclip está instalado (skipif).
"""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import pytest

from itzel_agents import system_agent
from itzel_agents.system_agent import SystemAgent

HAS_PYPERCLIP = importlib.util.find_spec("pyperclip") is not None


def _fake_proc(returncode: int = 0, stderr: str = "") -> SimpleNamespace:
    """Simula un subprocess.CompletedProcess."""
    return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)


# ─── get_system_info ──────────────────────────────────────────────────────────

class TestSystemInfo:
    def test_returns_os_and_base_fields(self, system_agent):
        r = system_agent.invoke("get_system_info", {})
        assert r.ok is True
        v = r.value
        assert "os" in v
        assert "python_version" in v
        assert "machine" in v

    def test_no_confirmation_needed(self, system_agent):
        # system_agent usa AutoDeny; get_system_info NO es irreversible → procede.
        r = system_agent.invoke("get_system_info", {})
        assert r.denied is False
        assert r.ok is True


# ─── close_app (fail-safe) ────────────────────────────────────────────────────

class TestCloseApp:
    def test_close_app_denied_never_runs(self, system_agent, monkeypatch):
        """SEGURIDAD: con auto-deny, close_app NO debe ejecutar taskkill/pkill."""
        called = []
        monkeypatch.setattr(
            "itzel_agents.system_agent._run",
            lambda *a, **k: called.append(a) or _fake_proc(),
        )
        r = system_agent.invoke("close_app", {"name": "notepad"})
        assert r.denied is True
        assert called == []  # _run NUNCA fue invocado

    def test_close_app_confirmed_runs(self, auto_confirm, temp_logger, monkeypatch):
        """Con confirmación, close_app ejecuta el comando (mockeado)."""
        called = []
        monkeypatch.setattr(
            "itzel_agents.system_agent._run",
            lambda argv, **k: called.append(argv) or _fake_proc(returncode=0),
        )
        agent = SystemAgent(confirm_handler=auto_confirm, action_sink=temp_logger)
        r = agent.invoke("close_app", {"name": "notepad"})
        assert r.ok is True
        assert len(called) == 1  # se ejecutó una vez
        agent.shutdown()


# ─── open_app ─────────────────────────────────────────────────────────────────

class TestOpenApp:
    def test_open_app_success(self, system_agent, monkeypatch):
        monkeypatch.setattr(
            "itzel_agents.system_agent._run",
            lambda argv, **k: _fake_proc(returncode=0),
        )
        r = system_agent.invoke("open_app", {"name": "calc"})
        assert r.ok is True
        assert "calc" in r.value

    def test_open_app_failure(self, system_agent, monkeypatch):
        monkeypatch.setattr(
            "itzel_agents.system_agent._run",
            lambda argv, **k: _fake_proc(returncode=1, stderr="not found"),
        )
        r = system_agent.invoke("open_app", {"name": "noexiste"})
        assert r.ok is False


# ─── notificaciones ───────────────────────────────────────────────────────────

class TestNotification:
    def test_send_notification_via_fallback(self, system_agent, monkeypatch):
        """Sin plyer, usa el fallback por OS (mockeado)."""
        # Forzar que plyer no esté disponible
        import builtins
        real_import = builtins.__import__

        def _no_plyer(name, *args, **kwargs):
            if name == "plyer":
                raise ImportError("no plyer")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _no_plyer)
        monkeypatch.setattr(
            "itzel_agents.system_agent._run",
            lambda argv, **k: _fake_proc(returncode=0),
        )
        # En Windows el fallback usa powershell; debe devolver algún mensaje.
        r = system_agent.invoke("send_notification", {"title": "Hola", "body": "mundo"})
        assert r.ok is True


# ─── clipboard ────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_PYPERCLIP, reason="pyperclip no instalado")
class TestClipboardWithDep:
    def test_set_get_roundtrip(self, system_agent):
        s = system_agent.invoke("set_clipboard", {"text": "Itzel rocío del cielo"})
        assert s.ok is True
        g = system_agent.invoke("get_clipboard", {})
        assert g.ok is True
        assert g.value == "Itzel rocío del cielo"


@pytest.mark.skipif(HAS_PYPERCLIP, reason="solo aplica si pyperclip NO está")
class TestClipboardMissingDep:
    def test_set_clipboard_clear_error(self, system_agent):
        r = system_agent.invoke("set_clipboard", {"text": "x"})
        assert r.ok is False
        assert "pyperclip" in r.error


# ─── registro de tools ────────────────────────────────────────────────────────

class TestToolRegistration:
    def test_all_six_tools_registered(self, system_agent):
        assert set(system_agent.tools) == {
            "open_app", "close_app", "get_clipboard",
            "set_clipboard", "send_notification", "get_system_info",
        }
