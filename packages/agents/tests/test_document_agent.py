"""
Tests del DocumentAgent.

summarize se prueba con resumen extractivo (sin LLM) y con summarizer inyectado.
read/write de DOCX y XLSX se prueban solo si las dependencias están instaladas
(skipif). Las dependencias faltantes deben producir un error legible.
"""

from __future__ import annotations

import importlib.util

import pytest

from itzel_core.tools import ActionLogger, AutoConfirmHandler, AutoDenyHandler
from itzel_agents.document_agent import DocumentAgent

HAS_DOCX     = importlib.util.find_spec("docx") is not None
HAS_OPENPYXL = importlib.util.find_spec("openpyxl") is not None
HAS_PDFMINER = importlib.util.find_spec("pdfminer") is not None


# ─── summarize ────────────────────────────────────────────────────────────────

class TestSummarize:
    def test_extractive_summary_no_llm(self, document_agent, tmp_workspace):
        doc = tmp_workspace / "largo.txt"
        doc.write_text(
            "Primera oración. Segunda oración. Tercera. Cuarta. Quinta. Sexta extra.",
            encoding="utf-8",
        )
        r = document_agent.invoke("summarize", {"path": str(doc)})
        assert r.ok is True
        # Toma 5 oraciones; la sexta no entra.
        assert "Primera oración" in r.value
        assert "Sexta" not in r.value

    def test_summarize_with_injected_llm(self, auto_confirm, temp_logger, tmp_workspace):
        doc = tmp_workspace / "nota.txt"
        doc.write_text("Texto cualquiera para resumir.", encoding="utf-8")

        agent = DocumentAgent(
            summarizer=lambda text: f"RESUMEN({len(text)})",
            confirm_handler=auto_confirm,
            action_sink=temp_logger,
        )
        r = agent.invoke("summarize", {"path": str(doc)})
        assert r.ok is True
        assert r.value.startswith("RESUMEN(")
        agent.shutdown()

    def test_summarize_empty_doc(self, document_agent, tmp_workspace):
        doc = tmp_workspace / "vacio.txt"
        doc.write_text("   ", encoding="utf-8")
        r = document_agent.invoke("summarize", {"path": str(doc)})
        assert r.ok is True
        assert "vacío" in r.value or "vacio" in r.value

    def test_summarize_unsupported_type(self, document_agent, tmp_workspace):
        doc = tmp_workspace / "imagen.png"
        doc.write_bytes(b"\x89PNG fake")
        r = document_agent.invoke("summarize", {"path": str(doc)})
        assert r.ok is False  # tipo no soportado


# ─── DOCX ─────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_DOCX, reason="python-docx no instalado")
class TestDocx:
    def test_write_and_read_roundtrip(self, document_agent, tmp_workspace):
        path = tmp_workspace / "salida.docx"
        w = document_agent.invoke(
            "write_docx",
            {"path": str(path), "content": "Línea uno\nLínea dos"},
        )
        assert w.ok is True
        assert path.exists()

        r = document_agent.invoke("read_docx", {"path": str(path)})
        assert r.ok is True
        assert "Línea uno" in r.value
        assert "Línea dos" in r.value

    def test_write_docx_overwrite_denied(self, tmp_workspace):
        path = tmp_workspace / "existente.docx"
        # Crear primero con auto-confirm
        a1 = DocumentAgent(confirm_handler=AutoConfirmHandler(),
                           action_sink=ActionLogger(log_file=tmp_workspace / "l.log"))
        a1.invoke("write_docx", {"path": str(path), "content": "original"})
        a1.shutdown()
        assert path.exists()

        # Sobrescribir con auto-deny → bloqueado
        a2 = DocumentAgent(confirm_handler=AutoDenyHandler(),
                           action_sink=ActionLogger(log_file=tmp_workspace / "l.log"))
        r = a2.invoke("write_docx", {"path": str(path), "content": "HACKED"})
        assert r.denied is True
        a2.shutdown()


# ─── XLSX ─────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_OPENPYXL, reason="openpyxl no instalado")
class TestXlsx:
    def test_read_xlsx(self, document_agent, tmp_workspace):
        from openpyxl import Workbook

        path = tmp_workspace / "datos.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "Hoja1"
        ws.append(["nombre", "edad"])
        ws.append(["Edwin", 30])
        wb.save(str(path))

        r = document_agent.invoke("read_xlsx", {"path": str(path)})
        assert r.ok is True
        assert "Hoja1" in r.value["sheet_names"]
        rows = r.value["sheets"]["Hoja1"]
        assert rows[0] == ["nombre", "edad"]
        assert rows[1] == ["Edwin", 30]


# ─── dependencias faltantes ───────────────────────────────────────────────────

@pytest.mark.skipif(HAS_PDFMINER, reason="solo aplica si pdfminer NO está")
class TestMissingDeps:
    def test_read_pdf_clear_error(self, document_agent, tmp_workspace):
        fake = tmp_workspace / "x.pdf"
        fake.write_bytes(b"%PDF-1.4 fake")
        r = document_agent.invoke("read_pdf", {"path": str(fake)})
        assert r.ok is False
        assert "pdfminer" in r.error


# ─── registro ─────────────────────────────────────────────────────────────────

class TestRegistration:
    def test_five_tools_registered(self, document_agent):
        assert set(document_agent.tools) == {
            "read_pdf", "read_docx", "read_xlsx", "write_docx", "summarize",
        }
