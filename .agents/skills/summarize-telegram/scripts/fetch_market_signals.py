#!/usr/bin/env python3
"""StockEasy 시장 신호 수집기.

https://stockeasy.intellio.kr/market-analysis?tab=overview 페이지의
SSR HTML에 임베딩된 RSC payload에서 시장 데이터를 추출해 요약 출력한다.

의존성: stdlib만 사용 (urllib, json, re).
실패 시 stderr에 에러 출력 후 exit 1 — 호출측(스킬)은 실패해도 브리핑을 계속 진행한다.
"""

import json
import sys
import urllib.request

URL = "https://stockeasy.intellio.kr/market-analysis?tab=overview"
TIMEOUT = 20

SIGNAL_EMOJI = {"red": "🔴", "yellow": "🟡", "green": "🟢"}
STATUS_KO = {
    "market_in_correction": "조정장",
    "rally_attempt": "랠리 시도",
    "confirmed_uptrend": "상승 추세 확인",
    "uptrend_under_pressure": "상승 추세 압박",
}


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def extract_json(html: str, key: str):
    """이스케이프된 RSC payload에서 key에 해당하는 JSON 값을 balanced 파싱으로 추출."""
    marker = f'\\"{key}\\"'
    i = html.find(marker)
    if i < 0:
        return None
    j = html.find(":", i + len(marker)) + 1
    # unicode_escape는 UTF-8 멀티바이트를 latin-1 문자로 깨뜨리므로 재인코딩으로 복원
    seg = (
        html[j : j + 500_000]
        .encode()
        .decode("unicode_escape")
        .encode("latin-1", "ignore")
        .decode("utf-8", "replace")
    )
    start = 0
    while start < len(seg) and seg[start] in " \t":
        start += 1
    if start >= len(seg) or seg[start] not in "{[":
        return None
    open_c, close_c = (seg[start], "}" if seg[start] == "{" else "]")
    depth, in_str, esc = 0, False, False
    for k in range(start, len(seg)):
        ch = seg[k]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == open_c:
            depth += 1
        elif ch == close_c:
            depth -= 1
            if depth == 0:
                return json.loads(seg[start : k + 1])
    return None


def fmt_pct(x) -> str:
    return f"{x:+.2f}%"


def print_signals(indices_data: dict) -> None:
    st = indices_data.get("short_term_signal", "?")
    lt = indices_data.get("long_term_signal", "?")
    print(f"[신호등] 단기 {SIGNAL_EMOJI.get(st, st)}({st}) / 장기 {SIGNAL_EMOJI.get(lt, lt)}({lt})")


def print_big_picture(bp: dict) -> None:
    print("[빅픽처]")
    for mk in ("kospi", "kosdaq"):
        d = bp.get(mk)
        if not d:
            continue
        dds = [x for x in d.get("distribution_days", []) if x.get("is_active")]
        dd_desc = ", ".join(f"{x['occurred_date']}({x['price_change_percent']}%)" for x in dds[:5])
        status = STATUS_KO.get(d.get("status"), d.get("status"))
        print(
            f"  {mk.upper()}: {status} | 랠리 {d.get('rally_day_count', 0)}일차 | "
            f"활성 분산일 {len(dds)}개 [{dd_desc}] | 최근 FTD {d.get('last_ftd_date')}"
        )


def print_indices(indices_data: dict) -> None:
    print("[지수]")
    for idx in indices_data.get("indices", []):
        print(
            f"  {idx['index_name']}: {idx['current_value']:,} ({fmt_pct(idx['price_change_percent'])}) | "
            f"상승 {idx['rising_stocks']} / 하락 {idx['falling_stocks']} | "
            f"상한가 {idx['upper_limit_stocks']} / 하한가 {idx['lower_limit_stocks']}"
        )


def print_breadth(mm: dict) -> None:
    rows = mm.get("data", [])[-5:]
    if not rows:
        return
    print("[Breadth 최근 5일] (날짜 | KOSPI | <20MA% | <200MA% | 52주 신고/신저 | ADR K/Q)")
    for r in rows:
        date = next(iter(r.values()))  # 첫 키(일자)가 인코딩 깨질 수 있어 값으로 접근
        print(
            f"  {date} | {r['KOSPI']:,} | {r['20down_ratio'] * 100:.1f}% | {r['200down_ratio'] * 100:.1f}% | "
            f"{r['52W_High_count']}/{r['52W_Low_count']} | {r['ADR(KOSPI)']}/{r['ADR(KOSDAQ)']}"
        )


def print_credit(cb: dict) -> None:
    rows = (cb.get("data") or cb.get("credit_balance") or [])
    if isinstance(cb, list):
        rows = cb
    rows = rows[-5:]
    if not rows:
        return
    print("[신용/수급 최근 5일] (날짜 | 신용잔고(억) | 예탁금(억) | 신용/예탁금% | 반대매매(억))")
    for r in rows:
        print(
            f"  {r['date']} | {r['total_credit']:,} | {r['investor_deposit']:,} | "
            f"{r['credit_deposit_ratio']:.1f}% | {r.get('margin_call_amount', '-')}"
        )


def main() -> int:
    try:
        html = fetch_html(URL)
    except Exception as e:
        print(f"ERROR: 페이지 fetch 실패 — {e}", file=sys.stderr)
        return 1

    indices_data = extract_json(html, "initialMarketIndicesData")
    bp = extract_json(html, "initialBigPictureData")
    mm = extract_json(html, "initialMarketMonitorData")
    cb = extract_json(html, "initialCreditBalanceData")

    if not indices_data and not bp:
        print("ERROR: payload 파싱 실패 — 페이지 구조 변경 가능성", file=sys.stderr)
        return 1

    print(f"=== StockEasy 시장 신호 ({URL}) ===")
    if indices_data:
        print_signals(indices_data)
        print_indices(indices_data)
    if bp:
        print_big_picture(bp)
    if mm:
        print_breadth(mm)
    if cb:
        print_credit(cb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
