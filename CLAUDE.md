# CLAUDE.md

이 프로젝트는 나의 투자 의사 결정을 돕는 도구들로 구성되어 있다.
작성된 코드 및 스크립트는 `invagent` 파이썬 패키지(v0.2.0)안에 포함된다.

**최근 업데이트 (2026-04-03):** CLI entry point 추가 — `uv run invagent <command>` 형태로 사용 가능.

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
- `click` >=8.0 - CLI framework
- `telethon` - Telegram client library
- `requests`, `beautifulsoup4` - For fetching link content

These are automatically installed with `uv sync`.

## Project Structure

```
src/invagent/
├── core/                    # 기반 모듈 (설정, 인증, 클라이언트)
│   ├── config.py           # Config - 환경변수 기반 설정 관리
│   ├── auth.py             # authenticate() - Telegram 1회 인증
│   ├── client.py           # TelegramClientManager - 싱글톤 클라이언트
│   └── __init__.py
├── telegram/               # Telegram 연동 모듈
│   ├── link_extractor.py   # LinkExtractor - URL 추출 및 내용 fetch
│   ├── fetch.py            # MessageFetcher - 저장된 메시지 조회
│   ├── downloader.py       # PDFDownloader - PDF 다운로드
│   └── __init__.py
├── parsers/                # 파일명 규칙 모듈 (확장 가능)
│   ├── pdf_namer.py        # PDFNamer - 채널별 파일명 규칙화
│   └── __init__.py
├── tracking/               # 주가 추적 모듈
│   ├── stock_tracker.py    # StockTracker - 종목/목표주가 관리
│   └── __init__.py
├── cli.py                  # Click 기반 CLI 인터페이스 (7개 커맨드)
└── __init__.py             # 공개 API 정의 (v0.2.0)
```

**기타:**
- `.claude/settings.local.json` - Local Claude Code configuration
- `pyproject.toml` - Package metadata and dependencies
- `context/` - Contexts, e.g., 관심 종목
- `template/` - Contains output templates
- `output/` - Outputs will be located here
- `docs/superpowers/` - 설계 문서 및 구현 계획

## Running Features

모든 기능은 CLI를 통해 실행됩니다. 환경변수 설정 후 아래 커맨드를 사용하세요.

**환경변수 설정:**
```bash
export TELEGRAM_API_ID="your_api_id"
export TELEGRAM_API_HASH="your_api_hash"
```

**1. 텔레그램 인증 (1회만):**
```bash
uv run invagent authenticate
```

**2. 저장된 메시지 조회:**
```bash
uv run invagent fetch-messages --days 1 --fetch-links
```
- `--days N`: 최근 N일치 메시지 (기본: 1)
- `--fetch-links`: 링크 내용 자동 추출 (선택사항)

**3. PDF 다운로드:**
```bash
uv run invagent download-pdfs --days 7
```
- `--days N`: 최근 N일치 PDF (기본: 1)
- `--channels ch1 ch2`: 특정 채널만 다운로드 (기본: 4개 채널)

**4. 종목 관리:**
```bash
# 종목 추가
uv run invagent add-stock AAPL --name "Apple" --sector "기술"

# 목표 주가 설정
uv run invagent set-target AAPL --price 180.0

# 종목 정보 조회
uv run invagent show-stock AAPL
```

**모든 커맨드 보기:**
```bash
uv run invagent --help
```

다운로드 결과는 `outputs/reports/{date}/` 디렉토리에 저장됩니다.

## Python API

CLI 외에도 Python 코드에서 직접 invagent 모듈을 import해서 사용할 수 있습니다.

```python
from invagent import (
    # Core 모듈
    Config, TelegramClientManager, authenticate,
    # Telegram 모듈
    MessageFetcher, PDFDownloader, LinkExtractor,
    # Parsers 모듈
    PDFNamer,
    # Tracking 모듈
    StockTracker,
)

# 예: 메시지 직접 조회
config = Config.from_env()
manager = TelegramClientManager()
fetcher = MessageFetcher(config, manager)

messages = asyncio.run(fetcher.fetch_saved_messages(days=1, fetch_links=True))
print(fetcher.format_messages(messages))

# 예: 종목 추적
tracker = StockTracker()
tracker.add_stock("AAPL", "Apple", "기술")
tracker.set_target_price("AAPL", 150.0, datetime.now())
tracker.save()
```

자세한 API 문서는 각 모듈의 docstring을 참고하세요.

## Development Workflow

### 새 기능 추가 방법

**1. 새 모듈 추가 (예: 뉴스 추적)**
```
src/invagent/news/
├── __init__.py
├── fetcher.py       # NewsFetcher 클래스
└── (테스트는 tests/test_news_*.py)
```

**2. 기존 모듈 확장 (예: PDFNamer 규칙 추가)**
- `src/invagent/parsers/pdf_namer.py`에서 새 Rule 클래스 추가
- 테스트: `tests/test_parsers_pdf_namer.py`

**3. CLI 커맨드 추가**
- `src/invagent/cli.py`에 `@cli.command()` 추가
- 테스트: `tests/test_cli.py`

### 개발 프로세스

1. Feature branch 생성: `git checkout -b feature/your-feature`
2. TDD로 개발 (테스트 먼저 작성)
3. 모든 테스트 통과 확인: `pytest tests/ -v`
4. 커밋 및 PR
5. Main branch로 merge

### 테스트 실행

```bash
# 전체 테스트 (33개)
pytest tests/ -v

# 특정 모듈만
pytest tests/test_core_config.py -v

# 커버리지
pytest tests/ --cov=src/invagent
```

### Architecture Principles

- **Core 모듈**: 외부 의존성 최소화 (다른 모듈에서 재사용)
- **계층 구조**: core → telegram → parsers → tracking → cli
- **테스트**: 모든 공개 API는 테스트로 검증
- **Type hints**: 모든 함수에 타입 주석 필수
- **문서화**: 클래스/함수 docstring 작성

## Version History

### v0.2.0 (2026-04-02) - 패키지 현대화

**주요 변경:**
- 스크립트 기반 → 모듈 기반 아키텍처로 리팩토링
- 계층화된 구조: `core` → `telegram` → `parsers` → `tracking` → `cli`
- Click 기반 CLI 인터페이스 (7개 커맨드)
- 새로운 기능:
  - `StockTracker`: 종목별 목표주가 추적
  - `PDFNamer`: 파일명 규칙화 시스템 (플러그인 방식)
  - `TelegramClientManager`: 싱글톤 클라이언트 관리

**개선:**
- 테스트 커버리지: 33개 테스트 (100% 통과)
- Type hints: 완전 적용
- 중복 제거: 공통 기능 core 모듈화

**마이그레이션 가이드:**
```bash
# 기존 (v0.1.0)
uv run python -m invagent.fetch_telegram --days 1 --fetch-links

# v0.2.0
uv run python -m invagent.cli fetch-messages --days 1 --fetch-links

# v0.2.1+ (현재)
uv run invagent fetch-messages --days 1 --fetch-links
```

**설계 문서:**
- [설계 스펙](docs/superpowers/specs/2026-04-02-invagent-modernization-design.md)
- [구현 계획](docs/superpowers/plans/2026-04-02-invagent-modernization-implementation.md)

### v0.2.1 (2026-04-03) - CLI entry point 추가

- `pyproject.toml`에 `[project.scripts]` 추가: `invagent = "invagent.cli:cli"`
- `uv run invagent <command>` 형태로 직접 실행 가능

### v0.1.0 (초기)

- 기본 Telegram 메시지 조회 및 PDF 다운로드 스크립트
