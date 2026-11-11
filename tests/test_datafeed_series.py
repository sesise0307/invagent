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


def test_ma_cross_reports_a_close_crossing_up_through_its_average_today() -> None:
    # 3일선: 어제 (10+10+9)/3 = 9.67 ≥ 종가 9 → 아래. 오늘 (10+9+12)/3 = 10.33 < 12 → 위로 돌파.
    cross = series.ma_cross([10.0, 10.0, 9.0, 12.0], 3)

    assert cross["event"] == "up"
    assert cross["above"] is True


def test_ma_cross_reports_a_close_falling_below_its_average_today() -> None:
    # 어제 (9+10+12)/3 = 10.33 < 12 → 위. 오늘 (10+12+8)/3 = 10 > 8 → 아래로 하회.
    cross = series.ma_cross([9.0, 10.0, 12.0, 8.0], 3)

    assert cross["event"] == "down"
    assert cross["above"] is False


def test_ma_cross_a_close_landing_exactly_on_the_average_is_not_a_cross() -> None:
    """종가가 이평선과 같으면 위도 아래도 아니다 — 하회 이벤트를 내지 않는다."""
    # 어제 위(12 > 10.33). 오늘 3일선 (10+12+11)/3 = 11 = 종가 11.
    cross = series.ma_cross([9.0, 10.0, 12.0, 11.0], 3)

    assert cross["event"] is None


def test_ma_cross_without_yesterdays_average_is_undecided() -> None:
    """돌파는 어제와 오늘의 위치 비교다 — 이평선이 어제 없으면 판정하지 않는다."""
    cross = series.ma_cross([10.0, 10.0, 12.0], 3)

    assert cross["insufficient"] is True
    assert cross["event"] is None
