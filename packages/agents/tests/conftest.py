"""
Fixtures compartidas para los tests de itzel-agents.

Ningún test toca recursos reales del usuario: todo ocurre en directorios
temporales y con un log de auditoría temporal. Las acciones irreversibles usan
AutoConfirmHandler / AutoDenyHandler en vez de stdin real.

Las dependencias opcionales (python-docx, openpyxl, pdfminer) se detectan con
helpers `has_*`; los tests que las requieren se saltan con pytest.mark.skipif
si no están instaladas.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Iterator

import pytest

from itzel_core.tools import ActionLogger, AutoConfirmHandler, AutoDenyHandler
from itzel_agents.code_agent import CodeAgent
from itzel_agents.document_agent import DocumentAgent
from itzel_agents.file_agent import FileAgent
from itzel_agents.system_agent import SystemAgent


# ─── detección de dependencias opcionales ─────────────────────────────────────

def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


HAS_DOCX     = _has("docx")
HAS_OPENPYXL = _has("openpyxl")
HAS_PDFMINER = _has("pdfminer")
HAS_PSUTIL   = _has("psutil")
HAS_PYPERCLIP = _has("pyperclip")


# ─── workspace temporal ───────────────────────────────────────────────────────

@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """
    Directorio temporal con archivos de muestra:
        notas.txt        — texto plano
        datos/lista.txt  — en subcarpeta
    """
    (tmp_path / "notas.txt").write_text(
        "Hola, soy Itzel.\nVivo en tu máquina.\n", encoding="utf-8"
    )
    sub = tmp_path / "datos"
    sub.mkdir()
    (sub / "lista.txt").write_text("uno\ndos\ntres\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def temp_logger(tmp_path: Path) -> ActionLogger:
    """ActionLogger que escribe a un log temporal aislado."""
    return ActionLogger(log_file=tmp_path / "actions.log")


# ─── handlers ─────────────────────────────────────────────────────────────────

@pytest.fixture
def auto_confirm() -> AutoConfirmHandler:
    return AutoConfirmHandler()


@pytest.fixture
def auto_deny() -> AutoDenyHandler:
    return AutoDenyHandler()


# ─── agentes pre-cableados ────────────────────────────────────────────────────

@pytest.fixture
def file_agent(auto_confirm, temp_logger) -> Iterator[FileAgent]:
    """FileAgent con auto-confirm (permite irreversibles) + log temporal."""
    agent = FileAgent(confirm_handler=auto_confirm, action_sink=temp_logger)
    yield agent
    agent.shutdown()


@pytest.fixture
def file_agent_deny(auto_deny, temp_logger) -> Iterator[FileAgent]:
    """FileAgent con auto-deny (rechaza irreversibles) — para tests de seguridad."""
    agent = FileAgent(confirm_handler=auto_deny, action_sink=temp_logger)
    yield agent
    agent.shutdown()


@pytest.fixture
def system_agent(auto_deny, temp_logger) -> Iterator[SystemAgent]:
    """
    SystemAgent con auto-DENY por defecto: los tests no deben abrir/cerrar
    apps reales. Las tools no destructivas no piden confirmación igual.
    """
    agent = SystemAgent(confirm_handler=auto_deny, action_sink=temp_logger)
    yield agent
    agent.shutdown()


@pytest.fixture
def code_agent(auto_confirm, temp_logger) -> Iterator[CodeAgent]:
    """CodeAgent con auto-confirm + log temporal."""
    agent = CodeAgent(confirm_handler=auto_confirm, action_sink=temp_logger)
    yield agent
    agent.shutdown()


@pytest.fixture
def code_agent_deny(auto_deny, temp_logger) -> Iterator[CodeAgent]:
    """CodeAgent con auto-deny — para verificar que el código NO se ejecuta."""
    agent = CodeAgent(confirm_handler=auto_deny, action_sink=temp_logger)
    yield agent
    agent.shutdown()


@pytest.fixture
def document_agent(auto_confirm, temp_logger) -> Iterator[DocumentAgent]:
    """DocumentAgent sin summarizer (usa resumen extractivo de respaldo)."""
    agent = DocumentAgent(confirm_handler=auto_confirm, action_sink=temp_logger)
    yield agent
    agent.shutdown()
