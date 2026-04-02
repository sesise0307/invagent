"""invagent - 투자 의사 결정 보조 도구"""

__version__ = "0.2.0"

# Core
from .core import Config, TelegramClientManager, authenticate

# Telegram
from .telegram import MessageFetcher, PDFDownloader, LinkExtractor

# Parsers
from .parsers import PDFNamer

# Tracking
from .tracking import StockTracker

__all__ = [
    # Version
    "__version__",

    # Core
    "Config",
    "TelegramClientManager",
    "authenticate",

    # Telegram
    "MessageFetcher",
    "PDFDownloader",
    "LinkExtractor",

    # Parsers
    "PDFNamer",

    # Tracking
    "StockTracker",
]
