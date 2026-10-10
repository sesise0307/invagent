"""analyze-stock `entry_decision.py` — 스캔·밸류 결과를 entry_policy 입력으로 엮는 조립기.

자식 스크립트(stage_scan·turn_scan)는 경로로 실행되므로 `run_child`가 이음매다. 테스트는 그 자리에
**실제 `--json` 출력**을 넣는다 — 두 스크립트의 `main`을 가짜 일봉·재무로 돌려 받은 stdout이다.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

from invagent.datafeed import daily, stockinfo, tickers
from invagent.datafeed.daily import Bars
from tests.conftest import SKILLS_ROOT, load_script

entry_decision = load_script("analyze-stock", "entry_decision")
stage_scan = load_script("stage-analysis", "stage_scan")
turn_scan = load_script("stage-analysis", "turn_scan")


# --- 자식 출력 캡처 ---------------------------------------------------------


def _bars(closes: list[float]) -> list[dict]:
    return [
        {"date": f"b{i:04d}", "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
         "volume": 1_000_000.0}
        for i, c in enumerate(closes)
    ]


def _path(anchors: list[tuple[int, float]]) -> list[float]:
    """(봉 번호, 종가) 꼭짓점을 직선으로 이은 종가열 — test_turn_scan과 같은 모양."""
    closes: list[float] = []
    for (i0, c0), (i1, c1) in zip(anchors, anchors[1:]):
        for i in range(i0, i1):
            closes.append(c0 + (c1 - c0) * (i - i0) / (i1 - i0))
    closes.append(anchors[-1][1])
    return closes


def _wave(base: float, drift: float, n: int) -> list[float]:
    return [base * (1 + drift / 100) ** i * (1 + 0.03 * math.sin(i / 9)) for i in range(n)]


UPTREND = _wave(10_000, 0.25, 400)      # stage_scan 2단계
DOWNTREND = _wave(40_000, -0.25, 400)   # stage_scan 4단계
TURN_BASE = [(0, 200.0), (100, 100.0), (110, 108.0), (120, 102.0)]
TURNING = _path(TURN_BASE + [(140, 112.0)])   # turn_scan 고개 들기
BASING = _path(TURN_BASE + [(145, 104.0)])    # turn_scan 바닥 다지기 + 선매집 가격 조건 충족


class _Info:
    """stage_scan이 읽는 `StockInfo` 표면만 흉내 낸다."""

    def __init__(self, quarters: dict[tuple[int, int], float]) -> None:
        self.fundamentals = {"financials": [{}]}
        self.name = "가나"
        self.sources = ["StockEasy info-tab"]
        self.gaps: list[str] = []
        self._quarters = quarters

    def quarterly(self, field: str, *, estimates: bool = False) -> dict:
        return {} if estimates else dict(self._quarters)


OP_UP = {(2025, 2): 100.0, (2026, 1): 110.0, (2026, 2): 130.0}
OP_UNKNOWN = {(2026, 2): 130.0}  # 전년 동기 확정치 없음 → increased None


def _capture(monkeypatch, capsys, script, closes, quarters=None) -> tuple[int, str, str]:
    """자식 스크립트의 `main([... , "--json"])`을 가짜 데이터로 돌려 (code, stdout, stderr)를 받는다."""
    with monkeypatch.context() as m:
        m.setattr(tickers, "resolve_stock",
                  lambda q, overrides=None: ({"stock_code": "000001", "stock_name": "가나"}, None, 0))
        m.setattr(daily, "fetch_daily_bars", lambda code, days, asof=None: Bars(_bars(closes)))
        m.setattr(stockinfo, "fetch", lambda ticker: _Info(quarters or {}))
        capsys.readouterr()
        code = script.main(["000001", "--json"])
        out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def children(monkeypatch, capsys):
    """`run_child`를 캡처한 실제 출력으로 바꾼다. 호출된 argv는 `calls`에 남는다."""

    def install(stage, turn):
        calls: list[tuple[str, list[str]]] = []
        results = {"stage_scan.py": stage, "turn_scan.py": turn}

        def fake(script: Path, args: list[str]):
            calls.append((Path(script).name, list(args)))
            return results[Path(script).name]

        monkeypatch.setattr(entry_decision, "run_child", fake)
        return calls

    def stage_out(closes, quarters=OP_UP):
        return _capture(monkeypatch, capsys, stage_scan, closes, quarters)

    def turn_out(closes):
        return _capture(monkeypatch, capsys, turn_scan, closes)

    return install, stage_out, turn_out


# --- 입력 파일 --------------------------------------------------------------


def _valuation(**overrides) -> dict:
    """하방이 막힌(하단 110 ≥ 현재가 100) 매수 후보 — 손익비 ≥ 2.5."""
    payload = {
        "asof": "2026-10-10",
        "current_price": 100.0,
        "consensus": {"low": 110.0, "average": 160.0, "high": 200.0, "coverage": 10,
                      "latest_report_date": "2026-10-01", "recent_report_count": 5,
                      "eps_revision": "flat"},
        "scenarios": {"bear": 105.0, "base": 160.0, "bull": 210.0,
                      "probabilities": {"bear": 20, "base": 60, "bull": 20}},
    }
    payload.update(overrides)
    return payload


def _context(**overrides) -> dict:
    payload = {
        "account_pause": {"status": "ok", "active": False, "remaining_trading_days": 0,
                          "review_complete": True},
        "event": {"status": "ok", "enabled": False},
        "long_bull": {"status": "ok", "active": False},
        "earnings": {"status": "ok", "surprise": False},
        "instrument": {"status": "ok", "kind": "stock", "leveraged": False},
        "tranche_plan": {"first_tranche_fraction": 0.03, "target_weight_fraction": 0.10,
                         "next_tranche_trigger": "저점 높임 유지 + 60일선 상향 전환"},
    }
    payload.update(overrides)
    return payload


def _run(tmp_path, capsys, *extra, valuation=None, context=None) -> tuple[int, dict]:
    val = tmp_path / "valuation.json"
    ctx = tmp_path / "context.json"
    val.write_text(json.dumps(valuation or _valuation()), encoding="utf-8")
    ctx.write_text(json.dumps(context or _context()), encoding="utf-8")
    capsys.readouterr()
    code = entry_decision.main(["000001", "--valuation", str(val), "--context", str(ctx),
                                "--asof", "2026-10-10", *extra])
    return code, json.loads(capsys.readouterr().out)


# --- 경로 -------------------------------------------------------------------


def test_stage_two_opens_the_trend_path(tmp_path, capsys, children):
    install, stage_out, turn_out = children
    install(stage_out(UPTREND), turn_out(TURNING))

    code, result = _run(tmp_path, capsys)

    assert code == 0
    assert result["inputs"]["stage"] == {"status": "ok", "value": 2}
    assert result["action"] == "eligible"
    assert result["max_tranche_fraction"] is None
    assert result["checks"]["stage"]["rule"] == "매매규칙 2(펀더 기반 매수)"


def test_stage_four_turning_with_a_passing_value_gate_opens_the_first_tranche(tmp_path, capsys, children):
    install, stage_out, turn_out = children
    install(stage_out(DOWNTREND), turn_out(TURNING))

    code, result = _run(tmp_path, capsys)

    assert code == 0
    inputs = result["inputs"]
    assert inputs["stage"] == {"status": "ok", "value": 4}
    assert inputs["turn"] == {"status": "ok", "value": "turning"}
    # 하단 110 ≥ 현재가 100 → 하방 막힘. 손익비 분모는 15% 하한.
    assert inputs["value_gate"]["downside_blocked"] is True
    assert inputs["value_gate"]["reward_risk"] > 2.5
    assert inputs["value_gate"]["next_tranche_trigger"] == "저점 높임 유지 + 60일선 상향 전환"
    assert inputs["valuation_grade"] == "buy_candidate"
    assert result["action"] == "watch"
    assert result["max_tranche_fraction"] == 0.03
    assert result["checks"]["stage"]["rule"] == "매매규칙 4(분할 매수)"


def test_stage_four_turning_without_blocked_downside_stays_closed(tmp_path, capsys, children):
    install, stage_out, turn_out = children
    install(stage_out(DOWNTREND), turn_out(TURNING))

    code, result = _run(tmp_path, capsys, valuation=_valuation(current_price=115.0))

    assert result["inputs"]["value_gate"]["downside_blocked"] is False
    assert result["checks"]["stage"]["outcome"] == "withhold"


def test_basing_with_accumulation_and_rising_operating_profit_opens_path_c(tmp_path, capsys, children):
    install, stage_out, turn_out = children
    install(stage_out(DOWNTREND, OP_UP), turn_out(BASING))

    code, result = _run(tmp_path, capsys)

    assert code == 0
    assert result["inputs"]["turn"] == {"status": "ok", "value": "basing"}
    assert result["inputs"]["accumulation"] == {
        "status": "ok", "eligible": True, "op_yoy_increased": True}
    assert result["action"] == "watch"
    assert result["max_tranche_fraction"] == pytest.approx(0.015)
    assert result["checks"]["stage"]["rule"] == "매매규칙 2 단서(선매집)"


def test_unknown_operating_profit_growth_becomes_null_and_keeps_path_c_shut(
        tmp_path, capsys, children):
    install, stage_out, turn_out = children
    install(stage_out(DOWNTREND, OP_UNKNOWN), turn_out(BASING))

    code, result = _run(tmp_path, capsys)

    assert result["inputs"]["accumulation"]["op_yoy_increased"] is None
    assert result["checks"]["stage"]["outcome"] == "withhold"
    assert result["max_tranche_fraction"] is None


def test_a_failed_child_becomes_unknown_with_its_reason_and_withholds(tmp_path, capsys, children):
    install, stage_out, turn_out = children
    install((1, "", "ERROR: 시세 수집 실패 — 빈 응답\n"), turn_out(TURNING))

    code, result = _run(tmp_path, capsys)

    assert code == 0
    stage = result["inputs"]["stage"]
    assert stage["status"] == "unknown"
    assert "시세 수집 실패" in stage["reason"]
    assert result["action"] == "withhold"


def test_unparseable_turn_output_makes_turn_and_accumulation_unknown(tmp_path, capsys, children):
    install, stage_out, turn_out = children
    install(stage_out(DOWNTREND), (0, "not json", ""))

    code, result = _run(tmp_path, capsys)

    assert result["inputs"]["turn"]["status"] == "unknown"
    assert result["inputs"]["accumulation"]["status"] == "unknown"
    assert result["action"] == "withhold"


def test_children_get_the_ticker_json_and_no_cache(tmp_path, capsys, children):
    install, stage_out, turn_out = children
    calls = install(stage_out(UPTREND), turn_out(TURNING))

    _run(tmp_path, capsys, "--no-cache")

    assert sorted(calls) == [
        ("stage_scan.py", ["000001", "--json", "--no-cache"]),
        ("turn_scan.py", ["000001", "--json", "--no-cache"]),
    ]


# --- context 검증 -----------------------------------------------------------


def test_context_keys_pass_through_verbatim(tmp_path, capsys, children):
    install, stage_out, turn_out = children
    install(stage_out(UPTREND), turn_out(TURNING))
    context = _context(long_bull={"status": "ok", "active": True})

    code, result = _run(tmp_path, capsys, context=context)

    for key in ("account_pause", "event", "long_bull", "earnings", "instrument"):
        assert result["inputs"][key] == context[key]
    assert result["inputs"]["asof"] == "2026-10-10"
    assert result["checks"]["long_bull"]["outcome"] == "watch"


@pytest.mark.parametrize("change", ["unknown", "missing"])
def test_bad_context_keys_exit_2(tmp_path, capsys, children, change):
    install, stage_out, turn_out = children
    install(stage_out(UPTREND), turn_out(TURNING))
    context = _context()
    if change == "unknown":
        context["overhang"] = {"status": "ok", "value": "none"}
    else:
        del context["earnings"]

    code, result = _run(tmp_path, capsys, context=context)

    assert code == 2
    assert result["ok"] is False
    assert ("overhang" if change == "unknown" else "earnings") in result["errors"][0]


def test_bad_valuation_input_exits_2(tmp_path, capsys, children):
    install, stage_out, turn_out = children
    install(stage_out(UPTREND), turn_out(TURNING))

    code, result = _run(tmp_path, capsys, valuation={"current_price": 100.0})

    assert code == 2
    assert "valuation" in result["errors"][0]


# --- 실제 실행 경로 ---------------------------------------------------------


@pytest.mark.parametrize("script", ["STAGE_SCAN", "TURN_SCAN"])
def test_run_child_really_starts_the_sibling_skill_script(script):
    """경로와 인터프리터가 맞는지만 본다 — `--help`는 네트워크에 닿지 않는다."""
    code, out, err = entry_decision.run_child(getattr(entry_decision, script), ["--help"])

    assert code == 0, err
    assert "--json" in out and "--no-cache" in out


def test_entry_decision_imports_no_other_skills_script():
    """다른 스킬 스크립트는 경로로 실행만 한다 — import·sys.path 주입은 AGENTS.md 배치 규칙 위반."""
    source = (SKILLS_ROOT / "analyze-stock" / "scripts" / "entry_decision.py").read_text(encoding="utf-8")
    imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]

    assert not any("stage_scan" in line or "turn_scan" in line for line in imports)
    assert "sys.path.insert(0, str(SCRIPTS_DIR))" in source
    assert entry_decision.STAGE_SCAN.is_file() and entry_decision.TURN_SCAN.is_file()
