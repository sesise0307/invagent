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


def test_market_signal_parser_with_embedded_payload(capsys: pytest.CaptureFixture[str]) -> None:
    script_path = SKILLS_ROOT / "summarize-telegram" / "scripts" / "fetch_market_signals.py"
    spec = importlib.util.spec_from_file_location("fetch_market_signals", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    payload = (
        r'\"initialMarketIndicesData\":'
        r'{\"short_term_signal\":\"green\",\"long_term_signal\":\"yellow\",'
        r'\"indices\":[]}'
    )
    data = module.extract_json(payload, "initialMarketIndicesData")

    assert data["short_term_signal"] == "green"
    module.print_signals(data)
    assert "green" in capsys.readouterr().out


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
