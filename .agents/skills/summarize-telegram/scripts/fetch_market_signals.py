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
from concurrent.futures import ThreadPoolExecutor
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

# --- 「레버리지 규칙 3(변동성 레버리지 청산)」 판정 상수 -------------------------
# 룰 원문: "지수가 ±5% 이상 변동하는 날이 최근 10거래일 이내에 3일 이상 나타나면 레버리지
# 투자는 모두 정리하고 신규로 진입하지도 않는다. (음의 복리 효과 방지) 상승 방향으로 일관적인
# 변동은 예외로 한다."
# 아래는 룰이 수치로 정하지 않은 부분에 대한 이 스킬의 운영 선택이다 (SKILL.md와 함께 고친다).
VOLATILE_DAY_PCT = 5.0        # 전일 종가 대비 종가 등락률. 장중 고저 폭은 쓰지 않는다
VOLATILE_WINDOW_DAYS = 10     # 거래일 기준. 휴장일은 행 자체가 없어 자동으로 빠진다
VOLATILE_DAY_TRIGGER = 3      # 이 수 이상이면 발동
# 판정 대상은 **지수뿐**이다. 섹터 레버리지는 「레버리지 규칙 2」대로 사용자가 판단한다.
LEVERAGE_MARKETS = ("KOSPI", "KOSDAQ")

# --- 「레버리지 규칙 2」 반대매매 클라이맥스 예외 -----------------------------
# 룰 원문: 반대매매 금액이 평소(직전 120거래일) 대비 +2σ 이상인 날은 「레버리지 규칙 3」
# 발동 중이어도 **지수** 레버리지를 분할 신규 매수할 수 있다.
# σ 배수(2.0)는 사용자가 정한 값이고, 창 길이는 이 스킬의 운영 선택이다 (SKILL.md 근거 참조).
MARGIN_CALL_SIGMA = 2.0
MARGIN_CALL_WINDOW_DAYS = 120

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


def margin_call_climax(rows: list[dict]) -> dict:
    """「레버리지 규칙 2」의 반대매매 클라이맥스 판정.

    **당일을 기준선에서 뺀다.** 오늘 값이 자기 평균·표준편차에 섞이면 값이 클수록 임계가
    함께 올라가 정작 클라이맥스에서 신호가 무뎌진다.

    판정 대상은 **지수 레버리지뿐**이다 — 섹터에는 이 예외를 적용하지 않는다.
    """
    values = [float(r["margin_call_amount"]) for r in rows if r.get("margin_call_amount") is not None]
    today = values[-1] if values else None
    baseline = values[-MARGIN_CALL_WINDOW_DAYS - 1 : -1]
    if today is None or len(baseline) < MARGIN_CALL_WINDOW_DAYS:
        return {"climax": False, "insufficient": True, "value": today,
                "threshold": None, "sigma": MARGIN_CALL_SIGMA}
    mean = sum(baseline) / len(baseline)
    var = sum((x - mean) ** 2 for x in baseline) / len(baseline)
    threshold = mean + MARGIN_CALL_SIGMA * (var ** 0.5)
    return {
        "climax": today >= threshold,
        "insufficient": False,
        "value": today,
        "mean": mean,
        "threshold": threshold,
        "sigma": MARGIN_CALL_SIGMA,
    }


def print_margin_call(rows: list[dict]) -> None:
    v = margin_call_climax(rows)
    head = f"[레버리지 규칙 2] 반대매매 클라이맥스 (직전 {MARGIN_CALL_WINDOW_DAYS}거래일 +{MARGIN_CALL_SIGMA:g}σ · 지수 한정)"
    if v["insufficient"]:
        print(f"{head}\n  ❔ 판정 불가 — 기준선 표본 부족")
        return
    state = (
        "🟢 성립 — 「레버리지 규칙 3」 발동 중이어도 지수 레버리지 분할 신규 매수 가능 (기존 물량 정리는 그대로)"
        if v["climax"]
        else "미성립"
    )
    print(f"{head}\n  반대매매 {v['value']:,.0f}억 / 임계 {v['threshold']:,.0f}억 (평균 {v['mean']:,.0f}억) → {state}")


def daily_changes(rows: list[dict], market: str) -> list[tuple[str, float]]:
    """market-monitor 종가 열 → (일자, 전일 대비 등락률%) 리스트."""
    series = [(r["일자"], r[market]) for r in rows if r.get(market)]
    return [
        (day, (close / prev - 1) * 100)
        for (_, prev), (day, close) in zip(series, series[1:])
        if prev
    ]


def leverage_liquidation(rows: list[dict]) -> dict:
    """「레버리지 규칙 3」을 지수별로 판정한다.

    market-monitor는 KOSPI·KOSDAQ 종가를 1년치 넘게 주므로 추가 호출 없이 계산된다.
    지수별로 따로 내는 이유: 레버리지 상품이 지수별로 다르다. **섹터 레버리지는 판정하지
    않는다** — 「레버리지 규칙 2」의 방향성·업황 확인은 사용자 몫이다.
    """
    out: dict[str, dict] = {}
    for market in LEVERAGE_MARKETS:
        changes = daily_changes(rows, market)
        window = changes[-VOLATILE_WINDOW_DAYS:]
        days = [(d, v) for d, v in window if abs(v) >= VOLATILE_DAY_PCT]
        insufficient = len(window) < VOLATILE_WINDOW_DAYS
        # 예외는 해당 변동일이 **전부 상승**일 때만이다. 하락이 하나라도 섞이면
        # 룰이 막으려는 음의 복리가 성립하므로 예외를 주지 않는다.
        exempt = bool(days) and all(v > 0 for _, v in days)
        fired = (
            not insufficient and len(days) >= VOLATILE_DAY_TRIGGER and not exempt
        )
        out[market] = {
            "fired": fired,
            "exempt": exempt and len(days) >= VOLATILE_DAY_TRIGGER,
            "insufficient": insufficient,
            "count": len(days),
            "days": days,
        }
    return out


def print_leverage(rows: list[dict]) -> None:
    print("[레버리지 규칙 3] 지수 ±5% 변동일 (최근 10거래일 · 섹터는 판정 대상 아님)")
    for market, v in leverage_liquidation(rows).items():
        detail = ", ".join(f"{d}({x:+.2f}%)" for d, x in v["days"]) or "없음"
        if v["insufficient"]:
            state = "❔ 판정 불가 — 일봉 부족"
        elif v["fired"]:
            state = "⛔ 발동 — 레버리지 전량 정리 · 신규 진입 금지"
        elif v["exempt"]:
            state = "✅ 예외 — 변동일이 전부 상승 방향"
        else:
            state = "✅ 미발동"
        print(f"  {market}: {v['count']}일 [{detail}] → {state}")


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


def fetch_all() -> tuple[dict, dict]:
    """네 엔드포인트를 동시에 받는다. 서로 독립이라 순서대로 기다릴 이유가 없다."""
    data, errors = {}, {}
    with ThreadPoolExecutor(max_workers=len(ENDPOINTS)) as pool:
        for name, (payload, err) in zip(ENDPOINTS, pool.map(fetch_api, ENDPOINTS)):
            data[name] = payload
            if err:
                errors[name] = err
    return data, errors


def main() -> int:
    data, errors = fetch_all()

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
        print_leverage(mm.get("data", []))
    if cb:
        print_credit(cb)
        rows = (cb.get("data") or cb.get("credit_balance") or []) if isinstance(cb, dict) else cb
        print_margin_call(rows)
    # 일부만 실패한 경우: 받은 부분은 출력하고 누락 사실을 남긴다
    for name, err in errors.items():
        print(f"[누락] {name} — {err}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
