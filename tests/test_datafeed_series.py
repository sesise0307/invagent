"""`invagent.datafeed.series` — 일봉 시리즈 도구."""

from invagent.datafeed import series


def test_sma_is_none_until_the_window_is_full() -> None:
    assert series.sma([1.0, 2.0, 3.0], 3) == [None, None, 2.0]


def test_sma_averages_the_trailing_window() -> None:
    assert series.sma([2.0, 4.0, 6.0, 8.0], 2) == [None, 3.0, 5.0, 7.0]
