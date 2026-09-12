"""`invagent.datafeed.series` — 일봉 시리즈 도구."""

from invagent.datafeed import series


def test_sma_is_none_until_the_window_is_full() -> None:
    assert series.sma([1.0, 2.0, 3.0], 3) == [None, None, 2.0]


def test_sma_averages_the_trailing_window() -> None:
    assert series.sma([2.0, 4.0, 6.0, 8.0], 2) == [None, 3.0, 5.0, 7.0]


def test_swing_pivots_can_read_closes_instead_of_the_intraday_range() -> None:
    """종가 기준 판정은 스윙도 종가로 잡는다 — 장중 고저가 평평해도 종가의 굴곡을 읽는다."""
    closes = [1, 2, 3, 2, 1, 2, 3, 4, 3, 2, 1]
    bars = [{"date": str(i), "high": 9, "low": 0, "close": c} for i, c in enumerate(closes)]

    highs, lows = series.swing_pivots(bars, 2, high_key="close", low_key="close")

    assert [(p["index"], p["price"]) for p in highs] == [(2, 3), (7, 4)]
    assert [(p["index"], p["price"]) for p in lows] == [(4, 1)]
    # 마지막 k봉은 오른쪽 창이 안 차서 확정되지 않는다.
    assert all(p["index"] <= len(bars) - 1 - 2 for p in highs + lows)
