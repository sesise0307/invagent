---
name: market-data
description: 외부 데이터를 어디서 어떻게 가져오는지에 대한 단일 참조. 시세·멀티플·컨센서스·일봉·시장 지표·텔레그램 저장 메시지·구글 시트 포트폴리오·DART 공시를 각각 어느 경로로 수집하는지, 인증(STOCKEASY_COOKIE)은 어떻게 갱신하는지, 응답 캐시는 어떻게 동작하는지를 규정한다. 다음 상황에서 트리거하라 - "이 데이터 어디서 가져와?", "시세 어떻게 받아와", "쿠키 만료됐대", "STOCKEASY_COOKIE 갱신", "401 뜨는데", "캐시 껐다 켜기", "일봉 어디서 받아", "포트폴리오 어떻게 읽어와", "수집이 실패했는데 계속 진행해도 돼?" 다른 스킬(`analyze-stock`·`stage-analysis`·`daily-digest`·`advice`)은 수집 방법을 다시 설명하지 않고 이 파일을 참조한다. 판정과 해석은 각 스킬의 몫이다.
---

# Market Data — 외부 데이터 수집 단일 참조

이 스킬은 **어디서 무엇을 어떻게 가져오는가**만 규정한다. 가져온 값으로 무엇을 판단할지는
`analyze-stock`(종목), `stage-analysis`(국면), `daily-digest`(브리핑), `advice`(대가 관점),
`opendart`(공시)의 몫이다.

## 공통 원칙

1. **수집 실패는 비블로킹이다.** 한 소스가 실패해도 나머지로 작업을 계속하고, 실패한 항목은
   "미수집"으로 자리를 남긴다. 실패를 0이나 빈 값으로 바꾸지 않는다.
2. **웹 검색으로 대체하지 않는다.** 지정된 경로가 죽었으면 미수집이다. 다른 출처의 숫자를
   같은 자리에 끼워 넣으면 근거의 출처가 섞인다.
3. **차단은 우회하지 않는다.** 403·401이 예상된 차단이면 그것이 답이다. 우회 수집기를 새로
   만들지 않는다.
4. **자격증명은 출력하지 않는다.** 쿠키·키 값은 로그·보고서·커밋 어디에도 남기지 않는다.

## 소스별 경로

| 데이터 | 경로 | 인증 |
|---|---|---|
| 시세·멀티플·52주·수급·섹터·투자지표 등급 | `invagent.datafeed.stockeasy` → `analyze-stock/scripts/fetch_stock_info.py` | 필요 |
| 증권사별 목표주가·컨센서스·EPS 리비전·리포트 요약 | 같음 (`securities-reports`) | 필요 |
| 종목 뉴스 | 같음 (`news/by-stock-code`) | 필요 |
| 종목명·티커 해석 | `invagent.datafeed.tickers` (오버라이드 우선) | 검색만 무인증 |
| 일봉 OHLCV | `invagent.datafeed.naver` (`api.finance.naver.com/siseJson.naver`) | 불필요 |
| 시장 지표(지수·빅픽처·breadth·신용잔고) | `invagent.datafeed.stockeasy.fetch_market_json` → `daily-digest/scripts/fetch_market_signals.py` | 불필요 |
| 텔레그램 저장 메시지·첨부 이미지·링크 본문 | `uv run invagent fetch-messages` | 텔레그램 세션 |
| 보유 포트폴리오 | Google Drive MCP → `daily-digest/scripts/extract_portfolio.py` | MCP |
| 공시·재무·지분·배당 | `opendart` 스킬 (MCP 전용) | MCP |
| 월간 투자일지 | Notion MCP (`monthly-investment-review`) | MCP |
| VKOSPI | investing.com WebFetch (`daily-digest` 1-2단계) | 없음 |

### 종목 데이터 (StockEasy)

```bash
uv run python .agents/skills/analyze-stock/scripts/fetch_stock_info.py "<종목명 또는 6자리 티커>" \
  [--since <기준일>] [--news 10] [--reports 12] [--summaries 5] [--detail-chars 800] [--no-cache]
```

종료 코드: `0` 정상 또는 부분 수집 · `1` 수집 실패 · `2` 종목명 후보 다수(후보를 그대로 제시하고
사용자에게 되묻는다).

### 일봉과 시장 지표

```bash
uv run python .agents/skills/stage-analysis/scripts/stage_scan.py "<종목명 또는 티커>" [--no-cache]
uv run python .agents/skills/daily-digest/scripts/fetch_market_signals.py
uv run invagent daily-prep   # 시장 신호 + 전고점 낙폭을 한 번에
```

### 임시 조회 (판정 없이 원자료만)

```bash
uv run python .agents/skills/market-data/scripts/fetch.py search "<종목명>"
uv run python .agents/skills/market-data/scripts/fetch.py quote "<종목명 또는 티커>"
uv run python .agents/skills/market-data/scripts/fetch.py bars "<종목명 또는 티커>" [--days 400]
uv run python .agents/skills/market-data/scripts/fetch.py market [indices|big_picture|market_monitor|credit_balance]
```

보고서·브리핑을 쓸 때는 이 CLI가 아니라 위의 스킬 스크립트를 쓴다. 이것은 "값 하나만 확인"용이다.

### 텔레그램

```bash
uv run invagent fetch-messages --days 1
```

원본은 `output/daily-digest/raw/<날짜>_raw.md`, 첨부 이미지는 `output/daily-digest/media/<날짜>/`,
링크 본문은 `output/daily-digest/links/<날짜>/`(raw에는 경로와 `[요약 대기]`만 남는다).
이미지와 링크 본문은 받아만 두고 읽지 않는다 — 읽는 것은 `daily-digest` 1-4단계(이미지)와
1-5단계(링크 요약)의 일이다.
링크 본문 수집은 신뢰할 수 없는 입력을 다루므로 `src/invagent/telegram/link_extractor.py`의
SSRF 방어(스킴 제한, 비공개 대역 거부, 홉마다 재검증, 응답 크기·시간 상한)를 통과한다.

### 포트폴리오 (Google Sheets)

Drive MCP로 `주식 포트폴리오` 파일(시트 `포트폴리오`)을 찾아 내용을 읽고, 덤프를 파서에 넘긴다.
파일 ID는 어느 문서에도 하드코딩하지 않는다.

```bash
uv run python .agents/skills/daily-digest/scripts/extract_portfolio.py <덤프 경로> \
  --out output/portfolio/$(date +%Y-%m-%d).md
```

같은 날 스냅샷이 이미 있으면 다시 받지 않고 그것을 읽는다.

## 인증 — `STOCKEASY_COOKIE`

`stock-search`를 뺀 모든 StockEasy 종목 엔드포인트가 로그인 세션을 요구한다. 값은 저장소 루트의
gitignore된 `.env`에 한 줄로, 따옴표로 감싸 둔다. 추적되는 파일에는 절대 넣지 않는다.

갱신 절차: 로그인한 브라우저에서 `securities-reports` 요청을 Chrome DevTools Network 탭으로 잡고,
요청 헤더의 `cookie:` 값을 그대로 복사해 `.env`의 해당 줄을 바꾼다.

증상 구분:

- `HTTP 401` + `STOCKEASY_COOKIE 미설정` → `.env`에 값이 없다.
- `HTTP 401` + `쿠키 만료·무효` → 값은 있으나 세션이 죽었다. 갱신한다.
- 쿠키가 없으면 시세·멀티플·컨센서스·뉴스를 아예 못 받는다. 리포트 요약만 비블로킹으로 빠진다.

## 우선주 — 어느 축을 본주로 돌리나

우선주는 같은 회사의 다른 주식 종류라 **분기 실적이 따로 없고** 목표주가·리포트·뉴스도 본주에
붙는다. `datafeed.tickers.common_code(code)`가 우선주 티커의 **마지막 자리를 `0`으로 바꿔**
본주 티커를 만든다. KRX가 그 자리에 주권 종류를 넣기 때문이다 — 보통주 `0`, 구형 우선주
`5`·`7`·`9`, 2013년 이후 신형 우선주 `K`부터. 2026-09-21까지 한 주간 DART에 공시한 보통주
164종목이 예외 없이 `0`으로 끝났고, 삼성물산우B `02826K` → `028260`(삼성물산)도 같은
데이터로 교차검증했다. 순수 문자열 연산이라 **네트워크를 타지 않는다.**

| 축 | 쓰는 티커 | 이유 |
|---|---|---|
| 일봉·이동평균 (`stage_scan`) | **우선주** | 본주와 괴리율이 따로 움직인다 |
| 현재가·52주 밴드·수급 (`fetch_stock_info`) | **우선주** | 내가 들고 있는 건 우선주다 |
| 영업이익 증가율·재무 | **본주** | 우선주 코드로는 확정 분기가 비어 가격 전용 판정으로 강등된다 |
| 멀티플·투자지표 등급 | **본주** | 본주 주가 기준 값이다 |
| 목표주가·컨센서스 상승여력 | **본주** | 목표가는 본주에 매겨진다. 우선주 주가에 대면 괴리율만큼 부풀려진다 |
| 뉴스·증권사 리포트 | **본주** | 기사·리포트가 본주 이름으로 나온다 |

그래서 `fetch_stock_info`는 우선주 한 종목에 `info-tab`을 **두 번** 부른다 — 본주 것(실적·컨센)과
우선주 자기 것(시세). 캐시가 받쳐 주므로 재실행 비용은 없다. 출력 첫 줄에 어느 축이 어디서 왔는지와
괴리율을 밝힌다 (삼성전자우 2026-09-20: -23.8%).

신형 **보통주** 코드(에임드바이오 `0009K0`)는 `0`으로 끝나므로 건드리지 않는다. 다만
`TICKER_RE`가 아직 `^\d{6}$`라 그 코드와 신형 우선주(`02826K`)는 티커로 조회되지 않는다 — 별건.

## 응답 캐시

같은 실행 안에서 `info-tab`(약 128KB)을 두 번 받는 경로가 있고, 브리핑을 다시 돌리면 모든 호출이
반복된다. `invagent.datafeed.cache`가 그 사이에 있다.

- TTL은 **15분**으로 짧다. `info-tab`이 **현재가**를 싣기 때문에 하루짜리 캐시는 오후 재실행에
  아침 가격을 돌려주게 된다.
- 캐시 키는 URL과 **쿠키를 보냈는지 여부**로만 만든다. 쿠키 값 자체는 키에 들어가지 않고 어디에도
  기록되지 않는다. 무인증 401 본문이 인증 호출로 재생되지 않는다.
- 성공한 응답만 저장한다. 일시적 401·타임아웃이 TTL 내내 굳지 않는다.
- 저장 위치는 gitignore된 `output/.cache/http/`.
- 끄기: `INVAGENT_HTTP_CACHE=0`, 창 조정: `INVAGENT_HTTP_CACHE_TTL`(초), 스크립트 플래그: `--no-cache`.
  `fetch_market_signals.py`에는 argparse가 없어 플래그를 받지 못하므로 환경변수를 쓴다.

## 티커 오버라이드

`stock-search`가 못 잡는 신규 상장 종목이나 시트 표기가 정식 종목명과 다른 경우는
`context/ticker_overrides.md`에 `종목명 = 6자리코드` 한 줄로 고정한다. 이 파일이 검색 API보다
먼저 읽힌다 — 해석 경로는 `invagent.datafeed.tickers` 하나뿐이다.

## 스킬 경계

- 값을 **판정**하는 일(스테이지 등급, 낙폭 밴드, 목표가 blend)은 각 스킬이 한다.
- 이 스킬은 판정하지 않는다. "어디서 가져오나 / 왜 실패했나 / 어떻게 고치나"까지가 범위다.
