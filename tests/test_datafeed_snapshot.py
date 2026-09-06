"""`invagent.datafeed.snapshot` — 포트폴리오 스냅샷 표 읽기."""

import pytest

from invagent.datafeed import snapshot

SNAPSHOT = """# 포트폴리오 2026-09-04

- 잔고 ₩512,340,000 · 총손익 ₩12,000

## 보유

| 종목 | 티커 | 섹터 | 현재가 | 평단 | 수익률 |
| --- | --- | --- | ---: | ---: | ---: |
| SK하이닉스 | 000660 | 반도체 | ₩1,647,000 | ₩697,000 | 136.09% |
| 리브스메디 | - | 의료기기 | ₩34,500 | ₩34,540 | -0.12% |
| 예수금 |  | 현금 | ₩0 | ₩0 | - |

## 섹터
"""


def test_split_row_keeps_the_cells_in_order() -> None:
    assert snapshot.split_row("| a | b | c |") == ["a", "b", "c"]


def test_split_row_can_undo_the_markdown_escapes() -> None:
    """쓰는 쪽은 이스케이프하고 읽는 쪽은 하지 않는다 — 방향을 인자로 고른다."""
    assert snapshot.split_row(r"| a\-b | c |") == [r"a\-b", "c"]
    assert snapshot.split_row(r"| a\-b | c |", unescape=True) == ["a-b", "c"]


def test_parse_holdings_drops_the_cash_row() -> None:
    rows = snapshot.parse_holdings(SNAPSHOT)

    assert [r["종목"] for r in rows] == ["SK하이닉스", "리브스메디"]
    assert rows[0]["티커"] == "000660"


def test_parse_holdings_names_the_missing_section() -> None:
    with pytest.raises(ValueError, match="## 보유"):
        snapshot.parse_holdings("# 아무것도 없음\n")


def test_parse_balance_reads_the_header_line() -> None:
    assert snapshot.parse_balance(SNAPSHOT) == 512_340_000.0
    assert snapshot.parse_balance("잔고 없음") is None


def test_to_float_strips_currency_and_percent_marks() -> None:
    assert snapshot.to_float("₩1,466,000") == 1_466_000.0
    assert snapshot.to_float("-2.85%") == -2.85


def test_to_float_rejects_the_sheet_sentinels() -> None:
    """빈 칸·'-'·시트 오류값은 0이 아니라 값 없음이다."""
    assert snapshot.to_float("-") is None
    assert snapshot.to_float("#DIV/0!") is None
    assert snapshot.to_float("") is None


def test_parse_holdings_accepts_the_alternate_name_header() -> None:
    """스냅샷 세대에 따라 헤더가 `종목` 또는 `종목명`이다."""
    text = "## 보유\n\n| 종목명 | 섹터 |\n| --- | --- |\n| 알테오젠 | 바이오 |\n"

    assert [r["종목명"] for r in snapshot.parse_holdings(text)] == ["알테오젠"]


def test_parse_holdings_can_tolerate_an_empty_table() -> None:
    """비교용으로 읽을 때는 보유가 0건인 스냅샷도 정상 입력이다."""
    text = "## 보유\n\n| 종목 | 섹터 |\n| --- | --- |\n"

    assert snapshot.parse_holdings(text, require_rows=False) == []
    with pytest.raises(ValueError, match="하나도"):
        snapshot.parse_holdings(text)
