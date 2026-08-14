"""Validate shared Claude Code and Codex repository configuration."""

from __future__ import annotations

import importlib.util
import re
import tomllib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / ".agents" / "skills"
SKILL_NAMES = (
    "advice",
    "monthly-investment-review",
    "opendart",
    "summarize-telegram",
)


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_skill_is_shared_with_claude(skill_name: str) -> None:
    canonical = SKILLS_ROOT / skill_name
    claude_link = REPO_ROOT / ".claude" / "skills" / skill_name

    assert (canonical / "SKILL.md").is_file()
    assert claude_link.is_symlink()
    assert claude_link.resolve() == canonical.resolve()


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_skill_has_valid_frontmatter_and_ui_metadata(skill_name: str) -> None:
    skill_dir = SKILLS_ROOT / skill_name
    content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    frontmatter_match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)

    assert frontmatter_match is not None
    frontmatter = frontmatter_match.group(1)
    assert re.search(rf"^name:\s*[\"']?{re.escape(skill_name)}[\"']?\s*$", frontmatter, re.MULTILINE)
    assert re.search(r"^description:\s*", frontmatter, re.MULTILINE)

    metadata = (skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert "display_name:" in metadata
    assert "short_description:" in metadata
    assert f"${skill_name}" in metadata


def test_skills_contain_no_credentials_or_machine_paths() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in SKILLS_ROOT.rglob("*")
        if path.is_file() and path.suffix in {".md", ".py", ".yaml"}
    )

    assert not re.search(r"opendart=[0-9a-f]{20,}", combined)
    assert "/Users/sesise" not in combined
    assert ".claude/skills" not in combined


def test_project_codex_config_is_credential_free() -> None:
    config_path = REPO_ROOT / ".codex" / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))

    assert config["approval_policy"] == "on-request"
    assert config["sandbox_mode"] == "workspace-write"
    assert "mcp_servers" not in config


def _load_market_signal_module():
    script_path = SKILLS_ROOT / "summarize-telegram" / "scripts" / "fetch_market_signals.py"
    spec = importlib.util.spec_from_file_location("fetch_market_signals", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_market_signal_endpoints_point_at_stockdata_api() -> None:
    """사이트가 클라이언트 렌더링으로 바뀐 뒤 실제 호출 경로는 /stockdata/api/v1/market."""
    module = _load_market_signal_module()

    assert module.API_BASE == "https://stockeasy.intellio.kr/stockdata/api/v1/market"
    assert set(module.ENDPOINTS) == {
        "indices",
        "big_picture",
        "market_monitor",
        "credit_balance",
    }


def test_market_signal_main_renders_api_payload(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_market_signal_module()

    payloads = {
        "indices": {
            "short_term_signal": "green",
            "long_term_signal": "yellow",
            "indices": [
                {
                    "index_name": "종합(KOSPI)",
                    "current_value": 6977.94,
                    "price_change_percent": 2.42,
                    "rising_stocks": 677,
                    "falling_stocks": 204,
                    "upper_limit_stocks": 3,
                    "lower_limit_stocks": 0,
                }
            ],
        },
        "big_picture": {
            "kospi": {
                "status": "confirmed_uptrend",
                "rally_day_count": 0,
                "distribution_days": [],
                "last_ftd_date": "2026-08-05",
            }
        },
        "market_monitor": {
            "data": [
                {
                    "일자": "2026-08-14",
                    "KOSPI": 6977.34,
                    "20down_ratio": 0.176,
                    "200down_ratio": 0.786,
                    "52W_High_count": 74.0,
                    "52W_Low_count": 43.0,
                    "ADR(KOSPI)": 125.84,
                    "ADR(KOSDAQ)": 118.65,
                }
            ]
        },
        "credit_balance": {
            "data": [
                {
                    "date": "2026-08-13",
                    "total_credit": 309262,
                    "investor_deposit": 1000683,
                    "credit_deposit_ratio": 30.9051,
                    "margin_call_amount": 50,
                }
            ]
        },
    }
    monkeypatch.setattr(module, "fetch_api", lambda name: (payloads[name], None))

    assert module.main() == 0

    out = capsys.readouterr().out
    assert "green" in out
    assert "6,977.94" in out
    assert "상승 추세 확인" in out
    assert "30.9%" in out


def test_market_signal_main_reports_api_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """지수·빅픽처가 모두 실패하면 사유를 stderr에 남기고 exit 1."""
    module = _load_market_signal_module()
    monkeypatch.setattr(module, "fetch_api", lambda name: (None, "HTTP 404"))

    assert module.main() == 1
    assert "HTTP 404" in capsys.readouterr().err


PORTFOLIO_DUMP = """|  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
| :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| 구분 |  계좌 | 섹터 | 종목 | 보유 | 평단 | 현재가 | 매수금액 | 평가금액 | 수익률 | 손익 | 비중 |  | 잔고 | ₩100,000,000 | 수익률 | 25.53% |
| B | 삼성 | 반도체 | 알파전자 | 95 | ₩703,575 | ₩1,466,000 | ₩66,839,625 | ₩139,270,000 | 108.36% | ₩72,430,375 | 26.3% | 슈퍼 사이클 |  |  |  |  |
| B | 삼성 | 바이오 | 베타파마 | 900 | ₩68,845 | ₩57,500 | ₩61,960,500 | ₩51,750,000 | \\-16.48% | \\-₩10,210,500 | 9.8% | 신약 |  |  |  |  |
| C | 삼성 | 소비재 | 감마엔터 | 500 | ₩30,278 | ₩28,400 | ₩15,139,000 | ₩14,200,000 | \\-9.20% | \\-₩939,000 | 2.7% | 신작 |  |  |  |  |
| 0 | 은행 | 현금 | \\_현금 | 1 | ₩5,000,000 | ₩5,000,000 | ₩5,000,000 | ₩5,000,000 | 0.00% | ₩0 | 5.1% | 현금도 종목이다 |  |  |  |  |

|  |  |  |  |  |
| :-: | :-: | :-: | :-: | :-: |
|  | 섹터 | 비중 | 평가금액 | 수익률 |
|  | 반도체 | 42.3% | ₩139,270,000 | 108.36% |
|  | 바이오 | 24.1% | ₩51,750,000 | \\-16.48% |
|  |  | 0.0% | ₩0 | \\#DIV/0\\! |

|  |  |  |  |
| :-: | :-: | :-: | :-: |
| 종목 | 최초 투자 | 투자 종료 | 수익률 |
| 델타중공업 | 2024-01-02 | 2024-05-05 | 31.00% |
"""


def _load_portfolio_module():
    script_path = SKILLS_ROOT / "summarize-telegram" / "scripts" / "extract_portfolio.py"
    spec = importlib.util.spec_from_file_location("extract_portfolio", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_portfolio_parser_extracts_first_sheet_only() -> None:
    module = _load_portfolio_module()
    snapshot = module.build_snapshot(PORTFOLIO_DUMP, "2026-08-09")

    assert "# 포트폴리오 스냅샷 — 2026-08-09" in snapshot
    assert "잔고 ₩100,000,000 · 수익률 25.53%" in snapshot
    assert "## 보유 (3종목 + 현금)" in snapshot
    # 다음 시트(매매기록)는 잘라낸다
    assert "델타중공업" not in snapshot
    assert "최초 투자" not in snapshot
    # 섹터 표는 유지하되 빈 행·#DIV/0! 행은 버린다
    assert "| 반도체 | 42.3% | ₩139,270,000 | 108.36% |" in snapshot
    assert "DIV/0" not in snapshot


def test_portfolio_parser_unescapes_and_flags_rules() -> None:
    module = _load_portfolio_module()
    snapshot = module.build_snapshot(PORTFOLIO_DUMP, "2026-08-09")

    assert "-16.48%" in snapshot and "\\-16.48%" not in snapshot
    assert "| _현금 | 현금 |" in snapshot

    assert "매매규칙 6(-15% 손절): 베타파마 -16.48% (비중 9.8%) ← **위반**" in snapshot
    assert "매매규칙 7(-8% 비중 축소 고려): 감마엔터 -9.20% (비중 2.7%)" in snapshot
    assert "매매규칙 11(24~30%↑ 익절 쿠션): 알파전자 +108.36% (비중 26.3%)" in snapshot
    assert "매매규칙 9(비중 30% 상한): 알파전자 26.3% ← 근접" in snapshot
    # 현금은 종목 수·룰 판정에서 제외
    assert "매매규칙 9(종목 수 5~12): 3종목 ← **미달**" in snapshot
    assert "현금 비중: 5.1% (₩5,000,000)" in snapshot
