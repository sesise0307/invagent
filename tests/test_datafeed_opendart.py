"""`invagent.datafeed.opendart` — OpenDART 공시검색(`list.json`)."""

import json
import urllib.parse
from datetime import date

from invagent.datafeed import http, opendart


def filing(rcept_no, corp_name="대덕전자", stock_code="353200", report_nm="단일판매ㆍ공급계약체결"):
    return {
        "corp_code": "01379347",
        "corp_name": corp_name,
        "stock_code": stock_code,
        "corp_cls": "Y",
        "report_nm": report_nm,
        "rcept_no": rcept_no,
        "flr_nm": corp_name,
        "rcept_dt": rcept_no[:8],
        "rm": "유",
    }


def page(items, page_no, total_page, status="000") -> bytes:
    return json.dumps(
        {
            "status": status,
            "message": "정상",
            "page_no": page_no,
            "page_count": 100,
            "total_count": len(items),
            "total_page": total_page,
            "list": items,
        }
    ).encode()


def test_every_page_of_the_window_is_read(monkeypatch) -> None:
    monkeypatch.setattr(opendart, "load_key", lambda: "k" * 40)
    pages = {
        "1": page([filing("20261026000001")], 1, 2),
        "2": page([filing("20261023000002", corp_name="하나금융지주", stock_code="086790")], 2, 2),
    }
    seen = []

    def read_url(url, headers, timeout):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        seen.append((query["bgn_de"][0], query["end_de"][0], query["page_count"][0]))
        return pages[query["page_no"][0]]

    monkeypatch.setattr(http, "read_url", read_url)

    filings, err = opendart.fetch_filings(date(2026, 10, 23), date(2026, 10, 26))

    assert err is None
    assert [f["rcept_no"] for f in filings] == ["20261026000001", "20261023000002"]
    assert seen == [("20261023", "20261026", "100")] * 2


def test_no_filing_in_the_window_is_an_empty_list(monkeypatch) -> None:
    """DART는 기간 안 공시가 없으면 status 013(「조회된 데이터가 없습니다」)을 준다 — 실패가 아니다."""
    monkeypatch.setattr(opendart, "load_key", lambda: "k" * 40)
    body = json.dumps({"status": "013", "message": "조회된 데이타가 없습니다."}).encode()
    monkeypatch.setattr(http, "read_url", lambda url, headers, timeout: body)

    assert opendart.fetch_filings(date(2026, 10, 24), date(2026, 10, 25)) == ([], None)


def test_an_api_error_status_is_a_reason_not_an_empty_list(monkeypatch) -> None:
    """키 오류·한도 초과도 HTTP 200으로 온다. 빈 목록으로 읽으면 「공시 없음」으로 오판한다."""
    monkeypatch.setattr(opendart, "load_key", lambda: "k" * 40)
    body = json.dumps({"status": "020", "message": "요청 제한을 초과하였습니다."}).encode()
    monkeypatch.setattr(http, "read_url", lambda url, headers, timeout: body)

    filings, err = opendart.fetch_filings(date(2026, 10, 23), date(2026, 10, 26))

    assert filings is None
    assert "020" in err and "요청 제한" in err


def test_without_a_key_nothing_is_requested(monkeypatch) -> None:
    monkeypatch.setattr(opendart, "load_key", lambda: None)

    def read_url(url, headers, timeout):
        raise AssertionError("키 없이 요청했다")

    monkeypatch.setattr(http, "read_url", read_url)

    filings, err = opendart.fetch_filings(date(2026, 10, 23), date(2026, 10, 26))

    assert filings is None
    assert "OPENDART_API_KEY" in err
