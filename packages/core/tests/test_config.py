"""Tests para el módulo de configuración."""

from itzel_core.config import ItzelConfig


def test_default_config_has_required_fields():
    cfg = ItzelConfig()
    assert cfg.version == "0.1.0"
    assert cfg.language == "es-MX"
    assert cfg.telemetry is False
    assert cfg.analytics is False


def test_telemetry_always_false():
    """Telemetría y analytics son siempre False — principio no negociable."""
    cfg = ItzelConfig(telemetry=False, analytics=False)
    assert cfg.telemetry is False
    assert cfg.analytics is False


def test_voice_defaults():
    cfg = ItzelConfig()
    assert cfg.voice.enabled is True
    assert cfg.voice.stt_model == "whisper-small"
    assert cfg.voice.tts_model == "kokoro"


def test_security_defaults():
    cfg = ItzelConfig()
    assert cfg.security.sandbox_enabled is True
    assert cfg.security.confirm_irreversible is True


def test_server_defaults():
    cfg = ItzelConfig()
    assert cfg.server.port == 7432
    assert cfg.server.host == "127.0.0.1"
