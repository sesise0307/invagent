# 단기 바닥 전환 판정 — 근거 기법

`turn_scan.py`가 「매매규칙 2」의 "바닥을 다진 후 고개를 들기 시작하는 초반"을 판정할 때 빌려 온
기법의 요약이다. 임계값은 이 기법들의 원문 수치가 아니라 이 스킬의 운영 기준이며, 근거는
`SKILL.md` 「단기 바닥 전환 판정」 표에 있다. 조사일 2026-09-12.

## 와이코프 매집 (Wyckoff Accumulation)

하락 끝의 매집 구조를 국면 A~E로 나눈다. 매도 클라이맥스(SC) → 자동 반등(AR) → 2차 테스트(ST) →
Spring(지지선을 잠깐 깨는 흔들기) → 강세 신호(SOS, 저항 돌파) → 마지막 지지(LPS, 돌파 후 눌림).

- 국면 C의 Spring은 SC 저점을 보통 2~5% 깨고 1~3거래일 안에 되돌아온다. 거래량이 줄어든 채 깨면
  매도자가 말랐다는 뜻이다. 국면 C는 1~2주로 짧다.
- 고전적 매수 자리는 국면 C의 Spring·테스트와 국면 D의 SOS 뒤 LPS·되돌림이다. 손절은 해당 저점 아래.

적용: 상태 골격(바닥 다지기 = 국면 B·C, 고개 들기 = 국면 D의 SOS, 2차 증량 = LPS 눌림)과 Spring 예외.

- [Wyckoff Accumulation Explained — Trading Wyckoff](https://tradingwyckoff.com/en/accumulation/)
- [Wyckoff Phases A–E — Trading Wyckoff](https://tradingwyckoff.com/en/phases-of-a-wyckoff-structure/)
- [Wyckoff Method — Wyckoff Analytics](https://www.wyckoffanalytics.com/wyckoff-method/)

## Sperandeo 1-2-3 추세 전환

빅터 스페란데오의 추세 전환 확인: (1) 하락 추세선 이탈 (2) 저점 재시험이 직전 저점을 깨지 못함 =
저점 높임 (3) 직전 반등 고점 돌파. 다우 이론의 "저점·고점이 높아진다"를 가장 이른 시점에 확인하는 방법이다.

적용: 고개 들기의 핵심 조건 ①저점 높임 ②돌파선(바닥과 저점 높임 사이 최고 종가) 종가 돌파.

## 앵커드 VWAP (Brian Shannon)

세션 시작이 아니라 사용자가 고른 사건(스윙 저점, 실적 갭 등)부터 누적한 거래량 가중 평균가다.
그 시점 이후 매수자의 평균 단가를 보여 준다. 저점에 고정한 VWAP 위로 가격이 올라서면 강세 확인으로
본다.

적용: 고개 들기 조건 ⑤ — 바닥 이후 매수자가 평균적으로 이익인가.

- [Anchored VWAP Explained — Alchemy Markets](https://alchemymarkets.com/education/indicators/anchored-vwap/)
- [Maximum Trading Gains With Anchored VWAP — Brian Shannon](https://books.google.com/books/about/Maximum_Trading_Gains_With_Anchored_VWAP.html?id=GjCoEAAAQBAJ)

## 패턴 통계 (Bulkowski)

쌍바닥은 평균 상승 37%·실패율 16%, 역헤드앤숄더는 실패율 11%로 반전 패턴 중 성과가 가장 좋다.
공통점은 넥라인 **돌파 확인**이다 — 돌파 전에 들어가면 이 통계의 모집단이 아니다.

적용: 고개 들기를 돌파선 **종가** 돌파로 확정하는 근거.

- [Bulkowski on Head-and-Shoulders Bottoms](https://thepatternsite.com/hsb.html)
- [Bulkowski on Ugly Double Bottoms](https://thepatternsite.com/udb.html)

## 보조 지표 — 거래량, RSI 다이버전스

- 평균 이하 거래량의 돌파는 실패율이 높다 — 실무 기준은 평균의 1.5배 이상.
- RSI 강세 다이버전스(가격 저점 낮춤 + RSI 저점 높임)는 단독 승률이 40~45% 수준이라는 보고가 있고,
  직전 스윙 고점의 종가 돌파 같은 확인을 붙여야 쓸 만하다.

적용: 둘 다 상태를 바꾸지 않고 확신도만 조정한다.

- [RSI Divergence guide — Pineify](https://pineify.app/resources/blog/rsi-divergence-the-complete-guide-to-bullish-bearish-and-hidden-signals)

## 가치 + 가격 확인

가치 신호는 이르거나 가치 함정에 빠지고, 모멘텀은 진입 시점을 잡지만 비싸게 산다 — 둘을 합치면
"싸면서 돌기 시작한" 종목을 겨냥한다. 가치주의 약 57%가 1~2년 구간에서 시장을 밑돈다는 점이
가격 확인을 붙이는 이유다.

적용: 경로 B는 밸류 게이트(싸다) + 고개 들기(돌기 시작했다)를 둘 다 요구한다.

- [Value Momentum — Picture Perfect Portfolios](https://pictureperfectportfolios.com/value-momentum-combining-undervalued-stocks-upward-trends/)
- [Improving the Piotroski F-Score — Alpha Architect](https://alphaarchitect.com/value-investing-research-simple-methods-to-improve-the-piotroski-f-score/)
