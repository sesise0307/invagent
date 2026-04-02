import pytest
from click.testing import CliRunner
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


def test_cli_download_pdfs_help():
    """download-pdfs 명령어 도움말"""
    runner = CliRunner()
    result = runner.invoke(cli, ['download-pdfs', '--help'])

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
