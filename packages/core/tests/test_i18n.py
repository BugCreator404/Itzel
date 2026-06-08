"""Tests para el sistema de internacionalización."""

from itzel_core.i18n.i18n import T


def test_spanish_key_exists():
    t = T("es-MX")
    result = t.get("cli.backend_offline")
    assert "setup" in result.lower()


def test_english_key_exists():
    t = T("en-US")
    result = t.get("cli.backend_offline")
    assert "setup" in result.lower()


def test_missing_key_returns_key():
    t = T("es-MX")
    result = t.get("does.not.exist")
    assert result == "does.not.exist"


def test_format_kwargs():
    t = T("es-MX")
    result = t.get("errors.backend_unreachable", url="http://localhost:7432")
    assert "localhost:7432" in result


def test_unsupported_lang_falls_back():
    t = T("fr-FR")
    assert t.lang == "en-US"
