# invagent

Personal investment-decision support: collects market data, judges stocks against the user's own rules, and writes briefings and reports.

## Language

**판정 일봉 (Judgement bars)**:
The regular-session daily bars every price rule is scored on, each judgement naming where its bars came from. Never carries after-hours prices except in a bar explicitly labelled as a fallback.
_Avoid_: chart, OHLCV, 시세 (when the rule-scoring series is meant)

**본주 (Parent common share)**:
The common share a preferred share belongs to; the company's earnings, consensus and news are its, not the preferred's.
_Avoid_: 보통주 (when the pairing to a preferred is the point), parent stock

**시세 축 (Price axis)**:
Everything read from a share's own trading — quote, 52-week band, flow, judgement bars. A preferred share's price axis is its own.
_Avoid_: quote data

**실적 축 (Fundamentals axis)**:
Everything read from the company's reported and estimated results — financials, multiples, consensus, target prices. A preferred share's fundamentals axis is its 본주's.
_Avoid_: stock evidence, financial data
