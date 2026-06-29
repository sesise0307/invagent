---
name: opendart
description: >
  OpenDART MCP를 통해 한국 상장기업의 공시·재무·지배구조·사업보고서 정보를 조회하는 스킬.
  DART 공식 데이터(연결·지배주주 귀속 기준) 기반이다. 사용자가 특정 기업의 실적·재무·공시·
  주주·배당·지배구조 등을 자세히 알고 싶어 하면 반드시 이 스킬을 사용하라.
  다음과 같은 상황에서 트리거하라:
  - "OO 최근 실적/재무 받아와", "OO 매출·영업이익 어때", "OO 분기 실적 추이"
  - "OO 공시 뭐 떴어", "OO 사업보고서 내용", "OO 감사의견"
  - "OO 최대주주/지분구조", "5% 대량보유 누구야", "OO 지배구조보고서"
  - "OO 배당 얼마", "자사주 샀어?", "밸류업 계획 있어?"
  - "OO 주총 안건/결과", "유상증자/CB 희석 얼마", "OO 수주 땄어?"
  - "OO 합병/인수 했어?", "OO 횡령·중대재해 사건", "최근 사고 터진 기업"
  - 종목 트랙킹·텔레그램 브리핑 중 펀더멘탈 교차검증이 필요할 때
---

# OpenDART 조회 스킬

한국 상장기업의 공시·재무·지배구조·사업보고서를 DART 공식 데이터로 조회한다.
모든 도구는 `opendart` MCP 서버가 제공한다. 추정·웹검색이 아닌 **공식 공시 원문 기반**이 핵심 가치다.

---

## 접속 (MCP 연결)

도구는 `opendart` MCP 서버(remote, HTTP)를 통해 제공된다. 연결 URL:

```
https://open-proxy-mcp.fly.dev/mcp?opendart=REDACTED
```

- `~/.claude.json`의 `mcpServers.opendart.url`에 이미 등록되어 있어, Claude Code 세션에서 자동 연결된다.
- URL 쿼리스트링의 `opendart=` 값은 OpenDART API 토큰이다. **노출 주의** — 공개 저장소·공유 채널에 그대로 올리지 말 것.
- 도구가 deferred 상태(스키마 미로드)면 호출 전에 ToolSearch로 로드한다:
  ```
  ToolSearch query "select:mcp__opendart__company,mcp__opendart__financial_metrics"
  ```
  필요한 도구를 한 번의 ToolSearch에 콤마로 묶어서 로드한다(라운드트립 절약).
- 토큰 재발급·API 키 발급은 공식 사이트에서: <https://opendart.fss.or.kr> (공시 원문 뷰어는 <https://dart.fss.or.kr>).

---

## 작동 방식

**1단계 — 항상 `company`로 시작한다.** 회사명/ticker/corp_code를 식별하고 ticker·corp_code를
확정한 뒤 최근 공시 인덱스를 훑는다. 상장사 전용이며 exact 매치만 자동 확정된다.

**2단계 — 의도에 맞는 도구를 호출한다.** 아래 표에서 고른다. 대부분 도구는 `scope` 파라미터로
상세 수준을 조절한다(`summary` 기본). 기간이 필요하면 `start_date`/`end_date`(YYYYMMDD) 또는
`year`/`years`/`lookback_months`를 쓴다.

**3단계 — 출처를 명시한다.** 응답의 Evidence/rcept_no를 인용한다. 숫자를 추정으로 덮어쓰지 말고
도구가 준 값을 그대로 전달한다. `proxy_advise_before_meeting`의 의결권 decision 컬럼은
**그대로 제시**하고 안건명 키워드만 보고 자체 판단으로 뒤집지 않는다.

요청이 모호하면(어느 기업인지, 무엇을 알고 싶은지) 먼저 물어라.

---

## 도구 레퍼런스

모든 도구는 `mcp__opendart__<name>` 형태다. `company` 외 대부분 `company` 인자를 받는다.

### 식별 · 진입점
| 도구 | 용도 | 핵심 scope/인자 |
|---|---|---|
| `company` | 기업 식별 + 최근 공시 인덱스. **모든 조회의 시작점** | query, max_recent_filings, start/end_date |
| `evidence` | rcept_no → 공시일·소스·뷰어 URL. 인용 출처 확인 (API 호출 없음) | rcept_no |

### 재무 · 실적
| 도구 | 용도 | 핵심 scope/인자 |
|---|---|---|
| `financial_metrics` | 재무 펀더멘탈 + 회계 risk. 듀퐁·FCF·NWC·이자보상배율·감사의견 자동 산출 | scope: summary / yearly / quarterly / yoy / qoq / audit_opinion |

- **연간 실적**: `scope=summary`(직전 사업연도) 또는 `yearly`(N년 추이, `years`).
- **분기 실적·추이**: `scope=quarterly`(최근 12분기 standalone + QoQ·YoY). 최신 분기 포함.
- **전년 대비 alert**: `scope=yoy`. **감사의견 3년**: `scope=audit_opinion`.
- 금융사(은행·지주)는 매출액 계정이 없어 영업이익·순이익 기준으로 해석한다.

### 주주환원 · 지분 · 지배구조
| 도구 | 용도 | 핵심 scope |
|---|---|---|
| `dividend` | 실지급·확정 배당(DPS·배당성향·시가배당률·추이). 미래 정책 아님 | summary / detail / history |
| `treasury_share` | 자사주 취득·처분·소각. 결정(사전)↔결과(사후) 매칭 | summary / annual |
| `value_up` | 밸류업 계획·주주환원 **미래 약속**(ROE/PBR/배당 목표) | summary / plan / commitments / timeline |
| `ownership_structure` | 최대주주·특수관계인·5% 대량보유 + 공동보유자 분해 | summary / major_holders / blocks / control_map / changes |
| `corp_gov_report` | 기업지배구조보고서 15개 지표 O/X (2026~ KOSPI 의무) | summary / metrics / principles / filings / timeline |

### 주주총회
| 도구 | 용도 | 핵심 scope |
|---|---|---|
| `shareholder_meeting_notice` | 주총 소집공고(사전): 안건·이사후보·보수한도·정관변경 | summary / board / compensation / aoi_change / prov_financials |
| `shareholder_meeting_results` | 주총 의결 결과(사후): 안건별 가결/부결 + 찬반율 | meeting_type: auto/annual/extraordinary |
| `proxy_advise_before_meeting` | 주총 전 안건별 의결권 권고(FOR/AGAINST/REVIEW). **decision 그대로 제시** | meeting_type, vote_style, check_audit_history |

### 경영권 · 자본거래 · M&A
| 도구 | 용도 | 비고 |
|---|---|---|
| `proxy_contest` | 경영권 분쟁·위임장·소송·5% 행동주의 시그널 | summary/fight/litigation/signals/timeline/vote_math |
| `dilutive_issuance` | 유상증자·CB·EB·BW·감자 — 잠재 희석률·3자배정·refixing | lookback_months |
| `order_contracts` | 수주(단일판매·공급계약): 계약금액·**매출액 대비%**·상대방 | 적자기업 미래 매출 가시성 |
| `corporate_deals` | 지분 인수·매각(타법인주식): 계열 출자·일감몰아주기 | include_details, details_limit |
| `corporate_restructuring` | 합병·분할·주식교환: 합병비율·매수청구가·상대방 재무 | — |

### 리스크
| 도구 | 용도 | 비고 |
|---|---|---|
| `risk_events` | 중대재해·횡령·배임·생산중단·영업정지 | `company` 공백 시 **시장 전체 최근 30일 스캔** |

---

## 예시

**예 1 — 최근 실적:**
"에이피알 최근 실적 받아와"
→ `company("에이피알")`로 식별 → `financial_metrics(scope=summary)`(연간) + `financial_metrics(scope=quarterly)`(최신 분기 추이). 매출·영업이익·OPM·ROE + 분기 YoY를 표로, Evidence rcept_no 인용.

**예 2 — 지배구조:**
"삼성전자 최대주주랑 5% 대량보유 누구야"
→ `company` → `ownership_structure(scope=summary)`. 공동보유자 분해 포함, `co_holders_verified=False`면 원문 대조 권고.

**예 3 — 시장 스캔:**
"최근 사고·횡령 터진 기업 알려줘"
→ `risk_events()` (company 공백) → 최근 30일 시장 전체 중대재해·횡령·배임 스캔.

**예 4 — 자본거래:**
"OO 유상증자나 CB로 희석 얼마나 됐어"
→ `company` → `dilutive_issuance()`. 5종 통합 + 잠재 희석률, `ownership_structure` 교차검증 권장.
