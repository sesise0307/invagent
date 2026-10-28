"""daily-digest `peak_drawdown.py` — 전고점 낙폭 · 계좌 MDD."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from invagent.core.archive import dated_path
from invagent.datafeed import tickers as datafeed_tickers
from invagent.datafeed.daily import Bars
from tests.conftest import load_script

REPO_ROOT = Path(__file__).resolve().parents[1]

# --- peak_drawdown.py (전고점 낙폭 · 계좌 MDD) --------------------------------

PEAK_SNAPSHOT = """# 포트폴리오 스냅샷 — 2026-08-28

> 출처: Google Sheets '주식 포트폴리오' / 「포트폴리오」 시트 · 수집 2026-08-28T21:03:43
> 잔고 ₩90,000,000 · 총손익 ₩10,000,000 · 투자금 ₩80,000,000 · 수익률 12.50%

## 보유 (2종목 + 현금)

| 종목 | 섹터 | 보유 | 평단 | 현재가 | 수익률 | 비중 | 평가금액 | 투자 아이디어 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| _현금 | 현금 | 1 | ₩9,000,000 | ₩9,000,000 | 0.00% | 10.0% | ₩9,000,000 | 현금도 종목이다 |
| 알파전자 | 반도체 | 100 | ₩50,000 | ₩70,000 | 40.00% | 40.0% | ₩36,000,000 | 슈퍼사이클 |
| 베타파마 | 바이오 | 200 | ₩60,000 | ₩50,000 | -16.67% | 50.0% | ₩45,000,000 | 임상 |

## 룰 자동 판정

- 매매규칙 6(-15% 손절): 베타파마 -16.67% (비중 50.0%) ← **위반**
"""


def _bars(closes: list[tuple[str, float]]) -> list[dict]:
    return [
        {"date": d, "open": c, "high": c, "low": c, "close": c, "volume": 1.0}
        for d, c in closes
    ]


@pytest.mark.parametrize(
    ("drawdown", "expected"),
    [
        (0.0, None),
        (-9.9, None),
        (-10.0, -10.0),
        (-14.9, -10.0),
        (-15.0, -15.0),
        (-20.0, -20.0),
        (-29.9, -20.0),
        (-30.5, -30.0),
        (None, None),
    ],
)
def test_peak_drawdown_bands_take_the_deepest_hit(drawdown, expected) -> None:
    module = load_script("daily-digest", "peak_drawdown")

    assert module.band_for(drawdown) == expected


def test_peak_drawdown_band_survives_float_boundary() -> None:
    """90,000/100,000-1 = -9.999999999999998 — 표에 -10.0%로 찍히면 밴드도 걸려야 한다."""
    module = load_script("daily-digest", "peak_drawdown")
    result = module.peak_drawdown(
        Bars(_bars([("20260601", 100_000.0), ("20260828", 90_000.0)]))
    )

    assert f"{result['drawdown']:+.1f}%" == "-10.0%"
    assert module.band_for(result["drawdown"]) == -10.0


def test_peak_drawdown_parses_holdings_and_drops_cash() -> None:
    module = load_script("daily-digest", "peak_drawdown")

    holdings = module.parse_holdings(PEAK_SNAPSHOT)

    assert [h["종목"] for h in holdings] == ["알파전자", "베타파마"]
    assert holdings[0]["수익률"] == "40.00%" and holdings[0]["비중"] == "40.0%"
    assert module.parse_balance(PEAK_SNAPSHOT) == 90_000_000.0


def test_peak_is_highest_close_in_window_with_its_date() -> None:
    module = load_script("daily-digest", "peak_drawdown")
    # 창(250거래일) 밖의 더 높은 종가는 전고점으로 잡히지 않는다
    old = [(f"2024{i:04d}", 999_000.0) for i in range(1, 3)]
    window = [("20260101", 80_000.0), ("20260615", 100_000.0), ("20260828", 70_000.0)]

    result = module.peak_drawdown(Bars(_bars(old + window * 100)))

    assert result["peak"] == 100_000.0
    assert result["peak_date"] == "20260615"
    assert result["close"] == 70_000.0
    assert result["drawdown"] == pytest.approx(-30.0)


def test_peak_drawdown_unresolved_ticker_does_not_block_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_script("daily-digest", "peak_drawdown")
    monkeypatch.setattr(
        datafeed_tickers,
        "resolve_stock",
        lambda name, overrides=None: (
            ({"stock_code": "000660"}, None, 0)
            if name == "알파전자"
            else (None, "종목 검색 결과 없음", 1)
        ),
    )
    monkeypatch.setattr(
        module.daily,
        "fetch_daily_bars",
        lambda code, days, asof=None: Bars(_bars([("20260601", 100_000.0), ("20260828", 60_000.0)])),
    )

    results = module.analyze_holdings(module.parse_holdings(PEAK_SNAPSHOT), {})

    assert results[0]["band"] == -30.0 and results[0]["drawdown"] == pytest.approx(-40.0)
    assert "티커 미해석" in results[1]["error"]
    section = module.render(results, {"error": "잔고 이력 없음"})
    assert "베타파마(티커 미해석" in section
    assert "ticker_overrides.md" in section


def test_peak_drawdown_flags_holdings_measured_on_naver_fallback_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """52주 시장 축은 정규장 종가(StockEasy)로 잰다. 네이버로 대체된 종목은 섹션이 이름째 밝힌다."""
    from invagent.datafeed import daily

    module = load_script("daily-digest", "peak_drawdown")
    monkeypatch.setattr(
        datafeed_tickers, "resolve_stock", lambda name, overrides=None: ({"stock_code": "000660"}, None, 0)
    )
    note = daily.fallback_note("STOCKEASY_COOKIE 미설정")
    monkeypatch.setattr(
        daily,
        "fetch_daily_bars",
        lambda code, days, asof=None: Bars(
            _bars([("20260601", 100_000.0), ("20260828", 60_000.0)]), fallback=note
        ),
    )

    results = module.analyze_holdings(module.parse_holdings(PEAK_SNAPSHOT), {})
    section = module.render(results, {"error": "잔고 이력 없음"})

    assert results[0]["drawdown"] == pytest.approx(-40.0)
    flagged = [line for line in section.splitlines() if note in line]
    assert flagged and "알파전자" in flagged[0]


def test_peak_drawdown_overrides_win_over_api(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_script("daily-digest", "peak_drawdown")
    monkeypatch.setattr(
        datafeed_tickers, "resolve_stock", lambda name, overrides=None: ({"stock_code": "999999"}, None, 0)
    )

    assert module.resolve_code("알파전자", {"알파전자": "000660"}) == ("000660", None)
    assert module.resolve_code("베타파마", {}) == ("999999", None)


def test_peak_drawdown_overrides_file_ignores_comments(tmp_path: Path) -> None:
    module = load_script("daily-digest", "peak_drawdown")
    path = tmp_path / "ticker_overrides.md"
    path.write_text(
        "# 티커 오버라이드\n> 설명 줄\n- 불릿 줄\n\n알파전자 = 000660   # 표기 오타\n베타파마 = bad\n",
        encoding="utf-8",
    )

    assert module.load_overrides(path) == {"알파전자": "000660"}


def test_peak_drawdown_fetch_failure_is_non_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_script("daily-digest", "peak_drawdown")
    monkeypatch.setattr(
        datafeed_tickers, "resolve_stock", lambda name, overrides=None: ({"stock_code": "000660"}, None, 0)
    )
    monkeypatch.setattr(module.daily, "fetch_daily_bars", lambda code, days, asof=None: Bars([], "HTTP 500"))

    results = module.analyze_holdings(module.parse_holdings(PEAK_SNAPSHOT), {})

    assert all("시세 수집 실패 — HTTP 500" in r["error"] for r in results)
    section = module.render(results, {"error": "잔고 이력 없음"})
    assert "전고점 낙폭 판정(52주 시장 종가 기준): 해당 없음" in section


def _write_snapshot(root: Path, day: str, text: str) -> Path:
    """스냅샷 저장소 배치 `<root>/<yyyy>/<mm>/<날짜>.md`에 쓴다."""
    path = dated_path(root, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_account_mdd_uses_snapshot_balance_peak(tmp_path: Path) -> None:
    module = load_script("daily-digest", "peak_drawdown")
    for day, balance in (("2026-08-20", 100_000_000), ("2026-08-25", 95_000_000)):
        _write_snapshot(tmp_path, day, f"> 잔고 ₩{balance:,} · 수익률 1.00%\n")

    account = module.account_mdd(tmp_path, 88_000_000.0, "2026-08-28")

    assert account["peak"] == 100_000_000.0 and account["peak_date"] == "2026-08-20"
    assert account["mdd"] == pytest.approx(-12.0)
    assert account["band"] == -10.0
    rendered = module.render([], account)
    assert "계좌 MDD(「기본 원칙 13」)" in rendered
    assert "-12.0% ← **발동 (-10%)**" in rendered
    assert "입출금을 보정하지 않는다" in rendered


def test_stock_band_warns_before_it_is_hit() -> None:
    """종목도 밴드를 밟기 전에 경고한다 — trailing stop은 밟은 뒤 정하면 늦다."""
    module = load_script("daily-digest", "peak_drawdown")

    pending = module.pending_bands(-9.8, 112_700.0, module.DRAWDOWN_BANDS)
    near = module.approaching_band(pending, module.DRAWDOWN_WARN_MARGIN_PP)

    assert [p["band"] for p in pending] == [-10.0, -15.0, -20.0, -30.0]
    assert near["band"] == -10.0
    assert near["trigger"] == pytest.approx(101_430.0)  # 발동가를 금액으로 준다
    assert near["gap_pp"] == pytest.approx(0.2, abs=0.01)


def test_stock_band_already_hit_is_not_an_approach() -> None:
    """발동한 밴드는 임박 대상이 아니고, 다음 밴드로 넘어간다."""
    module = load_script("daily-digest", "peak_drawdown")

    pending = module.pending_bands(-13.0, 100_000.0, module.DRAWDOWN_BANDS)

    assert [p["band"] for p in pending] == [-15.0, -20.0, -30.0]  # -10%는 이미 밟음
    assert module.approaching_band(pending, 2.0)["band"] == -15.0
    # 표의 경보 칸 = 발동이 임박을 가리지 않는다
    assert module.alert_label(-10.0, {"band": -15.0}) == "🟡 -10%"
    assert module.alert_label(None, {"band": -10.0}) == "⚠️ -10%"
    assert module.alert_label(None, None) == "—"


def test_stock_approach_uses_displayed_precision() -> None:
    """-12.969%는 표에 -13.0%로 찍히므로 -15%까지 2.0%p로 읽힌다 — 경고도 그 값으로 판단한다."""
    module = load_script("daily-digest", "peak_drawdown")
    drawdown = (406_000 / 466_500 - 1) * 100  # 에이피알 실제값 = -12.969%

    pending = module.pending_bands(drawdown, 466_500.0, module.DRAWDOWN_BANDS)
    near = module.approaching_band(pending, module.DRAWDOWN_WARN_MARGIN_PP)

    assert f"{drawdown:+.1f}%" == "-13.0%"
    assert near is not None and near["band"] == -15.0
    assert near["trigger"] == pytest.approx(396_525.0)


def test_stock_deepest_band_leaves_nothing_to_warn() -> None:
    module = load_script("daily-digest", "peak_drawdown")

    assert module.pending_bands(-45.3, 2_919_000.0, module.DRAWDOWN_BANDS) == []
    assert module.approaching_band([], 2.0) is None


def test_render_lists_stock_approaches_per_axis(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_script("daily-digest", "peak_drawdown")
    monkeypatch.setattr(
        datafeed_tickers, "resolve_stock", lambda name, overrides=None: ({"stock_code": "000660"}, None, 0)
    )
    # 52주 축 = -9.0%(-10% 임박) / 기록 축 = -9.5%(-10% 임박)
    monkeypatch.setattr(
        module.daily,
        "fetch_daily_bars",
        lambda code, days, asof=None: Bars(_bars([("20260601", 100_000.0), ("20260828", 91_000.0)])),
    )
    history = {"알파전자": [("2026-08-10", 77_348.0)]}  # 현재가 ₩70,000 → -9.5%

    results = module.analyze_holdings(
        module.parse_holdings(PEAK_SNAPSHOT), {}, history, "2026-08-28"
    )
    section = module.render(results, {"error": "잔고 이력 없음"})

    assert "⚠️ 임박(52주 시장 종가 기준): 알파전자 -9.0% → -10% 발동가 ₩90,000 (1.0%p 남음)" in section
    # 기록 축 임박은 시트 수집가 경보다 — 종가로 재확인하기 전엔 발동 근거가 아니다
    assert (
        "⚠️ 임박(계좌 스냅샷 기록 기준 · 시트 수집가 경보, 종가로 재확인): "
        "알파전자 -9.5% → -10% 발동가 ₩69,613"
    ) in section
    # 미발동이지만 임박한 종목은 표의 경보 칸에도 표시된다
    assert "| -9.0% | ⚠️ -10% |" in section


def test_render_omits_approach_line_when_nothing_is_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_script("daily-digest", "peak_drawdown")
    monkeypatch.setattr(
        datafeed_tickers, "resolve_stock", lambda name, overrides=None: ({"stock_code": "000660"}, None, 0)
    )
    monkeypatch.setattr(
        module.daily,
        "fetch_daily_bars",
        lambda code, days, asof=None: Bars(_bars([("20260601", 100_000.0), ("20260828", 99_000.0)])),
    )

    section = module.render(
        module.analyze_holdings(module.parse_holdings(PEAK_SNAPSHOT), {}, {}, "2026-08-28"),
        {"error": "잔고 이력 없음"},
    )

    assert "임박" not in section


def _account(tmp_path: Path, today_balance: float, peak: float = 100_000_000.0) -> dict:
    """스냅샷 고점이 `peak`인 계좌에서 오늘 잔고가 `today_balance`일 때의 MDD 판정."""
    _write_snapshot(tmp_path, "2026-08-20", f"> 잔고 ₩{peak:,.0f} · 수익률 1.00%\n")
    return load_script("daily-digest", "peak_drawdown").account_mdd(tmp_path, today_balance, "2026-08-28")


def test_account_mdd_warns_before_band_is_hit(tmp_path: Path) -> None:
    """-15% 밟은 뒤 알리면 「기본 원칙 13」 대응을 준비할 시간이 없다 — 도달 전에 경고한다."""
    module = load_script("daily-digest", "peak_drawdown")

    account = _account(tmp_path, 86_500_000.0)  # MDD -13.5%

    assert account["mdd"] == pytest.approx(-13.5)
    assert account["band"] == -10.0  # -10%는 이미 발동
    assert account["approaching"]["band"] == -15.0  # -15%는 임박
    assert account["approaching"]["gap_pp"] == pytest.approx(1.5)
    assert account["approaching"]["trigger"] == pytest.approx(85_000_000.0)
    assert account["approaching"]["gap_won"] == pytest.approx(1_500_000.0)

    rendered = module.render([], account)
    assert "⚠️⚠️ **계좌 MDD -15% 임박 — 남은 거리 1.5%p(₩1,500,000).**" in rendered
    assert "잔고가 ₩85,000,000 아래로 내려가면 발동한다" in rendered
    assert "5거래일 신규 매수 금지" in rendered
    # 이미 밟은 -10%는 임박 대상이 아니다
    assert "계좌 MDD -10% 임박" not in rendered


def test_account_mdd_stays_quiet_outside_warn_margin(tmp_path: Path) -> None:
    module = load_script("daily-digest", "peak_drawdown")

    account = _account(tmp_path, 87_500_000.0)  # MDD -12.5% = -15%까지 2.5%p

    assert account["approaching"] is None
    rendered = module.render([], account)
    assert "임박" not in rendered
    # 경고가 없어도 발동선과 남은 거리는 매일 보인다
    assert "계좌 MDD -15% 발동선: ₩85,000,000 (현 잔고에서 2.5%p · ₩2,500,000 남음)" in rendered


def test_account_mdd_warn_margin_boundary_counts_as_approaching(tmp_path: Path) -> None:
    """경계값(정확히 2.0%p)은 경고에 포함한다 — 한 발 늦는 쪽으로 반올림하지 않는다."""
    module = load_script("daily-digest", "peak_drawdown")

    account = _account(tmp_path, 87_000_000.0)  # MDD -13.0% = -15%까지 정확히 2.0%p

    assert account["approaching"]["band"] == -15.0
    assert account["approaching"]["gap_pp"] == pytest.approx(module.ACCOUNT_MDD_WARN_MARGIN_PP)


def test_account_mdd_deepest_band_hit_has_nothing_left_to_warn(tmp_path: Path) -> None:
    module = load_script("daily-digest", "peak_drawdown")

    account = _account(tmp_path, 80_000_000.0)  # MDD -20%

    assert account["band"] == -15.0
    assert account["pending"] == [] and account["approaching"] is None
    rendered = module.render([], account)
    assert "임박" not in rendered and "발동선" not in rendered


def test_account_mdd_shows_distance_to_both_bands_when_healthy(tmp_path: Path) -> None:
    """오늘 같은 -6.8% 구간 = 경고는 없지만 두 발동선까지의 거리는 항상 찍힌다."""
    module = load_script("daily-digest", "peak_drawdown")

    account = _account(tmp_path, 93_200_000.0)  # MDD -6.8%

    assert account["approaching"] is None
    assert [p["band"] for p in account["pending"]] == [-10.0, -15.0]
    rendered = module.render([], account)
    assert "계좌 MDD -10% 발동선: ₩90,000,000 (현 잔고에서 3.2%p · ₩3,200,000 남음)" in rendered
    assert "계좌 MDD -15% 발동선: ₩85,000,000 (현 잔고에서 8.2%p · ₩8,200,000 남음)" in rendered


def test_peak_drawdown_fetches_holdings_concurrently_keeping_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """보유 10종목이면 네이버 왕복 10회가 직렬로 쌓인다. 병렬로 돌리되 출력 순서는 입력 순서다."""
    module = load_script("daily-digest", "peak_drawdown")
    monkeypatch.setattr(datafeed_tickers, "resolve_stock", lambda name, overrides=None: ({"stock_code": "000660"}, None, 0))

    import threading
    import time

    live, peak_live = 0, 0
    lock = threading.Lock()

    def slow_fetch(code, days, asof=None):
        nonlocal live, peak_live
        with lock:
            live += 1
            peak_live = max(peak_live, live)
        time.sleep(0.05)
        with lock:
            live -= 1
        return Bars([{"date": "20260903", "close": 100.0}])

    monkeypatch.setattr(module.daily, "fetch_daily_bars", slow_fetch)

    holdings = [
        {"종목": f"종목{i}", "섹터": "반도체", "현재가": "90", "수익률": "0%", "비중": "1%"}
        for i in range(6)
    ]
    results = module.analyze_holdings(holdings, {}, {}, "2026-09-05")

    assert [r["종목"] for r in results] == [h["종목"] for h in holdings], "출력 순서가 흐트러졌다"
    assert peak_live > 1, "여전히 한 번에 하나씩 받고 있다"
    assert peak_live <= module.MAX_FETCH_WORKERS


def test_peak_drawdown_append_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = load_script("daily-digest", "peak_drawdown")
    monkeypatch.setattr(
        datafeed_tickers, "resolve_stock", lambda name, overrides=None: ({"stock_code": "000660"}, None, 0)
    )
    monkeypatch.setattr(
        module.daily,
        "fetch_daily_bars",
        lambda code, days, asof=None: Bars(_bars([("20260601", 100_000.0), ("20260828", 90_000.0)])),
    )
    snapshot = tmp_path / "2026-08-28.md"
    snapshot.write_text(PEAK_SNAPSHOT, encoding="utf-8")

    assert module.main([str(snapshot), "--append"]) == 0
    assert module.main([str(snapshot), "--append"]) == 0
    capsys.readouterr()

    text = snapshot.read_text(encoding="utf-8")
    assert text.count(module.SECTION_TITLE) == 1
    # 기존 룰 자동 판정 섹션은 그대로 남는다
    assert "매매규칙 6(-15% 손절)" in text
    assert "🟡 -10%" in text
    # 계좌 기록 축도 같은 섹션에 함께 쓰인다
    assert "계좌 기록 고점(일자)" in text


def test_peak_drawdown_exits_1_on_unparsable_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = load_script("daily-digest", "peak_drawdown")
    broken = tmp_path / "2026-08-28.md"
    broken.write_text("# 스냅샷\n\n표가 없다\n", encoding="utf-8")

    assert module.main([str(broken)]) == 1
    assert "스냅샷 파싱 실패" in capsys.readouterr().err


def test_peak_drawdown_thresholds_match_documented_bands() -> None:
    """밴드·창·마진은 운영 선택이다 — 상수만 바꾸고 SKILL.md 1-3-1단계가 옛 값이면 브리핑이 어긋난다."""
    module = load_script("daily-digest", "peak_drawdown")
    skill = (REPO_ROOT / ".agents" / "skills" / "daily-digest" / "SKILL.md").read_text(encoding="utf-8")
    section = skill.split("### 1-3-1단계")[1].split("\n### ")[0]

    window = re.search(r"최근 (\d+)거래일 최고 종가", section)
    assert window, "52주 축 고점 창이 1-3-1단계 표에 없다"
    assert int(window.group(1)) == module.PEAK_WINDOW_DAYS

    band_rows = re.findall(r"^\| (-\d+(?:\.\d+)?)% \|", section, flags=re.MULTILINE)
    assert tuple(float(b) for b in band_rows) == module.DRAWDOWN_BANDS

    account = re.search(r"잔고 고점 대비 ((?:-\d+% / )*-\d+%) 발동선", section)
    assert account, "계좌 MDD 밴드가 1-3-1단계에 없다"
    assert tuple(float(b) for b in re.findall(r"-\d+", account.group(1))) == module.ACCOUNT_MDD_BANDS

    for name in ("DRAWDOWN_WARN_MARGIN_PP", "ACCOUNT_MDD_WARN_MARGIN_PP"):
        margin = re.search(rf"`{name}`\(기본 (\d+(?:\.\d+)?)%p", section)
        assert margin, f"{name}의 기본값이 1-3-1단계에 없다"
        assert float(margin.group(1)) == getattr(module, name), name


# --- 계좌 기록 고점 축 --------------------------------------------------------


def _record_snapshot(date: str, prices: dict[str, int]) -> str:
    """합성 스냅샷 마크다운. 현금 행은 기록 축에서 제외돼야 한다."""
    rows = "\n".join(
        f"| {name} | 반도체 | 10 | ₩1,000 | ₩{price:,} | 1.00% | 10.0% | ₩10,000 | 아이디어 |"
        for name, price in prices.items()
    )
    return (
        f"# 포트폴리오 스냅샷 — {date}\n\n"
        f"> 잔고 ₩90,000,000 · 수익률 1.00%\n\n"
        f"## 보유 ({len(prices)}종목 + 현금)\n\n"
        "| 종목 | 섹터 | 보유 | 평단 | 현재가 | 수익률 | 비중 | 평가금액 | 투자 아이디어 |\n"
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |\n"
        "| _현금 | 현금 | 1 | ₩1,000 | ₩1,000 | 0.00% | 1.0% | ₩1,000 | 현금도 종목이다 |\n"
        f"{rows}\n"
    )


def test_record_history_skips_unparsable_snapshot(tmp_path: Path) -> None:
    """`## 보유` 표가 없는 손글씨 스냅샷(실제 2026-08-15)이 섞여도 나머지로 계속 계산한다."""
    module = load_script("daily-digest", "peak_drawdown")
    _write_snapshot(tmp_path, "2026-08-10", _record_snapshot("2026-08-10", {"알파전자": 355_000}))
    _write_snapshot(
        tmp_path,
        "2026-08-15",
        "# 포트폴리오 스냅샷 — 2026-08-15\n\n| 종목 | 비중 | 비고 |\n|---|---:|---|\n"
        "| 알파전자 | 26.0% | 반도체 |\n",
    )
    _write_snapshot(tmp_path, "2026-09-03", _record_snapshot("2026-09-03", {"알파전자": 283_000}))

    history, meta = module.load_record_history(tmp_path)

    assert [d for d, _ in history["알파전자"]] == ["2026-08-10", "2026-09-03"]
    assert meta["valid_files"] == 2 and meta["total_files"] == 3
    assert meta["from"] == "2026-08-10" and meta["to"] == "2026-09-03"
    # 현금 행은 기록 축에 들어오지 않는다
    assert "_현금" not in history


def test_record_drawdown_uses_history_peak() -> None:
    module = load_script("daily-digest", "peak_drawdown")
    history = {"알파전자": [("2026-08-01", 300_000.0), ("2026-08-10", 355_000.0)]}

    result = module.record_drawdown("알파전자", 283_000.0, "2026-09-03", history)

    assert result["peak"] == 355_000.0 and result["peak_date"] == "2026-08-10"
    assert result["drawdown"] == pytest.approx(-20.28, abs=0.01)
    assert result["band"] == -20.0
    assert result["samples"] == 3


def test_record_drawdown_today_value_overrides_disk_entry() -> None:
    """오늘 일자 항목은 디스크 값을 버리고 방금 파싱한 값을 쓴다(`account_mdd`와 같은 규약)."""
    module = load_script("daily-digest", "peak_drawdown")
    history = {"알파전자": [("2026-09-03", 999_000.0), ("2026-08-10", 355_000.0)]}

    result = module.record_drawdown("알파전자", 283_000.0, "2026-09-03", history)

    assert result["peak"] == 355_000.0
    assert result["current"] == 283_000.0
    assert result["samples"] == 2


def test_record_drawdown_without_prior_history_is_flat() -> None:
    """편입 첫날 = 고점도 오늘, 현재가도 오늘 → 낙폭 0%. 마커 없이 숫자만 남는다."""
    module = load_script("daily-digest", "peak_drawdown")

    result = module.record_drawdown("신규종목", 100_000.0, "2026-09-03", {})

    assert result["error"] is None
    assert result["peak"] == result["current"] == 100_000.0
    assert result["drawdown"] == pytest.approx(0.0)
    assert result["band"] is None

    assert module.record_drawdown("신규종목", None, "2026-09-03", {}) == {
        "error": "스냅샷 기록 없음"
    }


def test_record_drawdown_without_today_price_is_dated_and_silent() -> None:
    """오늘 현재가가 비면(`-`·`#N/A`) 마지막 기록일 값으로 재되, 그 날짜를 달고 경보·임박은 끈다."""
    module = load_script("daily-digest", "peak_drawdown")
    history = {"알파전자": [("2026-08-10", 100_000.0), ("2026-08-27", 88_000.0)]}

    result = module.record_drawdown("알파전자", None, "2026-08-28", history)

    assert result["peak"] == 100_000.0 and result["peak_date"] == "2026-08-10"
    assert result["drawdown"] == pytest.approx(-12.0)
    assert result["current_date"] == "2026-08-27"
    assert result["band"] is None
    assert result["approach"] is None
    assert module.record_cells(result) == "₩100,000 (2026-08-10) | -12.0% (08-27 기준)"


def test_record_drawdown_with_today_price_is_dated_today() -> None:
    module = load_script("daily-digest", "peak_drawdown")
    history = {"알파전자": [("2026-08-10", 100_000.0)]}

    result = module.record_drawdown("알파전자", 88_000.0, "2026-08-28", history)

    assert result["current_date"] == "2026-08-28"
    assert result["band"] == -10.0
    assert module.record_cells(result) == "₩100,000 (2026-08-10) | -12.0% 🟡"


def test_render_keeps_stale_record_value_out_of_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_script("daily-digest", "peak_drawdown")
    monkeypatch.setattr(
        datafeed_tickers, "resolve_stock", lambda name, overrides=None: (None, "종목 검색 결과 없음", 1)
    )
    stale = module.record_drawdown(
        "알파전자", None, "2026-08-28", {"알파전자": [("2026-08-10", 100_000.0), ("2026-08-27", 70_500.0)]}
    )
    results = [
        {"종목": "알파전자", "error": "종목 검색 결과 없음", "record": stale, "수익률": "-", "비중": "-"}
    ]

    section = module.render(results, {"error": "잔고 이력 없음"})

    assert "-29.5% (08-27 기준)" in section
    assert "기록 낙폭 경보(시트 수집가 기준 · 발동 판정 아님): 해당 없음" in section
    assert "임박(계좌 스냅샷" not in section


def test_record_axis_survives_market_data_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """네트워크가 죽어 52주 축이 비어도 계좌 기록 축은 그대로 표에 남아야 한다."""
    module = load_script("daily-digest", "peak_drawdown")
    monkeypatch.setattr(
        datafeed_tickers, "resolve_stock", lambda name, overrides=None: (None, "종목 검색 결과 없음", 1)
    )
    history = {"알파전자": [("2026-08-10", 100_000.0)]}

    results = module.analyze_holdings(
        module.parse_holdings(PEAK_SNAPSHOT), {}, history, "2026-08-28"
    )

    assert results[0]["error"] is not None  # 52주 축은 실패
    assert results[0]["record"]["drawdown"] == pytest.approx(-30.0)  # 기록 축은 살아있다
    section = module.render(results, {"error": "잔고 이력 없음"})
    assert "₩100,000 (2026-08-10) | -30.0% ⛔" in section


def test_peak_drawdown_reads_history_across_month_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """스냅샷은 `<yyyy>/<mm>/<날짜>.md`에 쌓인다 — 이력·MDD·상태 파일은 월 폴더가 아니라 루트 기준."""
    module = load_script("daily-digest", "peak_drawdown")
    monkeypatch.setattr(
        datafeed_tickers, "resolve_stock", lambda name, overrides=None: (None, "종목 검색 결과 없음", 1)
    )
    root = tmp_path / "portfolio"
    snapshots = {
        "2026-09-30": (100_000_000, 100_000),
        "2026-10-02": (88_000_000, 70_000),
    }
    for day, (balance, price) in snapshots.items():
        _write_snapshot(
            root, day, f"> 잔고 ₩{balance:,} · 수익률 1.00%\n\n" + _record_snapshot(day, {"알파전자": price})
        )

    assert module.main([str(root / "2026" / "10" / "2026-10-02.md"), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["account"]["peak_date"] == "2026-09-30"
    assert payload["account"]["mdd"] == pytest.approx(-12.0)
    assert payload["record_history"]["from"] == "2026-09-30"
    assert (root / ".peak_drawdown_state.json").is_file()
    assert not (root / "2026" / "10" / ".peak_drawdown_state.json").exists()


def test_render_names_both_axes_and_forbids_summing(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_script("daily-digest", "peak_drawdown")
    monkeypatch.setattr(
        datafeed_tickers, "resolve_stock", lambda name, overrides=None: ({"stock_code": "000660"}, None, 0)
    )
    monkeypatch.setattr(
        module.daily,
        "fetch_daily_bars",
        lambda code, days, asof=None: Bars(_bars([("20260601", 100_000.0), ("20260828", 60_000.0)])),
    )
    # 52주 축은 -40%(⛔), 기록 축은 -12.5%(🟡) — 두 축이 두 밴드만큼 어긋나는 상황
    history = {"알파전자": [("2026-08-10", 80_000.0)], "베타파마": [("2026-08-10", 80_000.0)]}

    results = module.analyze_holdings(
        module.parse_holdings(PEAK_SNAPSHOT), {}, history, "2026-08-28"
    )
    section = module.render(
        results,
        {"error": "잔고 이력 없음"},
        {"valid_files": 22, "total_files": 23, "from": "2026-08-09", "to": "2026-09-03"},
    )

    # 같은 종목이 두 축에서 두 밴드만큼 다르게 걸린다 — 합쳐 세면 안 되는 이유
    assert "전고점 낙폭 판정(52주 시장 종가 기준): 알파전자 -40.0% (⛔ -30%)" in section
    # 기록 축은 시트 수집가라 경보·관찰용이다 — 발동 판정으로 읽히면 안 된다(my_rules 「적용 방법」)
    assert "기록 낙폭 경보(시트 수집가 기준 · 발동 판정 아님):" in section
    assert "기록 낙폭 판정" not in section
    assert "알파전자 -12.5% (🟡 -10%)" in section
    assert "같은 룰 번호로 합산하지 않고" in section
    # 각주 = 기록 구간·개수·제외 파일 수
    assert "스냅샷 22개(2026-08-09~2026-09-03)" in section
    assert "1개는 형식이 달라 제외" in section
    assert "매도 후 재매수 구간도 구분하지 않고" in section


def test_append_section_removes_legacy_title(tmp_path: Path) -> None:
    """옛 제목으로 쓰인 스냅샷에 재실행해도 섹션이 둘로 갈라지지 않는다."""
    module = load_script("daily-digest", "peak_drawdown")
    snapshot = tmp_path / "2026-08-28.md"
    snapshot.write_text(
        PEAK_SNAPSHOT
        + f"\n{module.LEGACY_SECTION_TITLES[0]}\n\n| 종목 | 낙폭 |\n| --- | ---: |\n"
        "| 알파전자 | -45.3% |\n",
        encoding="utf-8",
    )

    module.append_section(snapshot, f"{module.SECTION_TITLE}\n\n- 새 내용\n")
    text = snapshot.read_text(encoding="utf-8")

    assert text.count(module.SECTION_TITLE) == 1
    assert module.LEGACY_SECTION_TITLES[0] not in text
    assert "-45.3%" not in text  # 옛 섹션 본문까지 걷어낸다
    assert "매매규칙 6(-15% 손절)" in text  # 다른 섹션은 보존


# --- MDD 상태 재생·저장 규칙 ----------------------------------------------------

_HEALTHY_ACCOUNT = {"band": None, "first_breaches": {}}


def test_mdd_state_written_for_a_later_day_is_ignored_and_not_saved() -> None:
    """과거 날짜 재실행은 그보다 뒤 날짜의 상태를 물려받지도, 덮어쓰지도 않는다."""
    module = load_script("daily-digest", "peak_drawdown")
    later = {
        "schema_version": 1,
        "account_mdd": {"asof": "2026-09-10", "first_10_breach_date": "2026-09-05", "resume_authorized": False},
    }

    state, should_save = module.update_mdd_state(_HEALTHY_ACCOUNT, later, "2026-08-28", [])

    assert should_save is False
    assert state["account_mdd"]["asof"] == "2026-08-28"
    assert "first_10_breach_date" not in state["account_mdd"]
    assert state["account_mdd"]["pause_active"] is False


def test_unchanged_mdd_state_is_not_saved() -> None:
    """같은 날 재실행이 같은 상태를 만들면 파일을 다시 쓰지 않는다."""
    module = load_script("daily-digest", "peak_drawdown")
    first, _ = module.update_mdd_state(_HEALTHY_ACCOUNT, {"schema_version": 1, "account_mdd": {}}, "2026-08-28", [])

    again, should_save = module.update_mdd_state(_HEALTHY_ACCOUNT, first, "2026-08-28", [])

    assert again == first
    assert should_save is False


def test_changed_same_day_mdd_state_is_saved() -> None:
    """같은 날 -10%를 처음 밟으면 최초 발동일이 기록되고 저장된다."""
    module = load_script("daily-digest", "peak_drawdown")
    previous, _ = module.update_mdd_state(_HEALTHY_ACCOUNT, {"schema_version": 1, "account_mdd": {}}, "2026-08-28", [])

    state, should_save = module.update_mdd_state({"band": -10.0, "first_breaches": {}}, previous, "2026-08-28", [])

    assert should_save is True
    assert state["account_mdd"]["first_10_breach_date"] == "2026-08-28"
    assert state["account_mdd"]["pause_active"] is True


def test_fresh_mdd_state_is_saved_on_first_run() -> None:
    module = load_script("daily-digest", "peak_drawdown")

    _, should_save = module.update_mdd_state(_HEALTHY_ACCOUNT, {"schema_version": 1, "account_mdd": {}}, "2026-08-28", [])

    assert should_save is True


def test_append_section_failed_write_leaves_snapshot_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """쓰기가 중간에 실패해도 스냅샷 원본은 그대로다 — 반쯤 쓰인 파일이 남지 않는다."""
    from invagent.core import files

    module = load_script("daily-digest", "peak_drawdown")
    snapshot = tmp_path / "2026-08-28.md"
    snapshot.write_text(PEAK_SNAPSHOT, encoding="utf-8")

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(files.os, "replace", broken_replace)

    with pytest.raises(OSError):
        module.append_section(snapshot, f"{module.SECTION_TITLE}\n\n- 새 내용\n")

    assert snapshot.read_text(encoding="utf-8") == PEAK_SNAPSHOT
    assert [p.name for p in tmp_path.iterdir()] == [snapshot.name]


def test_peak_drawdown_reads_the_종목명_snapshot_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """종목 열이 `종목명`인 세대의 스냅샷도 죽지 않고 종목별로 판정한다."""
    module = load_script("daily-digest", "peak_drawdown")
    monkeypatch.setattr(
        datafeed_tickers, "resolve_stock", lambda name, overrides=None: ({"stock_code": "000660"}, None, 0)
    )
    monkeypatch.setattr(
        module.daily,
        "fetch_daily_bars",
        lambda code, days, asof=None: Bars(_bars([("20260601", 100_000.0), ("20260828", 60_000.0)])),
    )
    snapshot = PEAK_SNAPSHOT.replace("| 종목 | 섹터 |", "| 종목명 | 섹터 |")

    results = module.analyze_holdings(module.parse_holdings(snapshot), {})

    assert [r["종목"] for r in results] == ["알파전자", "베타파마"]
    assert results[0]["drawdown"] == pytest.approx(-40.0)


def test_record_history_includes_the_종목명_snapshot_generation(tmp_path: Path) -> None:
    """종목 열이 `종목명`인 세대의 스냅샷도 기록 축 이력에 들어간다."""
    module = load_script("daily-digest", "peak_drawdown")
    _write_snapshot(
        tmp_path,
        "2026-08-10",
        _record_snapshot("2026-08-10", {"알파전자": 355_000}).replace("| 종목 | 섹터 |", "| 종목명 | 섹터 |"),
    )
    _write_snapshot(tmp_path, "2026-09-03", _record_snapshot("2026-09-03", {"알파전자": 283_000}))

    history, _ = module.load_record_history(tmp_path)

    assert history["알파전자"] == [("2026-08-10", 355_000.0), ("2026-09-03", 283_000.0)]
