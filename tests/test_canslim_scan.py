"""`analyze-stock/scripts/canslim_scan.py` — 오닐 CAN SLIM 7항목 참고 채점."""

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / ".agents" / "skills" / "analyze-stock" / "scripts" / "canslim_scan.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("canslim_scan", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


canslim = _load_module()


def _quarter(year: int, quarter: int, eps: float | None, value_type: str = "A") -> dict:
    return {"year": year, "quarter": quarter, "value_type": value_type, "eps": eps}


def _info(quarters=(), years=(), **extra) -> dict:
    info = {
        "primary_fs_type": "C",
        "financials": {"consolidated": list(quarters), "consolidatedYearly": list(years)},
    }
    info.update(extra)
    return info


def test_c_passes_when_latest_quarter_eps_is_up_25_percent_on_a_year_ago() -> None:
    info = _info(quarters=[_quarter(2025, 2, 1000), _quarter(2026, 1, 900), _quarter(2026, 2, 1250)])

    c = canslim.evaluate(info, None, [])["C"]

    assert c["mark"] == "pass"
    assert "2026.2Q" in c["detail"] and "+25.0%" in c["detail"]


def test_c_fails_just_under_25_percent() -> None:
    info = _info(quarters=[_quarter(2025, 2, 1000), _quarter(2026, 2, 1249)])

    assert canslim.evaluate(info, None, [])["C"]["mark"] == "fail"


def _year(year: int, eps: float | None) -> dict:
    return {"year": year, "month": 12, "value_type": "A", "eps": eps}


def test_a_passes_on_three_consecutive_annual_eps_increases() -> None:
    info = _info(years=[_year(2022, 100), _year(2023, 120), _year(2024, 150), _year(2025, 200)])

    a = canslim.evaluate(info, None, [])["A"]

    assert a["mark"] == "pass"
    assert "2022→2025" in a["detail"] and "+26.0%" in a["detail"]  # 3년 CAGR (200/100)^(1/3)-1


def test_a_fails_when_any_of_the_three_years_dipped() -> None:
    info = _info(years=[_year(2022, 100), _year(2023, 90), _year(2024, 150), _year(2025, 200)])

    assert canslim.evaluate(info, None, [])["A"]["mark"] == "fail"


def test_a_is_unknown_with_fewer_than_four_confirmed_years() -> None:
    info = _info(years=[_year(2023, 120), _year(2024, 150), _year(2025, 200)])

    assert canslim.evaluate(info, None, [])["A"]["mark"] == "unknown"


def _chart(closes, volumes=None) -> dict:
    """info-tab `chart` 모양. 날짜는 2025-01-01부터 하루씩."""
    from datetime import date, timedelta

    volumes = volumes or [1000] * len(closes)
    rows = [
        [(date(2025, 1, 1) + timedelta(days=i)).isoformat() + "T00:00:00+09:00", c, c, c, c, v]
        for i, (c, v) in enumerate(zip(closes, volumes))
    ]
    return {"schema": {"fields": ["timestamp", "open", "high", "low", "close", "volume"]}, "data": rows}


def test_n_passes_within_15_percent_of_the_52_week_closing_high() -> None:
    info = _info(chart=_chart([100] * 10 + [200] + [170] * 5))

    n = canslim.evaluate(info, None, [])["N"]

    assert n["mark"] == "pass"
    assert "-15.0%" in n["detail"] and "200" in n["detail"]


def test_n_fails_further_than_15_percent_below_the_high() -> None:
    info = _info(chart=_chart([200] + [169] * 5))

    assert canslim.evaluate(info, None, [])["N"]["mark"] == "fail"


def test_n_ignores_highs_older_than_250_sessions() -> None:
    info = _info(chart=_chart([500] + [100] * 250))

    assert canslim.evaluate(info, None, [])["N"]["mark"] == "pass"


def test_s_passes_when_up_day_volume_outweighs_down_day_volume_over_50_sessions() -> None:
    closes = [100, 101, 100] * 17          # 상승일 거래량 3000 vs 하락일 1000
    volumes = [1000, 3000, 1000] * 17
    info = _info(chart=_chart(closes, volumes), stock_info={"flo_stk": 10_000_000, "dstr_rt": "62.5"})

    s = canslim.evaluate(info, None, [])["S"]

    assert s["mark"] == "pass"
    assert "3.00" in s["detail"]
    assert "62.5%" in s["detail"]


def test_s_fails_when_down_day_volume_dominates() -> None:
    closes = [100, 101, 100] * 17
    volumes = [1000, 1000, 3000] * 17
    info = _info(chart=_chart(closes, volumes))

    assert canslim.evaluate(info, None, [])["S"]["mark"] == "fail"


def test_l_passes_at_rs_80_and_reports_the_rs_line_new_high() -> None:
    info = _info(
        rs_data={"rs": 80.0, "rs_1m": 72.5, "rs_3m": 19.9, "rs_6m": 91.0},
        rs_line_chart={"flags": {"is_52w_high": True, "is_ath": False}},
    )

    l = canslim.evaluate(info, None, [])["L"]

    assert l["mark"] == "pass"
    assert "RS 80" in l["detail"] and "3M 19.9" in l["detail"]
    assert "RS선 52주 신고가" in l["detail"]


def test_l_fails_below_80_and_is_unknown_without_rs_data() -> None:
    low = _info(rs_data={"rs": 79.9})

    assert canslim.evaluate(low, None, [])["L"]["mark"] == "fail"
    assert canslim.evaluate(_info(), None, [])["L"]["mark"] == "unknown"


def _trend(institution: list[int], foreign: int = 0) -> list[dict]:
    return [
        {"date": f"2026{i:04d}", "institution": q, "foreign": foreign, "individual": 0}
        for i, q in enumerate(institution, start=101)
    ]


def test_i_passes_on_net_institutional_buying_over_the_last_20_sessions() -> None:
    # 60일 전체로는 순매도지만 판정 창은 최근 20일이다.
    rows = _trend([-1000] * 40 + [100] * 20, foreign=-5)

    i = canslim.evaluate(_info(), None, rows)["I"]

    assert i["mark"] == "pass"
    assert "기관 20일 +2,000주" in i["detail"]
    assert "60일 -38,000주" in i["detail"]
    assert "외국인 20일 -100주" in i["detail"]


def test_i_fails_on_net_selling_and_is_unknown_without_data() -> None:
    assert canslim.evaluate(_info(), None, _trend([-1] * 20))["I"]["mark"] == "fail"
    assert canslim.evaluate(_info(), None, [])["I"]["mark"] == "unknown"


def _bp(status: str, distribution: int = 0) -> dict:
    return {
        "kospi": {"status": "confirmed_uptrend", "active_distribution_count": 0},
        "kosdaq": {"status": status, "active_distribution_count": distribution},
    }


def test_m_reads_the_big_picture_of_the_stocks_own_market() -> None:
    info = _info(stock_info={"market": "KOSDAQ"})

    m = canslim.evaluate(info, _bp("market_in_correction", 5), [])["M"]

    assert m["mark"] == "fail"
    assert "KOSDAQ 조정장" in m["detail"] and "분산일 5" in m["detail"]


def test_m_marks_an_uptrend_under_pressure_as_a_warning_not_a_pass() -> None:
    info = _info(stock_info={"market": "KOSDAQ"})

    assert canslim.evaluate(info, _bp("confirmed_uptrend"), [])["M"]["mark"] == "pass"
    assert canslim.evaluate(info, _bp("uptrend_under_pressure"), [])["M"]["mark"] == "warn"
    assert canslim.evaluate(info, _bp("rally_attempt"), [])["M"]["mark"] == "fail"
    assert canslim.evaluate(info, None, [])["M"]["mark"] == "unknown"


def test_score_counts_only_passes_and_lists_warnings_and_gaps_apart() -> None:
    info = _info(
        quarters=[_quarter(2025, 2, 100), _quarter(2026, 2, 200)],   # C pass
        rs_data={"rs": 90},                                         # L pass
        stock_info={"market": "KOSPI"},
    )
    big_picture = {"kospi": {"status": "uptrend_under_pressure"}}   # M warn

    result = canslim.evaluate(info, big_picture, _trend([-1] * 20))  # I fail

    assert result["score"] == {"passed": 2, "total": 7, "warn": ["M"], "unknown": ["A", "N", "S"]}


def test_preferred_share_takes_earnings_from_the_common_share_and_price_from_its_own() -> None:
    own = _info(chart=_chart([100] * 5), rs_data={"rs": 85})        # 우선주: 확정 실적 없음
    common = _info(quarters=[_quarter(2025, 2, 100), _quarter(2026, 2, 200)], rs_data={"rs": 10})

    result = canslim.evaluate(own, None, [], fundamentals=common)

    assert result["C"]["mark"] == "pass"
    assert result["L"]["mark"] == "pass"   # RS는 우선주 자기 값
    assert result["N"]["mark"] == "pass"


import json

import pytest

from invagent.datafeed import cache, http


@pytest.fixture
def fake_network(tmp_path, monkeypatch):
    """URL 경로로 응답을 고르는 가짜 `read_url`. 불린 URL을 기록한다."""
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)
    monkeypatch.setenv("STOCKEASY_COOKIE", "session=x")
    routes, seen = {}, []

    def read_url(url, headers, timeout):
        seen.append(url)
        for fragment, body in routes.items():
            if fragment in url:
                if isinstance(body, Exception):
                    raise body
                return json.dumps(body).encode()
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(http, "read_url", read_url)
    return routes, seen


def _naver_trend(institution: int) -> list[dict]:
    return [
        {"bizdate": f"202609{d:02d}", "organPureBuyQuant": f"{institution:+,}",
         "foreignerPureBuyQuant": "0", "individualPureBuyQuant": "0"}
        for d in range(1, 21)
    ]


def test_cli_prints_the_score_and_one_line_per_letter(fake_network, capsys) -> None:
    routes, _ = fake_network
    routes["info-tab/005930"] = _info(
        quarters=[_quarter(2025, 2, 100), _quarter(2026, 2, 200)],
        chart=_chart([100] * 5),
        rs_data={"rs": 91.7},
        stock_info={"name": "삼성전자", "market": "KOSPI"},
    )
    routes["/005930/trend"] = _naver_trend(10)
    routes["big-picture"] = {"kospi": {"status": "uptrend_under_pressure", "active_distribution_count": 3}}

    assert canslim.main(["005930"]) == 0

    out = capsys.readouterr().out
    assert "삼성전자(005930)" in out and "투자 판단 미반영" in out
    assert "점수 4/7" in out and "경계 M" in out and "미수집 A" in out
    assert "✅ C" in out and "⚠️ M" in out and "❓ A" in out


def test_cli_scores_a_preferred_share_on_both_axes(fake_network, capsys) -> None:
    routes, seen = fake_network
    routes["info-tab/005935"] = _info(chart=_chart([100] * 5), rs_data={"rs": 85},
                                      stock_info={"name": "삼성전자우", "market": "KOSPI"})
    routes["info-tab/005930"] = _info(quarters=[_quarter(2025, 2, 100), _quarter(2026, 2, 200)],
                                      stock_info={"name": "삼성전자"})
    routes["/005935/trend"] = _naver_trend(10)
    routes["big-picture"] = {}

    assert canslim.main(["005935"]) == 0

    out = capsys.readouterr().out
    assert "삼성전자우(005935)" in out
    assert "본주 삼성전자(005930)" in out
    assert "✅ C" in out and "✅ L" in out
    assert not any("/005930/trend" in url for url in seen)


def test_cli_keeps_going_when_info_tab_is_refused(fake_network, capsys) -> None:
    import urllib.error

    routes, _ = fake_network
    routes["info-tab/005930"] = urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
    routes["/005930/trend"] = _naver_trend(10)
    routes["big-picture"] = {"kospi": {"status": "confirmed_uptrend"}}

    assert canslim.main(["005930"]) == 0

    captured = capsys.readouterr()
    assert "[누락] info_tab" in captured.err
    assert "점수 1/7" in captured.out and "❓ C" in captured.out and "❓ M" in captured.out


def test_preferred_share_without_its_own_rs_shows_the_common_rs_as_reference_only() -> None:
    own = _info(chart=_chart([100] * 5))
    common = _info(rs_data={"rs": 91.7})

    l = canslim.evaluate(own, None, [], fundamentals=common)["L"]

    assert l["mark"] == "unknown"
    assert "본주 RS 91.7 (참고)" in l["detail"]


def test_every_threshold_is_documented_with_its_value() -> None:
    """기준값은 운영 선택이다 — 상수만 바꾸고 SKILL.md 표가 옛 값이면 다음 사람이 못 읽는다."""
    skill = (REPO_ROOT / ".agents" / "skills" / "analyze-stock" / "SKILL.md").read_text(encoding="utf-8")
    section = skill.split("**CAN SLIM 점검")[1].split("\n**")[0]

    for name in ("C_MIN_EPS_YOY_PCT", "A_YEARS", "N_HIGH_WINDOW", "N_MAX_BELOW_HIGH_PCT",
                 "S_WINDOW", "S_MIN_UPDOWN_RATIO", "L_MIN_RS", "I_WINDOW"):
        row = next((ln for ln in section.splitlines() if ln.startswith("|") and f"`{name}`" in ln), None)
        assert row, f"{name}이 기준표에 없다"
        cells = [c.strip() for c in row.split("|")]
        assert float(cells[3]) == getattr(canslim, name), name


def test_canslim_is_reference_only_and_absent_from_the_entry_gate() -> None:
    """참고 지표다 — entry_policy가 CAN SLIM을 읽기 시작하면 판단에 개입하게 된다."""
    policy = (SCRIPT.parent / "entry_policy.py").read_text(encoding="utf-8")

    assert "canslim" not in policy.lower()


def test_m_is_unknown_when_the_listing_market_is_unknown() -> None:
    """info-tab이 실패하면 상장 시장을 모른다 — KOSPI로 가정하면 코스닥 종목이 엉뚱한 시장으로 채점된다."""
    m = canslim.evaluate(None, _bp("confirmed_uptrend"), [])["M"]

    assert m["mark"] == "unknown"
    assert "상장 시장 미상" in m["detail"]
