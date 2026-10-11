"""daily-digest `fetch_market_signals.py` — 시장 지표와 레버리지·낙폭 사다리·반대매매 판정."""

from __future__ import annotations

from pathlib import Path

import pytest

from invagent.datafeed import http as datafeed_http, stockeasy
from tests.conftest import SKILLS_ROOT, load_script


def test_market_signal_endpoints_point_at_stockdata_api() -> None:
    """사이트가 클라이언트 렌더링으로 바뀐 뒤 실제 호출 경로는 /stockdata/api/v1/market."""
    module = load_script("daily-digest", "fetch_market_signals")

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
    module = load_script("daily-digest", "fetch_market_signals")

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
    monkeypatch.setattr(stockeasy, "fetch_market_json", lambda name: (payloads[name], None))

    assert module.main([]) == 0

    out = capsys.readouterr().out
    assert "green" in out
    assert "6,977.94" in out
    assert "상승 추세 확인" in out
    assert "30.9%" in out


def _monitor_rows(kospi: list[float], kosdaq: list[float] | None = None) -> list[dict]:
    kosdaq = kosdaq or [1000.0] * len(kospi)
    return [
        {"일자": f"2026-09-{i + 1:02d}", "KOSPI": k, "KOSDAQ": q}
        for i, (k, q) in enumerate(zip(kospi, kosdaq))
    ]


def test_leverage_rule_three_fires_on_three_volatile_days_in_ten() -> None:
    """「레버리지 규칙 3」은 하드 트리거인데 지금까지 아무도 계산하지 않고 모델이 눈대중했다."""
    module = load_script("daily-digest", "fetch_market_signals")

    # 10개 변화 중 ±5%가 정확히 3일 (-6.00 / -5.82 / -5.03)
    closes = [100.0, 94.0, 94.5, 89.0, 89.5, 85.0, 85.5, 86.0, 86.5, 87.0, 87.5]
    verdict = module.leverage_liquidation(_monitor_rows(closes))["KOSPI"]

    assert verdict["fired"] is True
    assert verdict["count"] == 3
    assert len(verdict["days"]) == 3


def test_leverage_rule_three_stays_quiet_below_the_threshold() -> None:
    """2일이면 발동하지 않는다 — 룰이 요구하는 것은 3일 이상이다."""
    module = load_script("daily-digest", "fetch_market_signals")

    # ±5%가 2일뿐 (-6.00 / -5.82)
    closes = [100.0, 94.0, 94.5, 89.0, 89.5, 90.0, 90.5, 91.0, 91.5, 92.0, 92.5]
    verdict = module.leverage_liquidation(_monitor_rows(closes))["KOSPI"]

    assert verdict["fired"] is False
    assert verdict["count"] == 2


def test_leverage_rule_three_exempts_a_run_that_is_all_upward() -> None:
    """룰의 예외는 '상승 방향으로 일관적인 변동'이다 — 음의 복리는 방향이 섞일 때 생긴다."""
    module = load_script("daily-digest", "fetch_market_signals")

    # ±5% 3일이 전부 상승 (+6.00 / +6.13 / +6.22)
    closes = [100.0, 106.0, 112.5, 119.5, 120.0, 120.5, 121.0, 121.5, 122.0, 122.5, 123.0]
    verdict = module.leverage_liquidation(_monitor_rows(closes))["KOSPI"]

    assert verdict["count"] == 3
    assert verdict["fired"] is False
    assert verdict["exempt"] is True

    # 하락이 하나라도 섞이면 예외가 깨진다.
    # 같은 3일인데 마지막이 하락 (+6.00 / +6.13 / -5.78)
    mixed = [100.0, 106.0, 112.5, 106.0, 106.5, 107.0, 107.5, 108.0, 108.5, 109.0, 109.5]
    broken = module.leverage_liquidation(_monitor_rows(mixed))["KOSPI"]
    assert broken["count"] == 3 and broken["fired"] is True and broken["exempt"] is False


def test_leverage_rule_three_judges_each_index_separately_and_no_sectors() -> None:
    """레버리지 상품은 지수별로 다르다. 섹터 레버리지는 「레버리지 규칙 2」대로 사용자 판단이다."""
    module = load_script("daily-digest", "fetch_market_signals")

    calm = [100.0] * 11
    wild = [100.0, 94.0, 94.5, 89.0, 89.5, 85.0, 85.5, 86.0, 86.5, 87.0, 87.5]
    result = module.leverage_liquidation(_monitor_rows(calm, wild))

    assert set(result) == {"KOSPI", "KOSDAQ"}
    assert result["KOSPI"]["fired"] is False
    assert result["KOSDAQ"]["fired"] is True

    script = (SKILLS_ROOT / "daily-digest" / "scripts" / "fetch_market_signals.py").read_text(encoding="utf-8")
    assert "섹터" in script, "판정 범위가 지수뿐이라는 사실이 스크립트에 남아야 한다"


def test_leverage_rule_three_needs_a_full_window() -> None:
    """행이 모자라면 '판정 불가'다 — 짧은 창을 채운 셈 치고 발동시키지 않는다."""
    module = load_script("daily-digest", "fetch_market_signals")

    verdict = module.leverage_liquidation(_monitor_rows([100.0, 94.0, 99.0]))["KOSPI"]
    assert verdict["fired"] is False and verdict["insufficient"] is True


def test_market_signal_main_reports_api_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """지수·빅픽처가 모두 실패하면 사유를 stderr에 남기고 exit 1."""
    module = load_script("daily-digest", "fetch_market_signals")
    monkeypatch.setattr(stockeasy, "fetch_market_json", lambda name: (None, "HTTP 404"))

    assert module.main([]) == 1
    assert "HTTP 404" in capsys.readouterr().err


def test_market_signal_no_cache_flag_refetches_every_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--no-cache`는 다른 스크립트처럼 캐시를 건너뛴다 — 직전 실행이 남긴 응답을 쓰지 않는다."""
    module = load_script("daily-digest", "fetch_market_signals")
    monkeypatch.setattr(module.http_cache, "_forced_off", False)
    monkeypatch.delenv("INVAGENT_HTTP_CACHE", raising=False)

    calls: list[str] = []

    def fake_read_url(url, headers, timeout):
        calls.append(url)
        return b"{}"

    monkeypatch.setattr(datafeed_http, "read_url", fake_read_url)

    module.main([])
    first_run = len(calls)
    assert first_run == len(module.ENDPOINTS)

    module.main(["--no-cache"])
    assert len(calls) == 2 * first_run, "--no-cache인데 캐시가 응답했다"


def _credit_rows(values: list[float]) -> list[dict]:
    return [{"date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "margin_call_amount": v}
            for i, v in enumerate(values)]


def _monitor_for(credit: list[dict], closes: list[float]) -> list[dict]:
    return [{"일자": r["date"], "KOSPI": c, "KOSDAQ": c} for r, c in zip(credit, closes)]


def _deep_drawdown(credit: list[dict]) -> list[dict]:
    """마지막 날이 고점 대비 -20%인 지수 경로 — 낙폭 게이트를 통과한다."""
    return _monitor_for(credit, [100.0] * (len(credit) - 1) + [80.0])


def test_margin_call_climax_fires_beyond_two_sigma() -> None:
    """지수 급락의 반대매매 클라이맥스는 지수 레버리지 신규 매수가 허용되는 자리다."""
    module = load_script("daily-digest", "fetch_market_signals")

    calm = [100.0, 120.0, 90.0, 110.0, 95.0] * 30      # 150일 평온
    credit = _credit_rows(calm + [2000.0])
    verdict = module.margin_call_climax(credit, _deep_drawdown(credit))["KOSPI"]

    assert verdict["climax"] is True
    assert verdict["value"] == 2000.0
    assert verdict["threshold"] < 2000.0
    assert verdict["sigma"] == module.MARGIN_CALL_SIGMA


def test_margin_call_climax_stays_quiet_on_an_ordinary_day() -> None:
    module = load_script("daily-digest", "fetch_market_signals")
    calm = [100.0, 120.0, 90.0, 110.0, 95.0] * 30
    # 기준선 평균 103.0 · σ 10.77 → 임계 124.5. 그 안쪽 값은 클라이맥스가 아니다.
    credit = _credit_rows(calm + [115.0])
    verdict = module.margin_call_climax(credit, _deep_drawdown(credit))["KOSPI"]

    assert verdict["climax"] is False
    assert verdict["value"] < verdict["threshold"]


def test_margin_call_climax_excludes_today_from_its_own_baseline() -> None:
    """오늘 값이 평균·표준편차에 섞이면 큰 값일수록 자기 임계를 끌어올려 신호가 무뎌진다."""
    module = load_script("daily-digest", "fetch_market_signals")
    calm = [100.0] * 150
    credit = _credit_rows(calm + [500.0])

    spike = module.margin_call_climax(credit, _deep_drawdown(credit))["KOSPI"]
    # 당일을 포함했다면 σ가 0이 아니게 되어 임계가 500 근처까지 밀린다.
    assert spike["threshold"] == 100.0
    assert spike["climax"] is True


def _ladder_monitor(closes: list[float]) -> list[dict]:
    return [{"일자": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "KOSPI": c, "KOSDAQ": c}
            for i, c in enumerate(closes)]


def test_drawdown_ladder_reports_every_five_percent_from_ten() -> None:
    """고점 대비 -10%부터 5%마다 단을 둔다 — 어느 단까지 밟았는지가 분할 매수의 눈금이다."""
    module = load_script("daily-digest", "fetch_market_signals")

    assert module.DRAWDOWN_RUNGS[:5] == (-10.0, -15.0, -20.0, -25.0, -30.0)
    assert -35.0 in module.DRAWDOWN_RUNGS and -40.0 in module.DRAWDOWN_RUNGS

    ladder = module.drawdown_ladder(_ladder_monitor([100.0] * 60 + [78.0]), "KOSPI")

    assert round(ladder["drawdown"], 1) == -22.0
    assert ladder["breached"] == [-10.0, -15.0, -20.0]
    assert ladder["next_rung"] == -25.0
    # 다음 단까지 지수가 얼마여야 하는지 — 미리 알아야 주문으로 옮긴다.
    assert round(ladder["next_level"], 1) == 75.0


def test_drawdown_ladder_flags_the_rung_first_touched_today() -> None:
    """새로 밟은 단이 행동 시점이다. 2026-07-30 저점에서 KOSPI는 -35%를 T-1에 처음 밟았다."""
    module = load_script("daily-digest", "fetch_market_signals")

    # 어제 -22%(=-20% 단), 오늘 -27%(=-25% 단 신규)
    ladder = module.drawdown_ladder(_ladder_monitor([100.0] * 60 + [78.0, 73.0]), "KOSPI")

    assert ladder["newly"] == [-25.0]

    # 같은 단에 머무르면 신규가 아니다.
    same = module.drawdown_ladder(_ladder_monitor([100.0] * 60 + [78.0, 77.0]), "KOSPI")
    assert same["newly"] == []


def test_drawdown_ladder_does_not_re_announce_a_rung_it_just_left() -> None:
    """경계선을 왕복하면 같은 단이 며칠 간격으로 되풀이 발동한다.

    실측 재현에서 KOSPI는 2026-07-28에 -30%를 처음 밟고, 반등 뒤 08-03·08-06에 같은 단을
    다시 「신규」로 알렸다. 분할 매수 신호가 같은 자리에서 세 번 울리면 눈금 구실을 못 한다.
    """
    module = load_script("daily-digest", "fetch_market_signals")

    # -30% 밟음 → 회복 → 재이탈. 재이탈은 신규가 아니다.
    closes = [100.0] * 60 + [69.0, 75.0, 69.5]
    ladder = module.drawdown_ladder(_ladder_monitor(closes), "KOSPI")
    assert -30.0 in ladder["breached"]
    assert ladder["newly"] == [], "쿨다운 안에서 같은 단이 다시 신규로 잡혔다"

    # 쿨다운을 넘겨 오래 떠 있다가 다시 내려오면 그때는 신규다.
    long_gap = [100.0] * 60 + [69.0] + [75.0] * (module.RUNG_RECLAIM_DAYS + 1) + [69.5]
    assert module.drawdown_ladder(_ladder_monitor(long_gap), "KOSPI")["newly"] == [-30.0]


def test_drawdown_ladder_is_quiet_above_the_first_rung() -> None:
    """-10%에 못 미치면 사다리는 아직 시작도 안 했다."""
    module = load_script("daily-digest", "fetch_market_signals")
    ladder = module.drawdown_ladder(_ladder_monitor([100.0] * 60 + [95.0]), "KOSPI")

    assert ladder["breached"] == [] and ladder["next_rung"] == -10.0


def test_leverage_entry_window_needs_both_the_drawdown_and_the_climax() -> None:
    """「레버리지 규칙 2」 베팅 가능 구간 = 낙폭 -10% 이하 **그리고** 반대매매 2σ."""
    module = load_script("daily-digest", "fetch_market_signals")
    calm = [100.0, 120.0, 90.0, 110.0, 95.0] * 30
    credit = _credit_rows(calm + [2000.0])
    deep = _deep_drawdown(credit)

    window = module.leverage_entry_window(credit, deep)
    assert window["KOSPI"]["open"] is True
    assert window["KOSPI"]["ladder"]["breached"], "사다리 단이 함께 실려야 어디서 살지 정할 수 있다"

    # 반대매매가 잠잠하면 낙폭이 깊어도 구간이 아니다.
    quiet = _credit_rows(calm + [110.0])
    assert module.leverage_entry_window(quiet, _deep_drawdown(quiet))["KOSPI"]["open"] is False


def test_margin_call_climax_is_judged_per_index() -> None:
    """반대매매는 시장 전체 값이지만 낙폭 국면은 지수마다 다르다 — 레버리지 상품이 지수별이므로 따로 낸다.

    실측(2025-01~2026-09): 같은 반대매매 시리즈에 KOSPI 게이트는 8건, KOSDAQ 게이트는 9건이
    걸리고 겹치는 날은 3일뿐이다.
    """
    module = load_script("daily-digest", "fetch_market_signals")
    calm = [100.0, 120.0, 90.0, 110.0, 95.0] * 30
    credit = _credit_rows(calm + [2000.0])
    n = len(credit)
    # KOSPI만 깊게 밀린 경로
    monitor = [
        {"일자": r["date"], "KOSPI": k, "KOSDAQ": q}
        for r, k, q in zip(credit, [100.0] * (n - 1) + [80.0], [100.0] * n)
    ]

    result = module.margin_call_climax(credit, monitor)

    assert set(result) == set(module.LEVERAGE_MARKETS)
    assert result["KOSPI"]["climax"] is True
    assert result["KOSDAQ"]["climax"] is False
    # 급증 자체는 시장 공통이므로 두 축에 같은 값이 실린다.
    assert result["KOSPI"]["value"] == result["KOSDAQ"]["value"] == 2000.0
    assert result["KOSPI"]["spike"] is result["KOSDAQ"]["spike"] is True


def test_margin_call_climax_requires_the_index_to_be_deep_in_drawdown() -> None:
    """반대매매 급증만으로는 부족하다 — 2026-05-11은 지수 **신고가**에서 +2σ가 켜졌다.

    사용자 조건은 "지수 급락 → 반대매매 클라이맥스"다. 급락은 그날 하락률이 아니라
    국면(고점 대비 낙폭)으로 읽는다 — 실제 클라이맥스 2026-07-31은 그날 +18.05%였다.
    """
    module = load_script("daily-digest", "fetch_market_signals")
    calm = [100.0, 120.0, 90.0, 110.0, 95.0] * 30
    credit = _credit_rows(calm + [2000.0])

    at_high = module.margin_call_climax(credit, _monitor_for(credit, [100.0] * len(credit)))["KOSPI"]
    assert at_high["climax"] is False
    assert at_high["drawdown"] == 0.0

    deep = module.margin_call_climax(credit, _deep_drawdown(credit))["KOSPI"]
    assert deep["climax"] is True
    assert deep["drawdown"] < module.MARGIN_CALL_MIN_DRAWDOWN_PCT


def test_margin_call_climax_needs_a_full_baseline() -> None:
    """창을 못 채우면 판정 불가다 — 짧은 표본의 σ로 레버리지 진입을 허가하지 않는다."""
    module = load_script("daily-digest", "fetch_market_signals")
    credit = _credit_rows([100.0] * 10)
    verdict = module.margin_call_climax(credit, _deep_drawdown(credit))["KOSPI"]

    assert verdict["climax"] is False and verdict["insufficient"] is True


def test_market_signals_fetch_endpoints_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    """4개 엔드포인트를 순서대로 기다릴 이유가 없다 — 서로 독립이다."""
    module = load_script("daily-digest", "fetch_market_signals")

    import threading
    import time

    live, peak_live = 0, 0
    lock = threading.Lock()

    def slow_api(name):
        nonlocal live, peak_live
        with lock:
            live += 1
            peak_live = max(peak_live, live)
        time.sleep(0.05)
        with lock:
            live -= 1
        return {"indices": []}, None

    monkeypatch.setattr(stockeasy, "fetch_market_json", slow_api)
    data, errors = module.fetch_all()

    assert set(data) == set(module.ENDPOINTS) and not errors
    assert peak_live > 1, "엔드포인트를 아직 직렬로 받고 있다"
