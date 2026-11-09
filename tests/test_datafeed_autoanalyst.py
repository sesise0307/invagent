"""`invagent.datafeed.autoanalyst` — autoanalyst 증권사 리포트 목록."""

import json
import urllib.error
import urllib.parse

from invagent.datafeed import autoanalyst, http


def _row(**overrides) -> dict:
    row = {
        "id": 1,
        "report_date": "2026-11-05",
        "brokerage": "KB증권",
        "title": "2027년 Top Pick",
        "company_code": "278470",
        "company_name": "에이피알",
        "summerized_contents": "매출 4조 전망함. /// 밸류에이션 매력 높음.",
        "manual_target_price": 450000,
        "manual_price_change_type": "신규",
        "opinion_extracted": "BUY",
    }
    row.update(overrides)
    return row


def _page(rows: list[dict], total: int) -> bytes:
    return json.dumps({"message": "Success", "total_count": total, "count": len(rows), "data": rows}).encode()


def test_a_report_comes_back_in_the_stockeasy_row_shape(monkeypatch) -> None:
    monkeypatch.setattr(http, "read_url", lambda url, headers, timeout: _page([_row()], 1))

    rows, err = autoanalyst.fetch_reports("278470")

    assert err is None
    assert rows == [
        {
            "report_date": "2026-11-05",
            "securities_company": "KB증권",
            "title": "2027년 Top Pick",
            "investment_opinion": "BUY",
            "target_price": 450000,
            "target_price_change": "신규",
            "summary_points": ["매출 4조 전망함.", "밸류에이션 매력 높음."],
            "source": "autoanalyst",
        }
    ]


def test_every_page_is_read_until_the_total_is_reached(monkeypatch) -> None:
    pages = {
        "1": _page([_row(id=i) for i in range(100)], 150),
        "2": _page([_row(id=100 + i) for i in range(50)], 150),
    }
    seen = []

    def fake_read(url, headers, timeout):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        seen.append(query)
        return pages[query["page"][0]]

    monkeypatch.setattr(http, "read_url", fake_read)

    rows, err = autoanalyst.fetch_reports("000660")

    assert err is None and len(rows) == 150
    assert [q["page"] for q in seen] == [["1"], ["2"]]
    assert all(q["company_code"] == ["000660"] for q in seen)


def test_a_missing_target_or_summary_stays_empty(monkeypatch) -> None:
    monkeypatch.setattr(
        http,
        "read_url",
        lambda url, headers, timeout: _page(
            [_row(manual_target_price=None, summerized_contents="", opinion_extracted=None)], 1
        ),
    )

    rows, _ = autoanalyst.fetch_reports("347850")

    assert rows[0]["target_price"] is None
    assert rows[0]["summary_points"] == []
    assert rows[0]["investment_opinion"] is None


def test_a_failed_request_returns_the_reason(monkeypatch) -> None:
    def fail(url, headers, timeout):
        raise urllib.error.HTTPError(url, 500, "err", {}, None)

    monkeypatch.setattr(http, "read_url", fail)

    rows, err = autoanalyst.fetch_reports("278470")

    assert rows is None
    assert err == "HTTP 500"
