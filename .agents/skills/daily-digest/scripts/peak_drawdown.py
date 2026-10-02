#!/usr/bin/env python3
"""보유 종목의 고점 대비 낙폭 경보(2개 축) + 계좌 MDD 판정기.

`extract_portfolio.py`가 만든 포트폴리오 스냅샷을 입력으로 받아 종목별 낙폭을 **두 축**으로
계산하고, 둘 다 -10/-15/-20/-30% 밴드로 경보한다.

1. **52주 시장 고점 축** — 최근 250거래일 최고 **종가**(StockEasy 정규장 일봉) 대비 하락률.
   내가 사기 전에 형성된 고점까지 포함하므로 "이 종목이 시장에서 얼마나 밀렸나"를 답한다.
2. **계좌 기록 고점 축** — `output/portfolio/` 스냅샷 이력에 기록된 최고 **현재가**(시트 수집가)
   대비 하락률. 내가 관측을 시작한 뒤의 고점만 보므로 "내 보유 구간에 얼마나 반납했나"를
   답하고, trailing stop이 실제로 걸리는 자리와 훨씬 가깝다. 네트워크가 필요 없어 시세
   수집이 실패한 종목에도 붙는다.

두 축은 고점의 정의도 가격 기준(시장 종가 vs 시트 수집가)도 다르다. 같은 룰 번호로 합산하지
않으며, 출력의 모든 판정 문장은 어느 축에서 걸렸는지를 명시한다.

같은 실행에서 과거 스냅샷들의 잔고 최고치 대비 계좌 MDD도 계산해
`context/my_rules.md` 「기본 원칙 13(계좌 MDD 관리)」의 -10/-15% 발동 여부를 표기한다.

기존 룰 판정(`extract_portfolio.py`)은 **평단 기준** 수익률만 본다. 이 스크립트는 **고점 기준**
축을 따로 본다. 크게 오른 뒤 꺾이는 종목은 평단 기준으로는 여전히 큰 수익이라 어떤 경고도
받지 못하므로, 「매매규칙 3(추세 기반 매도)」·「매매규칙 15(trailing stop 사전 설정)」가
요구하는 추세 훼손 감지를 이 축들이 담당한다.

데이터 소스:
- 일봉 OHLCV — `invagent.datafeed.daily` (StockEasy 정규장 우선, 네이버 대체 시 꼬리표).
- 티커 해석 — `context/ticker_overrides.md` → `analyze-stock/scripts/fetch_stock_info.py` 순.
- 계좌 기록 고점 — `output/portfolio/*.md` 스냅샷의 `## 보유` 표 (외부 조회 없음).

실패 정책: 티커 미해석·시세 수집 실패는 해당 행만 사유를 표기하고 계속 진행한다(exit 0).
브리핑을 블로킹해서는 안 된다. 스냅샷 파싱 자체가 실패할 때만 exit 1.

의존성: stdlib만 사용.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from invagent.datafeed import cache as http_cache, daily
from invagent.datafeed.snapshot import CASH_SECTOR, parse_balance, parse_holdings, split_row, to_float
from invagent.datafeed.tickers import TICKER_RE, load_overrides, resolve_code

# --- 판정 임계값 -----------------------------------------------------------
# 전고점 창과 밴드는 이 스킬의 운영 기준이다. 바꾸려면 SKILL.md의 밴드→룰 매핑도 함께 고친다.
PEAK_WINDOW_DAYS = 250      # 전고점 탐색 구간 (거래일 ≈ 52주)
FETCH_CALENDAR_DAYS = 400   # 250거래일을 확보하기 위한 달력일 요청 폭
DRAWDOWN_BANDS = (-10.0, -15.0, -20.0, -30.0)   # 종목 경보 밴드
ACCOUNT_MDD_BANDS = (-10.0, -15.0)              # 「기본 원칙 13」 계좌 MDD 밴드
# 다음 밴드에 이만큼(%p) 안으로 들어오면 발동 전에 미리 경고한다. 밴드가 요구하는 대응
# (계좌 = 레버리지 정리·현금 확보·신규 매수 금지 / 종목 = trailing stop·분할 매도 기준 확정)은
# 밟은 뒤 준비하면 늦기 때문에 도달 전에 알리는 것이 이 상수들의 목적이다.
# 계좌와 종목을 따로 둔 이유 = 밴드 간격이 다르다(계좌 -10/-15 vs 종목 -10/-15/-20/-30).
ACCOUNT_MDD_WARN_MARGIN_PP = 2.0
DRAWDOWN_WARN_MARGIN_PP = 2.0
# 보유 종목 시세를 동시에 받는 최대 개수. 종목 수만큼 왕복이 직렬로 쌓이던 것을 덮되,
# 한꺼번에 몰지 않도록 상한을 둔다. 호스트당 동시 요청 상한(`datafeed.ratelimit`)과 같은 2 —
# 그보다 많은 스레드는 슬롯을 기다릴 뿐이다.
MAX_FETCH_WORKERS = 2
# 90,000/100,000-1 은 부동소수점에서 -9.999999999999998이 된다. 표에 -10.0%로 찍히는 값이
# 밴드에 안 걸리는 어긋남을 막기 위한 경계 허용치.
BAND_EPS = 1e-9

BAND_MARK = {-10.0: "🟡", -15.0: "🟠", -20.0: "🔴", -30.0: "⛔"}

# 임박 경고가 함께 출력하는 대응 문구. `context/my_rules.md` 「기본 원칙 13」 원문을 요약한 것이라
# 룰 본문이 바뀌면 여기도 같이 고친다.
ACCOUNT_MDD_ACTION = {
    -10.0: "「기본 원칙 13」 = 레버리지 전량 정리 · 신규 매수 중단 · 확신 낮은 종목부터 축소해 현금 30% 이상 확보.",
    -15.0: "「기본 원칙 13」 = 5거래일 신규 매수 금지 · 포트폴리오와 룰 위반 복기 후에만 매매 재개.",
}

SECTION_TITLE = "## 전고점 낙폭 (52주 시장 고점 · 계좌 기록 고점)"
# 과거 실행이 남긴 제목들. `append_section`이 함께 제거해야 재실행 시 섹션이 둘로 갈라지지 않는다.
# `output/`은 gitignore라 이미 쓰인 스냅샷을 커밋으로 마이그레이션할 수 없으므로 코드가 정리한다.
LEGACY_SECTION_TITLES = ("## 전고점 낙폭 (52주 최고 종가 기준)",)


# --- 낙폭 계산 -------------------------------------------------------------


def band_for(drawdown: float | None) -> float | None:
    """낙폭(%) → 발동한 가장 깊은 밴드. 어디에도 안 걸리면 None."""
    if drawdown is None:
        return None
    hits = [b for b in DRAWDOWN_BANDS if drawdown <= b + BAND_EPS]
    return min(hits) if hits else None


def band_label(band: float | None) -> str:
    return f"{BAND_MARK[band]} {band:.0f}%" if band is not None else "—"


def pending_bands(drawdown: float | None, peak: float | None, bands) -> list[dict]:
    """아직 안 밟은 밴드마다 발동 지점과 남은 거리(%p). 얕은 밴드부터."""
    if drawdown is None or not peak:
        return []
    return [
        {"band": band, "trigger": peak * (1 + band / 100), "gap_pp": drawdown - band}
        for band in sorted(bands, reverse=True)
        if drawdown > band + BAND_EPS
    ]


def approaching_band(pending: list[dict], margin: float) -> dict | None:
    """마진(%p) 안으로 들어온 가장 가까운 미발동 밴드. 경계값은 경고하는 쪽으로 센다.

    비교는 **표에 찍히는 소수 첫째 자리로 반올림해서** 한다. -12.969%는 표에 -13.0%로 나오는데
    이때 -15%까지 남은 거리도 2.0%p로 읽히므로, 내부값 2.031%p로 경고를 빼면 출력끼리 어긋난다.
    (`BAND_EPS`가 밴드 발동에서 막는 것과 같은 종류의 어긋남이다.)
    """
    return next((p for p in pending if round(p["gap_pp"], 1) <= margin + BAND_EPS), None)


def alert_label(band: float | None, approach: dict | None) -> str:
    """발동 밴드가 있으면 그것, 없으면 임박 표시. 발동을 임박이 가리지 않는다."""
    if band is not None:
        return band_label(band)
    if approach:
        return f"⚠️ {approach['band']:.0f}%"
    return "—"


def peak_drawdown(bars: list[dict]) -> dict:
    """일봉 → 전고점·전고점 일자·현재 종가·낙폭."""
    window = bars[-PEAK_WINDOW_DAYS:]
    peak_bar = max(window, key=lambda b: b["close"])
    last_close = window[-1]["close"]
    return {
        "peak": peak_bar["close"],
        "peak_date": peak_bar["date"],
        "close": last_close,
        "drawdown": (last_close / peak_bar["close"] - 1) * 100 if peak_bar["close"] else None,
        "bars": len(window),
    }


def analyze_holdings(
    holdings: list[dict],
    overrides: dict[str, str],
    record_history: dict[str, list[tuple[str, float]]] | None = None,
    today: str = "",
) -> list[dict]:
    """보유 종목별 낙폭 결과 리스트. 실패 종목도 사유를 담아 그대로 포함한다.

    계좌 기록 축(`entry["record"]`)은 시세 조회 성공 여부와 무관하게 먼저 채운다 —
    네트워크가 죽어도 이 축은 남아야 한다.
    """
    history = record_history or {}
    results: list[dict] = []
    for row in holdings:
        name = row["종목"]
        entry = {
            "종목": name,
            "수익률": row.get("수익률", ""),
            "비중": row.get("비중", ""),
            "error": None,
        }
        entry["record"] = record_drawdown(name, to_float(row.get("현재가", "")), today, history)
        code, err = resolve_code(name, overrides)
        if code:
            entry["code"] = code
        else:
            entry["error"] = f"티커 미해석 — {err}"
        results.append(entry)

    # 시세는 종목끼리 독립이라 동시에 받는다. 결과는 `results`의 원래 자리에 되돌려
    # 넣으므로 완료 순서가 출력 순서를 흔들지 않는다.
    pending = [e for e in results if e.get("code")]
    if pending:
        def fetch(entry: dict):
            return daily.fetch_daily_bars(entry["code"], FETCH_CALENDAR_DAYS, asof=today or None)

        with ThreadPoolExecutor(max_workers=min(MAX_FETCH_WORKERS, len(pending))) as pool:
            fetched = list(pool.map(fetch, pending))
        for entry, (bars, ferr, bar_note) in zip(pending, fetched):
            entry["bar_note"] = bar_note
            if not bars:
                entry["error"] = f"시세 수집 실패 — {ferr or '응답 없음'}"
                continue
            entry.update(peak_drawdown(bars))
            entry["band"] = band_for(entry["drawdown"])
            entry["approach"] = approaching_band(
                pending_bands(entry["drawdown"], entry["peak"], DRAWDOWN_BANDS),
                DRAWDOWN_WARN_MARGIN_PP,
            )
    return results


# --- 계좌 MDD --------------------------------------------------------------


def account_mdd(snapshot_dir: Path, today_balance: float | None, today: str) -> dict:
    """과거 스냅샷들의 잔고 최고치 대비 오늘 잔고의 MDD."""
    history: list[tuple[str, float]] = []
    for path in sorted(snapshot_dir.glob("*.md")):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem) or path.stem > today:
            continue
        balance = parse_balance(path.read_text(encoding="utf-8"))
        if balance:
            history.append((path.stem, balance))
    if today_balance:
        history = [(d, b) for d, b in history if d != today] + [(today, today_balance)]
    if not history:
        return {"error": "잔고 이력 없음"}

    history.sort()
    running_peak = 0.0
    first_breaches: dict[str, str] = {}
    for sample_date, balance in history:
        running_peak = max(running_peak, balance)
        sample_mdd = (balance / running_peak - 1) * 100 if running_peak else 0.0
        for band in ACCOUNT_MDD_BANDS:
            key = f"{band:.0f}"
            if sample_mdd <= band + BAND_EPS and key not in first_breaches:
                first_breaches[key] = sample_date

    peak_date, peak = max(history, key=lambda item: item[1])
    current = today_balance or history[-1][1]
    mdd = (current / peak - 1) * 100
    hits = [b for b in ACCOUNT_MDD_BANDS if mdd <= b + BAND_EPS]

    # 아직 안 밟은 밴드마다 발동 잔고와 남은 거리를 계산해 둔다.
    # 밟은 뒤 알리면 「기본 원칙 13」의 대응을 준비할 시간이 없다.
    pending = pending_bands(mdd, peak, ACCOUNT_MDD_BANDS)
    for p in pending:
        p["gap_won"] = current - p["trigger"]  # 발동까지 남은 금액
    approaching = approaching_band(pending, ACCOUNT_MDD_WARN_MARGIN_PP)

    return {
        "peak": peak,
        "peak_date": peak_date,
        "current": current,
        "mdd": mdd,
        "band": min(hits) if hits else None,
        "pending": pending,
        "approaching": approaching,
        "samples": len(history),
        "from": history[0][0],
        "first_breaches": first_breaches,
        "error": None,
    }


# --- 계좌 기록 고점 낙폭 ----------------------------------------------------


def load_record_history(
    snapshot_dir: Path, asof: str | None = None
) -> tuple[dict[str, list[tuple[str, float]]], dict]:
    """스냅샷 이력 전체 → 종목명별 (일자, 현재가) 리스트 + 구간 메타.

    `## 보유` 표를 파싱할 수 없는 스냅샷(손으로 쓴 파일 등)은 조용히 건너뛴다.
    이 축의 존재 이유가 "네트워크 없이도 남는 낙폭"이므로 한 파일의 형식 차이로 죽으면 안 된다.
    """
    history: dict[str, list[tuple[str, float]]] = {}
    files = sorted(snapshot_dir.glob("*.md")) if snapshot_dir.is_dir() else []
    files = [
        path for path in files
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem)
        and (asof is None or path.stem <= asof)
    ]
    valid_dates: list[str] = []
    for path in files:
        try:
            rows = parse_holdings(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        valid_dates.append(path.stem)
        for row in rows:
            name = row.get("종목")
            price = to_float(row.get("현재가", ""))
            if name and price:
                history.setdefault(name, []).append((path.stem, price))

    meta = {
        "total_files": len(files),
        "valid_files": len(valid_dates),
        "from": valid_dates[0] if valid_dates else None,
        "to": valid_dates[-1] if valid_dates else None,
    }
    return history, meta


def record_drawdown(
    name: str,
    today_price: float | None,
    today: str,
    history: dict[str, list[tuple[str, float]]],
) -> dict:
    """스냅샷 기록 최고 현재가 대비 오늘 낙폭.

    오늘 일자 항목은 디스크 값을 버리고 호출자가 넘긴 값으로 덮어쓴다(`account_mdd`와 같은 규약).
    고점·현재가 모두 시트 수집가라 축 내부적으로는 같은 기준끼리 비교된다.
    """
    samples = [(d, p) for d, p in history.get(name, []) if d <= today and d != today]
    if today_price:
        samples.append((today, today_price))
    if not samples:
        return {"error": "스냅샷 기록 없음"}

    peak_date, peak = max(samples, key=lambda item: item[1])
    current = today_price or samples[-1][1]
    drawdown = (current / peak - 1) * 100 if peak else None
    return {
        "peak": peak,
        "peak_date": peak_date,
        "current": current,
        "drawdown": drawdown,
        "band": band_for(drawdown),
        "approach": approaching_band(
            pending_bands(drawdown, peak, DRAWDOWN_BANDS), DRAWDOWN_WARN_MARGIN_PP
        ),
        "samples": len(samples),
        "error": None,
    }


# --- 지속 상태 ------------------------------------------------------------


def load_state(path: Path | None) -> dict:
    """이전 MDD 상태를 읽는다. 명시한 파일이 없으면 새 상태를 반환한다."""
    if path is None or not path.exists():
        return {"schema_version": 1, "account_mdd": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("지원하지 않는 MDD 상태 형식")
    data.setdefault("account_mdd", {})
    return data


def save_state(path: Path, state: dict) -> None:
    """MDD 상태를 같은 디렉토리의 임시 파일을 거쳐 원자적으로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def update_mdd_state(
    account: dict,
    previous: dict,
    asof: str,
    session_dates: list[str],
    *,
    review_completed: bool = False,
    resume_authorized: bool = False,
) -> dict:
    """최초 MDD 발동일과 매수 중단 상태를 유지한다.

    -10% 해제 조건은 규칙에 없으므로 자동 해제하지 않는다. 재개는 사용자 입력으로만 기록한다.
    -15%는 최초 발동일부터 거래일 5개와 복기 완료를 각각 기록하되 재개를 추론하지 않는다.
    """
    previous_asof = previous.get("account_mdd", {}).get("asof")
    if previous_asof and previous_asof > asof:
        return json.loads(json.dumps(previous))
    state = json.loads(json.dumps(previous))
    state["schema_version"] = 1
    item = state.setdefault("account_mdd", {})
    band = account.get("band") if not account.get("error") else None
    first_breaches = account.get("first_breaches", {})
    if (first_breaches.get("-10") or (band is not None and band <= -10.0)) and not item.get("first_10_breach_date"):
        item["first_10_breach_date"] = first_breaches.get("-10", asof)
    if (first_breaches.get("-15") or (band is not None and band <= -15.0)) and not item.get("first_15_breach_date"):
        item["first_15_breach_date"] = first_breaches.get("-15", asof)
    if review_completed:
        item["review_completed"] = True
        item["review_completed_at"] = asof
    else:
        item.setdefault("review_completed", False)
    if resume_authorized:
        item["resume_authorized"] = True
        item["resume_authorized_at"] = asof
    else:
        item.setdefault("resume_authorized", False)

    first_15 = item.get("first_15_breach_date")
    sessions = sorted({d for d in session_dates if first_15 and first_15 <= d <= asof})
    if first_15 and session_dates:
        item["five_session_count"] = min(len(sessions), 5)
        item["five_session_complete"] = len(sessions) >= 5
    elif first_15:
        item["five_session_count"] = None
        item["five_session_complete"] = False
    else:
        item["five_session_count"] = 0
        item["five_session_complete"] = False
    item["pause_active"] = bool(item.get("first_10_breach_date") and not item["resume_authorized"])
    item["release_policy"] = "manual_user_resume_required"
    item["asof"] = asof
    return state


def load_session_dates(path: Path | None, asof: str) -> list[str]:
    """검증된 거래 세션 목록을 읽는다. 파일이 없으면 계산하지 않는다."""
    if path is None:
        return []
    raw = path.read_text(encoding="utf-8")
    try:
        loaded = json.loads(raw)
        values = loaded.get("sessions", []) if isinstance(loaded, dict) else loaded
    except json.JSONDecodeError:
        values = [line.strip() for line in raw.splitlines() if line.strip()]
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError("거래 세션 파일은 날짜 문자열 목록이어야 한다")
    invalid = [value for value in values if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)]
    if invalid:
        raise ValueError(f"잘못된 거래 세션 날짜: {invalid[0]}")
    return sorted({value for value in values if value <= asof})


# --- 렌더링 ----------------------------------------------------------------


def won(value: float | None) -> str:
    return f"₩{value:,.0f}" if value is not None else "-"


def ymd(raw: str) -> str:
    """일봉 날짜 `20260622` → `2026-06-22`."""
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) == 8 and raw.isdigit() else raw


def record_cells(record: dict) -> str:
    """계좌 기록 축 2개 셀(고점(일자) · 낙폭). 경보·임박은 낙폭 셀에 인라인으로 붙인다."""
    if not record or record.get("error"):
        return "- | -"
    if record["band"] is not None:
        mark = f" {BAND_MARK[record['band']]}"
    elif record.get("approach"):
        mark = f" ⚠️{record['approach']['band']:.0f}%"
    else:
        mark = ""
    return (
        f"{won(record['peak'])} ({record['peak_date']}) | "
        f"{record['drawdown']:+.1f}%{mark}"
    )


def approach_line(axis: str, items: list[tuple[str, dict, dict]]) -> str | None:
    """축별 임박 경고 한 줄. 발동가를 금액으로 찍어 바로 주문에 쓸 수 있게 한다."""
    if not items:
        return None
    desc = ", ".join(
        f"{name} {r['drawdown']:+.1f}% → {a['band']:.0f}% 발동가 {won(a['trigger'])} "
        f"({a['gap_pp']:.1f}%p 남음)"
        for name, r, a in sorted(items, key=lambda it: it[2]["gap_pp"])
    )
    return f"- ⚠️ 임박({axis}): {desc}"


def render(
    results: list[dict],
    account: dict,
    record_meta: dict | None = None,
    state: dict | None = None,
) -> str:
    meta = record_meta or {}
    lines = [
        SECTION_TITLE,
        "",
        f"> **52주 시장 고점** = 최근 {PEAK_WINDOW_DAYS}거래일 최고 **종가**(StockEasy 정규장 일봉). "
        f"내가 사기 전 고점까지 포함한다.",
        "> **계좌 기록 고점** = `output/portfolio/` 스냅샷에 기록된 최고 **현재가**(시트 수집가). "
        "내가 관측을 시작한 뒤의 고점만 본다.",
        "> 두 축은 고점의 정의도 가격 기준도 다르고, 평단 기준 룰(「매매규칙 6·7」)과도 다른 축이다.",
        "",
        "| 종목 | 52주 고점(일자) | 현재 종가 | 52주 낙폭 | 경보 | "
        "계좌 기록 고점(일자) | 기록 낙폭 | 평단 수익률 | 비중 |",
        "| --- | ---: | ---: | ---: | :-: | ---: | ---: | ---: | ---: |",
    ]
    for r in results:
        record = r.get("record") or {}
        if r["error"]:
            lines.append(
                f"| {r['종목']} | ({r['error']}) | - | - | - | "
                f"{record_cells(record)} | {r['수익률']} | {r['비중']} |"
            )
            continue
        lines.append(
            f"| {r['종목']} | {won(r['peak'])} ({ymd(r['peak_date'])}) | {won(r['close'])} | "
            f"{r['drawdown']:+.1f}% | {alert_label(r['band'], r.get('approach'))} | "
            f"{record_cells(record)} | {r['수익률']} | {r['비중']} |"
        )

    triggered = [r for r in results if not r["error"] and r["band"] is not None]
    if triggered:
        desc = ", ".join(
            f"{r['종목']} {r['drawdown']:+.1f}% ({band_label(r['band'])})"
            for r in sorted(triggered, key=lambda r: r["drawdown"])
        )
        lines += ["", f"- 전고점 낙폭 판정(52주 시장 종가 기준): {desc}"]
    else:
        lines += ["", "- 전고점 낙폭 판정(52주 시장 종가 기준): 해당 없음"]

    market_near = approach_line(
        "52주 시장 종가 기준",
        [(r["종목"], r, r["approach"]) for r in results if not r["error"] and r.get("approach")],
    )
    if market_near:
        lines.append(market_near)

    rec_hit = [
        r for r in results
        if (r.get("record") or {}).get("band") is not None
    ]
    if rec_hit:
        desc = ", ".join(
            f"{r['종목']} {r['record']['drawdown']:+.1f}% "
            f"({band_label(r['record']['band'])})"
            for r in sorted(rec_hit, key=lambda r: r["record"]["drawdown"])
        )
        lines.append(f"- 기록 낙폭 판정(계좌 스냅샷 기록 기준): {desc}")
    else:
        lines.append("- 기록 낙폭 판정(계좌 스냅샷 기록 기준): 해당 없음")

    record_near = approach_line(
        "계좌 스냅샷 기록 기준",
        [
            (r["종목"], r["record"], r["record"]["approach"])
            for r in results
            if (r.get("record") or {}).get("approach")
        ],
    )
    if record_near:
        lines.append(record_near)

    lines.append(
        "- (주의) 두 축은 고점의 정의도 가격 기준도 다르다(시장 종가 vs 시트 수집가). "
        "같은 룰 번호로 합산하지 않고 어느 축에서 걸렸는지를 밝혀 각각 읽는다."
    )
    if meta.get("valid_files"):
        skipped = meta.get("total_files", 0) - meta["valid_files"]
        skip_note = f" ({skipped}개는 형식이 달라 제외)" if skipped > 0 else ""
        lines.append(
            f"- (주의) 계좌 기록 고점은 스냅샷 {meta['valid_files']}개"
            f"({meta['from']}~{meta['to']}) 안에서만 찾은 값이라 그 이전 고점·장중 등락은 "
            f"담기지 않는다{skip_note}. 매도 후 재매수 구간도 구분하지 않고 전체 기록의 "
            "최고 현재가를 쓴다."
        )

    fallback: dict[str, list[str]] = {}
    for r in results:
        if r.get("bar_note") and not r["error"]:
            fallback.setdefault(r["bar_note"], []).append(r["종목"])
    for note, names in fallback.items():
        lines.append(f"- (주의) 52주 시장 축 {', '.join(names)}: {note}")

    failed = [r for r in results if r["error"]]
    if failed:
        lines.append(
            f"- 미판정 {len(failed)}종목: "
            + ", ".join(f"{r['종목']}({r['error']})" for r in failed)
            + " → `context/ticker_overrides.md`에 `종목명 = 티커` 추가로 고정 가능"
        )

    if account.get("error"):
        lines.append(f"- 계좌 MDD(「기본 원칙 13」): 계산 불가 — {account['error']}")
    else:
        state_label = (
            f"← **발동 ({account['band']:.0f}%)**" if account["band"] is not None else "→ 미발동"
        )
        lines.append(
            f"- 계좌 MDD(「기본 원칙 13」): 잔고 {won(account['current'])} · "
            f"스냅샷 고점 {won(account['peak'])}({account['peak_date']}) · "
            f"{account['mdd']:+.1f}% {state_label}"
        )

        near = account.get("approaching")
        if near:
            lines.append(
                f"- ⚠️⚠️ **계좌 MDD {near['band']:.0f}% 임박 — 남은 거리 "
                f"{near['gap_pp']:.1f}%p({won(near['gap_won'])}).** "
                f"잔고가 {won(near['trigger'])} 아래로 내려가면 발동한다. "
                f"{ACCOUNT_MDD_ACTION[near['band']]}"
            )
        for p in account.get("pending", []):
            lines.append(
                f"- 계좌 MDD {p['band']:.0f}% 발동선: {won(p['trigger'])} "
                f"(현 잔고에서 {p['gap_pp']:.1f}%p · {won(p['gap_won'])} 남음)"
            )

        lines.append(
            f"- (주의) 계좌 고점은 스냅샷 보유 구간({account['from']}~, {account['samples']}개) "
            "기준이며 입출금을 보정하지 않는다."
        )
    persisted = (state or {}).get("account_mdd", {}) if isinstance(state, dict) else {}
    if persisted.get("first_10_breach_date"):
        pause = "유지" if persisted.get("pause_active") else "사용자 재개 승인 기록"
        lines.append(
            f"- 계좌 MDD 지속 상태: -10% 최초 발동 {persisted['first_10_breach_date']} · "
            f"신규 매수 중단 {pause}. 자동 해제 기준은 정하지 않는다."
        )
    if persisted.get("first_15_breach_date"):
        count = persisted.get("five_session_count")
        count_text = f"{count}/5" if count is not None else "미확인(거래 세션 입력 없음)"
        lines.append(
            f"- -15% 최초 발동 {persisted['first_15_breach_date']} · 5거래일 "
            f"{count_text} · 복기 완료 "
            f"{'확인' if persisted.get('review_completed') else '미확인'} · 재개는 사용자 명시가 필요하다."
        )
    return "\n".join(lines) + "\n"


def append_section(path: Path, section: str) -> None:
    """스냅샷 끝에 섹션을 쓴다. 같은 제목 + 과거 제목 섹션을 모두 걷어내고 쓴다(멱등)."""
    text = path.read_text(encoding="utf-8")
    for title in (SECTION_TITLE, *LEGACY_SECTION_TITLES):
        while True:
            lines = text.split("\n")
            start = next((i for i, l in enumerate(lines) if l.strip() == title), None)
            if start is None:
                break
            end = next(
                (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines)
            )
            text = "\n".join(lines[:start] + lines[end:])
    path.write_text(text.rstrip("\n") + "\n\n" + section, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="포트폴리오 스냅샷 경로")
    parser.add_argument("--no-cache", action="store_true", help="캐시를 쓰지 않고 매번 새로 받는다 (캐시 오염 의심 시)")
    parser.add_argument("--append", action="store_true", help="스냅샷 파일에 섹션을 덧붙인다")
    parser.add_argument("--overrides", type=Path, help="티커 override 파일 경로")
    parser.add_argument("--portfolio-dir", type=Path, help="계좌 MDD용 스냅샷 디렉토리")
    parser.add_argument("--state-file", type=Path, help="MDD 최초 발동일과 재개 상태 JSON")
    parser.add_argument("--sessions", type=Path, help="검증된 거래일 날짜 목록(JSON 또는 한 줄 한 날짜)")
    parser.add_argument("--review-completed", action="store_true", help="-15% 발동 후 복기 완료를 명시한다")
    parser.add_argument("--resume-authorized", action="store_true", help="사용자가 신규 매수 재개를 명시했다")
    parser.add_argument("--json", action="store_true", help="렌더링 대신 구조화 JSON을 출력한다")
    args = parser.parse_args(argv)
    if args.no_cache:
        http_cache.disable()

    try:
        text = args.snapshot.read_text(encoding="utf-8")
        holdings = parse_holdings(text)
    except (OSError, ValueError) as e:
        print(f"ERROR: 스냅샷 파싱 실패 — {e}", file=sys.stderr)
        return 1

    snapshot_dir = args.portfolio_dir or args.snapshot.parent
    history, history_meta = load_record_history(snapshot_dir, args.snapshot.stem)
    results = analyze_holdings(
        holdings, load_overrides(args.overrides), history, args.snapshot.stem
    )
    account = account_mdd(snapshot_dir, parse_balance(text), args.snapshot.stem)
    try:
        state_path = args.state_file or snapshot_dir / ".peak_drawdown_state.json"
        previous_state = load_state(state_path)
        # Historical replays must not inherit a state written for a later day.
        if previous_state.get("account_mdd", {}).get("asof", "") > args.snapshot.stem:
            previous_state = {"schema_version": 1, "account_mdd": {}}
        state = update_mdd_state(
            account,
            previous_state,
            args.snapshot.stem,
            load_session_dates(args.sessions, args.snapshot.stem),
            review_completed=args.review_completed,
            resume_authorized=args.resume_authorized,
        )
        if state != previous_state and args.snapshot.stem >= previous_state.get("account_mdd", {}).get("asof", args.snapshot.stem):
            save_state(state_path, state)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"ERROR: MDD 상태 처리 실패 — {e}", file=sys.stderr)
        return 1
    section = render(results, account, history_meta, state)

    if args.append:
        append_section(args.snapshot, section)
        print(f"추가: {args.snapshot}", file=sys.stderr)
    if args.json:
        print(json.dumps({
            "asof": args.snapshot.stem,
            "holdings": results,
            "account": account,
            "record_history": history_meta,
            "state": state,
            "rendered": section,
        }, ensure_ascii=False, indent=2))
    else:
        print(section, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
