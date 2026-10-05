"""윌리엄 오닐 CAN SLIM 7항목을 결정론적으로 채점한다 — 참고 지표.

투자 판단(매수/관망/회피·확신도·진입 경로·수량)에는 넣지 않는다. `analyze-stock` §5 「CAN SLIM
점검」에 그대로 옮겨 지켜보는 용도다. 기준값은 아래 상수이고 근거는 `analyze-stock/SKILL.md`
「CAN SLIM 점검」 표에 있다 — 둘을 함께 바꾼다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from invagent.datafeed import cache as http_cache, daily, naver, stockeasy, tickers

C_MIN_EPS_YOY_PCT = 25.0
A_YEARS = 3
N_HIGH_WINDOW = 250
N_MAX_BELOW_HIGH_PCT = 15.0
S_WINDOW = 50
S_MIN_UPDOWN_RATIO = 1.0
L_MIN_RS = 80.0
I_WINDOW = 20
# 오닐 빅픽처 상태 → (표기, mark). 압박받는 상승세는 경계로 보여 주되 점수에 넣지 않는다.
M_STATUS = {
    "confirmed_uptrend": ("상승 추세 확인", "pass"),
    "uptrend_under_pressure": ("상승 추세 압박", "warn"),
    "rally_attempt": ("랠리 시도", "fail"),
    "market_in_correction": ("조정장", "fail"),
}


def _pct_change(new: float, old: float) -> float:
    return (new - old) / abs(old) * 100


def _check_c(financials: dict, primary: str) -> dict:
    confirmed, _ = stockeasy.fs_rows(financials, primary, yearly=False)
    rows = {
        (r.get("year"), r.get("quarter")): r.get("eps")
        for r in confirmed
        if r.get("value_type") == "A"
    }
    if not rows:
        return {"mark": "unknown", "detail": "확정 분기 EPS 없음"}
    year, quarter = max(rows)
    label = f"{year}.{quarter}Q"
    now, ago = rows[(year, quarter)], rows.get((year - 1, quarter))
    if now is None or ago is None:
        return {"mark": "unknown", "detail": f"{label} 또는 전년 동기 EPS 없음"}
    if now <= 0:
        return {"mark": "fail", "detail": f"{label} EPS {now:,.0f}원 — 적자"}
    if ago <= 0:
        return {"mark": "pass", "detail": f"{label} EPS {now:,.0f}원 — 흑자전환 (전년 동기 {ago:,.0f}원)"}
    growth = _pct_change(now, ago)
    mark = "pass" if growth >= C_MIN_EPS_YOY_PCT else "fail"
    return {"mark": mark, "detail": f"{label} EPS 전년 동기 대비 {growth:+.1f}% ({ago:,.0f} → {now:,.0f}원)"}


def _check_a(financials: dict, primary: str) -> dict:
    confirmed, _ = stockeasy.fs_rows(financials, primary, yearly=True)
    by_year = {r.get("year"): r.get("eps") for r in confirmed if r.get("value_type") == "A"}
    years = sorted(by_year)[-(A_YEARS + 1):]
    if len(years) < A_YEARS + 1 or any(by_year[y] is None for y in years):
        return {"mark": "unknown", "detail": f"확정 연간 EPS {A_YEARS + 1}개년 미만"}
    eps = [by_year[y] for y in years]
    series = " → ".join(f"{e:,.0f}" for e in eps)
    rising = all(b > a for a, b in zip(eps, eps[1:]))
    detail = f"{years[0]}→{years[-1]} EPS {series}원"
    if eps[0] > 0 and eps[-1] > 0:
        cagr = ((eps[-1] / eps[0]) ** (1 / A_YEARS) - 1) * 100
        detail += f" · {A_YEARS}년 CAGR {cagr:+.1f}%"
    return {"mark": "pass" if rising and eps[-1] > 0 else "fail", "detail": detail}


def _check_n(bars: list[dict]) -> dict:
    """신고가권 — 종가 기준. 장중 고가는 쓰지 않는다 (가격 판단은 종가만)."""
    if not bars:
        return {"mark": "unknown", "detail": "일봉 없음"}
    window = bars[-N_HIGH_WINDOW:]
    high = max(window, key=lambda b: b["close"])
    close = bars[-1]["close"]
    gap = _pct_change(close, high["close"])
    mark = "pass" if gap >= -N_MAX_BELOW_HIGH_PCT else "fail"
    return {
        "mark": mark,
        "detail": f"종가 {close:,.0f}원 · 52주 종가 고점 {high['close']:,.0f}원({high['date']}) 대비 {gap:+.1f}%",
    }


def _check_s(bars: list[dict], stock_info: dict) -> dict:
    """수급 — 최근 50거래일 상승일 거래량 ÷ 하락일 거래량이 1.0을 넘으면 매집 우위."""
    window = bars[-(S_WINDOW + 1):]
    if len(window) < 2:
        return {"mark": "unknown", "detail": "일봉 없음"}
    up = sum(b["volume"] for a, b in zip(window, window[1:]) if b["close"] > a["close"])
    down = sum(b["volume"] for a, b in zip(window, window[1:]) if b["close"] < a["close"])
    if down == 0:
        ratio_text, mark = "하락일 없음", "pass" if up else "unknown"
    else:
        ratio = up / down
        ratio_text, mark = f"{ratio:.2f}", "pass" if ratio > S_MIN_UPDOWN_RATIO else "fail"
    detail = f"{len(window) - 1}거래일 상승일/하락일 거래량 {ratio_text}"
    float_shares, float_ratio = stock_info.get("flo_stk"), stock_info.get("dstr_rt")
    if float_shares:
        detail += f" · 상장주식 {int(float_shares):,}주"
    if float_ratio:
        detail += f" · 유통비율 {float(float_ratio):.1f}%"
    return {"mark": mark, "detail": detail}


def _check_l(rs_data: dict, rs_line: dict, common_rs: float | None = None) -> dict:
    """주도주 — StockEasy `info-tab`의 `rs_data.rs`(시장 대비 상대강도, 높을수록 강세).

    StockEasy는 우선주 RS를 주지 않는다. 본주 RS로 채점하면 가격 축이 섞이므로 미수집으로 두고
    본주 값은 참고로만 적는다.
    """
    rs = rs_data.get("rs")
    if rs is None:
        detail = "RS 없음" + (f" — 본주 RS {common_rs:g} (참고)" if common_rs is not None else "")
        return {"mark": "unknown", "detail": detail}
    periods = " / ".join(
        f"{label} {rs_data[key]:g}" for label, key in (("1M", "rs_1m"), ("3M", "rs_3m"), ("6M", "rs_6m"))
        if rs_data.get(key) is not None
    )
    detail = f"RS {rs:g}" + (f" ({periods})" if periods else "")
    flags = rs_line.get("flags") or {}
    if flags.get("is_ath"):
        detail += " · RS선 사상 최고"
    elif flags.get("is_52w_high"):
        detail += " · RS선 52주 신고가"
    return {"mark": "pass" if rs >= L_MIN_RS else "fail", "detail": detail}


def _check_i(trend: list[dict]) -> dict:
    """기관 지원 — 네이버 투자자별 매매동향, 최근 20거래일 기관 누적 순매수가 양수면 충족.

    외국인은 판정에 넣지 않고 함께 적기만 한다 (오닐의 I는 기관 보유 증가다).
    """
    if not trend:
        return {"mark": "unknown", "detail": "투자자별 매매동향 없음"}
    recent = trend[-I_WINDOW:]
    inst = sum(r["institution"] for r in recent)
    foreign = sum(r["foreign"] for r in recent)
    inst_all = sum(r["institution"] for r in trend)
    detail = (
        f"기관 {len(recent)}일 {inst:+,}주 ({len(trend)}일 {inst_all:+,}주)"
        f" · 외국인 {len(recent)}일 {foreign:+,}주 (참고)"
    )
    return {"mark": "pass" if inst > 0 else "fail", "detail": detail}


def _check_m(big_picture: dict | None, market: str | None) -> dict:
    """시장 방향 — StockEasy 빅픽처에서 종목이 상장된 시장의 상태를 읽는다."""
    if not market:
        return {"mark": "unknown", "detail": "상장 시장 미상 (info-tab 미수집)"}
    state = (big_picture or {}).get(market.lower())
    if not state or state.get("status") not in M_STATUS:
        return {"mark": "unknown", "detail": f"{market} 빅픽처 없음"}
    label, mark = M_STATUS[state["status"]]
    detail = f"{market} {label} · 활성 분산일 {state.get('active_distribution_count', 0)}개"
    return {"mark": mark, "detail": detail}


LETTERS = ("C", "A", "N", "S", "L", "I", "M")


def evaluate(
    info: dict | None,
    big_picture: dict | None,
    trend: list[dict],
    fundamentals: dict | None = None,
    market: str | None = None,
) -> dict:
    """info-tab 페이로드·빅픽처·투자자별 매매동향 → 항목별 `{mark, detail}`과 `score`.

    mark는 `pass`·`fail`·`warn`(경계, 점수 미산입)·`unknown`(미수집) 중 하나다.
    우선주는 실적이 따로 없으므로 C·A만 `fundamentals`(본주 info-tab)에서 읽고, 가격·RS·수급은
    우선주 자기 `info`로 잰다. `market`은 info-tab이 없을 때 다른 출처에서 받은 상장 시장이다.
    """
    info = info or {}
    fs_info = fundamentals or info
    financials = fs_info.get("financials") or {}
    primary = fs_info.get("primary_fs_type") or "C"
    bars = daily.chart_bars(info)
    result = {
        "C": _check_c(financials, primary),
        "A": _check_a(financials, primary),
        "N": _check_n(bars),
        "S": _check_s(bars, info.get("stock_info") or {}),
        "L": _check_l(
            info.get("rs_data") or {},
            info.get("rs_line_chart") or {},
            ((fundamentals or {}).get("rs_data") or {}).get("rs"),
        ),
        "I": _check_i(trend),
        "M": _check_m(big_picture, (info.get("stock_info") or {}).get("market") or market),
    }
    result["score"] = {
        "passed": sum(result[k]["mark"] == "pass" for k in LETTERS),
        "total": len(LETTERS),
        "warn": [k for k in LETTERS if result[k]["mark"] == "warn"],
        "unknown": [k for k in LETTERS if result[k]["mark"] == "unknown"],
    }
    return result


MARK_ICON = {"pass": "✅", "fail": "❌", "warn": "⚠️", "unknown": "❓"}


def _fetch_info(code: str, cookie: str | None) -> dict | None:
    info, err = stockeasy.fetch_stock_json(
        stockeasy.ENDPOINTS["info_tab"].format(code=code),
        referer=f"{stockeasy.PAGE_BASE}/{code}",
        cookie=cookie,
    )
    if info:
        return info
    hint = "쿠키 만료·무효" if cookie else f"{stockeasy.COOKIE_ENV} 미설정"
    reason = f"{err or '빈 응답'}" + (f" ({hint})" if err == "HTTP 401" or not cookie else "")
    print(f"[누락] info_tab {code} — {reason}", file=sys.stderr)
    return None


def print_result(name: str, code: str, result: dict, sources: list[str]) -> None:
    score = result["score"]
    print(f"[CAN SLIM] {name}({code}) · 조회일 {date.today().isoformat()} · 참고 지표 — 투자 판단 미반영")
    print(
        f"점수 {score['passed']}/{score['total']}"
        f" · 경계 {', '.join(score['warn']) or '없음'}"
        f" · 미수집 {', '.join(score['unknown']) or '없음'}"
    )
    for letter in LETTERS:
        item = result[letter]
        print(f"  {MARK_ICON[item['mark']]} {letter}  {item['detail']}")
    print(f"출처: {' · '.join(sources)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="오닐 CAN SLIM 7항목 참고 채점")
    parser.add_argument("query", help="종목명 또는 6자리 티커")
    parser.add_argument("--no-cache", action="store_true", help="캐시를 쓰지 않고 매번 새로 받는다")
    parser.add_argument("--json", action="store_true", help="채점 결과 JSON 덤프")
    args = parser.parse_args(argv)
    if args.no_cache:
        http_cache.disable()

    stock, err, code = tickers.resolve_stock(args.query)
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        return code
    ticker = stock["stock_code"]
    name = stock.get("stock_name") or ticker

    cookie = stockeasy.load_cookie()
    info = _fetch_info(ticker, cookie)
    sources = ["StockEasy info-tab"]
    if info:
        name = (info.get("stock_info") or {}).get("name") or name
    # 우선주는 실적이 따로 없다 — C·A만 본주로 잰다. 가격·RS·수급은 우선주 자기 것.
    fs_ticker, is_preferred = tickers.fundamentals_code(ticker)
    fundamentals = None
    if is_preferred:
        fundamentals = _fetch_info(fs_ticker, cookie)
        parent = ((fundamentals or {}).get("stock_info") or {}).get("name")
        sources.append(f"우선주 실적은 본주 {parent}({fs_ticker})" if parent else f"우선주 실적은 본주 {fs_ticker}")

    # 상장 시장은 info-tab에서 읽는다. 막혔으면 네이버에서 받아 M을 엉뚱한 시장으로 채점하지 않는다.
    market = None
    if not ((info or {}).get("stock_info") or {}).get("market"):
        market, me = naver.fetch_listing_market(ticker)
        if me:
            print(f"[누락] 상장 시장 — {me}", file=sys.stderr)
        else:
            sources.append(f"상장 시장은 네이버({market})")

    trend, te = naver.fetch_investor_trend(ticker)
    if te:
        print(f"[누락] 투자자별 매매동향 — {te}", file=sys.stderr)
    sources.append("네이버 투자자별 매매동향")
    big_picture, be = stockeasy.fetch_market_json("big_picture")
    if be:
        print(f"[누락] 빅픽처 — {be}", file=sys.stderr)
    sources.append("StockEasy 빅픽처")

    result = evaluate(info, big_picture, trend, fundamentals=fundamentals, market=market)
    if args.json:
        print(json.dumps({"stock_code": ticker, "stock_name": name, "fetched_at": date.today().isoformat(),
                          "sources": sources, **result}, ensure_ascii=False))
        return 0
    print_result(name, ticker, result, sources)
    return 0


if __name__ == "__main__":
    sys.exit(main())
