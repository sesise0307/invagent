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
   만들지 않는다. 요청 한도는 피해 가는 대상이 아니라 지키는 대상이다 — 아래 「요청 제한」.
4. **자격증명은 출력하지 않는다.** 쿠키·키 값은 로그·보고서·커밋 어디에도 남기지 않는다.

### 실행 환경 DNS 장애 구분

StockEasy와 Naver가 함께 `<urlopen error [Errno 8] nodename nor servname provided, or not known>`로 실패하면, 먼저 두 호스트와 무관한 공개 호스트의 DNS를 같은 실행 환경에서 확인한다. 세 호스트가 모두 실패하면 소스 장애나 쿠키 만료로 판정하지 않는다. **동일한 읽기 전용 CLI를 네트워크가 허용된 실행 환경에서 다시 실행해 성공 여부를 확인한다.** Codex의 `workspace-write` 샌드박스에서는 DNS가 차단될 수 있으므로 승인된 `require_escalated` 실행이 이 비교에 해당한다. 외부 환경에서 성공하면 수집 코드 변경 없이 그 실행 결과를 쓰고, 두 환경 모두 실패하면 호스트 DNS·VPN을 점검한다. 인증값은 어떤 진단 출력에도 포함하지 않는다.

`uv run`이 사용자 홈의 uv 캐시에 `Operation not permitted`를 내면 이것도 실행 환경 권한 문제다. 저장소 `.venv/bin/python`으로 재현 신호를 분리하고, 최종 수집은 접근이 허용된 환경에서 문서화된 `uv run` 명령으로 다시 검증한다.

## 소스별 경로

| 데이터 | 경로 | 인증 |
|---|---|---|
| 시세·멀티플·52주·수급·섹터·투자지표 등급 | `invagent.datafeed.stockeasy` → `analyze-stock/scripts/fetch_stock_info.py` | 필요 |
| 증권사별 목표주가·컨센서스·EPS 리비전·리포트 요약 | 같음 (`securities-reports`) | 필요 |
| 종목 뉴스 | 같음 (`news/by-stock-code`) | 필요 |
| 종목 DART 공시 목록 | 같음 (`stock-info/analysis-tab` — 2026-09 개편 페이지의 소식 탭, 공시·리포트·뉴스 각 최근 20건) | 필요 |
| 시간외·NXT 체결가 (참고) | 같음 (`info-tab`의 `after_hours_quote`·`nxt_quote`) — `stock_info.cur_prc`와 차트 종가는 정규장가 그대로다. 판정에 쓰지 않는다 | 필요 |
| 종목 RS(상대강도)·RS선 | 같음 (`info-tab`의 `rs_data`·`rs_line_chart`) → `analyze-stock/scripts/canslim_scan.py`. 업종 RS(`/rs/dashboard-data`)는 앱 토큰 없이는 HTTP 403이라 받지 않는다 | 필요 |
| 종목 투자자별 매매동향(기관·외국인·개인 순매수량) | `invagent.datafeed.naver.fetch_investor_trend` — `m.stock.naver.com/api/stock/<code>/trend`, 최근 60거래일까지 | 불필요 |
| 종목명·상장 시장(KOSPI·KOSDAQ) 대체 | `invagent.datafeed.naver.fetch_basic` — `m.stock.naver.com/api/stock/<code>/basic`. 정본은 `info-tab`의 `stock_info.market`이고, 그것이 막혔을 때만 쓴다 | 불필요 |
| 종목명·티커 해석 | `invagent.datafeed.tickers` — 오버라이드 → 네이버 검색(`ac.stock.naver.com/ac`) → 네이버가 실패했을 때만 StockEasy `stock-search`. StockEasy 요청 한도를 StockEasy에만 있는 값에 남겨 두기 위해서다 | 불필요 |
| 일봉 OHLCV (판정용) | `invagent.datafeed.daily.fetch_daily_bars` — **금융위원회 주식시세정보**(`invagent.datafeed.fsc`, data.go.kr `GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2`)가 정본: KRX 공식 정규장 OHLCV, 한 번에 3년치, 개발계정 하루 10,000회. 기준일 다음 영업일 13시 뒤에 올라오므로 그 뒤의 빈 평일 봉만 StockEasy `info-tab`의 `chart`로 잇는다 — StockEasy는 장중·마감 직후에만 불린다. 빈 봉을 StockEasy로도 못 채우면 그 봉만 네이버로 채우고 날짜를 대체 사유에 적는다. 키가 없거나 공식 호출이 실패하면 StockEasy 3년치 → 네이버(`siseJson`) 순이다. **네이버 종가는 장 마감 후 시간외가가 섞인다**(2026-09-28 사용자 확정; 2026-10-07 공식 일봉으로 대덕전자 9/14 정규장 종가 97,000원 vs 네이버 95,600원 확인) — 대체 꼬리표가 붙은 가격선은 정규장 종가로 다시 잰다 | `DATA_GO_KR_SERVICE_KEY` 권장 (없으면 StockEasy 쿠키, 대체는 불필요) |
| 시장 지표(지수·빅픽처·breadth·신용잔고) | `invagent.datafeed.stockeasy.fetch_market_json` → `daily-digest/scripts/fetch_market_signals.py` | 불필요 |
| 텔레그램 저장 메시지·첨부 이미지·링크 본문 | `uv run invagent fetch-messages` | 텔레그램 세션 |
| 보유 포트폴리오 | Google Drive MCP → `daily-digest/scripts/extract_portfolio.py` | MCP |
| 공시·재무·지분·배당 | `opendart` 스킬 (MCP 전용) | MCP |
| 월간 투자일지 | Notion MCP (`monthly-investment-review`) | MCP |
| VKOSPI | investing.com WebFetch (`daily-digest` 1-2단계) | 없음 |

### 종목 데이터 (StockEasy)

```bash
uv run python .agents/skills/analyze-stock/scripts/fetch_stock_info.py "<종목명 또는 6자리 티커>" \
  [--since <기준일>] [--news 10] [--reports 12] [--summaries 5] [--disclosures 10] [--detail-chars 800] [--no-cache]
```

종료 코드: `0` 정상 또는 부분 수집 · `1` 수집 실패 · `2` 종목명 후보 다수(후보를 그대로 제시하고
사용자에게 되묻는다).

2026-09-29 종목정보 페이지 개편(구 페이지는 `/stock-info-old`) 뒤에도 `info-tab`·`news/by-stock-code`·
`securities-reports`는 그대로 응답한다. 새 페이지의 소식 탭은 `analysis-tab`(GET, 종목 1개)과
`analysis-tab-batch`(POST, 관심종목 묶음)를 부르고, 리포트·공시 본문은 `stock-info/report-content/<id>`·
`stock-info/disclosure-content/<id>`로 따로 받는다. 뉴스나 리포트 요약 엔드포인트가 끊기면
`analysis-tab`이 같은 목록을 싣고 있으니 그리로 옮긴다 — 다만 리포트 요약 논지는 `report-content`를
건마다 불러야 한다.

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

본문이 링크 주소에 없는 사이트는 같은 모듈이 홉 루프 안에서 따라간다(2026-10-02 실측, 홉 한도 5):

| 대상 | 받는 곳 | 저장 |
|---|---|---|
| 증권사 StreamDocs 뷰어 `…/streamdocs/view/sd;streamdocsId=X` (한투) | `…/streamdocs/v4/documents/X` | `.pdf` |
| 같은 뷰어의 `mail` 경로 (삼성증권) | `…/streamdocs/v4/documents/X/custom` (`/custom` 없으면 HTTP 500) | `.pdf` |
| 한투 `securities.koreainvestment.com/download_pdf.jsp` | 페이지 스크립트의 `document.location` → openResearch → 위 뷰어 | `.pdf` |
| `.pdf` 직링크·`%PDF`로 시작하는 응답 | 그대로 (trafilatura를 거치지 않는다, 상한 20MB) | `.pdf` |
| DART `dsaf001/main.do` (틀과 목차뿐) | 첫 `viewDoc(...)` 인자로 `report/viewer.do` (MS949) | `.md` |
| awakeplus `/board/` (로그인 필요) | 받지 않는다 — `[건너뜀: 로그인 필요]` | 없음 |

스크립트 리다이렉트는 `SCRIPT_REDIRECT_HOSTS`에 있는 호스트에서만 따른다. 뷰어 주소는 증권사 내부
구조라 바뀔 수 있다 — 바뀌면 그 링크만 실패 표시로 돌아가고 수집은 막히지 않는다. 링크 1건의 상한은
요청 타임아웃 10초 × `HARD_TIMEOUT_MULTIPLIER`(9) = 90초다(리포트 PDF 12MB가 단독 35초).

### 포트폴리오 (Google Sheets)

Drive MCP로 `주식 포트폴리오` 파일을 정확 일치 검색으로 찾고, `download_file_content`를
`exportMimeType = text/csv`로 호출해 첫 시트(`포트폴리오`)를 CSV로 받는다. 응답
`{"content": "<base64 CSV>", ...}` JSON을 **그대로** 파일에 저장해 파서에 넘긴다.
파일 ID는 어느 문서에도 하드코딩하지 않는다.

```bash
uv run python .agents/skills/daily-digest/scripts/extract_portfolio.py <CSV 응답 파일> \
  --out output/portfolio/$(date +%Y/%m/%Y-%m-%d).md
```

- `read_file_content`는 2026-09-28부터 셀 값을 잘라 쓴 요약만 돌려준다 — 포트폴리오 수집에 쓰지 않는다.
  파서는 예전 덤프(`fileContent`)와 평문 CSV도 계속 받는다.
- **잘림 가드**: 보유 행 평가금액 합계가 시트 `잔고`와 0.5% 넘게 어긋나면 파서가 exit 1로 거부한다.
  응답을 옮겨 적다 base64가 잘리면 마지막 행이 빠지기 때문이다(2026-09-28). 거부되면 다시 받는다.

같은 날 스냅샷이 이미 있으면 다시 받지 않고 그것을 읽는다.

## 인증 — `DATA_GO_KR_SERVICE_KEY` (공식 일봉)

data.go.kr 로그인 → 「금융위원회_주식시세정보」 활용신청(자동 승인) → 마이페이지의 **일반 인증키**를 `.env`의
`DATA_GO_KR_SERVICE_KEY`에 넣는다. 값은 어디에도 출력하지 않는다.

- 엔드포인트는 **V2**다: `https://apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2`.
  구버전 경로(`/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo`)는 새 키를 `SERVICE_KEY_IS_NOT_REGISTERED_ERROR`
  (code 30)로 거절해 키 문제처럼 보인다(2026-10-06~07, 반나절 허비). 포털 미리보기는 되는데 code 30이면 경로부터 의심한다.
- 오류도 HTTP 200 본문(`OpenAPI_ServiceResponse.cmmMsgHeader`)으로 온다. `fsc`는 이를 실패로 돌려주고 `errMsg`를 사유로 쓴다.
- 이용 조건: 공공누리 제4유형 — 개인 분석용, 제3자 재배포 금지.

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
- 단, 시세 URL(`info-tab`, Naver `siseJson`)을 **장이 닫힌 동안**(평일 20:00 NXT 마감 뒤 ~ 09:00,
  주말) 받았다면 다음 평일 09:00(KST)까지 그대로 쓴다. 마감 뒤에는 값이 바뀌지 않으므로 저녁·새벽
  재실행이 같은 값을 다시 받을 이유가 없다. 뉴스·공시·리포트는 마감 뒤에도 새로 나오므로 15분
  그대로다. 공휴일은 모른다(그날은 15분으로 다시 받을 뿐). 보관은 나흘.
- 판정용 일봉(`daily.fetch_daily_bars`)은 그 창을 **평일 15:40**부터로 앞당긴다. 정규장 봉은 15:30에
  확정되고 시간외 시세가 움직여도 바뀌지 않으므로, 오후 재실행이 `info-tab`을 다시 받지 않는다.
  같은 URL을 부르는 다른 호출(`fetch_stock_info`의 시간외·NXT 줄)은 20:00 규칙 그대로다.
- 캐시 키는 URL과 **쿠키를 보냈는지 여부**로만 만든다. 쿠키 값 자체는 키에 들어가지 않고 어디에도
  기록되지 않는다. 무인증 401 본문이 인증 호출로 재생되지 않는다.
- 성공한 응답만 저장한다. 일시적 401·타임아웃이 TTL 내내 굳지 않는다.
- 저장 위치는 gitignore된 `output/.cache/http/`.
- 끄기: `INVAGENT_HTTP_CACHE=0`, 창 조정: `INVAGENT_HTTP_CACHE_TTL`(초), 스크립트 플래그: `--no-cache`.
  `fetch_market_signals.py`에는 argparse가 없어 플래그를 받지 못하므로 환경변수를 쓴다.

## 요청 제한

StockEasy는 짧은 시간에 요청이 몰리면 연결을 끊거나 거절한다. 서브에이전트 여러 개가 종목을 동시에
분석하면 프로세스끼리 서로를 모르고 두드리므로, 조절은 모든 요청이 지나는 `datafeed.http`에서
`invagent.datafeed.ratelimit`이 한다. 호스트마다 따로 적용되고 캐시 적중은 거치지 않는다.

- **간격·동시 수**: 같은 호스트로 나가는 요청 시작 사이 최소 0.5초, 동시 요청 최대 2개. 프로세스
  사이에서 `output/.cache/ratelimit/`의 잠금 파일로 공유한다. **StockEasy는 `HOST_LIMITS`로 따로
  8초 간격·동시 1개다** — 2026-10-05 21종목을 순차로 돌렸는데 14번째에서 끊겼고(분당 30~40건 수준),
  3초로 넓힌 2026-10-06에도 순차 스캔 12건째에서 다시 끊겨 8초(분당 7.5건)로 넓혔다.
- **재시도**: 429·502·503·504, 연결 끊김, 타임아웃이면 최대 3번까지 1→2초(+지터) 백오프로
  다시 부른다. `Retry-After`가 오면 그 값(상한 30초)을 따른다. 401은 재시도하지 않는다.
- **cool-down**: 재시도를 다 써도 거절되면 그 호스트를 2분(StockEasy는 10분, 또는 `Retry-After`) 동안
  아무도 부르지 않는다. StockEasy는 2분 뒤 다시 부르자 차단이 연장됐다(2026-10-05). 메시지는 `<호스트> 요청 제한으로 대기 중 — N초 남음 · 사유 <원인> (HH:MM:SS 진입)`. 원인은 `HTTP 429` 같은
  서버 거절, `연결 끊김: <오류>`, `시간 초과` 중 하나로 `output/.cache/ratelimit/<호스트>.cooldown`(JSON)에 남는다.
  **여러 호스트가 몇 초 안에 같이 `연결 끊김`·`시간 초과`로 들어갔으면 로컬 네트워크 순단이고, 한 호스트만
  `HTTP 4xx/5xx`면 그 서버의 제한이다** — 간격 상수는 후자일 때만 조정한다. 옛 형식 파일(숫자만)은 `사유 미기록`으로 읽힌다.
  그동안 일봉은 Naver 대체 경로로 간다.
  풀리기 전에 다시 돌려도 소용없다 — 기다리거나 다른 단계부터 진행한다.
- **지난 응답 대체**: 제한으로 실패한 URL에 보관 중인(나흘 이내) 응답이 있으면 그것을 쓰고 stderr에
  `[캐시 대체] … N분 전 응답을 쓴다`를 남긴다. 이 표시가 시세 URL에 붙었으면 그 가격은 수집 시점
  기준으로 적는다(장중이면 판정용 종가가 아니다). 401에는 대체하지 않는다 — 쿠키 만료를 가린다.
- 실제 한도는 공개돼 있지 않다. 간격·동시 수 상수는 거절이 다시 보일 때 그 로그를 보고 조정한다.
  여러 종목을 연달아 돌릴 때(관심종목 일괄 점검 등) 8초 간격이 그대로 시간이 된다 — `turn_scan` 한
  종목이 캐시 미스면 info-tab 1건이라 20종목 스캔에 3분 가까이 걸린다. 병렬로 돌려도 빨라지지 않는다.

## 티커 오버라이드

검색(네이버·StockEasy)이 못 잡는 신규 상장 종목이나 시트 표기가 정식 종목명과 다른 경우는
`context/ticker_overrides.md`에 `종목명 = 6자리코드` 한 줄로 고정한다. 이 파일이 검색 API보다
먼저 읽힌다 — 해석 경로는 `invagent.datafeed.tickers` 하나뿐이다.

## 스킬 경계

- 값을 **판정**하는 일(스테이지 등급, 낙폭 밴드, 목표가 blend)은 각 스킬이 한다.
- 이 스킬은 판정하지 않는다. "어디서 가져오나 / 왜 실패했나 / 어떻게 고치나"까지가 범위다.
