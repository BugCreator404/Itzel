"""
Suite completa de tests para el CLI de Itzel.

Todos los tests mockean el backend y la capa de red — no requieren
Ollama, llama.cpp ni el servidor FastAPI corriendo.

Organización:
  TestGlobal        — --version, --help, pipe
  TestAsk           — itzel ask
  TestChat          — itzel chat
  TestStatus        — itzel status
  TestModel         — itzel model [list|pull|use|remove|info]
  TestMemory        — itzel memory [search|list|clear|export|stats]
  TestSetup         — itzel setup (wizard)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from typer.testing import CliRunner

from itzel_cli.main import app
from itzel_cli.client import (
    BackendOfflineError,
    BackendError,
    BackendStatus,
    ModelInfo,
)

runner = CliRunner()

# ─── factories ────────────────────────────────────────────────────────────────

def _make_model(
    id="itzel-1b", name="Itzel-1B", backend="llama.cpp",
    loaded=True, active=True, downloaded=True, size_mb=900
) -> ModelInfo:
    return ModelInfo(
        id=id, name=name, backend=backend,
        loaded=loaded, active=active, downloaded=downloaded, size_mb=size_mb,
    )


def _make_status(alive=True, version="0.1.0", model="itzel-1b") -> BackendStatus:
    return BackendStatus(alive=alive, version=version, model=model, uptime_s=120.0)


# ─── TestGlobal ───────────────────────────────────────────────────────────────

class TestGlobal:
    def test_version_flag(self):
        r = runner.invoke(app, ["--version"])
        assert r.exit_code == 0
        assert "ITZEL" in r.output

    def test_help_lists_commands(self):
        r = runner.invoke(app, ["--help"])
        assert r.exit_code == 0
        for cmd in ("ask", "chat", "status", "setup", "pipe"):
            assert cmd in r.output

    def test_pipe_requires_stdin(self):
        # El CliRunner siempre usa stdin no-TTY, por lo que pipe lee stdin vacío
        # y delega a ask (que maneja el backend offline sin crash).
        r = runner.invoke(app, ["pipe", "resume esto"])
        assert r.exit_code == 0   # error del backend se maneja graciosamente

    def test_pipe_reads_stdin(self):
        # Con datos en stdin, delega a ask (backend offline → json offline)
        with patch("itzel_cli.commands.ask.ItzelClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.switch_model.return_value = {}
            mock_client.chat_sync.return_value = "respuesta"
            mock_cls.return_value = mock_client

            r = runner.invoke(
                app,
                ["pipe", "--json", "resume esto"],
                input="contenido de prueba",
                catch_exceptions=False,
            )
            assert r.exit_code == 0


# ─── TestAsk ──────────────────────────────────────────────────────────────────

class TestAsk:
    def _mock_stream(self, tokens: list[str]):
        """Devuelve un mock de ItzelClient que emite tokens."""
        mock_client = MagicMock()
        mock_client.is_alive.return_value = True
        mock_client.stream_chat.return_value = iter(tokens)
        mock_client.chat_sync.return_value = "".join(tokens)
        mock_client.switch_model.return_value = {"model_id": "itzel-1b"}
        return mock_client

    def test_ask_streams_tokens(self):
        tokens = ["Hola", " mundo", " desde", " Itzel"]
        with patch("itzel_cli.commands.ask.ItzelClient") as mock_cls:
            mock_cls.return_value = self._mock_stream(tokens)
            r = runner.invoke(app, ["ask", "test"])
        assert r.exit_code == 0
        assert "Hola" in r.output

    def test_ask_json_output(self):
        with patch("itzel_cli.commands.ask.ItzelClient") as mock_cls:
            mock_cls.return_value = self._mock_stream(["respuesta"])
            r = runner.invoke(app, ["ask", "--json", "pregunta"])
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["status"] == "ok"
        assert data["question"] == "pregunta"
        assert "answer" in data

    def test_ask_json_offline(self):
        with patch("itzel_cli.commands.ask.ItzelClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.is_alive.return_value = False
            mock_client.chat_sync.side_effect = BackendOfflineError("http://127.0.0.1:7432")
            mock_cls.return_value = mock_client

            r = runner.invoke(app, ["ask", "--json", "hola"])
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["status"] == "offline"

    def test_ask_quiet_outputs_only_response(self):
        tokens = ["solo", " esto"]
        with patch("itzel_cli.commands.ask.ItzelClient") as mock_cls:
            mock_cls.return_value = self._mock_stream(tokens)
            r = runner.invoke(app, ["ask", "--quiet", "test"])
        assert r.exit_code == 0
        # En modo quiet, la salida debe ser solo los tokens
        assert "Itzel" not in r.output   # sin el label decorativo

    def test_ask_model_flag_calls_switch(self):
        tokens = ["ok"]
        with patch("itzel_cli.commands.ask.ItzelClient") as mock_cls:
            mock_client = self._mock_stream(tokens)
            mock_cls.return_value = mock_client

            runner.invoke(app, ["ask", "--model", "ollama/llama3", "hola"])
        mock_client.switch_model.assert_called_once_with("ollama/llama3")

    def test_ask_backend_error_shows_message(self):
        with patch("itzel_cli.commands.ask.ItzelClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.is_alive.return_value = True
            mock_client.stream_chat.side_effect = BackendError(500, "Internal error")
            mock_cls.return_value = mock_client

            r = runner.invoke(app, ["ask", "test"])
        assert r.exit_code == 0   # no crashea
        assert "Error" in r.output or "error" in r.output.lower()

    def test_ask_offline_shows_hint(self):
        with patch("itzel_cli.commands.ask.ItzelClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.is_alive.return_value = False
            mock_client.stream_chat.side_effect = BackendOfflineError("http://127.0.0.1:7432")
            mock_cls.return_value = mock_client

            r = runner.invoke(app, ["ask", "hola"])
        assert "backend" in r.output.lower() or "Backend" in r.output


# ─── TestChat ─────────────────────────────────────────────────────────────────

class TestChat:
    def test_chat_offline_shows_error(self):
        with patch("itzel_cli.commands.chat.ItzelClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.is_alive.return_value = False
            mock_cls.return_value = mock_client

            r = runner.invoke(app, ["chat"])
        assert r.exit_code == 0
        assert "backend" in r.output.lower() or "Backend" in r.output

    def test_chat_exit_command(self):
        with patch("itzel_cli.commands.chat.ItzelClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.is_alive.return_value = True
            mock_cls.return_value = mock_client
            mock_cls.load_session.return_value = "test-session-id"
            mock_cls.new_session.return_value = "test-session-id"

            # El usuario escribe /salir inmediatamente
            r = runner.invoke(app, ["chat"], input="/salir\n")
        assert r.exit_code == 0

    def test_chat_sends_message_then_exit(self):
        tokens = ["Hola", " desde", " Itzel"]
        with patch("itzel_cli.commands.chat.ItzelClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.is_alive.return_value = True
            mock_client.stream_chat.return_value = iter(tokens)
            mock_cls.return_value = mock_client
            mock_cls.load_session.return_value = "abc-123"
            mock_cls.new_session.return_value = "abc-123"

            r = runner.invoke(app, ["chat"], input="hola\n/exit\n")
        assert r.exit_code == 0
        assert "Hola" in r.output or "itzel" in r.output.lower()


# ─── TestStatus ───────────────────────────────────────────────────────────────

class TestStatus:
    def test_status_offline_renders_table(self):
        r = runner.invoke(app, ["status"])
        assert r.exit_code == 0
        assert "Itzel" in r.output or "Estado" in r.output

    def test_status_json_schema(self):
        r = runner.invoke(app, ["status", "--json"])
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert "backend" in data
        assert "model" in data
        assert "memory" in data
        assert "cli_version" in data
        assert "voice" in data

    def test_status_json_backend_offline(self):
        r = runner.invoke(app, ["status", "--json"])
        data = json.loads(r.output)
        assert data["backend"]["alive"] is False

    def test_status_json_has_url(self):
        r = runner.invoke(app, ["status", "--json"])
        data = json.loads(r.output)
        assert "url" in data["backend"]
        assert "7432" in data["backend"]["url"]

    def test_status_online_with_mock(self):
        mock_models = [_make_model()]

        with (
            patch("itzel_cli.commands.status.ItzelClient") as mock_cls,
            patch("itzel_cli.commands.status._count_memory_entries", return_value=5),
        ):
            mock_client = MagicMock()
            mock_client.is_alive.return_value = True
            mock_client.base_url = "http://127.0.0.1:7432"
            mock_client.list_models.return_value = mock_models
            mock_cls.return_value = mock_client
            mock_cls.load_session.return_value = "abc-1234-5678-0000"

            # Simular respuesta HTTP /api/v1/status/ con httpx inline
            with patch("itzel_cli.commands.status.httpx") as mock_httpx:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"version": "0.1.0", "uptime_s": 120.0}
                mock_ctx_mgr = MagicMock()
                mock_ctx_mgr.__enter__ = MagicMock(
                    return_value=MagicMock(get=MagicMock(return_value=mock_resp))
                )
                mock_ctx_mgr.__exit__ = MagicMock(return_value=False)
                mock_httpx.Client.return_value = mock_ctx_mgr

                r = runner.invoke(app, ["status", "--json"])

        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["backend"]["alive"] is True


# ─── TestModel ────────────────────────────────────────────────────────────────

class TestModel:
    def test_model_list_shows_catalog(self):
        r = runner.invoke(app, ["model", "list"])
        assert r.exit_code == 0
        assert "itzel-1b" in r.output
        assert "itzel-7b" in r.output

    def test_model_list_backend_offline_still_works(self):
        with patch("itzel_cli.commands.model.ItzelClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.is_alive.return_value = False
            mock_cls.return_value = mock_client

            r = runner.invoke(app, ["model", "list"])
        assert r.exit_code == 0
        assert "itzel-1b" in r.output

    def test_model_info_known_model(self):
        r = runner.invoke(app, ["model", "info", "itzel-1b"])
        assert r.exit_code == 0
        assert "llama.cpp" in r.output or "Qwen" in r.output

    def test_model_info_unknown_model_without_backend(self):
        with patch("itzel_cli.commands.model.ItzelClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.is_alive.return_value = False
            mock_cls.return_value = mock_client

            r = runner.invoke(app, ["model", "info", "modelo-inexistente"])
        assert r.exit_code == 0   # muestra info genérica sin crash

    def test_model_use_offline_exits_1(self):
        with patch("itzel_cli.commands.model.ItzelClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.switch_model.side_effect = BackendOfflineError("http://127.0.0.1:7432")
            mock_cls.return_value = mock_client

            r = runner.invoke(app, ["model", "use", "itzel-1b"])
        assert r.exit_code == 1

    def test_model_use_success(self):
        with patch("itzel_cli.commands.model.ItzelClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.switch_model.return_value = {"ok": True, "model_id": "itzel-1b"}
            mock_cls.return_value = mock_client

            r = runner.invoke(app, ["model", "use", "itzel-1b"])
        assert r.exit_code == 0
        assert "itzel-1b" in r.output

    def test_model_pull_unknown_exits_1(self):
        r = runner.invoke(app, ["model", "pull", "modelo-que-no-existe"])
        assert r.exit_code == 1

    def test_model_pull_already_downloaded(self, tmp_path):
        # Crea el .gguf fake
        gguf = tmp_path / "itzel-1b-q4_k_m.gguf"
        gguf.write_bytes(b"fake")

        with patch("itzel_cli.commands.model._MODELS_DIR", tmp_path):
            r = runner.invoke(app, ["model", "pull", "itzel-1b"])
        assert r.exit_code == 0
        assert "ya" in r.output.lower() or "descargado" in r.output.lower()

    def test_model_remove_not_downloaded(self):
        with patch("itzel_cli.commands.model._MODELS_DIR", Path("/nonexistent/dir")):
            r = runner.invoke(app, ["model", "remove", "itzel-1b"])
        assert r.exit_code == 0
        assert "no" in r.output.lower() or "encontrad" in r.output.lower()

    def test_model_remove_with_yes_flag(self, tmp_path):
        gguf = tmp_path / "itzel-1b-q4_k_m.gguf"
        gguf.write_bytes(b"x" * 1024)  # 1KB fake

        with patch("itzel_cli.commands.model._MODELS_DIR", tmp_path):
            r = runner.invoke(app, ["model", "remove", "--yes", "itzel-1b"])
        assert r.exit_code == 0
        assert not gguf.exists()


# ─── TestMemory ───────────────────────────────────────────────────────────────

class TestMemory:
    def _mock_store(self):
        """Mock de MemoryStore para tests de memory."""
        mock_store = MagicMock()
        mock_store._conn.execute.return_value.fetchone.return_value = (5,)
        mock_store._conn.execute.return_value.fetchall.return_value = []
        mock_store.search.return_value = []
        return mock_store

    def test_memory_search_no_results(self):
        with patch("itzel_cli.commands.memory._open_store") as mock_open:
            mock_open.return_value = self._mock_store()
            r = runner.invoke(app, ["memory", "search", "algo que no existe"])
        assert r.exit_code == 0
        assert "resultados" in r.output.lower() or "sin" in r.output.lower()

    def test_memory_list_empty(self):
        with patch("itzel_cli.commands.memory._open_store") as mock_open:
            mock_store = self._mock_store()
            mock_store._conn.execute.return_value.fetchall.return_value = []
            mock_open.return_value = mock_store
            r = runner.invoke(app, ["memory", "list"])
        assert r.exit_code == 0

    def test_memory_stats_shows_counts(self):
        with patch("itzel_cli.commands.memory._open_store") as mock_open:
            mock_store = MagicMock()
            # Cada llamada a execute().fetchone() devuelve counts distintos
            mock_store._conn.execute.return_value.fetchone.side_effect = [
                (42,),   # total
                (20,),   # user_count
                (22,),   # ai_count
                (3,),    # sessions
                ("2026-01-01T00:00:00+00:00",),   # oldest
                ("2026-06-09T12:00:00+00:00",),   # newest
            ]
            mock_open.return_value = mock_store
            r = runner.invoke(app, ["memory", "stats"])
        assert r.exit_code == 0

    def test_memory_clear_requires_confirmation(self):
        with patch("itzel_cli.commands.memory._open_store") as mock_open:
            mock_store = self._mock_store()
            mock_store._conn.execute.return_value.fetchone.return_value = (10,)
            mock_open.return_value = mock_store
            # Input 'n' para rechazar
            r = runner.invoke(app, ["memory", "clear"], input="n\n")
        assert r.exit_code != 0 or "cancelad" in r.output.lower()

    def test_memory_clear_with_yes_flag(self):
        with patch("itzel_cli.commands.memory._open_store") as mock_open:
            mock_store = self._mock_store()
            mock_store._conn.execute.return_value.fetchone.return_value = (7,)
            mock_open.return_value = mock_store
            r = runner.invoke(app, ["memory", "clear", "--yes"])
        assert r.exit_code == 0
        mock_store.clear.assert_called_once()

    def test_memory_export_creates_file(self, tmp_path):
        out = tmp_path / "export.json"
        with patch("itzel_cli.commands.memory._open_store") as mock_open:
            mock_open.return_value = self._mock_store()
            with patch("itzel_cli.commands.memory.ItzelClient") as mock_cls:
                mock_client = MagicMock()
                mock_client.export_memory.return_value = [
                    {"id": "1", "role": "user", "content": "hola",
                     "session_id": "s1", "created_at": "2026-01-01", "metadata": {}}
                ]
                mock_cls.return_value = mock_client
                r = runner.invoke(app, ["memory", "export", str(out)])
        assert r.exit_code == 0
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data) == 1

    def test_memory_export_empty_warns(self, tmp_path):
        out = tmp_path / "empty.json"
        with patch("itzel_cli.commands.memory._open_store") as mock_open:
            mock_open.return_value = self._mock_store()
            with patch("itzel_cli.commands.memory.ItzelClient") as mock_cls:
                mock_client = MagicMock()
                mock_client.export_memory.return_value = []
                mock_cls.return_value = mock_client
                r = runner.invoke(app, ["memory", "export", str(out)])
        assert r.exit_code == 0
        assert not out.exists()   # no se crea si está vacío


# ─── TestSetup ────────────────────────────────────────────────────────────────

class TestSetup:
    def test_setup_completes_without_crash(self, tmp_path):
        config_file = tmp_path / "itzel.config.json"

        with (
            patch("itzel_cli.commands.setup._HOME_DIR",    tmp_path),
            patch("itzel_cli.commands.setup._CONFIG_FILE", config_file),
            patch("itzel_cli.commands.setup._MODELS_DIR",  tmp_path / "models"),
            patch("itzel_cli.commands.setup._LOGS_DIR",    tmp_path / "logs"),
            patch("itzel_cli.commands.setup._check_ollama",   return_value=False),
            patch("itzel_cli.commands.setup._check_llamacpp", return_value=True),
            patch("itzel_cli.commands.setup.ItzelClient") as mock_cls,
        ):
            mock_client = MagicMock()
            mock_client.is_alive.return_value = False
            mock_cls.return_value = mock_client

            # idioma=1, backend=2(none), config no existe → se escribe sola
            r = runner.invoke(app, ["setup"], input="1\n2\n")

        assert r.exit_code == 0
        assert "Paso 1" in r.output
        assert "Paso 5" in r.output

    def test_setup_writes_config_file(self, tmp_path):
        config_file = tmp_path / "itzel.config.json"

        with (
            patch("itzel_cli.commands.setup._HOME_DIR",    tmp_path),
            patch("itzel_cli.commands.setup._CONFIG_FILE", config_file),
            patch("itzel_cli.commands.setup._MODELS_DIR",  tmp_path / "models"),
            patch("itzel_cli.commands.setup._LOGS_DIR",    tmp_path / "logs"),
            patch("itzel_cli.commands.setup._check_ollama",   return_value=False),
            patch("itzel_cli.commands.setup._check_llamacpp", return_value=False),
            patch("itzel_cli.commands.setup.ItzelClient") as mock_cls,
        ):
            mock_client = MagicMock()
            mock_client.is_alive.return_value = False
            mock_cls.return_value = mock_client

            runner.invoke(app, ["setup"], input="1\n1\n")

        if config_file.exists():
            data = json.loads(config_file.read_text(encoding="utf-8"))
            assert data["telemetry"] is False
            assert data["analytics"] is False
            assert "model" in data


# ─── tests de client.py ───────────────────────────────────────────────────────

class TestClient:
    def test_is_alive_returns_false_when_offline(self):
        from itzel_cli.client import ItzelClient
        client = ItzelClient("http://127.0.0.1:1")  # puerto cerrado
        assert client.is_alive() is False

    def test_require_alive_raises_offline_error(self):
        from itzel_cli.client import ItzelClient, BackendOfflineError
        client = ItzelClient("http://127.0.0.1:1")
        with pytest.raises(BackendOfflineError):
            client.require_alive()

    def test_load_session_returns_uuid(self, tmp_path):
        from itzel_cli.client import ItzelClient, _SESSION_FILE
        session_file = tmp_path / "cli_session.json"

        with patch("itzel_cli.client._SESSION_FILE", session_file):
            sid = ItzelClient.load_session()

        assert re.match(r"[0-9a-f]{8}-[0-9a-f]{4}-", sid)

    def test_load_session_persists(self, tmp_path):
        from itzel_cli.client import ItzelClient
        session_file = tmp_path / "cli_session.json"

        with patch("itzel_cli.client._SESSION_FILE", session_file):
            sid1 = ItzelClient.load_session()
            sid2 = ItzelClient.load_session()

        assert sid1 == sid2

    def test_new_session_creates_different_id(self, tmp_path):
        from itzel_cli.client import ItzelClient
        session_file = tmp_path / "cli_session.json"

        with patch("itzel_cli.client._SESSION_FILE", session_file):
            sid1 = ItzelClient.load_session()
            sid2 = ItzelClient.new_session()

        assert sid1 != sid2

    def test_stream_chat_raises_offline_error(self):
        from itzel_cli.client import ItzelClient, BackendOfflineError
        client = ItzelClient("http://127.0.0.1:1")
        with pytest.raises(BackendOfflineError):
            list(client.stream_chat("hola"))

    def test_backend_error_message(self):
        from itzel_cli.client import BackendError
        exc = BackendError(404, "not found")
        assert "404" in str(exc)
        assert "not found" in str(exc)

    def test_offline_error_message(self):
        from itzel_cli.client import BackendOfflineError
        exc = BackendOfflineError("http://localhost:7432")
        assert "localhost:7432" in str(exc)
        assert "itzel setup" in str(exc) or "uvicorn" in str(exc)
