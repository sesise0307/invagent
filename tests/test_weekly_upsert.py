"""weekly-investment-review `weekly_upsert.py` — 노션 주말 블록 병합과 가드."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import load_script


def test_weekly_upsert_guard_rejects_a_lost_heading() -> None:
    """2026-09-20 사고 고정점 — 본문 헤딩이 하나라도 사라지면 병합 결과를 거부한다."""
    module = load_script("weekly-investment-review", "weekly_upsert")

    before = "## 19\\~20(주말)\n- 주간 투자 반성\n\n## 18(금)\n- 매매 없음\n"
    after = "## 19\\~20(주말)\n- 주간 투자 반성\n"

    with pytest.raises(ValueError) as excinfo:
        module.assert_no_heading_loss(before, after)

    assert "18(금)" in str(excinfo.value)



def test_weekly_upsert_guard_rejects_a_lost_toggle_heading() -> None:
    """2026-10-25 fetch 형식 — 요일 섹션이 `<toggle heading="h2">` 다음 줄 제목으로 온다.

    `## `만 세던 가드는 이 헤딩이 사라져도 통과시켰다.
    """
    module = load_script("weekly-investment-review", "weekly_upsert")

    before = (
        "## 24\\~25(주말)\n- 주간 투자 반성\n\n"
        "<toggle heading=\"h2\">\n23(금)\n\n- 매매 및 시황\n</toggle>\n"
    )
    after = "## 24\\~25(주말)\n- 주간 투자 반성\n"

    with pytest.raises(ValueError) as excinfo:
        module.assert_no_heading_loss(before, after)

    assert "23(금)" in str(excinfo.value)

_WEEKLY_BODY = (
    "- 상단 원칙 불릿\n"
    "\n"
    "## 19\\~20(주말)\n"
    "- 주간 투자 반성\n"
    "\t- 옛 내용 한 줄\n"
    "\t\t- 옛 하위 불릿\n"
    "\n"
    "## 18(금) {toggle=\"true\"}\n"
    "\t- 매매 및 시황\n"
    "\t\t- 매매 없음\n"
    "\n"
    "## 14(월) {toggle=\"true\"}\n"
    "\t- 매도: 알테오젠(80주 전량)\n"
)


def test_weekly_upsert_replaces_only_the_weekly_block() -> None:
    """주말 헤딩 아래 블록만 갈아끼우고, 다른 날짜 헤딩은 전부 남는다."""
    module = load_script("weekly-investment-review", "weekly_upsert")

    merged = module.upsert(_WEEKLY_BODY, "- 주간 투자 반성\n\t- 새 내용\n", "19~20")

    assert "새 내용" in merged
    assert "옛 내용 한 줄" not in merged
    assert "옛 하위 불릿" not in merged
    assert module.headings(merged) == ["19~20(주말)", '18(금) {toggle="true"}', '14(월) {toggle="true"}']
    assert "- 상단 원칙 불릿" in merged
    assert "매매 없음" in merged
    assert "매도: 알테오젠(80주 전량)" in merged


def test_weekly_upsert_inserts_when_no_prior_block_exists() -> None:
    """주말 헤딩은 있는데 반성 블록이 아직 없으면 헤딩 바로 뒤에 넣는다."""
    module = load_script("weekly-investment-review", "weekly_upsert")
    body = "## 26\\~27(주말)\n\n## 25(금)\n\t- 매매 없음\n"

    merged = module.upsert(body, "- 주간 투자 반성\n\t- 첫 기록\n", "26~27")

    lines = merged.split("\n")
    assert lines[0].startswith("## 26")
    assert lines[1] == "- 주간 투자 반성"
    assert lines[2] == "\t- 첫 기록"
    assert module.headings(merged) == ["26~27(주말)", "25(금)"]
    assert "매매 없음" in merged


def test_weekly_upsert_refuses_a_missing_weekend_heading() -> None:
    """헤딩이 없으면 조용히 맨 뒤에 붙이지 않고 거부한다."""
    module = load_script("weekly-investment-review", "weekly_upsert")

    with pytest.raises(ValueError) as excinfo:
        module.upsert("## 18(금)\n\t- 매매 없음\n", "- 주간 투자 반성\n", "19~20")

    assert "weekend heading not found" in str(excinfo.value)


def test_weekly_upsert_cli_writes_nothing_when_it_refuses(tmp_path: Path) -> None:
    """거부 시 exit 2이고 --prepared-out은 만들어지지 않는다(반쯤 쓴 본문 금지)."""
    module = load_script("weekly-investment-review", "weekly_upsert")
    existing = tmp_path / "body.md"
    existing.write_text("## 18(금)\n\t- 매매 없음\n", encoding="utf-8")
    proposed = tmp_path / "week.md"
    proposed.write_text("- 주간 투자 반성\n", encoding="utf-8")
    out = tmp_path / "merged.md"

    code = module.main([
        "--existing", str(existing),
        "--proposed", str(proposed),
        "--weekend", "19~20",
        "--prepared-out", str(out),
        "--json",
    ])

    assert code == 2
    assert not out.exists()


def test_weekly_upsert_cli_reports_the_merge(tmp_path: Path, capsys) -> None:
    """성공 시 exit 0, 병합 본문 기록, 헤딩 수를 보고한다."""
    module = load_script("weekly-investment-review", "weekly_upsert")
    existing = tmp_path / "body.md"
    existing.write_text(_WEEKLY_BODY, encoding="utf-8")
    proposed = tmp_path / "week.md"
    proposed.write_text("- 주간 투자 반성\n\t- 새 내용\n", encoding="utf-8")
    out = tmp_path / "merged.md"

    code = module.main([
        "--existing", str(existing),
        "--proposed", str(proposed),
        "--weekend", "19~20",
        "--prepared-out", str(out),
        "--json",
    ])

    assert code == 0
    assert "새 내용" in out.read_text(encoding="utf-8")
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["replaced"] is True
    assert payload["headings"] == 3


def test_weekly_upsert_finds_a_toggled_weekend_heading() -> None:
    """사용자가 주말 섹션을 토글로 바꿔도 주말 헤딩을 찾아 반성 블록만 갈아끼운다."""
    module = load_script("weekly-investment-review", "weekly_upsert")
    body = (
        "<toggle heading=\"h2\">\n"
        "24\\~25(주말)\n"
        "\n"
        "- 주간 투자 반성\n"
        "\t- 옛 내용\n"
        "</toggle>\n"
        "\n"
        "<toggle heading=\"h2\">\n"
        "23(금)\n"
        "\n"
        "- 매매 및 시황\n"
        "</toggle>\n"
    )

    merged = module.upsert(body, "- 주간 투자 반성\n\t- 새 내용\n", "24~25")

    assert "새 내용" in merged
    assert "옛 내용" not in merged
    assert merged.index("새 내용") < merged.index("23(금)")
    assert module.headings(merged) == ["24~25(주말)", "23(금)"]


def test_weekly_upsert_replaces_a_block_that_follows_other_weekend_bullets() -> None:
    """2026-10-25 — 반성 불릿 위에 「포트폴리오 고민」이 있으면 옛 블록을 남긴 채 하나 더 넣었다."""
    module = load_script("weekly-investment-review", "weekly_upsert")
    body = (
        "## 24\\~25(주말)\n"
        "\n"
        "- 포트폴리오 고민\n"
        "\t- 메모 한 줄\n"
        "- 주간 투자 반성\n"
        "\t- 옛 내용\n"
        "\n"
        "<toggle heading=\"h2\">\n"
        "23(금)\n"
        "</toggle>\n"
    )

    merged = module.upsert(body, "- 주간 투자 반성\n\t- 새 내용\n", "24~25")

    assert merged.count("- 주간 투자 반성") == 1
    assert "옛 내용" not in merged
    assert merged.index("- 포트폴리오 고민") < merged.index("메모 한 줄") < merged.index("새 내용")


def test_weekly_upsert_cli_counts_both_heading_forms(tmp_path: Path, capsys) -> None:
    """보고하는 헤딩 수 = SKILL.md 검증 명령(`^(## |<toggle heading)`)이 백업본에서 세는 수."""
    module = load_script("weekly-investment-review", "weekly_upsert")
    existing = tmp_path / "body.md"
    existing.write_text(
        "## 24\\~25(주말)\n- 주간 투자 반성\n\n"
        "<toggle heading=\"h2\">\n23(금)\n</toggle>\n\n"
        "<toggle heading=\"h2\">\n22(목)\n</toggle>\n",
        encoding="utf-8",
    )
    proposed = tmp_path / "week.md"
    proposed.write_text("- 주간 투자 반성\n\t- 새 내용\n", encoding="utf-8")

    code = module.main([
        "--existing", str(existing),
        "--proposed", str(proposed),
        "--weekend", "24~25",
        "--prepared-out", str(tmp_path / "merged.md"),
        "--json",
    ])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["headings"] == 3
