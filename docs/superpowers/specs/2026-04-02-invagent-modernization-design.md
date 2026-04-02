# invagent 패키지 현대화 설계 문서

**작성일:** 2026-04-02  
**목표:** 코드 구조 개선 + 파일명 통일 + 새 기능(주가 추적) 추가

---

## 1. 전체 아키텍처

```
src/invagent/
├── __init__.py
├── core/                    # 공통 기반
│   ├── __init__.py
│   ├── config.py           # 환경변수, 설정 관리
│   ├── auth.py             # Telegram 인증
│   └── client.py           # TelegramClient 래퍼
│
├── telegram/               # Telegram 연동 모듈
│   ├── __init__.py
│   ├── fetch.py            # 저장된 메시지 조회
│   ├── downloader.py       # PDF 다운로드
│   └── link_extractor.py   # URL 추출, 내용 fetch
│
├── parsers/                # 파일명 규칙 기반 파싱
│   ├── __init__.py
│   └── pdf_namer.py        # 채널별 파일명 규칙
│
├── tracking/               # 종목/주가 추적 (새 기능)
│   ├── __init__.py
│   └── stock_tracker.py    # 종목, 목표주가 관리
│
└── cli.py                  # CLI 인터페이스
```

---

## 2. 모듈별 상세 설계

### 2.1 `core/` 모듈

**목표:** 모든 모듈이 공유하는 인증, 설정, Telegram 클라이언트

#### `core/config.py`
- `Config` 클래스: 환경변수 읽기, 기본값 제공, 검증
- 필드: `api_id`, `api_hash`, `session_path`, `output_dir`
- 메서드: `from_env()` 클래스 메서드

#### `core/auth.py`
- `authenticate(config: Config) -> TelegramClient` 함수
- 현재 `auth_telegram.py` 코드를 통합
- 1회 인증 후 세션 파일 생성

#### `core/client.py`
- `TelegramClientManager` 클래스 (싱글톤 패턴)
- `get_client(config: Config) -> TelegramClient` 메서드
- 여러 모듈이 동시에 재사용 가능하도록 관리

### 2.2 `telegram/` 모듈

**목표:** 현재 기능(메시지 fetch, PDF 다운로드, 링크 추출) 분리

#### `telegram/link_extractor.py`
- `LinkExtractor` 클래스
- `extract_urls(text: str) -> list[str]`: URL 추출
- `fetch_content(url: str) -> str`: URL 내용 추출
- `extract_and_fetch(text: str) -> dict`: 한 번에 처리

#### `telegram/fetch.py`
- `MessageFetcher` 클래스
- `__init__(config, client_manager)`: 초기화
- `fetch_saved_messages(days, fetch_links) -> list[dict]`: 저장된 메시지 조회
- `format_messages(messages) -> str`: 메시지 포맷팅

#### `telegram/downloader.py`
- `PDFDownloader` 클래스
- `__init__(config, client_manager, pdf_namer)`: 초기화
- `download_pdfs(channels, days) -> dict`: PDF 다운로드
- `_get_output_path()`: pdf_namer를 통한 파일명 생성

### 2.3 `parsers/` 모듈

**목표:** 채널별 파일명 규칙을 플러그인처럼 관리

#### `parsers/pdf_namer.py`
- `PDFNamer` 클래스
- `rules` dict: 채널별 Rule 인스턴스 관리
- `generate_filename(channel, original_filename, date) -> str`: 파일명 생성

- `Rule` base 클래스
  - `parse_and_generate(filename, date) -> str`: 채널별 구현

- `ReportGalleryRule`, `PreciousMemoryRule` 등: 채널별 구현
  - 실제 파일명 예시 받으면 구현
  - 규칙 없는 채널은 기본 패턴 사용

### 2.4 `tracking/` 모듈

**목표:** 종목별 목표 주가, 투자 의견 트랙킹

#### `tracking/stock_tracker.py`
- `StockTracker` 클래스
- `add_stock(ticker, name, sector)`: 종목 추가
- `set_target_price(ticker, price, date)`: 목표 주가 설정
- `get_target_history(ticker) -> list[dict]`: 이력 조회
- `save()`: 파일에 저장
- 데이터 파일: `context/stocks.json`

### 2.5 `cli.py`

**목표:** 모든 기능을 CLI 커맨드로 노출

- `click` 라이브러리 사용
- 커맨드:
  - `fetch-messages`: 저장된 메시지 조회
  - `download-pdfs`: PDF 다운로드 (파일명 통일)
  - `authenticate`: 텔레그램 인증
  - `set-target`: 종목 목표 주가 설정

---

## 3. 구현 순서

### Phase 1: 기반 모듈 (core)
1. `core/config.py` — 설정 중앙화
2. `core/auth.py` — 현재 auth_telegram.py 통합
3. `core/client.py` — TelegramClient 싱글톤 관리

### Phase 2: Telegram 연동 (telegram)
4. `telegram/link_extractor.py` — 링크 처리 분리
5. `telegram/fetch.py` — MessageFetcher 작성
6. `telegram/downloader.py` — PDFDownloader 작성

### Phase 3: 파일명 규칙 (parsers)
7. `parsers/pdf_namer.py` — 기본 규칙 + 플러그인 구조

### Phase 4: 새 기능 (tracking)
8. `tracking/stock_tracker.py` — 종목 추적

### Phase 5: CLI + 통합
9. `cli.py` — 모든 기능 CLI로 노출
10. `__init__.py` 정리 — 공개 API 정의

---

## 4. 주요 설계 결정

| 결정 | 이유 |
|------|------|
| 계층화된 구조 (B 옵션) | 새 기능 추가 시 기존 코드 건드릴 필요 없음 |
| Rule 패턴 (parsers) | 채널별 규칙 추가/수정이 쉬움 |
| 싱글톤 (TelegramClientManager) | 동시성 문제 방지, 재사용성 높음 |
| JSON 데이터 저장 (tracking) | 간단하고 확장성 있음 |

---

## 5. 시간 예상

- **Phase 1-2:** 1-2일 (기존 코드 리팩토링)
- **Phase 3:** 0.5일 (파일명 규칙 기본 구조)
- **Phase 4:** 0.5일 (새 기능 기본 구현)
- **Phase 5:** 0.5일 (CLI + 통합 테스트)

**총 예상:** 3일 (이번 주말/다음주 안에 완료 가능)

---

## 6. 향후 확장 포인트

- **추가 규칙:** `parsers/` 에 Rule 클래스 추가만으로 OK
- **종목 뉴스 추적:** `tracking/` 에 뉴스 모듈 추가
- **공시 추적:** 마찬가지로 tracking에 추가
- **투자 조언:** 별도 모듈로 추가 가능
