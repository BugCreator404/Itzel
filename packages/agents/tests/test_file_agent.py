"""
Tests del FileAgent.

Verifican las 8 tools de archivo, la confirmación condicional (sobrescritura),
el fail-safe de borrado y que cada acción quede auditada en el log.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ─── read / write / info ──────────────────────────────────────────────────────

class TestReadWrite:
    def test_write_and_read_roundtrip(self, file_agent, tmp_workspace):
        target = tmp_workspace / "nuevo.txt"
        w = file_agent.invoke("write_file", {"path": str(target), "content": "contenido"})
        assert w.ok is True
        assert target.exists()

        r = file_agent.invoke("read_file", {"path": str(target)})
        assert r.ok is True
        assert r.value == "contenido"

    def test_read_nonexistent_fails(self, file_agent, tmp_workspace):
        r = file_agent.invoke("read_file", {"path": str(tmp_workspace / "nope.txt")})
        assert r.ok is False

    def test_read_directory_fails(self, file_agent, tmp_workspace):
        r = file_agent.invoke("read_file", {"path": str(tmp_workspace / "datos")})
        assert r.ok is False

    def test_get_info(self, file_agent, tmp_workspace):
        r = file_agent.invoke("get_info", {"path": str(tmp_workspace / "notas.txt")})
        assert r.ok is True
        assert r.value["type"] == "file"
        assert r.value["size"] > 0
        assert r.value["suffix"] == ".txt"

    def test_write_creates_parent_dirs(self, file_agent, tmp_workspace):
        target = tmp_workspace / "a" / "b" / "c.txt"
        r = file_agent.invoke("write_file", {"path": str(target), "content": "x"})
        assert r.ok is True
        assert target.exists()


# ─── confirmación de sobrescritura ────────────────────────────────────────────

class TestOverwriteConfirmation:
    def test_write_existing_confirmed(self, file_agent, tmp_workspace):
        """write_file sobre archivo existente: con auto-confirm procede."""
        existing = tmp_workspace / "notas.txt"
        r = file_agent.invoke("write_file", {"path": str(existing), "content": "nuevo"})
        assert r.ok is True
        assert existing.read_text(encoding="utf-8") == "nuevo"

    def test_write_existing_denied(self, file_agent_deny, tmp_workspace):
        """write_file sobre archivo existente: con auto-deny se bloquea."""
        existing = tmp_workspace / "notas.txt"
        original = existing.read_text(encoding="utf-8")
        r = file_agent_deny.invoke("write_file", {"path": str(existing), "content": "HACK"})
        assert r.denied is True
        assert existing.read_text(encoding="utf-8") == original  # intacto

    def test_write_new_no_confirmation(self, file_agent_deny, tmp_workspace):
        """write_file a archivo NUEVO no pide confirmación (procede aún con deny)."""
        target = tmp_workspace / "fresco.txt"
        r = file_agent_deny.invoke("write_file", {"path": str(target), "content": "ok"})
        assert r.ok is True
        assert target.exists()


# ─── move / copy ──────────────────────────────────────────────────────────────

class TestMoveCopy:
    def test_move_new_destination(self, file_agent, tmp_workspace):
        src = tmp_workspace / "notas.txt"
        dst = tmp_workspace / "renombrado.txt"
        r = file_agent.invoke("move_file", {"src": str(src), "dst": str(dst)})
        assert r.ok is True
        assert dst.exists() and not src.exists()

    def test_move_existing_destination_denied(self, file_agent_deny, tmp_workspace):
        """Mover sobre un destino existente requiere confirmación → deny lo bloquea."""
        src = tmp_workspace / "notas.txt"
        dst = tmp_workspace / "datos" / "lista.txt"  # ya existe
        r = file_agent_deny.invoke("move_file", {"src": str(src), "dst": str(dst)})
        assert r.denied is True
        assert src.exists()  # no se movió

    def test_copy_file(self, file_agent, tmp_workspace):
        src = tmp_workspace / "notas.txt"
        dst = tmp_workspace / "copia.txt"
        r = file_agent.invoke("copy_file", {"src": str(src), "dst": str(dst)})
        assert r.ok is True
        assert dst.exists() and src.exists()
        assert dst.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")


# ─── delete (fail-safe) ───────────────────────────────────────────────────────

class TestDelete:
    def test_delete_confirmed(self, file_agent, tmp_workspace):
        target = tmp_workspace / "notas.txt"
        r = file_agent.invoke("delete_file", {"path": str(target)})
        assert r.ok is True
        assert not target.exists()

    def test_delete_denied_preserves_file(self, file_agent_deny, tmp_workspace):
        """SEGURIDAD: con auto-deny, delete_file NO borra el archivo."""
        target = tmp_workspace / "notas.txt"
        r = file_agent_deny.invoke("delete_file", {"path": str(target)})
        assert r.denied is True
        assert target.exists()  # el archivo sigue ahí

    def test_delete_directory(self, file_agent, tmp_workspace):
        target = tmp_workspace / "datos"
        r = file_agent.invoke("delete_file", {"path": str(target)})
        assert r.ok is True
        assert not target.exists()


# ─── list / search ────────────────────────────────────────────────────────────

class TestListSearch:
    def test_list_dir(self, file_agent, tmp_workspace):
        r = file_agent.invoke("list_dir", {"path": str(tmp_workspace)})
        assert r.ok is True
        names = {e["name"] for e in r.value}
        assert "notas.txt" in names
        assert "datos" in names

    def test_list_dir_types(self, file_agent, tmp_workspace):
        r = file_agent.invoke("list_dir", {"path": str(tmp_workspace)})
        by_name = {e["name"]: e for e in r.value}
        assert by_name["datos"]["type"] == "dir"
        assert by_name["notas.txt"]["type"] == "file"

    def test_search_by_name(self, file_agent, tmp_workspace):
        r = file_agent.invoke("search_files", {"query": "lista", "path": str(tmp_workspace)})
        assert r.ok is True
        assert any(e["name"] == "lista.txt" for e in r.value)
        assert all(e["match"] == "name" for e in r.value)

    def test_search_by_content(self, file_agent, tmp_workspace):
        r = file_agent.invoke(
            "search_files",
            {"query": "ajolote", "path": str(tmp_workspace), "in_content": True},
        )
        # 'ajolote' no está en los archivos de muestra → sin resultados
        assert r.ok is True
        assert r.value == []

    def test_search_content_match(self, file_agent, tmp_workspace):
        (tmp_workspace / "secreto.txt").write_text("clave: ajolote123", encoding="utf-8")
        r = file_agent.invoke(
            "search_files",
            {"query": "ajolote", "path": str(tmp_workspace), "in_content": True},
        )
        assert any(e["name"] == "secreto.txt" and e["match"] == "content" for e in r.value)


# ─── auditoría ────────────────────────────────────────────────────────────────

class TestAudit:
    def test_actions_are_logged(self, file_agent, temp_logger, tmp_workspace):
        file_agent.invoke("read_file", {"path": str(tmp_workspace / "notas.txt")})
        file_agent.invoke("delete_file", {"path": str(tmp_workspace / "notas.txt")})

        history = temp_logger.read_history()
        actions = [(e["agent"], e["action"], e["result"]) for e in history]
        assert ("file", "read_file", "ok") in actions
        assert ("file", "delete_file", "ok") in actions

    def test_denied_action_logged(self, file_agent_deny, temp_logger, tmp_workspace):
        file_agent_deny.invoke("delete_file", {"path": str(tmp_workspace / "notas.txt")})
        history = temp_logger.read_history()
        assert any(e["action"] == "delete_file" and e["result"] == "denied" for e in history)
