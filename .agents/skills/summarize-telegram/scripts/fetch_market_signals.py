#!/usr/bin/env python3
"""StockEasy 시장 신호 수집기.

stockeasy.intellio.kr의 `/stockdata/api/v1/market/*` JSON API에서 시장 데이터를
직접 받아 요약 출력한다.

2026-08-14 이전에는 페이지 HTML의 SSR RSC payload를 파싱했으나, 사이트가
클라이언트 렌더링으로 전환하면서 payload가 사라져 수집이 실패했다. 지금은
페이지가 실제로 호출하는 API를 같은 방식으로 호출한다.

의존성: stdlib만 사용 (urllib, json).
실패 시 stderr에 에러 출력 후 exit 1 — 호출측(스킬)은 실패해도 브리핑을 계속 진행한다.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

_STOCK_INFO_DIR = Path(__file__).resolve().parents[2] / "analyze-stock" / "scripts"
if str(_STOCK_INFO_DIR) not in sys.path:
    sys.path.insert(0, str(_STOCK_INFO_DIR))

import http_cache  # noqa: E402  (경로 주입 후에만 import된다)

PAGE_URL = "https://stockeasy.intellio.kr/market-analysis?tab=overview"
API_BASE = "https://stockeasy.intellio.kr/stockdata/api/v1/market"
ENDPOINTS = {
    "indices": "/indices",
    "big_picture": "/big-picture",
    "market_monitor": "/market-monitor",
    "credit_balance": "/credit-balance",
}
TIMEOUT = 20

SIGNAL_EMOJI = {"red": "🔴", "yellow": "🟡", "green": "🟢"}
STATUS_KO = {
    "market_in_correction": "조정장",
    "rally_attempt": "랠리 시도",
    "confirmed_uptrend": "상승 추세 확인",
    "uptrend_under_pressure": "상승 추세 압박",
}


def fetch_api(name: str):
    """market API 하나를 호출해 JSON을 반환한다. 실패하면 (None, 사유)."""
    url = API_BASE + ENDPOINTS[name]
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": PAGE_URL,
        },
    )
    # cash_deploy_check도 같은 엔드포인트를 부른다 — 브리핑 한 번에 두 번 나가던 호출이다.
    cached = http_cache.load(url, authed=False)
    if cached is not None:
        try:
            return json.loads(cached.decode("utf-8")), None
        except ValueError:
            pass

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
        payload = json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:  # 네트워크 오류·JSON 파싱 실패 등
        return None, str(e)[:80]
    http_cache.store(url, body, authed=False)
    return payload, None


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
    data, errors = {}, {}
    for name in ENDPOINTS:
        data[name], err = fetch_api(name)
        if err:
            errors[name] = err

    indices_data, bp = data["indices"], data["big_picture"]
    mm, cb = data["market_monitor"], data["credit_balance"]

    if not indices_data and not bp:
        detail = ", ".join(f"{k}={v}" for k, v in errors.items()) or "빈 응답"
        print(f"ERROR: 시장 API 호출 실패 — {detail}", file=sys.stderr)
        return 1

    print(f"=== StockEasy 시장 신호 ({API_BASE}) ===")
    if indices_data:
        print_signals(indices_data)
        print_indices(indices_data)
    if bp:
        print_big_picture(bp)
    if mm:
        print_breadth(mm)
    if cb:
        print_credit(cb)
    # 일부만 실패한 경우: 받은 부분은 출력하고 누락 사실을 남긴다
    for name, err in errors.items():
        print(f"[누락] {name} — {err}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
