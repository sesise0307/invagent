"""CLI commands for invagent."""

import asyncio
from datetime import datetime

import click

from invagent.core.config import Config
from invagent.core.auth import authenticate
from invagent.core.client import TelegramClientManager
from invagent.telegram import MessageFetcher, PDFDownloader
from invagent.parsers import PDFNamer
from invagent.tracking.stock_tracker import StockTracker


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
        asyncio.run(_authenticate_and_report(config))
    except ValueError as e:
        click.echo(f"❌ Configuration Error: {e}", err=True)
        raise SystemExit(1)
    except RuntimeError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"❌ Authentication failed: {e}", err=True)
        raise SystemExit(1)


async def _authenticate_and_report(config: Config) -> None:
    """Authenticate once and report the logged-in Telegram account."""
    client = await authenticate(config)
    try:
        me = await client.get_me()
        click.echo(f"✅ 인증 성공: {me.first_name} (@{me.username})")
        click.echo(f"세션 파일: {config.session_path}")
    finally:
        await client.disconnect()


@cli.command()
@click.option("--days", type=int, default=1, help="Fetch messages from last N days (default: 1)")
@click.option("--fetch-links", is_flag=True, default=True, help="Extract and include link content (default: enabled)")
def fetch_messages_cmd(days, fetch_links):
    """Fetch saved messages from Telegram.

    Retrieves messages from Telegram 'Saved Messages' channel.
    Saves formatted output to output/telegram-daily/<YYYY-MM-DD>_raw.txt
    """
    try:
        config = Config.from_env()
        click.echo(f"📨 Fetching messages from last {days} day(s)...")

        client_manager = TelegramClientManager()
        fetcher = MessageFetcher(config, client_manager)

        try:
            messages = asyncio.run(fetcher.fetch_saved_messages(days, fetch_links=fetch_links))
        finally:
            asyncio.run(client_manager.disconnect())

        # Format as markdown
        formatted_content = fetcher.format_messages_markdown(messages)

        # Create output directory
        output_dir = config.telegram_daily_dir()
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save to file
        today = datetime.now().strftime("%Y-%m-%d")
        output_file = output_dir / f"{today}_raw.md"
        output_file.write_text(formatted_content, encoding="utf-8")

        click.echo(f"✅ Saved: {output_file}")
        click.echo(f"   Messages: {len(messages)}")
        click.echo(f"   Links fetched: {'yes' if fetch_links else 'no'}")

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
    default=(),
    help="Channel names to download from (default: configured channel list)"
)
def download_pdfs_cmd(days, channels):
    """Download PDF reports from Telegram channels.

    Saves PDFs to output/reports/<YYYY-MM-DD>/ directory.
    """
    try:
        config = Config.from_env()

        # Use provided channels or defaults
        channels_to_use = list(channels) if channels else list(config.default_channels)

        click.echo(f"📥 Downloading PDFs from {len(channels_to_use)} channel(s)...")
        click.echo(f"   Period: last {days} day(s)")

        client_manager = TelegramClientManager()
        pdf_namer = PDFNamer()
        downloader = PDFDownloader(config, client_manager, pdf_namer)

        try:
            results = asyncio.run(downloader.download_pdfs(channels_to_use, days))
        finally:
            asyncio.run(client_manager.disconnect())

        # Print summary
        downloaded = sum(
            1
            for files in results.values()
            for item in files
            if item.get("status") == "downloaded"
        )
        skipped = sum(
            1
            for files in results.values()
            for item in files
            if item.get("status") == "skipped"
        )
        failed_channels = [
            channel for channel, files in results.items() if any(item.get("status") == "error" for item in files)
        ]

        click.echo(f"\n✅ Downloaded {downloaded} PDF(s)")
        click.echo(f"   Skipped: {skipped}")
        if failed_channels:
            click.echo(f"   Failed channels: {', '.join(failed_channels)}")

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
