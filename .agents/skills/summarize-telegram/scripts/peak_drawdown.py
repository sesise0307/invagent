#!/usr/bin/env python3
"""보유 종목의 고점 대비 낙폭 경보(2개 축) + 계좌 MDD 판정기.

`extract_portfolio.py`가 만든 포트폴리오 스냅샷을 입력으로 받아 종목별 낙폭을 **두 축**으로
계산하고, 둘 다 -10/-15/-20/-30% 밴드로 경보한다.

1. **52주 시장 고점 축** — 최근 250거래일 최고 **종가**(네이버 일봉) 대비 하락률.
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
- 일봉 OHLCV — 네이버 금융 `siseJson` (무인증). `stage-analysis/scripts/stage_scan.py` 재사용.
- 티커 해석 — `context/ticker_overrides.md` → `analyze-stock/scripts/fetch_stock_info.py` 순.
- 계좌 기록 고점 — `output/portfolio/*.md` 스냅샷의 `## 보유` 표 (외부 조회 없음).

실패 정책: 티커 미해석·시세 수집 실패는 해당 행만 사유를 표기하고 계속 진행한다(exit 0).
브리핑을 블로킹해서는 안 된다. 스냅샷 파싱 자체가 실패할 때만 exit 1.

의존성: stdlib만 사용.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_SKILLS_ROOT = Path(__file__).resolve().parents[2]
for _extra in ("stage-analysis/scripts", "analyze-stock/scripts"):
    _path = str(_SKILLS_ROOT / _extra)
    if _path not in sys.path:
        sys.path.insert(0, _path)

import fetch_stock_info as si_api  # noqa: E402  (경로 주입 후에만 import된다)
import stage_scan  # noqa: E402
import http_cache  # noqa: E402  (같은 경로에 있다)

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
CASH_SECTOR = "현금"
TICKER_RE = re.compile(r"^\d{6}$")
OVERRIDES_REL = Path("context") / "ticker_overrides.md"


# --- 스냅샷 파싱 -----------------------------------------------------------


def split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_holdings(text: str) -> list[dict]:
    """스냅샷의 `## 보유` 표 → 종목 dict 리스트 (현금 행 제외)."""
    lines = text.split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("## 보유"))
    except StopIteration:
        raise ValueError("스냅샷에서 '## 보유' 섹션을 찾지 못했다")

    header: list[str] | None = None
    rows: list[dict] = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        if not line.strip().startswith("|"):
            continue
        cells = split_row(line)
        if set("".join(cells)) <= {"-", ":"}:
            continue
        if header is None:
            header = cells
            continue
        row = {header[i]: cells[i] for i in range(min(len(header), len(cells)))}
        if not row.get("종목") or row.get("섹터") == CASH_SECTOR:
            continue
        rows.append(row)

    if header is None or not rows:
        raise ValueError("보유 종목 행을 하나도 파싱하지 못했다")
    return rows


def parse_balance(text: str) -> float | None:
    """스냅샷 머리말의 `잔고 ₩…` → float."""
    m = re.search(r"잔고\s*₩([\d,]+)", text)
    return float(m.group(1).replace(",", "")) if m else None


def to_float(text: str) -> float | None:
    cleaned = re.sub(r"[₩,%\s]", "", text or "")
    try:
        return float(cleaned)
    except ValueError:
        return None


# --- 티커 해석 -------------------------------------------------------------


def load_overrides(path: Path | None) -> dict[str, str]:
    """`종목명 = 123456` 라인을 읽는다. 파일이 없으면 빈 맵."""
    if path is None:
        for parent in Path(__file__).resolve().parents:
            candidate = parent / OVERRIDES_REL
            if candidate.is_file():
                path = candidate
                break
    if path is None or not path.is_file():
        return {}

    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ">", "-")) or "=" not in line:
            continue
        name, _, code = line.partition("=")
        code = code.split("#")[0].strip()
        if TICKER_RE.match(code):
            out[name.strip()] = code
    return out


def resolve_code(name: str, overrides: dict[str, str]) -> tuple[str | None, str | None]:
    """종목명 → (티커, 실패 사유). override가 API보다 우선한다."""
    if name in overrides:
        return overrides[name], None
    hit, err, *_ = si_api.resolve_stock(name)
    if hit and hit.get("stock_code"):
        return hit["stock_code"], None
    return None, err or "티커 해석 실패"


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
        if not code:
            entry["error"] = f"티커 미해석 — {err}"
            results.append(entry)
            continue
        entry["code"] = code

        bars, ferr = stage_scan.fetch_bars(code, FETCH_CALENDAR_DAYS)
        if not bars:
            entry["error"] = f"시세 수집 실패 — {ferr or '응답 없음'}"
            results.append(entry)
            continue

        entry.update(peak_drawdown(bars))
        entry["band"] = band_for(entry["drawdown"])
        entry["approach"] = approaching_band(
            pending_bands(entry["drawdown"], entry["peak"], DRAWDOWN_BANDS),
            DRAWDOWN_WARN_MARGIN_PP,
        )
        results.append(entry)
    return results


# --- 계좌 MDD --------------------------------------------------------------


def account_mdd(snapshot_dir: Path, today_balance: float | None, today: str) -> dict:
    """과거 스냅샷들의 잔고 최고치 대비 오늘 잔고의 MDD."""
    history: list[tuple[str, float]] = []
    for path in sorted(snapshot_dir.glob("*.md")):
        balance = parse_balance(path.read_text(encoding="utf-8"))
        if balance:
            history.append((path.stem, balance))
    if today_balance:
        history = [(d, b) for d, b in history if d != today] + [(today, today_balance)]
    if not history:
        return {"error": "잔고 이력 없음"}

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
        "error": None,
    }


# --- 계좌 기록 고점 낙폭 ----------------------------------------------------


def load_record_history(snapshot_dir: Path) -> tuple[dict[str, list[tuple[str, float]]], dict]:
    """스냅샷 이력 전체 → 종목명별 (일자, 현재가) 리스트 + 구간 메타.

    `## 보유` 표를 파싱할 수 없는 스냅샷(손으로 쓴 파일 등)은 조용히 건너뛴다.
    이 축의 존재 이유가 "네트워크 없이도 남는 낙폭"이므로 한 파일의 형식 차이로 죽으면 안 된다.
    """
    history: dict[str, list[tuple[str, float]]] = {}
    files = sorted(snapshot_dir.glob("*.md")) if snapshot_dir.is_dir() else []
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
    samples = [(d, p) for d, p in history.get(name, []) if d != today]
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


# --- 렌더링 ----------------------------------------------------------------


def won(value: float | None) -> str:
    return f"₩{value:,.0f}" if value is not None else "-"


def ymd(raw: str) -> str:
    """네이버 일봉의 `20260622` → `2026-06-22`."""
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


def render(results: list[dict], account: dict, record_meta: dict | None = None) -> str:
    meta = record_meta or {}
    lines = [
        SECTION_TITLE,
        "",
        f"> **52주 시장 고점** = 최근 {PEAK_WINDOW_DAYS}거래일 최고 **종가**(네이버 일봉). "
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
        state = (
            f"← **발동 ({account['band']:.0f}%)**" if account["band"] is not None else "→ 미발동"
        )
        lines.append(
            f"- 계좌 MDD(「기본 원칙 13」): 잔고 {won(account['current'])} · "
            f"스냅샷 고점 {won(account['peak'])}({account['peak_date']}) · "
            f"{account['mdd']:+.1f}% {state}"
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
    history, history_meta = load_record_history(snapshot_dir)
    results = analyze_holdings(
        holdings, load_overrides(args.overrides), history, args.snapshot.stem
    )
    account = account_mdd(snapshot_dir, parse_balance(text), args.snapshot.stem)
    section = render(results, account, history_meta)

    if args.append:
        append_section(args.snapshot, section)
        print(f"추가: {args.snapshot}", file=sys.stderr)
    print(section, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
