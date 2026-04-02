# CLAUDE.md

이 프로젝트는 나의 투자 의사 결정을 돕는 도구들로 구성되어 있다.
작성된 코드 및 스크립트는 `invagent` 파이썬 패키지안에 포함된다.

## Features

- 텔레그램 "저장된 메시지" 저장 및 핵심 요약
- 텔레그램에서 PDF 형태의 리포트 일괄 다운로드
- 종목별 목표 주가 및 투자 의견 변경 트랙킹
- 관심 종목 뉴스 및 공시 트랙킹
- 투자 대가들의 관점에서 투자 조언

## Quick Start

```bash
# Install dependencies
uv sync

# Verify installation
uv run python -c "import invagent; print('invagent ready')"
```

## Environment Setup

The project requires Telegram API credentials to function. Set these environment variables:

```bash
export TELEGRAM_API_ID="your_api_id"
export TELEGRAM_API_HASH="your_api_hash"
```

Get credentials from [Telegram's API documentation](https://core.telegram.org/api/obtaining_api_id).

**Session File:** First run will create `~/.telegram_session` for authentication. Delete this file if you need to re-authenticate.

## Dependencies

**Required:**

- Python >=3.12
- `uv` (package manager)

**Optional (for Telegram features):**

- `telethon` - Telegram client library
- `requests`, `beautifulsoup4` - For fetching link content in `fetch_telegram.py`

Install all optional deps: `uv add telethon requests beautifulsoup4`

## Project Structure

- `src/invagent/` - Main Python package
- `.claude/settings.local.json` - Local Claude Code configuration
- `pyproject.toml` - Package metadata and dependencies
- `context/` - Contexts, e.g., 관심 종목
- `template/` - Contains output templates
- `output/` - Outputs will be located here

## Running Features

**Fetch saved messages from Telegram:**

```bash
uv run python -m invagent.fetch_telegram --days 1 --fetch-links
```

- `--days N`: Fetch messages from last N days (default: 1)
- `--fetch-links`: Extract and include content from links in messages

**Download PDF reports from Telegram channels:**

```bash
uv run python -m invagent.download_telegram_pdfs --days 1
```

- `--days N`: Download PDFs from last N days (default: 1)
- `--channels ch1 ch2`: Specify channels (default: predefined list)

Outputs saved to `output/` directory.

## Development Workflow

- Work on feature branches (not main)
- Commit changes with clear messages
- Submit PRs for review before merging to main
