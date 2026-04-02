"""Test that all public API imports work correctly."""


def test_imports():
    """모든 공개 API import 가능"""
    from invagent import (
        Config,
        TelegramClientManager,
        authenticate,
        MessageFetcher,
        PDFDownloader,
        LinkExtractor,
        PDFNamer,
        StockTracker,
    )

    assert Config is not None
    assert TelegramClientManager is not None
    assert authenticate is not None
    assert MessageFetcher is not None
    assert PDFDownloader is not None
    assert LinkExtractor is not None
    assert PDFNamer is not None
    assert StockTracker is not None


def test_version():
    """Version is accessible"""
    from invagent import __version__

    assert __version__ == "0.2.0"
    assert isinstance(__version__, str)
