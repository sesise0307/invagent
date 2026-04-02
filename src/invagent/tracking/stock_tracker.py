"""Stock price tracking module."""

import json
from pathlib import Path
from datetime import datetime


class StockTracker:
    """Track stock information and target prices."""

    def __init__(self, data_file: Path = None):
        """Initialize StockTracker.

        Args:
            data_file: Path to the JSON file for storing stock data.
                      Defaults to context/stocks.json
        """
        if data_file is None:
            data_file = Path("context/stocks.json")
        self.data_file = Path(data_file)
        self.stocks = self._load_stocks()

    def add_stock(self, ticker: str, name: str, sector: str) -> None:
        """Add a new stock to track.

        Args:
            ticker: Stock ticker symbol (e.g., "AAPL")
            name: Company name (e.g., "Apple")
            sector: Industry sector (e.g., "기술")

        Raises:
            ValueError: If stock already exists
        """
        if ticker in self.stocks:
            raise ValueError(f"Stock {ticker} already exists")

        self.stocks[ticker] = {
            "name": name,
            "sector": sector,
            "target_prices": [],
        }

    def set_target_price(self, ticker: str, price: float, date: datetime) -> None:
        """Set a target price for a stock.

        Args:
            ticker: Stock ticker symbol
            price: Target price
            date: Date of the target price setting

        Raises:
            ValueError: If stock does not exist
        """
        if ticker not in self.stocks:
            raise ValueError(f"Stock {ticker} not found")

        # Format date as "%Y-%m-%d"
        date_str = date.strftime("%Y-%m-%d")

        self.stocks[ticker]["target_prices"].append({
            "date": date_str,
            "price": price,
        })

    def get_target_history(self, ticker: str) -> list[dict]:
        """Get target price history for a stock.

        Args:
            ticker: Stock ticker symbol

        Returns:
            List of dicts with "date" and "price" keys.
            Empty list if stock not found.
        """
        if ticker not in self.stocks:
            return []

        return self.stocks[ticker]["target_prices"]

    def save(self) -> None:
        """Save stocks data to JSON file.

        Creates parent directories if they don't exist.
        """
        # Create parent directory if it doesn't exist
        self.data_file.parent.mkdir(parents=True, exist_ok=True)

        # Save to JSON file with UTF-8 encoding
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(self.stocks, f, ensure_ascii=False, indent=2)

    def _load_stocks(self) -> dict:
        """Load stocks data from JSON file.

        Returns:
            Dictionary of stocks. Empty dict if file doesn't exist.
        """
        if not self.data_file.exists():
            return {}

        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}
