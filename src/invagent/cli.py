"""CLI commands for invagent."""

import asyncio
import click
from datetime import datetime
from pathlib import Path

from invagent.core.config import Config
from invagent.core.auth import authenticate
from invagent.core.client import TelegramClientManager
from invagent.telegram import MessageFetcher, PDFDownloader, LinkExtractor
from invagent.parsers import PDFNamer
from invagent.tracking.stock_tracker import StockTracker

# Default channels for PDF download
DEFAULT_CHANNELS = [
    "소중한추억.",
    "선진짱 주식공부방",
    "리포트 갤러리",
    "영리한타이거의 주식공부방",
]


@click.group()
def cli():
    """invagent - Investment decision support tool."""
    pass


@cli.command()
def authenticate_cmd():
    """Authenticate with Telegram API.

    Generates ~/.telegram_session file for future API calls.
    """
    try:
        config = Config.from_env()
        click.echo("🔐 Starting Telegram authentication...")
        asyncio.run(authenticate(config))
    except ValueError as e:
        click.echo(f"❌ Configuration Error: {e}", err=True)
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"❌ Authentication failed: {e}", err=True)
        raise SystemExit(1)


@cli.command()
@click.option("--days", type=int, default=1, help="Fetch messages from last N days (default: 1)")
@click.option("--fetch-links", is_flag=True, help="Extract and include link content")
def fetch_messages_cmd(days, fetch_links):
    """Fetch saved messages from Telegram.

    Retrieves messages from Telegram 'Saved Messages' channel.
    """
    try:
        config = Config.from_env()
        click.echo(f"📨 Fetching messages from last {days} day(s)...")

        client_manager = TelegramClientManager(config)
        fetcher = MessageFetcher(config, client_manager)

        messages = asyncio.run(fetcher.fetch_saved_messages(days, fetch_links=fetch_links))

        # Print messages
        for msg in messages:
            click.echo(f"\n📝 {msg['date']}")
            click.echo(f"   {msg['text']}")
            if "links_content" in msg and msg["links_content"]:
                click.echo(f"   📎 Links:")
                for link in msg["links_content"]:
                    click.echo(f"      {link}")

    except ValueError as e:
        click.echo(f"❌ Configuration Error: {e}", err=True)
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        raise SystemExit(1)


@cli.command()
@click.option("--days", type=int, default=1, help="Download PDFs from last N days (default: 1)")
@click.option(
    "--channels",
    multiple=True,
    default=DEFAULT_CHANNELS,
    help=f"Channel names to download from (default: {', '.join(DEFAULT_CHANNELS)})"
)
def download_pdfs_cmd(days, channels):
    """Download PDF reports from Telegram channels.

    Saves PDFs to output/reports/<YYYY-MM-DD>/ directory.
    """
    try:
        config = Config.from_env()

        # Use provided channels or defaults
        channels_to_use = list(channels) if channels else DEFAULT_CHANNELS

        click.echo(f"📥 Downloading PDFs from {len(channels_to_use)} channel(s)...")
        click.echo(f"   Period: last {days} day(s)")

        client_manager = TelegramClientManager(config)
        pdf_namer = PDFNamer()
        downloader = PDFDownloader(config, client_manager, pdf_namer)

        results = asyncio.run(downloader.download_pdfs(channels_to_use, days))

        # Print summary
        total_pdfs = sum(len(files) for files in results.values())
        click.echo(f"\n✅ Downloaded {total_pdfs} PDF(s)")
        for channel, files in results.items():
            if files:
                click.echo(f"\n   {channel}:")
                for file_info in files:
                    click.echo(f"      ✓ {file_info}")

    except ValueError as e:
        click.echo(f"❌ Configuration Error: {e}", err=True)
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        raise SystemExit(1)


@cli.command()
@click.argument("ticker")
@click.option("--name", required=True, help="Company name")
@click.option("--sector", required=True, help="Industry sector")
def add_stock_cmd(ticker, name, sector):
    """Add a new stock to track.

    TICKER: Stock ticker symbol (e.g., AAPL)
    """
    try:
        tracker = StockTracker()

        if ticker in tracker.stocks:
            click.echo(f"❌ Stock {ticker} already exists", err=True)
            raise SystemExit(1)

        tracker.add_stock(ticker, name, sector)
        tracker.save()

        click.echo(f"✅ Added stock: {ticker} ({name}) - {sector}")

    except ValueError as e:
        click.echo(f"❌ Error: {e}", err=True)
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        raise SystemExit(1)


@cli.command()
@click.argument("ticker")
@click.option("--price", type=float, required=True, help="Target price")
@click.option("--date", type=click.DateTime(formats=["%Y-%m-%d"]), default=None, help="Date (YYYY-MM-DD, default: today)")
def set_target_cmd(ticker, price, date):
    """Set a target price for a stock.

    TICKER: Stock ticker symbol
    """
    try:
        tracker = StockTracker()

        if ticker not in tracker.stocks:
            click.echo(f"❌ Stock {ticker} not found", err=True)
            raise SystemExit(1)

        # Use provided date or today
        target_date = date if date else datetime.now()

        tracker.set_target_price(ticker, price, target_date)
        tracker.save()

        date_str = target_date.strftime("%Y-%m-%d")
        click.echo(f"✅ Set target price: {ticker} = {price:,.0f} ({date_str})")

    except ValueError as e:
        click.echo(f"❌ Error: {e}", err=True)
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        raise SystemExit(1)


@cli.command()
@click.argument("ticker")
def show_stock_cmd(ticker):
    """Show stock information and target price history.

    TICKER: Stock ticker symbol
    """
    try:
        tracker = StockTracker()

        if ticker not in tracker.stocks:
            click.echo(f"❌ Stock {ticker} not found", err=True)
            raise SystemExit(1)

        stock = tracker.stocks[ticker]

        click.echo(f"\n📊 Stock: {ticker}")
        click.echo(f"   Name: {stock['name']}")
        click.echo(f"   Sector: {stock['sector']}")

        target_prices = stock.get("target_prices", [])
        if target_prices:
            click.echo(f"\n   Target Price History:")
            for entry in target_prices:
                click.echo(f"      {entry['date']}: {entry['price']:,.0f}")
        else:
            click.echo(f"\n   No target prices set yet")

        click.echo()

    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        raise SystemExit(1)


# Alias old function names to new command names for CLI
cli.add_command(authenticate_cmd, name="authenticate")
cli.add_command(fetch_messages_cmd, name="fetch-messages")
cli.add_command(download_pdfs_cmd, name="download-pdfs")
cli.add_command(add_stock_cmd, name="add-stock")
cli.add_command(set_target_cmd, name="set-target")
cli.add_command(show_stock_cmd, name="show-stock")


if __name__ == "__main__":
    cli()
