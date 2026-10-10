"""analyze-stock `fetch_stock_info.py` — 시세·컨센·뉴스·공시 요약."""

from __future__ import annotations

import urllib.error
from pathlib import Path

import pytest

from invagent.datafeed import http as datafeed_http
from invagent.datafeed import stockeasy as datafeed_stockeasy
from tests.conftest import load_script

STOCK_INFO_PAYLOAD = {
    "stock_code": "064290",
    "primary_fs_type": "C",
    "stock_info": {
        "name": "인텍플러스",
        "market": "KOSDAQ",
        # 가격 필드는 등락 방향이 부호로 박혀 온다 (음수 가격이 아니다)
        "cur_prc": "-30850",
        "pred_pre": "-1250",
        "flu_rt": "-3.89",
        "mac": "3997",
        "trde_qty": "169623",
        "per": "",
        "pbr": "7.76",
        "eps": "-257",
        "bps": "3982",
        "roe": "-7.2",
        "250hgst": "+54100",
        "250lwst": "-8540",
        "250hgst_pric_dt": "20260706",
        "250lwst_pric_dt": "20250806",
        "250hgst_pric_pre_rt": "-42.88",
        "250lwst_pric_pre_rt": "+261.83",
        "for_exh_rt": "+4.68",
        "dstr_rt": "81.6",
        "total_shares": 12958000,
    },
    "sector_info": {"major_name": "반도체", "mid_name": "반도체장비"},
    "rs_data": {"rs": 91.25, "rs_1m": 1.78, "rs_3m": 88.85, "rs_6m": 92.05},
    "investment": {
        "base_period": "2026.1Q",
        "financial_type": "C",
        "overall_grade": "B",
        "growth": {
            "category_name": "성장성",
            "average_grade": "A",
            "metrics": [{"name": "매출성장률", "formatted_value": "-11.1%", "grade": "D"}],
        },
    },
    "target_price_history": [
        {
            "report_date": "2026-07-03",
            "securities_company": "메리츠증권",
            "author": "김동관",
            "investment_opinion": "Buy",
            "target_price": 75000,
            "target_price_change": "상향",
            "current_price": 45800,
            "upside_potential": 63.76,
            "title": "넘치는 수주와 폭발적 실적 성장",
        },
        {
            "report_date": "2026-04-22",
            "securities_company": "메리츠증권",
            "author": "김동관",
            "investment_opinion": "Buy",
            "target_price": 41000,
            "target_price_change": "신규",
            "current_price": 31450,
            "upside_potential": 30.4,
            "title": "4년만의 턴어라운드",
        },
        {
            "report_date": "2026-05-28",
            "securities_company": "NH투자증권",
            "author": "심의섭",
            "investment_opinion": "Not Rated",
            "target_price": None,
            "target_price_change": None,
            "current_price": 40650,
            "upside_potential": None,
            "title": "호황기에 더욱 돋보이는 경쟁력",
        },
    ],
    "financials": {
        "consolidated": [
            {
                "year": 2026,
                "quarter": 2,
                "month": 6,
                "revenue": 8960989867,
                "operating_income": -5117444745,
                "net_income": -6094354375,
                "eps": -470.32,
                "operating_margin": -57.11,
                "per": None,
                "pbr": None,
            }
        ],
        "consolidatedEstimate": [
            {
                "year": 2026,
                "quarter": 4,
                "month": 12,
                "revenue": 49267000000,
                "operating_income": 8533000000,
                "net_income": 8400000000,
                "eps": 578.81,
                "operating_margin": 17.32,
                "per": None,
                "pbr": 7.66,
            }
        ],
        "consolidatedYearly": [],
        "consolidatedYearlyEstimate": [],
    },
    "eps_changes": {
        "count": 10,
        "changes": [
            {
                "quarter": "2026Q4",
                "change_date_str": "2026-08-12",
                "value_old": 443.43,
                "value_new": 427.55,
                "change_rate": -3.58,
            }
        ],
    },
}

NEWS_PAYLOAD = {
    "items": [
        {
            "title": "TSMC, 첨단 패키징 외주 확대",
            "link": "https://n.news.naver.com/mnews/article/277/0005803223",
            "published_at": "2026-08-14T02:20:00Z",
            "source": "아시아경제",
        },
        {
            "title": "지난달 기사",
            "link": "https://example.com/old",
            "published_at": "2026-07-01T00:00:00Z",
            "source": "예시",
        },
    ]
}


REPORTS_PAYLOAD = {
    "total_count": 35,
    "items": [
        {
            "id": 1,
            "report_date": "2026-07-03",
            "securities_company": "메리츠증권",
            "author": "김동관",
            "investment_opinion": "Buy",
            "target_price": 75000,
            "target_price_change": "상향",
            "title": "넘치는 수주와 폭발적 실적 성장",
            # 실제 응답 모양: point 키를 가진 dict 리스트
            "summary_points": [
                {"point": "CoWoS 검사 장비 본계약 임박"},
                {"point": "생산능력 50% 확대 <b>계획</b>"},
            ],
            # 실제 응답 모양: 문자열이 아니라 섹션 dict
            "detail_content": {
                "card_news_sections": [
                    {"header": "3Q26 전망", "content": "수주잔고 인식 본격화"},
                ]
            },
            "file_name": "20260703_인텍플러스_064290_기계·장비_기업리포트_Meritz.pdf",
        }
    ],
}


def _patch_stock_info_fetch(
    module, monkeypatch: pytest.MonkeyPatch, search=None, cookie="session=abc", reports_error=None
) -> None:
    """네트워크를 수집 계층 한 곳에서 막는다.

    `resolve_stock`도 같은 모듈 안에서 `fetch_stock_json`을 부르므로, 스크립트 쪽 별칭이
    아니라 수집 계층을 패치해야 종목 해석까지 함께 덮인다.
    """
    calls = {}

    def fake_fetch(path, params=None, referer=module.PAGE_BASE, cookie=None):
        calls[path] = {"params": params, "cookie": cookie}
        if path == module.ENDPOINTS["search"]:
            return (search if search is not None else []), None
        if path.startswith("/stock-info/info-tab/"):
            return STOCK_INFO_PAYLOAD, None
        if path.startswith("/news/by-stock-code/"):
            return NEWS_PAYLOAD, None
        if path == module.ENDPOINTS["reports"]:
            if reports_error:
                return None, reports_error
            return REPORTS_PAYLOAD, None
        return None, "HTTP 404"

    monkeypatch.setattr(datafeed_stockeasy, "fetch_stock_json", fake_fetch)
    monkeypatch.setattr(datafeed_stockeasy, "load_cookie", lambda: cookie)
    return calls


def test_stock_info_serves_a_repeat_call_from_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """한 번의 analyze-stock 실행이 info-tab(약 128KB)을 두 번 받던 경로를 없앤다."""

    calls: list[str] = []

    def fake_read_url(url, headers, timeout):
        calls.append(url)
        return b'{"stock_code": "064290"}'

    monkeypatch.setattr(datafeed_http, "read_url", fake_read_url)

    first, err1 = datafeed_stockeasy.fetch_stock_json("/stock-info/info-tab/064290")
    second, err2 = datafeed_stockeasy.fetch_stock_json("/stock-info/info-tab/064290")

    assert (err1, err2) == (None, None)
    assert first == second == {"stock_code": "064290"}
    assert len(calls) == 1, "두 번째 호출이 네트워크를 다시 탔다"


def test_stock_info_never_caches_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """일시적인 401·타임아웃이 TTL 동안 고착되면 쿠키를 고쳐도 계속 실패로 보인다."""

    state = {"fail": True}

    def flaky_read_url(url, headers, timeout):
        if state["fail"]:
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
        return b'{"ok": 1}'

    monkeypatch.setattr(datafeed_http, "read_url", flaky_read_url)

    assert datafeed_stockeasy.fetch_stock_json("/stock-info/info-tab/064290") == (None, "HTTP 401")
    state["fail"] = False
    payload, err = datafeed_stockeasy.fetch_stock_json("/stock-info/info-tab/064290")
    assert (payload, err) == ({"ok": 1}, None)


def test_stock_info_endpoints_point_at_stockdata_api() -> None:
    """종목 데이터는 /stockdata/api/v1 경로에서 받는다."""
    module = load_script("analyze-stock", "fetch_stock_info")

    assert module.API_BASE == "https://stockeasy.intellio.kr/stockdata/api/v1"
    assert set(module.ENDPOINTS) == {"search", "info_tab", "analysis_tab", "news", "reports"}
    assert module.COOKIE_ENV == "STOCKEASY_COOKIE"


def test_stock_info_sends_cookie_to_info_tab_and_news(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """2026-08 이후 info-tab·news도 로그인 세션을 요구한다 — 쿠키를 함께 보낸다."""
    module = load_script("analyze-stock", "fetch_stock_info")
    calls = _patch_stock_info_fetch(module, monkeypatch)

    assert module.main(["064290", "--news", "3"]) == 0
    capsys.readouterr()

    info_call = next(v for k, v in calls.items() if k.startswith("/stock-info/info-tab/"))
    news_call = next(v for k, v in calls.items() if k.startswith("/news/by-stock-code/"))
    assert info_call["cookie"] == "session=abc"
    assert news_call["cookie"] == "session=abc"


def test_stock_info_consensus_counts_latest_report_per_broker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """같은 증권사의 옛 목표가가 평균을 끌어올리면 안 된다 — 증권사별 최신 1건만 센다."""
    module = load_script("analyze-stock", "fetch_stock_info")
    _patch_stock_info_fetch(module, monkeypatch)

    assert module.main(["064290"]) == 0
    out = capsys.readouterr().out

    # 메리츠 2건(2026-07-03 75,000 / 2026-04-22 41,000) → 최신 1건만
    assert "평균 목표가 75,000원 (최고 75,000 / 최저 75,000)" in out
    assert "상향 1 / 하향 0" in out
    # 전체 이력 단순평균은 참고로 남기되 blend 입력이 아님을 명시한다
    assert "전체 이력 2건 단순평균은 58,000원" in out
    assert "9단계 blend 입력이 아니다" in out


def test_stock_info_info_tab_401_points_at_cookie(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """info-tab이 401이면 원인은 쿠키다 — 사유를 stderr에 특정해 준다."""
    module = load_script("analyze-stock", "fetch_stock_info")

    def fake_fetch(path, params=None, referer=module.PAGE_BASE, cookie=None):
        if path == module.ENDPOINTS["search"]:
            return [{"stock_code": "064290", "stock_name": "인텍플러스", "market": "KR"}], None
        return None, "HTTP 401"

    monkeypatch.setattr(datafeed_stockeasy, "fetch_stock_json", fake_fetch)
    monkeypatch.setattr(datafeed_stockeasy, "load_cookie", lambda: "session=stale")

    assert module.main(["064290"]) == 1
    err = capsys.readouterr().err
    assert "STOCKEASY_COOKIE 만료·무효" in err


def test_stock_info_price_fields_strip_direction_sign() -> None:
    """`cur_prc: "-30850"`의 부호는 등락 방향 마커다. 가격은 절대값으로 읽는다."""
    module = load_script("analyze-stock", "fetch_stock_info")

    assert module.unsigned("-30850") == 30850
    assert module.unsigned("+54100") == 54100
    assert module.unsigned("") is None
    # 등락률·손익 지표의 부호는 의미가 있으므로 유지한다
    assert module.signed("-3.89") == -3.89
    assert module.signed("+261.83") == 261.83


def test_stock_info_main_renders_quote_and_consensus(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = load_script("analyze-stock", "fetch_stock_info")
    _patch_stock_info_fetch(module, monkeypatch)

    assert module.main(["064290"]) == 0

    out = capsys.readouterr().out
    assert "인텍플러스(064290)" in out
    assert "[시세] 30,850원 (-3.89%" in out  # 음수 가격으로 새지 않는다
    assert "고 54,100원(2026-07-06" in out
    assert "저 8,540원(2025-08-06" in out
    assert "메리츠증권" in out and "75,000원" in out and "상향" in out
    # 목표가 미제시(Not Rated) 건은 빠지고, 같은 증권사는 최신 1건(2026-07-03 75,000)만 센다.
    assert "평균 목표가 75,000원" in out
    assert "커버 1사 / 목표가 제시 2건" in out
    assert "추정 — 컨센서스, DART 데이터 아님" in out
    assert "2026.2Q | 확정" in out and "2026.4Q | **추정 E**" in out
    assert "2026Q4 2026-08-12: 443.43 → 427.55 (-3.58%)" in out


def test_stock_info_since_filters_news_and_flags_new_reports(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """승계 작성 시 --since 이후 뉴스만 남기고 신규 리포트를 표시한다."""
    module = load_script("analyze-stock", "fetch_stock_info")
    _patch_stock_info_fetch(module, monkeypatch)

    assert module.main(["064290", "--since", "2026-08-01"]) == 0

    out = capsys.readouterr().out
    assert "[뉴스] 1건 (기준일 2026-08-01 이후)" in out
    assert "지난달 기사" not in out
    assert "🆕" not in out  # 리포트는 전부 기준일 이전


def test_stock_info_renders_report_summaries_with_cookie(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`securities-reports`는 로그인 쿠키를 붙여야 요약이 나온다."""
    module = load_script("analyze-stock", "fetch_stock_info")
    calls = _patch_stock_info_fetch(module, monkeypatch, cookie="session=abc")

    assert module.main(["064290", "--since", "2026-08-01"]) == 0

    out = capsys.readouterr().out
    assert "[리포트 요약] 1건 (총 35건)" in out
    assert "2026-07-03 [메리츠증권] 김동관 · Buy 목표가 75,000원(상향)" in out
    assert "· CoWoS 검사 장비 본계약 임박" in out
    assert "· 생산능력 50% 확대 계획" in out  # HTML 태그 제거
    assert "파일: 20260703_인텍플러스_064290" in out  # 로컬 PDF 중복 판정용
    assert "본문:" not in out  # --detail-chars 0이면 본문 생략
    # 쿠키는 세 엔드포인트 모두에 붙고, 리포트에는 date_from으로 기준일이 넘어간다
    reports_call = calls[module.ENDPOINTS["reports"]]
    assert reports_call["cookie"] == "session=abc"
    assert reports_call["params"]["date_from"] == "2026-08-01"
    assert calls["/stock-info/info-tab/064290"]["cookie"] == "session=abc"
    # 쿠키 값은 stdout으로 새지 않는다
    assert "session=abc" not in out


def test_stock_info_detail_content_sections_flatten(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`detail_content`는 문자열이 아니라 card_news_sections dict로 온다."""
    module = load_script("analyze-stock", "fetch_stock_info")
    _patch_stock_info_fetch(module, monkeypatch)

    assert module.main(["064290", "--detail-chars", "200"]) == 0
    assert "본문: 3Q26 전망: 수주잔고 인식 본격화" in capsys.readouterr().out


def test_stock_info_missing_cookie_only_skips_reports(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """쿠키가 없으면 리포트 섹션만 비고 나머지는 정상 출력된다 (비블로킹)."""
    module = load_script("analyze-stock", "fetch_stock_info")
    _patch_stock_info_fetch(module, monkeypatch, cookie=None)

    assert module.main(["064290"]) == 0

    captured = capsys.readouterr()
    assert "[리포트 요약] 미수집 — STOCKEASY_COOKIE 미설정" in captured.out
    assert "[시세] 30,850원" in captured.out
    assert "STOCKEASY_COOKIE 미설정" in captured.err


def test_stock_info_expired_cookie_hints_refresh(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = load_script("analyze-stock", "fetch_stock_info")
    _patch_stock_info_fetch(module, monkeypatch, reports_error="HTTP 401")

    assert module.main(["064290"]) == 0
    assert "쿠키 만료" in capsys.readouterr().out


def test_stock_info_cookie_read_from_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """저장소는 dotenv를 쓰지 않으므로 수집 계층이 .env를 직접 훑는다."""
    from invagent.datafeed import env

    module = load_script("analyze-stock", "fetch_stock_info")
    monkeypatch.delenv(module.COOKIE_ENV, raising=False)

    nested = tmp_path / "src" / "invagent" / "datafeed"
    nested.mkdir(parents=True)
    (tmp_path / ".env").write_text(
        "# comment\nTELEGRAM_API_ID=1\nSTOCKEASY_COOKIE='session=from-env-file'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env, "__file__", str(nested / "env.py"))

    assert datafeed_stockeasy.load_cookie() == "session=from-env-file"

    monkeypatch.setenv(module.COOKIE_ENV, "session=from-environ")
    assert datafeed_stockeasy.load_cookie() == "session=from-environ"


def test_stock_info_shows_after_hours_quotes_apart_from_the_regular_close(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """2026-09 개편된 info-tab은 KRX 시간외·NXT 체결가를 따로 싣는다.

    판정은 정규장 종가(`cur_prc`)로만 하므로 시간외가는 참고줄로 떼어 보여 주고, 차이는
    API의 `flu_rt`(전일 종가 대비)가 아니라 오늘 정규장가 대비로 다시 잰다.
    """
    module = load_script("analyze-stock", "fetch_stock_info")
    _patch_stock_info_fetch(module, monkeypatch)
    payload = {
        **STOCK_INFO_PAYLOAD,
        "after_hours_quote": {
            "dt": "20260929", "cur_prc": "-31467", "pred_pre": "-633", "flu_rt": "-1.97",
            "cntr_tm": "195959", "market": "krx", "session": "after",
        },
        "nxt_quote": {
            "dt": "20260929", "cur_prc": "-30233", "pred_pre": "-1867", "flu_rt": "-5.82",
            "cntr_tm": "195959", "session": "after",
        },
    }
    fake = datafeed_stockeasy.fetch_stock_json

    def with_after_hours(path, params=None, referer=module.PAGE_BASE, cookie=None):
        if path.startswith("/stock-info/info-tab/"):
            return payload, None
        return fake(path, params, referer=referer, cookie=cookie)

    monkeypatch.setattr(datafeed_stockeasy, "fetch_stock_json", with_after_hours)

    assert module.main(["064290"]) == 0
    out = capsys.readouterr().out

    assert "[시세] 30,850원 (-3.89%" in out  # 판정 가격은 그대로 정규장가
    line = next(l for l in out.splitlines() if l.startswith("[시간외·NXT]"))
    # 30,850 → 31,467은 +2.0%, 30,233은 -2.0% (API 등락률 -1.97/-5.82%는 전일 종가 기준)
    assert "KRX 시간외 31,467원(정규장가 대비 +2.0%)" in line
    assert "NXT 애프터마켓 30,233원(정규장가 대비 -2.0%)" in line
    assert "2026-09-29 19:59:59" in line
    assert "판정 미사용" in line


def test_stock_info_omits_the_after_hours_line_when_none_is_served(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = load_script("analyze-stock", "fetch_stock_info")
    _patch_stock_info_fetch(module, monkeypatch)

    assert module.main(["064290"]) == 0
    assert "[시간외·NXT]" not in capsys.readouterr().out


ANALYSIS_TAB_PAYLOAD = {
    "stock_code": "064290",
    "disclosures": [
        {
            "id": 406219,
            "rcept_no": "20260818800754",
            # 실제 응답은 제목 뒤에 공백을 채워 온다
            "report_nm": "최대주주등소유주식변동신고서              ",
            "rcept_dt": "2026-08-18",
            "corp_name": "인텍플러스",
            "disclosure_type": "common_table",
        },
        {
            "id": 406100,
            "rcept_no": "20260714000123",
            "report_nm": "반기보고서 (2026.06)",
            "rcept_dt": "2026-07-14",
            "corp_name": "인텍플러스",
            "disclosure_type": "interim_results",
        },
    ],
    "reports": [],
    "news": [],
    "target_price_history": [],
    "status": "success",
}


def test_stock_info_lists_disclosures_from_the_analysis_tab(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """2026-09 개편 페이지의 소식 탭(`analysis-tab`)이 종목별 DART 공시 목록을 싣는다."""
    module = load_script("analyze-stock", "fetch_stock_info")
    calls = _patch_stock_info_fetch(module, monkeypatch)
    fake = datafeed_stockeasy.fetch_stock_json

    def with_disclosures(path, params=None, referer=module.PAGE_BASE, cookie=None):
        if path == "/stock-info/analysis-tab/064290":
            calls[path] = {"params": params, "cookie": cookie}
            return ANALYSIS_TAB_PAYLOAD, None
        return fake(path, params, referer=referer, cookie=cookie)

    monkeypatch.setattr(datafeed_stockeasy, "fetch_stock_json", with_disclosures)

    assert module.main(["064290", "--since", "2026-08-01"]) == 0
    out = capsys.readouterr().out

    assert "[공시] 1건 (기준일 2026-08-01 이후) · API 최근 2건 중 필터" in out
    assert "  - 2026-08-18 최대주주등소유주식변동신고서 · rcept_no 20260818800754" in out
    assert "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260818800754" in out
    assert "반기보고서" not in out
    assert calls["/stock-info/analysis-tab/064290"]["cookie"] == "session=abc"


def test_stock_info_missing_disclosures_do_not_block(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = load_script("analyze-stock", "fetch_stock_info")
    _patch_stock_info_fetch(module, monkeypatch)  # analysis-tab → HTTP 404

    assert module.main(["064290"]) == 0
    out = capsys.readouterr().out
    assert "[공시] 미수집 — HTTP 404" in out
    assert "[시세] 30,850원" in out


def test_stock_info_ambiguous_name_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """후보가 여러 개면 스킬이 사용자에게 되묻도록 exit 2로 알린다."""
    module = load_script("analyze-stock", "fetch_stock_info")
    _patch_stock_info_fetch(
        module,
        monkeypatch,
        search=[
            {"market": "KR", "stock_code": "007280", "stock_name": "한국특강", "exchange": "KOSPI"},
            {"market": "KR", "stock_code": "161890", "stock_name": "한국콜마", "exchange": "KOSPI"},
        ],
    )

    assert module.main(["한국"]) == 2
    assert "후보 다수" in capsys.readouterr().err


def test_stock_info_unknown_name_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = load_script("analyze-stock", "fetch_stock_info")
    _patch_stock_info_fetch(module, monkeypatch, search=[])

    assert module.main(["없는종목명"]) == 1
    assert "검색 결과 없음" in capsys.readouterr().err


def test_stock_info_splits_a_preferred_share_between_two_tickers(monkeypatch):
    """우선주는 시세만 자기 코드, 실적·컨센·뉴스·리포트는 본주 코드로 받는다."""
    from invagent.datafeed import stockeasy

    module = load_script("analyze-stock", "fetch_stock_info")
    payloads = {
        "005935": {"stock_info": {"name": "삼성전자우", "cur_prc": "196900"}},
        "005930": {
            "stock_info": {"name": "삼성전자", "cur_prc": "200000"},
            "financials": {"quarterly": []},
            "target_price_history": [],
        },
    }
    paths: list[str] = []

    def fake_fetch(path, params=None, referer=None, cookie=None):
        paths.append(path)
        code = (params or {}).get("stock_code") or path.rstrip("/").split("/")[-1]
        return payloads.get(code, {"items": []}), None

    monkeypatch.setattr(stockeasy, "fetch_stock_json", fake_fetch)

    info, news, reports, own, analysis, errors = module.collect_stock_payloads(
        "005935", news_limit=5, summaries=0, since=None, cookie="x", disclosures=5
    )

    assert "/stock-info/info-tab/005930" in paths, "실적은 본주 코드로 받는다"
    assert "/stock-info/info-tab/005935" in paths, "시세는 우선주 코드로 따로 받는다"
    assert "/news/by-stock-code/005930" in paths, "뉴스는 본주 코드로 받는다"
    assert "/stock-info/analysis-tab/005930" in paths, "공시는 회사 것이라 본주 코드로 받는다"
    assert info["stock_info"]["name"] == "삼성전자", "info는 본주 페이로드다"
    assert own["stock_info"]["cur_prc"] == "196900", "own은 우선주 페이로드다"


def test_stock_info_summary_keeps_the_preferred_price_and_the_common_fundamentals():
    module = load_script("analyze-stock", "fetch_stock_info")
    summary = module.build_summary(
        {"stock_code": "005935", "stock_name": "삼성전자우"},
        {"stock_info": {"name": "삼성전자", "cur_prc": "200000"},
         "financials": {"quarterly": [1]}, "primary_fs_type": "C"},
        None,
        None,
        own_info={"stock_info": {"name": "삼성전자우", "cur_prc": "196900"}},
    )

    assert summary["stock_info"]["cur_prc"] == "196900", "시세는 우선주 것이다"
    assert summary["financials"] == {"quarterly": [1]}, "실적은 본주 것이다"
    assert summary["preferred_of"] == "005930"
    assert summary["preferred_discount_pct"] == pytest.approx(-1.55, abs=0.01)


def test_stock_info_summary_is_unchanged_for_a_common_share():
    module = load_script("analyze-stock", "fetch_stock_info")
    summary = module.build_summary(
        {"stock_code": "005930", "stock_name": "삼성전자"},
        {"stock_info": {"name": "삼성전자", "cur_prc": "200000"}},
        None,
        None,
    )

    assert summary["stock_info"]["cur_prc"] == "200000"
    assert summary["preferred_of"] is None
    assert summary["preferred_discount_pct"] is None


def test_stock_info_summary_keeps_after_hours_quotes_and_disclosures():
    """--json도 텍스트 출력과 같은 신규 필드를 싣는다 — 시간외가는 우선주면 자기 것이다."""
    module = load_script("analyze-stock", "fetch_stock_info")
    summary = module.build_summary(
        {"stock_code": "005935", "stock_name": "삼성전자우"},
        {"stock_info": {"cur_prc": "200000"}, "after_hours_quote": {"cur_prc": "+201000"}},
        None,
        None,
        own_info={
            "stock_info": {"cur_prc": "150000"},
            "after_hours_quote": {"cur_prc": "+151000"},
            "nxt_quote": {"cur_prc": "+150500", "session": "after"},
        },
        analysis=ANALYSIS_TAB_PAYLOAD,
    )

    assert summary["after_hours_quote"]["cur_prc"] == "+151000"
    assert summary["nxt_quote"]["cur_prc"] == "+150500"
    assert [d["rcept_no"] for d in summary["disclosures"]] == [
        "20260818800754",
        "20260714000123",
    ]
