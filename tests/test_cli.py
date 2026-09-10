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
    """브리핑 준비 단계는 서로 독립인데 각각 별도 bash 왕복으로 순서대로 돌았다."""
    import time

    from invagent.daily_prep import Step, run_steps

    steps = [
        Step("시장 신호", [sys.executable, "-c", "import time; time.sleep(0.15); print('signals')"]),
        Step("전고점 낙폭", [sys.executable, "-c", "import time; time.sleep(0.15); print('drawdown')"]),
    ]

    started = time.monotonic()
    results = run_steps(steps)
    elapsed = time.monotonic() - started

    assert [r.name for r in results] == ["시장 신호", "전고점 낙폭"]
    assert [r.stdout.strip() for r in results] == ["signals", "drawdown"]
    assert all(r.ok for r in results)
    assert elapsed < 2 * 0.15, "여전히 한 단계씩 기다린다"


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

    names = [s.name for s in build_steps(snapshot=tmp_path / "없음.md", no_cache=False)]
    assert "전고점 낙폭" not in names
    assert "시장 신호" in names


def test_daily_prep_no_cache_reaches_every_step():
    """단계마다 `--no-cache`를 붙이면 그 플래그가 없는 스크립트(fetch_market_signals)는 조용히 캐시를 쓴다."""
    from invagent.daily_prep import build_steps

    off = build_steps(snapshot=None, no_cache=True)
    assert off, "단계가 하나도 없다"
    for step in off:
        assert step.env.get("INVAGENT_HTTP_CACHE") == "0", f"{step.name}이 캐시를 계속 쓴다"

    on = build_steps(snapshot=None, no_cache=False)
    for step in on:
        assert "INVAGENT_HTTP_CACHE" not in step.env


def test_daily_prep_step_env_reaches_the_subprocess():
    """Step.env가 실제로 자식 프로세스에 전달되지 않으면 위 테스트는 형식만 검사하는 셈이다."""
    from invagent.daily_prep import Step, run_steps

    result = run_steps([
        Step(
            "환경",
            [sys.executable, "-c", "import os; print(os.environ.get('INVAGENT_HTTP_CACHE', 'unset'))"],
            env={"INVAGENT_HTTP_CACHE": "0"},
        )
    ])[0]
    assert result.ok and result.stdout.strip() == "0"

def test_daily_prep_cli_exits_zero_even_when_a_step_fails(monkeypatch):
    """단계 실패가 브리핑을 막지 않는다는 것이 이 명령의 계약이다 — 종료 코드로 지켜야 한다."""
    from invagent import daily_prep

    monkeypatch.setattr(
        daily_prep, "build_steps",
        lambda **kw: [daily_prep.Step("죽는 단계", [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(1)"])],
    )

    result = CliRunner().invoke(cli, ["daily-prep"])

    assert result.exit_code == 0
    assert "### 죽는 단계" in result.output
    assert "수집 실패" in result.output and "boom" in result.output


def test_daily_prep_render_keeps_partial_output_and_its_warnings():
    """일부만 실패한 단계는 받은 출력과 누락 사유를 같이 남긴다 — 스킬의 비블로킹 규정 그대로다."""
    from invagent.daily_prep import StepResult, render

    text = render([
        StepResult("시장 신호", True, "지수 6,687", "[누락] credit_balance — HTTP 500"),
        StepResult("전고점 낙폭", False, "", "cookie 없음"),
    ])

    assert "지수 6,687" in text
    assert "credit_balance" in text
    assert "(수집 실패 — cookie 없음)" in text


def test_fetch_messages_passes_todays_blog_dir(tmp_path):
    """fetch-messages는 raw와 같은 날짜의 blogs 폴더를 넘기고 저장한 글 수를 알린다"""
    from datetime import datetime
    from invagent.core.config import Config

    config = Config(api_id=1, api_hash="h", session_path=tmp_path / "s", output_dir=tmp_path)
    captured = {}

    class FakeFetcher:
        def __init__(self, config, client_manager):
            pass

        async def fetch_saved_messages(self, days, **kwargs):
            captured.update(kwargs)
            return []

        def format_messages_markdown(self, messages):
            return ""

    with patch("invagent.cli.Config.from_env", return_value=config), \
         patch("invagent.cli.MessageFetcher", FakeFetcher), \
         patch("invagent.cli.TelegramClientManager") as manager_cls:
        manager_cls.return_value.disconnect = AsyncMock()
        result = CliRunner().invoke(cli, ["fetch-messages"])

    today = datetime.now().strftime("%Y-%m-%d")
    assert result.exit_code == 0, result.output
    assert captured["blog_dir"] == tmp_path / "daily-digest/blogs" / today
    assert "Blog posts saved: 0" in result.output
