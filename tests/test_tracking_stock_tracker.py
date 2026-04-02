import pytest
from pathlib import Path
from datetime import datetime
from invagent.tracking.stock_tracker import StockTracker


def test_stock_tracker_add_stock(tmp_path):
    """종목 추가"""
    data_file = tmp_path / "stocks.json"
    tracker = StockTracker(data_file=data_file)

    tracker.add_stock("AAPL", "Apple", "기술")

    assert "AAPL" in tracker.stocks
    assert tracker.stocks["AAPL"]["name"] == "Apple"
    assert tracker.stocks["AAPL"]["sector"] == "기술"


def test_stock_tracker_set_target_price(tmp_path):
    """목표 주가 설정"""
    data_file = tmp_path / "stocks.json"
    tracker = StockTracker(data_file=data_file)

    tracker.add_stock("AAPL", "Apple", "기술")
    tracker.set_target_price("AAPL", 150.0, datetime(2026, 4, 1))
    tracker.set_target_price("AAPL", 155.0, datetime(2026, 4, 2))

    history = tracker.get_target_history("AAPL")

    assert len(history) == 2
    assert history[0]["price"] == 150.0
    assert history[1]["price"] == 155.0


def test_stock_tracker_get_target_history(tmp_path):
    """목표 주가 이력 조회"""
    data_file = tmp_path / "stocks.json"
    tracker = StockTracker(data_file=data_file)

    tracker.add_stock("MSFT", "Microsoft", "기술")
    tracker.set_target_price("MSFT", 300.0, datetime(2026, 3, 1))
    tracker.set_target_price("MSFT", 310.0, datetime(2026, 3, 15))

    history = tracker.get_target_history("MSFT")

    assert len(history) == 2
    assert all("date" in h and "price" in h for h in history)


def test_stock_tracker_save_and_load(tmp_path):
    """저장 및 로드"""
    data_file = tmp_path / "stocks.json"

    # 저장
    tracker1 = StockTracker(data_file=data_file)
    tracker1.add_stock("AAPL", "Apple", "기술")
    tracker1.set_target_price("AAPL", 150.0, datetime(2026, 4, 1))
    tracker1.save()

    # 로드
    tracker2 = StockTracker(data_file=data_file)

    assert "AAPL" in tracker2.stocks
    assert tracker2.stocks["AAPL"]["name"] == "Apple"
    assert len(tracker2.get_target_history("AAPL")) == 1


def test_stock_tracker_stock_not_found(tmp_path):
    """존재하지 않는 종목 처리"""
    data_file = tmp_path / "stocks.json"
    tracker = StockTracker(data_file=data_file)

    with pytest.raises(ValueError):
        tracker.set_target_price("UNKNOWN", 100.0, datetime(2026, 4, 1))
