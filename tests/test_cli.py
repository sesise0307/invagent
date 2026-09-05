import sys
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
    assert '--download-images' in result.output
    assert '--no-download-images' in result.output


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

def test_cli_daily_prep_help():
    """daily-prep 명령어 도움말"""
    runner = CliRunner()
    result = runner.invoke(cli, ["daily-prep", "--help"])

    assert result.exit_code == 0


def test_daily_prep_runs_steps_concurrently_in_a_fixed_order():
    """브리핑 준비 3단계는 서로 독립인데 각각 별도 bash 왕복으로 순서대로 돌았다."""
    import time

    from invagent.daily_prep import Step, run_steps

    steps = [
        Step("시장 신호", [sys.executable, "-c", "import time; time.sleep(0.15); print('signals')"]),
        Step("현금 사다리", [sys.executable, "-c", "import time; time.sleep(0.15); print('ladder')"]),
        Step("전고점 낙폭", [sys.executable, "-c", "import time; time.sleep(0.15); print('drawdown')"]),
    ]

    started = time.monotonic()
    results = run_steps(steps)
    elapsed = time.monotonic() - started

    assert [r.name for r in results] == ["시장 신호", "현금 사다리", "전고점 낙폭"]
    assert [r.stdout.strip() for r in results] == ["signals", "ladder", "drawdown"]
    assert all(r.ok for r in results)
    assert elapsed < 3 * 0.15, "여전히 한 단계씩 기다린다"


def test_daily_prep_step_failure_never_blocks_the_others():
    """각 단계는 비블로킹이다 — 하나가 죽어도 브리핑 준비가 멈추면 안 된다."""
    from invagent.daily_prep import Step, run_steps

    results = run_steps([
        Step("죽는 단계", [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(1)"]),
        Step("사는 단계", [sys.executable, "-c", "print('alive')"]),
    ])

    assert results[0].ok is False and "boom" in results[0].stderr
    assert results[1].ok is True and results[1].stdout.strip() == "alive"


def test_daily_prep_skips_a_step_whose_input_is_missing(tmp_path):
    """스냅샷이 없으면 전고점 낙폭 단계는 건너뛴다 — 실패가 아니라 미실행이다."""
    from invagent.daily_prep import build_steps

    names = [s.name for s in build_steps(snapshot=tmp_path / "없음.md", vkospi=None, net_buy_days=None, no_cache=False)]
    assert "전고점 낙폭" not in names
    assert "시장 신호" in names and "현금 투입 사다리" in names


def test_daily_prep_passes_optional_inputs_through():
    """VKOSPI·수급 일수는 자동 수집 경로가 없어 인자로만 들어온다. 안 주면 붙이지 않는다."""
    from invagent.daily_prep import build_steps

    with_args = build_steps(snapshot=None, vkospi=28.5, net_buy_days=5, no_cache=True)
    ladder = next(s for s in with_args if s.name == "현금 투입 사다리")
    assert "--vkospi" in ladder.command and "28.5" in ladder.command
    assert "--net-buy-days" in ladder.command and "5" in ladder.command
    assert "--no-cache" in ladder.command

    without = build_steps(snapshot=None, vkospi=None, net_buy_days=None, no_cache=False)
    ladder = next(s for s in without if s.name == "현금 투입 사다리")
    assert "--vkospi" not in ladder.command and "--no-cache" not in ladder.command
