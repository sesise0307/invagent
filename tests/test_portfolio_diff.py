"""`daily-digest/scripts/portfolio_diff.py` — 스냅샷 간 보유 수량 변화."""

import json
from pathlib import Path

import pytest

from tests.conftest import load_script


def test_an_alphanumeric_krx_code_identifies_the_position_by_ticker() -> None:
    """신형 KRX 코드(에임드바이오 `0009K0`)도 이름이 아니라 티커로 묶는다."""
    module = load_script("daily-digest", "portfolio_diff")

    assert module.identity({"ticker": "0009K0", "name": "에임드바이오"}) == "ticker:0009K0"


def _row(name: str, ticker: str, account: str, quantity: float, cost: float | None = 700_000.0) -> dict:
    return {
        "name": name,
        "ticker": ticker,
        "account": account,
        "quantity": quantity,
        "average_cost": 70_000.0,
        "cost": cost,
    }


def test_a_ticker_column_appearing_between_snapshots_is_not_an_exit_and_entry() -> None:
    """티커 열이 없던 스냅샷과 있는 스냅샷을 비교해도 같은 종목·같은 수량이면 변화가 없다."""
    module = load_script("daily-digest", "portfolio_diff")

    result = module.diff_portfolios(
        [_row("삼성전자", "", "A", 10)],
        [_row("삼성전자", "005930", "A", 10)],
    )

    assert result["changes"] == []


def test_a_purchase_cost_change_alone_is_not_an_allocation_change() -> None:
    """계좌별 수량이 같으면 매수금액 재계산·반올림 차이는 변화가 아니다."""
    module = load_script("daily-digest", "portfolio_diff")

    result = module.diff_portfolios(
        [_row("삼성전자", "005930", "A", 10, cost=700_000.0)],
        [_row("삼성전자", "005930", "A", 10, cost=700_004.0)],
    )

    assert result["changes"] == []


def test_moving_shares_between_accounts_still_reports_allocation_with_cost() -> None:
    """총수량이 같아도 계좌별 수량이 바뀌면 배분 변화로 남기고 매수금액도 함께 싣는다."""
    module = load_script("daily-digest", "portfolio_diff")

    result = module.diff_portfolios(
        [_row("삼성전자", "005930", "A", 10, cost=700_000.0)],
        [_row("삼성전자", "005930", "B", 10, cost=700_000.0)],
    )

    [change] = result["changes"]
    assert change["kind"] == "account_allocation_change"
    assert change["before_accounts"] == {"A": {"quantity": 10.0, "cost": 700_000.0}}
    assert change["after_accounts"] == {"B": {"quantity": 10.0, "cost": 700_000.0}}


def test_same_ticker_rows_aggregate_across_accounts_and_names() -> None:
    """같은 티커는 계좌·표기 이름이 달라도 한 포지션으로 합산한다."""
    module = load_script("daily-digest", "portfolio_diff")

    result = module.diff_portfolios(
        [_row("삼성전자", "005930", "A", 10)],
        [_row("삼성전자", "005930", "A", 10), _row("삼성전자보통주", "005930", "B", 5)],
    )

    [change] = result["changes"]
    assert (change["kind"], change["key"]) == ("quantity_change", "ticker:005930")
    assert change["names"] == ["삼성전자", "삼성전자보통주"]
    assert change["quantity_delta"] == 5.0


HEADER = (
    "| 계좌 | 종목 | 섹터 | 보유 | 평단 | 현재가 | 매수금액 | 수익률 | 비중 | 평가금액 | 투자 아이디어 |\n"
    "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |\n"
)
CASH = "| 은행 | _현금 | 현금 | 1 | ₩100 | ₩100 | ₩100 | 0.00% | 50.0% | ₩100 |  |\n"


def _snapshot(*rows: str) -> str:
    return "# 포트폴리오 스냅샷 — 2026-10-23\n\n## 보유 (n종목 + 현금)\n\n" + HEADER + CASH + "".join(rows)


def _holding(account: str, name: str, quantity: str, cost: str = "₩15,000,000") -> str:
    return f"| {account} | {name} | AI | {quantity} | ₩100,000 | ₩137,500 | {cost} | 37.50% | 4.2% | ₩20,625,000 |  |\n"


def test_parse_snapshot_reads_the_holdings_table_without_cash() -> None:
    module = load_script("daily-digest", "portfolio_diff")

    rows = module.parse_snapshot(_snapshot(_holding("삼성", "대덕전자", "1,150")))

    assert rows == [
        {
            "name": "대덕전자",
            "ticker": "",
            "account": "삼성",
            "quantity": 1150.0,
            "average_cost": 100_000.0,
            "cost": 15_000_000.0,
        }
    ]


def test_parse_snapshot_rejects_an_unparseable_quantity() -> None:
    module = load_script("daily-digest", "portfolio_diff")

    with pytest.raises(ValueError, match="대덕전자"):
        module.parse_snapshot(_snapshot(_holding("삼성", "대덕전자", "많음")))


def _events_file(tmp_path, payload) -> Path:
    path = tmp_path / "events.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "payload",
    [
        {"events": [{"type": "dividend", "tag": "배당"}]},
        {"events": [{"type": "trade"}]},
        {"events": {"type": "trade", "tag": "매수"}},
        ["not-a-dict"],
    ],
)
def test_load_events_rejects_bad_type_missing_tag_or_non_list(tmp_path, payload) -> None:
    module = load_script("daily-digest", "portfolio_diff")

    with pytest.raises(ValueError):
        module.load_events(_events_file(tmp_path, payload))


def test_load_events_accepts_a_bare_list_and_none(tmp_path) -> None:
    module = load_script("daily-digest", "portfolio_diff")
    event = {"type": "cash_flow", "tag": "입금"}

    assert module.load_events(None) == []
    assert module.load_events(_events_file(tmp_path, [event])) == [event]


def test_a_change_matched_by_a_user_event_is_tagged_and_others_stay_unclassified() -> None:
    """사용자 이벤트(티커 또는 이름)에 걸린 변화만 user_tagged, 나머지는 unclassified."""
    module = load_script("daily-digest", "portfolio_diff")
    event = {"type": "trade", "tag": "추가 매수", "name": "대덕전자"}

    result = module.diff_portfolios(
        [_row("대덕전자", "", "A", 10), _row("삼성전자", "005930", "A", 10)],
        [_row("대덕전자", "", "A", 20), _row("삼성전자", "005930", "A", 5)],
        [event],
    )

    by_key = {change["key"]: change for change in result["changes"]}
    assert by_key["name:대덕전자"]["classification"] == "user_tagged"
    assert by_key["name:대덕전자"]["events"] == [event]
    assert by_key["ticker:005930"]["classification"] == "unclassified"
    assert by_key["ticker:005930"]["events"] == []
    assert result["event_count"] == 1


def _snapshots(tmp_path, before: str, after: str) -> tuple[Path, Path]:
    b, a = tmp_path / "before.md", tmp_path / "after.md"
    b.write_text(before, encoding="utf-8")
    a.write_text(after, encoding="utf-8")
    return b, a


def test_main_prints_one_text_line_per_change(tmp_path, capsys) -> None:
    module = load_script("daily-digest", "portfolio_diff")
    before, after = _snapshots(
        tmp_path, _snapshot(_holding("삼성", "대덕전자", "150")), _snapshot(_holding("삼성", "대덕전자", "100"))
    )

    assert module.main([str(before), str(after)]) == 0
    assert capsys.readouterr().out.strip() == "quantity_change: 대덕전자 150 → 100 (unclassified)"


def test_main_prints_no_change_when_holdings_match(tmp_path, capsys) -> None:
    module = load_script("daily-digest", "portfolio_diff")
    text = _snapshot(_holding("삼성", "대덕전자", "150"))
    before, after = _snapshots(tmp_path, text, text)

    assert module.main([str(before), str(after)]) == 0
    assert capsys.readouterr().out.strip() == "변화 없음"


def test_main_json_carries_changes_events_and_run_manifest(tmp_path, capsys) -> None:
    module = load_script("daily-digest", "portfolio_diff")
    before, after = _snapshots(
        tmp_path, _snapshot(), _snapshot(_holding("삼성", "대덕전자", "150"))
    )
    events = _events_file(tmp_path, {"events": [{"type": "trade", "tag": "신규", "name": "대덕전자"}]})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"run_id": "r1", "asof": "2026-10-23", "extra": 1}), encoding="utf-8")

    code = module.main([str(before), str(after), "--events", str(events),
                        "--run-manifest", str(manifest), "--json"])

    assert code == 0
    result = json.loads(capsys.readouterr().out)
    [change] = result["changes"]
    assert (change["kind"], change["classification"]) == ("entry", "user_tagged")
    assert result["run"] == {"run_id": "r1", "asof": "2026-10-23"}
    assert (result["before"], result["after"]) == (str(before), str(after))


def test_main_returns_1_on_a_bad_snapshot_or_event_file(tmp_path, capsys) -> None:
    module = load_script("daily-digest", "portfolio_diff")
    good = _snapshot(_holding("삼성", "대덕전자", "150"))
    before, after = _snapshots(tmp_path, good, "# 보유 섹션 없음\n")

    assert module.main([str(before), str(after)]) == 1
    assert "ERROR" in capsys.readouterr().err

    _, ok_after = _snapshots(tmp_path, good, good)
    bad_events = _events_file(tmp_path, {"events": [{"type": "trade"}]})
    assert module.main([str(before), str(ok_after), "--events", str(bad_events)]) == 1
    assert module.main([str(tmp_path / "missing.md"), str(ok_after)]) == 1
