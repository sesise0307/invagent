# Invagent

Personal CLI tooling for collecting Telegram investment notes, downloading PDF reports, and tracking stock targets.

## Requirements

- Python 3.12+
- `uv`
- Telegram API credentials from https://my.telegram.org

## Setup

```bash
uv sync
cp .env.example .env
```

Set these environment variables before running commands:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`

Optional overrides:

- `TELEGRAM_SESSION_PATH`
- `INVAGENT_OUTPUT_DIR`
- `INVAGENT_DEFAULT_CHANNELS` as a comma-separated list

## CLI

Authenticate once:

```bash
uv run invagent authenticate
```

Export saved messages from the last day:

```bash
uv run invagent fetch-messages --days 1
```

Download recent PDF reports:

```bash
uv run invagent download-pdfs --days 1
```

Track a stock and target price:

```bash
uv run invagent add-stock AAPL --name Apple --sector Technology
uv run invagent set-target AAPL --price 250 --date 2026-04-04
uv run invagent show-stock AAPL
```

## Output Layout

```text
output/
  telegram-daily/
    raw/
      YYYY-MM-DD_raw.md           # 원본 (오늘자만 유지, 브리핑 후 자동 정리)
    YYYY-MM/
      YYYY-MM-DD.md               # 일일 브리핑 (월별 서브 디렉토리)
    monthly_context.md            # 월간 누적 컨텍스트
  reports/
    YYYY-MM-DD/
      *.pdf
context/
  stocks.json
```

## Test

```bash
uv run pytest -q
```
