#!/usr/bin/env python3
"""보유 종목의 전고점(52주 최고 종가) 대비 낙폭 경보 + 계좌 MDD 판정기.

`extract_portfolio.py`가 만든 포트폴리오 스냅샷을 입력으로 받아,
종목별로 최근 250거래일 최고 종가 대비 하락률을 계산하고 -10/-15/-20/-30% 밴드로 경보한다.
같은 실행에서 과거 스냅샷들의 잔고 최고치 대비 계좌 MDD도 계산해
`context/my_rules.md` 「기본 원칙 13(계좌 MDD 관리)」의 -10/-15% 발동 여부를 표기한다.

기존 룰 판정(`extract_portfolio.py`)은 **평단 기준** 수익률만 본다. 이 스크립트는 **고점 기준**
축을 따로 본다. 크게 오른 뒤 꺾이는 종목은 평단 기준으로는 여전히 큰 수익이라 어떤 경고도
받지 못하므로, 「매매규칙 3(추세 기반 매도)」·「매매규칙 15(trailing stop 사전 설정)」가
요구하는 추세 훼손 감지를 이 축이 담당한다.

데이터 소스:
- 일봉 OHLCV — 네이버 금융 `siseJson` (무인증). `stage-analysis/scripts/stage_scan.py` 재사용.
- 티커 해석 — `context/ticker_overrides.md` → `analyze-stock/scripts/fetch_stock_info.py` 순.

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

# --- 판정 임계값 -----------------------------------------------------------
# 전고점 창과 밴드는 이 스킬의 운영 기준이다. 바꾸려면 SKILL.md의 밴드→룰 매핑도 함께 고친다.
PEAK_WINDOW_DAYS = 250      # 전고점 탐색 구간 (거래일 ≈ 52주)
FETCH_CALENDAR_DAYS = 400   # 250거래일을 확보하기 위한 달력일 요청 폭
DRAWDOWN_BANDS = (-10.0, -15.0, -20.0, -30.0)   # 종목 경보 밴드
ACCOUNT_MDD_BANDS = (-10.0, -15.0)              # 「기본 원칙 13」 계좌 MDD 밴드
# 90,000/100,000-1 은 부동소수점에서 -9.999999999999998이 된다. 표에 -10.0%로 찍히는 값이
# 밴드에 안 걸리는 어긋남을 막기 위한 경계 허용치.
BAND_EPS = 1e-9

BAND_MARK = {-10.0: "🟡", -15.0: "🟠", -20.0: "🔴", -30.0: "⛔"}

SECTION_TITLE = "## 전고점 낙폭 (52주 최고 종가 기준)"
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


def analyze_holdings(holdings: list[dict], overrides: dict[str, str]) -> list[dict]:
    """보유 종목별 낙폭 결과 리스트. 실패 종목도 사유를 담아 그대로 포함한다."""
    results: list[dict] = []
    for row in holdings:
        name = row["종목"]
        entry = {
            "종목": name,
            "수익률": row.get("수익률", ""),
            "비중": row.get("비중", ""),
            "error": None,
        }
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
    return {
        "peak": peak,
        "peak_date": peak_date,
        "current": current,
        "mdd": mdd,
        "band": min(hits) if hits else None,
        "samples": len(history),
        "from": history[0][0],
        "error": None,
    }


# --- 렌더링 ----------------------------------------------------------------


def won(value: float | None) -> str:
    return f"₩{value:,.0f}" if value is not None else "-"


def ymd(raw: str) -> str:
    """네이버 일봉의 `20260622` → `2026-06-22`."""
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) == 8 and raw.isdigit() else raw


def render(results: list[dict], account: dict) -> str:
    lines = [
        SECTION_TITLE,
        "",
        f"> 전고점 = 최근 {PEAK_WINDOW_DAYS}거래일 최고 **종가** (네이버 일봉). "
        f"평단 기준 룰(「매매규칙 6·7」)과 다른 축이다.",
        "",
        "| 종목 | 52주 고점(일자) | 현재 종가 | 낙폭 | 경보 | 평단 수익률 | 비중 |",
        "| --- | ---: | ---: | ---: | :-: | ---: | ---: |",
    ]
    for r in results:
        if r["error"]:
            lines.append(
                f"| {r['종목']} | ({r['error']}) | - | - | - | {r['수익률']} | {r['비중']} |"
            )
            continue
        lines.append(
            f"| {r['종목']} | {won(r['peak'])} ({ymd(r['peak_date'])}) | {won(r['close'])} | "
            f"{r['drawdown']:+.1f}% | {band_label(r['band'])} | {r['수익률']} | {r['비중']} |"
        )

    triggered = [r for r in results if not r["error"] and r["band"] is not None]
    if triggered:
        desc = ", ".join(
            f"{r['종목']} {r['drawdown']:+.1f}% ({band_label(r['band'])})"
            for r in sorted(triggered, key=lambda r: r["drawdown"])
        )
        lines += ["", f"- 전고점 낙폭 판정: {desc}"]
    else:
        lines += ["", "- 전고점 낙폭 판정: 해당 없음"]

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
        lines.append(
            f"- (주의) 계좌 고점은 스냅샷 보유 구간({account['from']}~, {account['samples']}개) "
            "기준이며 입출금을 보정하지 않는다."
        )
    return "\n".join(lines) + "\n"


def append_section(path: Path, section: str) -> None:
    """스냅샷 끝에 섹션을 쓴다. 같은 제목 섹션이 있으면 교체한다(멱등)."""
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    start = next((i for i, l in enumerate(lines) if l.strip() == SECTION_TITLE), None)
    if start is not None:
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines)
        )
        text = "\n".join(lines[:start] + lines[end:])
    path.write_text(text.rstrip("\n") + "\n\n" + section, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="포트폴리오 스냅샷 경로")
    parser.add_argument("--append", action="store_true", help="스냅샷 파일에 섹션을 덧붙인다")
    parser.add_argument("--overrides", type=Path, help="티커 override 파일 경로")
    parser.add_argument("--portfolio-dir", type=Path, help="계좌 MDD용 스냅샷 디렉토리")
    args = parser.parse_args(argv)

    try:
        text = args.snapshot.read_text(encoding="utf-8")
        holdings = parse_holdings(text)
    except (OSError, ValueError) as e:
        print(f"ERROR: 스냅샷 파싱 실패 — {e}", file=sys.stderr)
        return 1

    results = analyze_holdings(holdings, load_overrides(args.overrides))
    account = account_mdd(
        args.portfolio_dir or args.snapshot.parent,
        parse_balance(text),
        args.snapshot.stem,
    )
    section = render(results, account)

    if args.append:
        append_section(args.snapshot, section)
        print(f"추가: {args.snapshot}", file=sys.stderr)
    print(section, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
