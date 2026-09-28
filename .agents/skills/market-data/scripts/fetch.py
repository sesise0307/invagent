#!/usr/bin/env python3
"""수집 계층 직통 CLI — 판정 없이 원자료만 확인한다.

보고서·브리핑을 쓸 때는 각 스킬의 스크립트(`fetch_stock_info.py`·`stage_scan.py`·
`fetch_market_signals.py`)를 쓴다. 그것들은 값을 해석해서 정해진 모양으로 찍는다.
이 CLI는 "지금 이 값이 얼마인지, 이 경로가 살아 있는지"만 볼 때 쓴다.

수집 실패는 비블로킹이라는 공통 원칙을 따라 사유를 stderr에 남기고 종료 코드로만 알린다.
종료 코드: 0 정상 · 1 수집 실패 · 2 종목명 후보 다수.

의존성: stdlib + 저장소 패키지(`invagent.datafeed`)만 사용.
"""

from __future__ import annotations

import argparse
import json
import sys

from invagent.datafeed import daily, stockeasy, tickers


def _resolve(query: str) -> tuple[str | None, int]:
    """종목명 → 티커. 실패하면 사유를 stderr에 남기고 종료 코드를 돌려준다."""
    hit, err, code = tickers.resolve_stock(query)
    if not hit:
        print(f"ERROR: {err}", file=sys.stderr)
        return None, code or 1
    return hit["stock_code"], 0


def cmd_search(args) -> int:
    data, err = stockeasy.fetch_stock_json(stockeasy.ENDPOINTS["search"], {"q": args.query})
    if err:
        print(f"ERROR: 종목 검색 실패 — {err}", file=sys.stderr)
        return 1
    for hit in data or []:
        print(f"{hit.get('stock_name')}\t{hit.get('stock_code')}\t{hit.get('exchange')}")
    return 0


def cmd_quote(args) -> int:
    ticker, code = _resolve(args.query)
    if not ticker:
        return code

    cookie = stockeasy.load_cookie()
    if not cookie:
        print(f"ERROR: {stockeasy.COOKIE_ENV} 미설정 — .env에 브라우저 Cookie 헤더가 필요하다", file=sys.stderr)
        return 1

    info, err = stockeasy.fetch_stock_json(
        stockeasy.ENDPOINTS["info_tab"].format(code=ticker),
        referer=f"{stockeasy.PAGE_BASE}/{ticker}",
        cookie=cookie,
    )
    if err:
        print(f"ERROR: 시세 수집 실패 — {err}", file=sys.stderr)
        return 1

    si = (info or {}).get("stock_info") or {}
    # API는 가격 필드에 등락 방향을 부호로 박는다. 가격은 절대값으로 읽는다.
    price = abs(float(str(si.get("cur_prc") or 0).replace(",", "") or 0))
    print(f"{ticker}\t{price:,.0f}원\t{si.get('flu_rt')}%")
    return 0


def cmd_bars(args) -> int:
    ticker, code = _resolve(args.query)
    if not ticker:
        return code

    bars, err, note = daily.fetch_daily_bars(ticker, args.days)
    if err or not bars:
        print(f"ERROR: 일봉 수집 실패 — {err or '빈 응답'}", file=sys.stderr)
        return 1

    print(f"{ticker}\t{len(bars)}봉\t{bars[0]['date']} ~ {bars[-1]['date']}\t{note or 'StockEasy 일봉'}")
    for bar in bars[-args.tail:]:
        print(f"{bar['date']}\t{bar['close']:,.0f}\t{bar['volume']:,.0f}")
    return 0


def cmd_market(args) -> int:
    data, err = stockeasy.fetch_market_json(args.endpoint)
    if err:
        print(f"ERROR: {args.endpoint} 수집 실패 — {err}", file=sys.stderr)
        return 1
    print(json.dumps(data, ensure_ascii=False, indent=2)[: args.chars])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="수집 계층 직통 조회 (판정 없음)")
    parser.add_argument("--no-cache", action="store_true", help="응답 캐시를 쓰지 않는다")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="종목명 후보 나열")
    p.add_argument("query")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("quote", help="현재가 한 줄")
    p.add_argument("query")
    p.set_defaults(func=cmd_quote)

    p = sub.add_parser("bars", help="일봉")
    p.add_argument("query")
    p.add_argument("--days", type=int, default=400, help="달력일 기준 조회 창")
    p.add_argument("--tail", type=int, default=5, help="찍을 최근 봉 수")
    p.set_defaults(func=cmd_bars)

    p = sub.add_parser("market", help="시장 지표 원본 JSON")
    p.add_argument("endpoint", choices=sorted(stockeasy.MARKET_ENDPOINTS))
    p.add_argument("--chars", type=int, default=4000, help="출력 상한")
    p.set_defaults(func=cmd_market)

    args = parser.parse_args(argv)
    if args.no_cache:
        from invagent.datafeed import cache

        cache.disable()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
