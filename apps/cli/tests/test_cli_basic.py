"""Tests básicos del CLI de Itzel."""

from typer.testing import CliRunner

from itzel_cli.main import app

runner = CliRunner()


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "ITZEL" in result.output


def test_help_shows_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ask" in result.output
    assert "run" in result.output
    assert "chat" in result.output


def test_status_command():
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0


def test_status_json():
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 0
    import json
    data = json.loads(result.output)
    assert "cli_version" in data
    assert "backend" in data
