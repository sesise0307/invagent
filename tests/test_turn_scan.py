"""`stage-analysis` 스킬의 단기 바닥 전환 판정(`turn_scan.py`) 검증.

네트워크를 타지 않는다. 꺾은선으로 만든 합성 종가열만 쓴다.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / ".agents" / "skills" / "stage-analysis" / "scripts" / "turn_scan.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("turn_scan", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


turn_scan = _load_module()


def _path(anchors: list[tuple[int, float]]) -> list[float]:
    """(봉 번호, 종가) 꼭짓점을 직선으로 이은 종가열."""
    closes: list[float] = []
    for (i0, c0), (i1, c1) in zip(anchors, anchors[1:]):
        for i in range(i0, i1):
            closes.append(c0 + (c1 - c0) * (i - i0) / (i1 - i0))
    closes.append(anchors[-1][1])
    return closes


def _bars(closes: list[float], volumes: list[float] | None = None) -> list[dict]:
    """종가열을 일봉으로 부풀린다. 고가·저가는 종가 ±1%."""
    volumes = volumes or [1_000_000.0] * len(closes)
    return [
        {"date": f"b{i:04d}", "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": v}
        for i, (c, v) in enumerate(zip(closes, volumes))
    ]


# 200 → 100(봉 100)까지 하락, 108(봉 110)까지 반등, 102(봉 120)에서 저점을 높인 뒤의 흐름만 바꾼다.
BASE = [(0, 200.0), (100, 100.0), (110, 108.0), (120, 102.0)]


def test_a_fresh_low_is_still_falling():
    """「매매규칙 2」 — 방금 신저가를 쓴 종목은 바닥을 다진 게 아니라 하락하는 와중이다."""
    result = turn_scan.analyze_turn(_bars(_path([(0, 200.0), (140, 100.0)])))

    assert result["state"] == "falling"
    assert result["label"] == "하락 중"


def test_a_crash_from_a_fresh_peak_is_judged_against_its_own_low():
    """100 → 200으로 오른 뒤 128까지 -36% 무너진 종목의 바닥은 랠리 전 100이 아니라 이번 하락의 저점이다.

    2026-09-12 실데이터 — SK하이닉스가 고점 대비 -38%인데 랠리 전 저점 기준 +124%라 「초입 지남」이 나왔다.
    """
    result = turn_scan.analyze_turn(
        _bars(_path([(0, 120.0), (30, 100.0), (70, 200.0), (120, 130.0), (140, 128.0)]))
    )

    assert result["state"] == "falling"
    assert result["base_low"]["price"] == 128.0


def test_a_modest_pullback_in_an_uptrend_is_not_a_new_base():
    """고점 대비 -10% 눌림은 새 바닥이 아니다 — 랠리 전 저점 기준으로 이미 초입을 지났다."""
    result = turn_scan.analyze_turn(
        _bars(_path([(0, 150.0), (30, 100.0), (100, 200.0), (140, 180.0)]))
    )

    assert result["state"] == "extended"
    assert result["base_low"]["price"] == 100.0


def test_a_higher_low_then_a_close_above_the_rebound_high_is_turning():
    """Sperandeo 1-2-3 — 저점을 높인 뒤(102 > 100) 직전 반등 고점 108을 종가로 넘으면 고개 들기."""
    result = turn_scan.analyze_turn(_bars(_path(BASE + [(140, 112.0)])))

    assert result["state"] == "turning"
    assert result["label"] == "고개 들기"
    assert result["base_low"]["price"] == 100.0
    assert result["higher_low"]["price"] == 102.0
    assert result["breakout"] == 108.0


def test_holding_above_the_low_below_the_rebound_high_is_basing():
    """저점을 높였어도 108을 종가로 넘지 못했으면 아직 바닥 다지기다 — 매수는 고개 들기부터."""
    result = turn_scan.analyze_turn(_bars(_path(BASE + [(140, 105.0)])))

    assert result["state"] == "basing"
    assert "돌파선 종가 돌파" in result["missing"]


def test_a_rally_far_off_the_low_is_past_the_early_stage():
    """저점 100에서 +40%인 140은 '고개 드는 초반'이 아니다 — 추격 금지, 눌림 대기."""
    result = turn_scan.analyze_turn(_bars(_path(BASE + [(140, 140.0)])))

    assert result["state"] == "extended"
    assert result["label"] == "초입 지남"


def test_a_modest_close_well_past_the_breakout_is_also_past_the_early_stage():
    """저점 대비 +22%라도 돌파선 108에서 +13%(122)면 이미 돌파 초입을 지났다."""
    result = turn_scan.analyze_turn(_bars(_path(BASE + [(140, 122.0)])))

    assert result["state"] == "extended"


def test_after_a_breakout_the_early_stage_is_measured_from_the_breakout_line():
    """V자 반등은 저점 높임(136)부터 이미 저점 +36%다 — 저점 기준이면 초입 창이 처음부터 없다.

    돌파 뒤에는 돌파선 140 대비로만 잰다: 145(+3.6%)는 돌파 초입, 고개 들기다
    (2026-09-12 사용자 확정, 산일전기 실데이터).
    """
    result = turn_scan.analyze_turn(
        _bars(_path([(0, 200.0), (100, 100.0), (110, 140.0), (120, 136.0), (140, 145.0)]))
    )

    assert result["rise_from_low_pct"] > turn_scan.EXTENDED_FROM_LOW_PCT
    assert result["state"] == "turning"


def test_below_a_set_breakout_line_the_stock_is_basing_however_far_off_the_low():
    """저점 높임(140)이 저점 +40%에 잡혀 돌파선(150)이 정해졌으면, 그 아래(145)는 돌파 대기다.

    저점 기준 +35%로 「초입 지남」을 붙이면 돌파 전엔 추격 금지, 돌파하면 고개 들기로 뜻이 뒤집힌다
    (2026-09-12 큐리옥스·이수페타시스 실데이터, 사용자 확정).
    """
    result = turn_scan.analyze_turn(
        _bars(_path([(0, 200.0), (100, 100.0), (110, 150.0), (120, 140.0), (140, 145.0)]))
    )

    assert result["rise_from_low_pct"] > turn_scan.EXTENDED_FROM_LOW_PCT
    assert result["state"] == "basing"
    assert "돌파선 종가 돌파" in result["missing"]


def test_a_throwback_under_the_breakout_line_is_basing_not_extended():
    """돌파(146) 뒤 돌파선 140 아래(139)로 되돌아오면 추격 금지가 아니라 되돌림 — 바닥 다지기다."""
    result = turn_scan.analyze_turn(
        _bars(_path([(0, 200.0), (100, 100.0), (110, 140.0), (120, 136.0), (130, 146.0),
                     (140, 139.0)]))
    )

    assert result["state"] == "basing"
    assert "돌파선 종가 돌파" in result["missing"]


def test_a_brief_undercut_that_recovers_is_a_spring_not_a_new_fall():
    """와이코프 Spring — 바닥 100을 98(-2%)까지 잠깐 깼다가 종가로 되찾으면 하락 중이 아니다.

    구조상 바닥은 100을 유지하고, 무효화선은 Spring 저점 98로 내려간다.
    """
    result = turn_scan.analyze_turn(
        _bars(_path(BASE + [(130, 106.0), (133, 98.0), (140, 104.0)]))
    )

    assert result["state"] != "falling"
    assert result["spring"] is True
    assert result["base_low"]["price"] == 100.0
    assert result["invalidation"] == 98.0


def test_an_undercut_deeper_than_the_spring_tolerance_is_still_falling():
    """바닥 100을 95(-5%)까지 깼으면 Spring이 아니라 이탈이다 — 종가를 되찾아도 3주는 기다린다."""
    result = turn_scan.analyze_turn(
        _bars(_path(BASE + [(130, 106.0), (133, 95.0), (140, 104.0)]))
    )

    assert result["state"] == "falling"
    assert result["spring"] is False


def test_an_undercut_that_has_not_recovered_the_base_is_still_falling():
    """-2%만 깼어도 종가가 바닥 100 아래(99)에 머물면 되찾은 게 아니다."""
    result = turn_scan.analyze_turn(
        _bars(_path(BASE + [(130, 106.0), (133, 98.0), (140, 99.0)]))
    )

    assert result["state"] == "falling"


def test_breakout_volume_raises_confidence_without_changing_the_state():
    """거래량은 보조 증거다 — 돌파일 3배 거래량은 확신도만 올리고 상태는 그대로 둔다."""
    closes = _path(BASE + [(140, 112.0)])
    breakout_day = next(i for i, c in enumerate(closes) if i > 120 and c > 108.0)
    spiked = [1_000_000.0] * len(closes)
    spiked[breakout_day] = 3_000_000.0

    quiet = turn_scan.analyze_turn(_bars(closes))
    loud = turn_scan.analyze_turn(_bars(closes, spiked))

    assert quiet["state"] == loud["state"] == "turning"
    assert quiet["evidence"]["breakout_volume"] is False
    assert loud["evidence"]["breakout_volume"] is True
    assert quiet["confidence"] == "중간"
    assert loud["confidence"] == "높음"


def test_a_lower_low_on_weaker_selling_is_a_bullish_rsi_divergence():
    """직전 저점 130은 60일 연속 하락 끝(RSI 0), 새 저점 128은 오르내리며 밀린 끝이다.

    가격은 저점을 낮췄는데 RSI는 저점을 높였다 — 매도 힘이 빠졌다는 보조 증거.
    """
    closes = [200.0 - 70.0 * i / 60 for i in range(61)]          # 0~60: 200 → 130 연속 하락
    closes += [130.0 + (i + 1) for i in range(15)]               # 61~75: 145까지 반등
    for _ in range(15):                                           # 76~105: 오르내리며 128까지
        closes += [closes[-1] + 1.8666666666666667, closes[-1] + 1.8666666666666667 - 3.0]
    closes[-1] = 128.0
    closes += [128.0 + 12.0 * (i + 1) / 35 for i in range(35)]    # 106~140: 140까지 회복

    result = turn_scan.analyze_turn(_bars(closes))

    assert result["base_low"]["price"] == 128.0
    assert result["evidence"]["rsi_divergence"] is True


def test_the_sixty_day_line_turns_up_only_after_a_sustained_grind():
    """2차 증량 트리거 = 저점 높임 유지 + 60일선 상향 전환. 막 고개 든 날엔 60일선이 아직 내려간다."""
    early = turn_scan.analyze_turn(_bars(_path(BASE + [(140, 112.0)])))
    later = turn_scan.analyze_turn(_bars(_path(BASE + [(200, 118.0)])))

    assert early["state"] == later["state"] == "turning"
    assert early["ma60_rising"] is False
    assert later["ma60_rising"] is True


def _fake_feed(monkeypatch, bars: list[dict]) -> None:
    from invagent.datafeed import naver, tickers

    monkeypatch.setattr(
        tickers, "resolve_stock",
        lambda q: ({"stock_code": "000001", "stock_name": "가나"}, None, 0),
    )
    monkeypatch.setattr(naver, "fetch_bars", lambda code, days, asof=None: (bars, None))


def test_cli_prints_the_state_with_its_watch_lines(monkeypatch, capsys):
    _fake_feed(monkeypatch, _bars(_path(BASE + [(140, 112.0)])))

    assert turn_scan.main(["가나"]) == 0

    out = capsys.readouterr().out
    assert "고개 들기" in out
    assert "무효화" in out and "100" in out
    assert "돌파선" in out and "108" in out


def test_cli_json_carries_the_state_code_entry_policy_reads(monkeypatch, capsys):
    import json

    _fake_feed(monkeypatch, _bars(_path(BASE + [(140, 112.0)])))

    assert turn_scan.main(["가나", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "turning"
    assert payload["stock_code"] == "000001"


def test_every_threshold_is_documented_with_its_value():
    """임계값은 운영 선택이다 — 상수만 바꾸고 SKILL.md 근거 표가 옛 값이면 다음 사람이 못 읽는다."""
    skill = (REPO_ROOT / ".agents" / "skills" / "stage-analysis" / "SKILL.md").read_text(encoding="utf-8")
    section = skill.split("## 단기 바닥 전환 판정")[1].split("\n## ")[0]

    for name in ("MIN_BARS", "BASE_LOOKBACK", "DECLINE_MIN_PCT", "BASE_MIN_AGE", "TURN_PIVOT_K", "MA_SHORT",
                 "MA_SHORT_RISE_DAYS", "MA_MID", "MA_MID_SLOPE_DAYS", "EXTENDED_FROM_LOW_PCT",
                 "EXTENDED_FROM_PIVOT_PCT", "SPRING_TOL_PCT", "BREAKOUT_VOL_RATIO",
                 "VOLUME_AVG_DAYS", "BASE_VOL_RATIO", "RSI_PERIOD"):
        row = next((ln for ln in section.splitlines() if ln.startswith("|") and f"`{name}`" in ln), None)
        assert row, f"{name}이 근거 표에 없다"
        cells = [c.strip() for c in row.split("|")]
        names = [n.strip(" `") for n in cells[1].split("/")]
        values = [v.strip() for v in cells[2].split("/")]
        assert float(values[names.index(name)]) == getattr(turn_scan, name), name


def test_cli_refuses_to_judge_a_short_history(monkeypatch, capsys):
    """상장 초기처럼 일봉이 모자라면 바닥을 판정하지 않는다 — exit 1."""
    _fake_feed(monkeypatch, _bars(_path([(0, 100.0), (60, 110.0)])))

    assert turn_scan.main(["가나"]) == 1
    assert "일봉" in capsys.readouterr().err
