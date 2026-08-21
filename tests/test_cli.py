import pytest
from click.testing import CliRunner
from unittest.mock import AsyncMock, patch

from invagent.cli import cli


def test_cli_help():
    """CLI 도움말 표시"""
    runner = CliRunner()
    result = runner.invoke(cli, ['--help'])

    assert result.exit_code == 0
    assert "invagent" in result.output.lower()


def test_cli_authenticate_help():
    """authenticate 명령어 도움말"""
    runner = CliRunner()
    result = runner.invoke(cli, ['authenticate', '--help'])

    assert result.exit_code == 0


def test_cli_fetch_messages_help():
    """fetch-messages 명령어 도움말"""
    runner = CliRunner()
    result = runner.invoke(cli, ['fetch-messages', '--help'])

    assert result.exit_code == 0


def test_cli_add_stock_help():
    """add-stock 명령어 도움말"""
    runner = CliRunner()
    result = runner.invoke(cli, ['add-stock', '--help'])

    assert result.exit_code == 0


def test_cli_set_target_help():
    """set-target 명령어 도움말"""
    runner = CliRunner()
    result = runner.invoke(cli, ['set-target', '--help'])

    assert result.exit_code == 0


def test_cli_show_stock_help():
    """show-stock 명령어 도움말"""
    runner = CliRunner()
    result = runner.invoke(cli, ['show-stock', '--help'])

    assert result.exit_code == 0


def test_cli_authenticate_reports_success(monkeypatch):
    """authenticate 명령이 성공 메시지와 세션 경로를 출력"""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123def456")

    runner = CliRunner()

    with patch("invagent.cli.authenticate") as mock_authenticate:
        mock_client = AsyncMock()
        mock_client.get_me = AsyncMock(return_value=AsyncMock(first_name="Test", username="tester"))
        mock_client.disconnect = AsyncMock()
        mock_authenticate.return_value = mock_client

        result = runner.invoke(cli, ["authenticate"])

    assert result.exit_code == 0
    assert "인증 성공" in result.output
    assert ".telegram_session" in result.output
