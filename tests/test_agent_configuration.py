"""Validate shared Claude Code and Codex repository configuration."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import time
import re
import sys
import tomllib
from datetime import date
from pathlib import Path

import pytest

from invagent.datafeed import naver as datafeed_naver
from invagent.datafeed import stockeasy as datafeed_stockeasy


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / ".agents" / "skills"
SKILL_NAMES = (
    "advice",
    "analyze-stock",
    "daily-digest",
    "market-data",
    "monthly-investment-review",
    "opendart",
    "stage-analysis",
    "weekly-investment-review",
)


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_skill_is_shared_with_claude(skill_name: str) -> None:
    canonical = SKILLS_ROOT / skill_name
    claude_link = REPO_ROOT / ".claude" / "skills" / skill_name

    assert (canonical / "SKILL.md").is_file()
    assert claude_link.is_symlink()
    assert claude_link.resolve() == canonical.resolve()


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_skill_has_valid_frontmatter_and_ui_metadata(skill_name: str) -> None:
    skill_dir = SKILLS_ROOT / skill_name
    content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    frontmatter_match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)

    assert frontmatter_match is not None
    frontmatter = frontmatter_match.group(1)
    assert re.search(rf"^name:\s*[\"']?{re.escape(skill_name)}[\"']?\s*$", frontmatter, re.MULTILINE)
    assert re.search(r"^description:\s*", frontmatter, re.MULTILINE)

    metadata = (skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert "display_name:" in metadata
    assert "short_description:" in metadata
    assert f"${skill_name}" in metadata


def test_skills_contain_no_credentials_or_machine_paths() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in SKILLS_ROOT.rglob("*")
        if path.is_file() and path.suffix in {".md", ".py", ".yaml"}
    )

    assert not re.search(r"opendart=[0-9a-f]{20,}", combined)
    assert "/Users/sesise" not in combined
    assert ".claude/skills" not in combined


def test_analyze_stock_skill_defines_scenario_target_price() -> None:
    """목표가는 컨센 단독이 아니라 자체 시나리오와의 동적 가중 결합으로 산정한다."""
    content = (SKILLS_ROOT / "analyze-stock" / "SKILL.md").read_text(encoding="utf-8")

    assert "### 9단계 — 목표주가 산정" in content
    for scenario in ("Bull", "Base", "Bear"):
        assert scenario in content
    assert "w_c" in content and "w_s" in content
    assert "손익비" in content
    assert "기본 원칙 2" in content and "매매규칙 6" in content

    steps = re.findall(r"^### (\d+)단계", content, re.MULTILINE)
    assert [int(step) for step in steps] == list(range(1, 12))


def test_analyze_stock_target_price_is_a_range_not_a_point() -> None:
    """컨센 평균은 중심만 주고 분산을 못 준다 — 하단/중심/상단 3점으로 낸다."""
    content = (SKILLS_ROOT / "analyze-stock" / "SKILL.md").read_text(encoding="utf-8")

    for point in ("하단 = w_c × 컨센최저", "중심 = w_c × 컨센평균", "상단 = w_c × 컨센최고"):
        assert point in content
    # 분산도를 안 뽑으면 범위를 내도 의미가 없다.
    for metric in ("범위 폭", "컨센 산포", "시나리오 산포"):
        assert metric in content
    # blend 금지는 중심값 괴리가 아니라 두 범위의 겹침 여부로 판정한다.
    assert "전혀 겹치지 않으면" in content
    assert "괴리 ≥30%" not in content


def test_analyze_stock_weight_rules_are_ordered_and_deterministic() -> None:
    """삼성전자 실행에서 커버 23사(상향)와 산포 2.61배(하향)가 동시 성립 — 순서가 곧 충돌 해소다."""
    content = (SKILLS_ROOT / "analyze-stock" / "SKILL.md").read_text(encoding="utf-8")

    assert "처음 걸리는 것 하나만" in content
    # 산포가 커버 수보다 위에 와야 하향이 우선한다.
    dispersion = content.index("1. 컨센 산포(최고/최저) > 2배")
    coverage = content.index("3. 커버 ≥8사")
    assert dispersion < coverage
    assert "커버 수는 산포를 못 이긴다" in content
    # 범위 가중치·미정의 「다수」는 같은 입력에 두 값을 낳는다.
    assert "0.6~0.7" not in content
    assert "리포트 ≥3건" in content


def test_analyze_stock_downside_drives_stop_and_position_cap() -> None:
    """손익비 분모가 상수 15%면 중심 기대수익의 재진술이 된다 — 실측 하방을 쓴다."""
    content = (SKILLS_ROOT / "analyze-stock" / "SKILL.md").read_text(encoding="utf-8")

    # 「매매규칙 6」의 최종 이탈선은 -20%다. 2%룰 분모를 15%로 내리면 1회 최대 손실이
    # 계좌의 2.67%가 되어 「기본 원칙 4」를 넘기므로, 실효 손절폭 하한은 20%로 남는다.
    assert "실효 손절폭  = max(하방, 20%)" in content
    # 손익비 분모는 그와 별개의 값이고, 상수가 아니라 실측 하방과의 max다
    # (사용자 확정 2026-09-22).
    assert "손익비 분모  = max(하방, 15%)" in content
    assert "손익비      = 중심 기대수익 / 손익비 분모" in content
    assert "두 분모는 다른 것을 잰다. 하나로 합치지 마라" in content
    # 「기본 원칙 2」 단서(하방 막힘 시 30%)가 판정선에 들어와야 한다.
    assert "중심 30~50% & 하방 ≤ 0%" in content
    # 「매매규칙 9」는 두 축이다. 진입 크기를 정하는 것은 매수원금 축뿐이고,
    # 평가 비중 35%는 진입 후 주가 상승분에 걸리는 별개 상한이다.
    assert "매수원금 비중 상한 = 2% / 실효 손절폭" in content
    assert "평가 비중 상한 = 35%" in content
    assert "min(2% / 실효 손절폭, 30%)" not in content


def test_analyze_stock_wires_detected_signals_to_user_rules() -> None:
    """6단계가 잡은 서프라이즈·20주선 신호가 10단계 룰 환산까지 이어져야 한다."""
    content = (SKILLS_ROOT / "analyze-stock" / "SKILL.md").read_text(encoding="utf-8")

    assert "매매규칙 13" in content
    assert "기술적 분석 규칙 1" in content
    assert "확신도 판정" in content


def test_analyze_stock_keeps_overhang_as_reference() -> None:
    """5-1단계 오버행·수급 점검은 참고 사항이다 — 수집·표기는 하되 10단계 판단에는 넣지 않는다."""
    skill = (SKILLS_ROOT / "analyze-stock" / "SKILL.md").read_text(encoding="utf-8")

    assert "### 5-1단계 — 오버행 · 수급 점검 (참고)" in skill
    # 물량 출처가 도구 이름으로 고정된다 — 웹 추측으로 대체하지 않는다.
    for tool in ("dilutive_issuance", "treasury_share", "ownership_structure", "risk_events"):
        assert tool in skill
    # 크기 환산·일정·미확정 처리 3원칙.
    assert "상장주식수 대비 %로 환산" in skill
    assert "리픽싱" in skill
    assert "회사채면 중립, 메자닌(CB·BW)이면 희석" in skill
    # 스톡옵션 행사를 장내매수로 승격하지 않는다.
    assert "스톡옵션 행사는 내부자 장내매수와 다르다" in skill
    # 10단계는 요약을 인용만 한다 — 게이트도, 밸류 게이트 항목도 아니다.
    assert "오버행 게이트" not in skill
    assert "오버행 참고 (5-1단계 요약 인용)" in skill
    value_gate = skill.split("**밸류 게이트**")[1].split("⛔ **기대수익이 크다는 것만으로")[0]
    assert "오버행" not in value_gate


def test_analyze_stock_gates_entry_on_the_scripted_stage() -> None:
    """스테이지가 3관점 중 1표에 그치면 3·4단계 종목이 기대수익만으로 🟢를 받는다.
    게이트는 유지하되, 1·3단계는 밸류 게이트라는 명시된 문을 통해서만 열린다."""
    skill = (SKILLS_ROOT / "analyze-stock" / "SKILL.md").read_text(encoding="utf-8")
    stage = (SKILLS_ROOT / "stage-analysis" / "SKILL.md").read_text(encoding="utf-8")

    # 두 스킬이 3단계를 같은 강도로 다뤄야 한다 — 기본은 금지, 예외는 1차 분할 한정.
    assert "신규 매수 금지 — 밸류 게이트 통과 시 1차 분할만 예외" in stage
    assert "밸류 게이트 통과 시 1차 분할 한정" in stage
    assert "진입 가부의 정본은 `analyze-stock` 10단계" in stage

    assert "진입 경로 판정" in skill
    gate = skill.split("진입 경로 판정")[1].split("오버행 참고")[0]
    assert "`1단계` · `3단계` · `4단계`" in gate and "🟡" in gate
    assert "`하락 중` → 🔴" in gate
    assert "stage_scan" in gate
    assert "기대수익이 크다는 것은 뒤집을 근거가 아니다" in gate


def test_value_path_waits_for_the_scripted_turn() -> None:
    """「매매규칙 2」의 '고개 드는 초반'은 turn_scan이 정본이다 — 6단계가 돌리고 10단계·템플릿·advice가 인용한다.

    150일선 스테이지로 읽으면 가치주 바닥은 저점 +30~50% 뒤에야 2단계가 된다 (2026-09-12).
    """
    skill = (SKILLS_ROOT / "analyze-stock" / "SKILL.md").read_text(encoding="utf-8")
    step6 = skill.split("### 6단계")[1].split("### 7단계")[0]
    assert "stage-analysis/scripts/turn_scan.py" in step6
    for code in ("`falling`", "`basing`", "`turning`", "`extended`"):
        assert code in step6

    gate = skill.split("경로 B (가치 우선 진입)")[1].split("오버행 참고")[0]
    assert "`turn_scan` 판정이 `고개 들기`일 때만" in gate
    assert "2차 = 저점 높임 가격 위 종가 유지 + 60일선 상향 전환" in gate

    template = (REPO_ROOT / "template" / "stock_analysis.md").read_text(encoding="utf-8")
    assert "- 단기 바닥 전환:" in template
    assert "turn_scan" in template.split("### 🚪 진입 경로 판정")[1]

    advice = (SKILLS_ROOT / "advice" / "SKILL.md").read_text(encoding="utf-8")
    row = next(ln for ln in advice.splitlines() if ln.startswith("| 진입 국면"))
    assert "turn_scan" in row and "고개 들기" in row


def test_analyze_stock_keeps_the_20week_rule_advisory() -> None:
    """「기술적 분석 규칙 1」은 단독 절대 조건이 아니라고 못 박는다 — 스킬이 이를 진입 금지로 격상하면 안 된다."""
    rules = _my_rules()
    assert "단독으로 매수·매도를 결정하는 절대 조건으로 삼지 않는다" in rules
    assert "20주선 아래에서도 분할 진입할 수 있다" in rules

    skill = (SKILLS_ROOT / "analyze-stock" / "SKILL.md").read_text(encoding="utf-8")
    line = [ln for ln in skill.splitlines() if "주봉 20주선 방향" in ln]
    assert line, "10단계 20주선 항목을 찾지 못했다"
    assert "신규 진입 보류" not in "\n".join(line)
    assert "보수적" in skill.split("주봉 20주선 방향")[1][:400]


def test_stock_analysis_template_has_overhang_section() -> None:
    """§4-A가 오버행 점검의 출력 정본이다."""
    template = (REPO_ROOT / "template" / "stock_analysis.md").read_text(encoding="utf-8")

    assert "### 4-A. 오버행 · 수급 점검" in template
    for row in ("CB · BW · 전환우선주", "보호예수 · 임원 락업", "대량보유(5%) 변동 · 블록딜"):
        assert row in template
    assert "상장주식수 대비" in template
    assert "회사채(중립) / 메자닌(희석) / 미확정" in template
    # 빈칸 대신 해당 없음/미수집을 강제한다.
    assert "빈칸으로 두지 않는다" in template
    # 참고 사항이다 — §5에 인용만 하고 밸류 게이트·§6 뒤집는 조건으로 올리지 않는다.
    assert "오버행(§4-A): [요약 한 줄] · 판단 미반영" in template
    assert "오버행 해소 / 해당 없음" not in template
    assert "오버행 판정이 `미해소`면" not in template


STOCK_REPORT_SECTIONS = [
    "결론",
    "밸류에이션 · 목표주가",
    "포트폴리오 비교",
    "최신 뉴스 · 촉매 · 수급",
    "투자 판단",
    "리스크 · 뒤집는 조건",
    "사업 구조",
    "사업보고서 델타 (최신 vs 직전)",
    "내 아카이브 이력",
    "Evidence",
    "개정 이력",
]


def test_stock_analysis_template_leads_with_the_decision_sections() -> None:
    """결론 다음에 밸류에이션·포트폴리오·촉매·판단·리스크가 오고, 배경 자료는 그 뒤로 간다."""
    content = (REPO_ROOT / "template" / "stock_analysis.md").read_text(encoding="utf-8")

    headings = re.findall(r"^## (\d+)\. (.+)$", content, re.MULTILINE)
    assert headings == [(str(n), title) for n, title in enumerate(STOCK_REPORT_SECTIONS, 1)]


def test_stock_analysis_template_has_target_price_block() -> None:
    """§2가 목표주가 산정의 정본이고, 리포트 컨센서스는 그 입력이라 §2 안에 들어간다."""
    content = (REPO_ROOT / "template" / "stock_analysis.md").read_text(encoding="utf-8")

    assert "## 2. 밸류에이션 · 목표주가" in content
    for heading in (
        "### 2-A. 리포트 컨센서스",
        "### 2-B. 멀티플",
        "### 2-C. 자체 시나리오",
        "### 2-D. 종합 목표가",
    ):
        assert heading in content
    valuation = content.split("## 2. 밸류에이션 · 목표주가")[1].split("\n## 3. ")[0]
    assert "target_price_history" in valuation
    assert "**컨센 요약**" in valuation
    assert "종합 목표주가" in content
    assert "기대수익" in content
    assert "손익비" in content
    # §2-D·§1 모두 단일값이 아니라 하단/중심/상단을 요구한다.
    for point in ("하단", "중심", "상단"):
        assert point in content
    assert "범위 폭" in content
    assert "실효 손절폭" in content


def test_analyze_stock_inheritance_keeps_one_line_change_log() -> None:
    """승계 보고서는 본문에 현재 상태만 두고 변경은 §11 한 줄 요약으로만 남긴다."""
    skill = (SKILLS_ROOT / "analyze-stock" / "SKILL.md").read_text(encoding="utf-8")
    template = (REPO_ROOT / "template" / "stock_analysis.md").read_text(encoding="utf-8")

    assert "누적 파일 1개만 유지" in skill
    assert "파일명을 목표 경로로 변경" in skill
    assert "복사본을 만들지 않는다" in skill
    assert "불변 스냅샷" not in skill

    # 본문은 현재 상태만 — 날짜 업데이트 블록·보존 블록은 폐지됐다.
    # (폐지된 형식은 옛 보고서를 이관하기 위해 이름으로만 지목한다. 새로 만들라는 지시는 없어야 한다.)
    assert "블록은 폐지됐다" in skill
    assert "블록에 추가한다" not in skill
    assert "기존 분석 (보존)" not in template
    assert "기존 분석을 삭제·축약하지 않는다" not in template
    assert "본문 11섹션은 현재 상태만 담는다" in skill
    assert "본문을 현재 상태로 갱신한다" in template

    # 변경 추적은 §11 한 줄 형식이 정본이다.
    assert "`- {yyyy-mm-dd}: {구분} — {요약}`" in skill
    assert "`- {yyyy-mm-dd}: {구분} — {요약 한 줄}`" in template
    assert "정정 / 갱신 / 판단변경" in skill
    assert "정정 / 갱신 / 판단변경" in template
    assert "정정·판단변경은 근거" in skill
    assert "정정·판단변경은 근거" in template


def test_analyze_stock_inheritance_reads_and_migrates_revision_log() -> None:
    """승계 시 §11를 반드시 읽고, 옛 보존 본문은 §11로 옮긴 뒤 본문에서 뺀다."""
    skill = (SKILLS_ROOT / "analyze-stock" / "SKILL.md").read_text(encoding="utf-8")

    # 본문을 현재 상태로 덮으므로 과거 판단은 §11에만 남는다 — 읽기가 필수다.
    assert "`개정 이력` 절 전체" in skill
    assert "반드시 읽는다" in skill

    # 이관 순서: §11에 먼저 올린 뒤 본문에서 뺀다.
    assert "먼저 올린 뒤" in skill
    assert "본문만 지우고 §11에 안 올리면" in skill
    # 옛 12섹션 배치는 다음 승계 때 템플릿 순서로 옮긴다 — `output/`은 커밋으로 이관할 수 없다.
    assert "템플릿 순서로\n  재배치" in skill


def test_project_codex_config_is_credential_free() -> None:
    config_path = REPO_ROOT / ".codex" / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))

    assert config["approval_policy"] == "on-request"
    assert config["sandbox_mode"] == "workspace-write"
    assert "mcp_servers" not in config


def _load_market_signal_module():
    script_path = SKILLS_ROOT / "daily-digest" / "scripts" / "fetch_market_signals.py"
    spec = importlib.util.spec_from_file_location("fetch_market_signals", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_market_signal_endpoints_point_at_stockdata_api() -> None:
    """사이트가 클라이언트 렌더링으로 바뀐 뒤 실제 호출 경로는 /stockdata/api/v1/market."""
    module = _load_market_signal_module()

    assert module.API_BASE == "https://stockeasy.intellio.kr/stockdata/api/v1/market"
    assert set(module.ENDPOINTS) == {
        "indices",
        "big_picture",
        "market_monitor",
        "credit_balance",
    }


def test_market_signal_main_renders_api_payload(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_market_signal_module()

    payloads = {
        "indices": {
            "short_term_signal": "green",
            "long_term_signal": "yellow",
            "indices": [
                {
                    "index_name": "종합(KOSPI)",
                    "current_value": 6977.94,
                    "price_change_percent": 2.42,
                    "rising_stocks": 677,
                    "falling_stocks": 204,
                    "upper_limit_stocks": 3,
                    "lower_limit_stocks": 0,
                }
            ],
        },
        "big_picture": {
            "kospi": {
                "status": "confirmed_uptrend",
                "rally_day_count": 0,
                "distribution_days": [],
                "last_ftd_date": "2026-08-05",
            }
        },
        "market_monitor": {
            "data": [
                {
                    "일자": "2026-08-14",
                    "KOSPI": 6977.34,
                    "20down_ratio": 0.176,
                    "200down_ratio": 0.786,
                    "52W_High_count": 74.0,
                    "52W_Low_count": 43.0,
                    "ADR(KOSPI)": 125.84,
                    "ADR(KOSDAQ)": 118.65,
                }
            ]
        },
        "credit_balance": {
            "data": [
                {
                    "date": "2026-08-13",
                    "total_credit": 309262,
                    "investor_deposit": 1000683,
                    "credit_deposit_ratio": 30.9051,
                    "margin_call_amount": 50,
                }
            ]
        },
    }
    monkeypatch.setattr(module, "fetch_api", lambda name: (payloads[name], None))

    assert module.main() == 0

    out = capsys.readouterr().out
    assert "green" in out
    assert "6,977.94" in out
    assert "상승 추세 확인" in out
    assert "30.9%" in out


def _monitor_rows(kospi: list[float], kosdaq: list[float] | None = None) -> list[dict]:
    kosdaq = kosdaq or [1000.0] * len(kospi)
    return [
        {"일자": f"2026-09-{i + 1:02d}", "KOSPI": k, "KOSDAQ": q}
        for i, (k, q) in enumerate(zip(kospi, kosdaq))
    ]


def test_leverage_rule_three_fires_on_three_volatile_days_in_ten() -> None:
    """「레버리지 규칙 3」은 하드 트리거인데 지금까지 아무도 계산하지 않고 모델이 눈대중했다."""
    module = _load_market_signal_module()

    # 10개 변화 중 ±5%가 정확히 3일 (-6.00 / -5.82 / -5.03)
    closes = [100.0, 94.0, 94.5, 89.0, 89.5, 85.0, 85.5, 86.0, 86.5, 87.0, 87.5]
    verdict = module.leverage_liquidation(_monitor_rows(closes))["KOSPI"]

    assert verdict["fired"] is True
    assert verdict["count"] == 3
    assert len(verdict["days"]) == 3


def test_leverage_rule_three_stays_quiet_below_the_threshold() -> None:
    """2일이면 발동하지 않는다 — 룰이 요구하는 것은 3일 이상이다."""
    module = _load_market_signal_module()

    # ±5%가 2일뿐 (-6.00 / -5.82)
    closes = [100.0, 94.0, 94.5, 89.0, 89.5, 90.0, 90.5, 91.0, 91.5, 92.0, 92.5]
    verdict = module.leverage_liquidation(_monitor_rows(closes))["KOSPI"]

    assert verdict["fired"] is False
    assert verdict["count"] == 2


def test_leverage_rule_three_exempts_a_run_that_is_all_upward() -> None:
    """룰의 예외는 '상승 방향으로 일관적인 변동'이다 — 음의 복리는 방향이 섞일 때 생긴다."""
    module = _load_market_signal_module()

    # ±5% 3일이 전부 상승 (+6.00 / +6.13 / +6.22)
    closes = [100.0, 106.0, 112.5, 119.5, 120.0, 120.5, 121.0, 121.5, 122.0, 122.5, 123.0]
    verdict = module.leverage_liquidation(_monitor_rows(closes))["KOSPI"]

    assert verdict["count"] == 3
    assert verdict["fired"] is False
    assert verdict["exempt"] is True

    # 하락이 하나라도 섞이면 예외가 깨진다.
    # 같은 3일인데 마지막이 하락 (+6.00 / +6.13 / -5.78)
    mixed = [100.0, 106.0, 112.5, 106.0, 106.5, 107.0, 107.5, 108.0, 108.5, 109.0, 109.5]
    broken = module.leverage_liquidation(_monitor_rows(mixed))["KOSPI"]
    assert broken["count"] == 3 and broken["fired"] is True and broken["exempt"] is False


def test_leverage_rule_three_judges_each_index_separately_and_no_sectors() -> None:
    """레버리지 상품은 지수별로 다르다. 섹터 레버리지는 「레버리지 규칙 2」대로 사용자 판단이다."""
    module = _load_market_signal_module()

    calm = [100.0] * 11
    wild = [100.0, 94.0, 94.5, 89.0, 89.5, 85.0, 85.5, 86.0, 86.5, 87.0, 87.5]
    result = module.leverage_liquidation(_monitor_rows(calm, wild))

    assert set(result) == {"KOSPI", "KOSDAQ"}
    assert result["KOSPI"]["fired"] is False
    assert result["KOSDAQ"]["fired"] is True

    script = (SKILLS_ROOT / "daily-digest" / "scripts" / "fetch_market_signals.py").read_text(encoding="utf-8")
    assert "섹터" in script, "판정 범위가 지수뿐이라는 사실이 스크립트에 남아야 한다"


def test_leverage_rule_three_needs_a_full_window() -> None:
    """행이 모자라면 '판정 불가'다 — 짧은 창을 채운 셈 치고 발동시키지 않는다."""
    module = _load_market_signal_module()

    verdict = module.leverage_liquidation(_monitor_rows([100.0, 94.0, 99.0]))["KOSPI"]
    assert verdict["fired"] is False and verdict["insufficient"] is True


def test_market_signal_main_reports_api_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """지수·빅픽처가 모두 실패하면 사유를 stderr에 남기고 exit 1."""
    module = _load_market_signal_module()
    monkeypatch.setattr(module, "fetch_api", lambda name: (None, "HTTP 404"))

    assert module.main() == 1
    assert "HTTP 404" in capsys.readouterr().err


def _load_stock_info_module():
    script_path = SKILLS_ROOT / "analyze-stock" / "scripts" / "fetch_stock_info.py"
    spec = importlib.util.spec_from_file_location("fetch_stock_info", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STOCK_INFO_PAYLOAD = {
    "stock_code": "064290",
    "primary_fs_type": "C",
    "stock_info": {
        "name": "인텍플러스",
        "market": "KOSDAQ",
        # 가격 필드는 등락 방향이 부호로 박혀 온다 (음수 가격이 아니다)
        "cur_prc": "-30850",
        "pred_pre": "-1250",
        "flu_rt": "-3.89",
        "mac": "3997",
        "trde_qty": "169623",
        "per": "",
        "pbr": "7.76",
        "eps": "-257",
        "bps": "3982",
        "roe": "-7.2",
        "250hgst": "+54100",
        "250lwst": "-8540",
        "250hgst_pric_dt": "20260706",
        "250lwst_pric_dt": "20250806",
        "250hgst_pric_pre_rt": "-42.88",
        "250lwst_pric_pre_rt": "+261.83",
        "for_exh_rt": "+4.68",
        "dstr_rt": "81.6",
        "total_shares": 12958000,
    },
    "sector_info": {"major_name": "반도체", "mid_name": "반도체장비"},
    "rs_data": {"rs": 91.25, "rs_1m": 1.78, "rs_3m": 88.85, "rs_6m": 92.05},
    "investment": {
        "base_period": "2026.1Q",
        "financial_type": "C",
        "overall_grade": "B",
        "growth": {
            "category_name": "성장성",
            "average_grade": "A",
            "metrics": [{"name": "매출성장률", "formatted_value": "-11.1%", "grade": "D"}],
        },
    },
    "target_price_history": [
        {
            "report_date": "2026-07-03",
            "securities_company": "메리츠증권",
            "author": "김동관",
            "investment_opinion": "Buy",
            "target_price": 75000,
            "target_price_change": "상향",
            "current_price": 45800,
            "upside_potential": 63.76,
            "title": "넘치는 수주와 폭발적 실적 성장",
        },
        {
            "report_date": "2026-04-22",
            "securities_company": "메리츠증권",
            "author": "김동관",
            "investment_opinion": "Buy",
            "target_price": 41000,
            "target_price_change": "신규",
            "current_price": 31450,
            "upside_potential": 30.4,
            "title": "4년만의 턴어라운드",
        },
        {
            "report_date": "2026-05-28",
            "securities_company": "NH투자증권",
            "author": "심의섭",
            "investment_opinion": "Not Rated",
            "target_price": None,
            "target_price_change": None,
            "current_price": 40650,
            "upside_potential": None,
            "title": "호황기에 더욱 돋보이는 경쟁력",
        },
    ],
    "financials": {
        "consolidated": [
            {
                "year": 2026,
                "quarter": 2,
                "month": 6,
                "revenue": 8960989867,
                "operating_income": -5117444745,
                "net_income": -6094354375,
                "eps": -470.32,
                "operating_margin": -57.11,
                "per": None,
                "pbr": None,
            }
        ],
        "consolidatedEstimate": [
            {
                "year": 2026,
                "quarter": 4,
                "month": 12,
                "revenue": 49267000000,
                "operating_income": 8533000000,
                "net_income": 8400000000,
                "eps": 578.81,
                "operating_margin": 17.32,
                "per": None,
                "pbr": 7.66,
            }
        ],
        "consolidatedYearly": [],
        "consolidatedYearlyEstimate": [],
    },
    "eps_changes": {
        "count": 10,
        "changes": [
            {
                "quarter": "2026Q4",
                "change_date_str": "2026-08-12",
                "value_old": 443.43,
                "value_new": 427.55,
                "change_rate": -3.58,
            }
        ],
    },
}

NEWS_PAYLOAD = {
    "items": [
        {
            "title": "TSMC, 첨단 패키징 외주 확대",
            "link": "https://n.news.naver.com/mnews/article/277/0005803223",
            "published_at": "2026-08-14T02:20:00Z",
            "source": "아시아경제",
        },
        {
            "title": "지난달 기사",
            "link": "https://example.com/old",
            "published_at": "2026-07-01T00:00:00Z",
            "source": "예시",
        },
    ]
}


REPORTS_PAYLOAD = {
    "total_count": 35,
    "items": [
        {
            "id": 1,
            "report_date": "2026-07-03",
            "securities_company": "메리츠증권",
            "author": "김동관",
            "investment_opinion": "Buy",
            "target_price": 75000,
            "target_price_change": "상향",
            "title": "넘치는 수주와 폭발적 실적 성장",
            # 실제 응답 모양: point 키를 가진 dict 리스트
            "summary_points": [
                {"point": "CoWoS 검사 장비 본계약 임박"},
                {"point": "생산능력 50% 확대 <b>계획</b>"},
            ],
            # 실제 응답 모양: 문자열이 아니라 섹션 dict
            "detail_content": {
                "card_news_sections": [
                    {"header": "3Q26 전망", "content": "수주잔고 인식 본격화"},
                ]
            },
            "file_name": "20260703_인텍플러스_064290_기계·장비_기업리포트_Meritz.pdf",
        }
    ],
}


def _patch_stock_info_fetch(
    module, monkeypatch: pytest.MonkeyPatch, search=None, cookie="session=abc", reports_error=None
) -> None:
    """네트워크를 수집 계층 한 곳에서 막는다.

    `resolve_stock`도 같은 모듈 안에서 `fetch_stock_json`을 부르므로, 스크립트 쪽 별칭이
    아니라 수집 계층을 패치해야 종목 해석까지 함께 덮인다.
    """
    calls = {}

    def fake_fetch(path, params=None, referer=module.PAGE_BASE, cookie=None):
        calls[path] = {"params": params, "cookie": cookie}
        if path == module.ENDPOINTS["search"]:
            return (search if search is not None else []), None
        if path.startswith("/stock-info/info-tab/"):
            return STOCK_INFO_PAYLOAD, None
        if path.startswith("/news/by-stock-code/"):
            return NEWS_PAYLOAD, None
        if path == module.ENDPOINTS["reports"]:
            if reports_error:
                return None, reports_error
            return REPORTS_PAYLOAD, None
        return None, "HTTP 404"

    monkeypatch.setattr(datafeed_stockeasy, "fetch_stock_json", fake_fetch)
    monkeypatch.setattr(datafeed_stockeasy, "load_cookie", lambda: cookie)
    return calls


def _load_http_cache_module():
    """캐시는 스킬 스크립트가 아니라 패키지 모듈이다 — 평범하게 import한다.

    `importlib.reload`로 새 모듈 객체를 돌려주는 이유: 이 아래 테스트들이 모듈 전역
    상태(`_forced_off`·`_purged`·`CACHE_ROOT`)를 건드리므로, 파일 로더가 매번 새
    모듈을 주던 예전 동작을 유지해야 서로 새지 않는다.
    """
    import importlib

    from invagent.datafeed import cache

    return importlib.reload(cache)


def test_http_cache_writes_under_the_ignored_output_dir() -> None:
    """`output/`만 gitignore다 — 한 칸 어긋나면 캐시 바이너리가 추적 대상 경로에 쌓인다."""
    module = _load_http_cache_module()
    assert module.CACHE_ROOT == REPO_ROOT / "output" / ".cache" / "http"


def test_http_cache_round_trips_within_the_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """같은 URL을 짧은 간격으로 두 번 부르는 경로(analyze-stock의 info-tab 중복)를 없애는 것이 목적이다."""
    module = _load_http_cache_module()
    monkeypatch.setattr(module, "CACHE_ROOT", tmp_path)

    url = "https://example.test/stock-info/info-tab/000660"
    assert module.load(url, authed=True) is None

    module.store(url, b'{"ok": 1}', authed=True)
    assert module.load(url, authed=True) == b'{"ok": 1}'


def test_http_cache_expires_and_never_mixes_auth_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """비인증 401 본문이 인증 호출에 재생되면 쿠키를 넣어도 계속 401로 보인다."""
    module = _load_http_cache_module()
    monkeypatch.setattr(module, "CACHE_ROOT", tmp_path)
    url = "https://example.test/stock-info/info-tab/000660"

    module.store(url, b"anon", authed=False)
    assert module.load(url, authed=True) is None
    assert module.load(url, authed=False) == b"anon"

    # TTL이 지나면 없는 것으로 취급한다.
    assert module.load(url, authed=False, ttl=0) is None


def test_http_cache_key_never_carries_the_cookie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """캐시 경로도 파일 내용도 쿠키 값을 남기면 안 된다 — `output/`은 gitignore지만 평문 디스크다.

    실제 쿠키를 붙여 수집 경로를 통째로 한 번 태운 뒤, 캐시 루트 어디에도 그 값이 없는지 본다.
    """
    secret = "SESSIONID=super-secret-value-42"
    module = _load_stock_info_module()
    monkeypatch.setattr(module.http_cache, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(module.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResponse(b'{"ok": 1}'))

    payload, err = datafeed_stockeasy.fetch_stock_json("/stock-info/info-tab/000660", cookie=secret)
    assert (payload, err) == ({"ok": 1}, None)

    written = list(tmp_path.rglob("*"))
    assert any(p.is_file() for p in written), "캐시가 쓰이지 않아 검사가 무의미하다"
    for path in written:
        assert secret not in str(path)
        if path.is_file():
            assert secret.encode() not in path.read_bytes()


def test_http_cache_evicts_stale_entries_once_per_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """purge()를 아무도 부르지 않으면 `output/.cache/`가 무한히 자란다.

    쓸 때 한 번만 쓸어내는 이유: 전수 스캔을 store()마다 돌리면 종목 수만큼 반복된다.
    """
    module = _load_http_cache_module()
    monkeypatch.setattr(module, "CACHE_ROOT", tmp_path)

    stale = tmp_path / "aa" / "aaaa.body"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    old_mtime = time.time() - module.RETENTION_SECONDS - 60
    os.utime(stale, (old_mtime, old_mtime))

    module.store("https://example.test/new", b"fresh", authed=False)

    assert not stale.exists(), "오래된 항목이 그대로 남았다"
    assert module.load("https://example.test/new", authed=False) == b"fresh"

    # 두 번째 쓰기는 다시 전수 스캔하지 않는다 — 프로세스당 한 번이 계약이다.
    later = tmp_path / "bb" / "bbbb.body"
    later.parent.mkdir(parents=True)
    later.write_bytes(b"old2")
    os.utime(later, (old_mtime, old_mtime))
    module.store("https://example.test/other", b"fresh2", authed=False)
    assert later.exists()


def test_http_cache_disable_does_not_leak_into_the_process_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--no-cache`가 프로세스 환경을 고치면 같은 프로세스의 뒤 작업까지 조용히 캐시를 잃는다.

    테스트에서도 `monkeypatch.delenv(raising=False)`는 **없던 변수**를 복원하지 않으므로
    한 테스트의 disable()이 뒤 테스트로 새어 나간다 — 통과 여부가 실행 순서에 걸린다.
    """
    module = _load_http_cache_module()
    monkeypatch.delenv(module.ENV_ENABLED, raising=False)

    module.disable()

    assert module.enabled() is False
    assert module.ENV_ENABLED not in os.environ, "프로세스 환경변수가 오염됐다"


def test_http_cache_can_be_switched_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """재현이 필요한 순간(캐시 오염 의심)에 끌 수 있어야 한다."""
    module = _load_http_cache_module()
    monkeypatch.setattr(module, "CACHE_ROOT", tmp_path)
    monkeypatch.setenv("INVAGENT_HTTP_CACHE", "0")

    module.store("https://example.test/x", b"body", authed=False)
    assert module.load("https://example.test/x", authed=False) is None


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def test_stock_info_serves_a_repeat_call_from_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """한 번의 analyze-stock 실행이 info-tab(약 128KB)을 두 번 받던 경로를 없앤다."""
    module = _load_stock_info_module()
    monkeypatch.setattr(module.http_cache, "CACHE_ROOT", tmp_path)

    calls: list[str] = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        return _FakeResponse(b'{"stock_code": "064290"}')

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    first, err1 = datafeed_stockeasy.fetch_stock_json("/stock-info/info-tab/064290")
    second, err2 = datafeed_stockeasy.fetch_stock_json("/stock-info/info-tab/064290")

    assert (err1, err2) == (None, None)
    assert first == second == {"stock_code": "064290"}
    assert len(calls) == 1, "두 번째 호출이 네트워크를 다시 탔다"


def test_stock_info_never_caches_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """일시적인 401·타임아웃이 TTL 동안 고착되면 쿠키를 고쳐도 계속 실패로 보인다."""
    module = _load_stock_info_module()
    monkeypatch.setattr(module.http_cache, "CACHE_ROOT", tmp_path)

    state = {"fail": True}

    def flaky_urlopen(req, timeout=None):
        if state["fail"]:
            raise module.urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
        return _FakeResponse(b'{"ok": 1}')

    monkeypatch.setattr(module.urllib.request, "urlopen", flaky_urlopen)

    assert datafeed_stockeasy.fetch_stock_json("/stock-info/info-tab/064290") == (None, "HTTP 401")
    state["fail"] = False
    payload, err = datafeed_stockeasy.fetch_stock_json("/stock-info/info-tab/064290")
    assert (payload, err) == ({"ok": 1}, None)


def _load_stage_scan_module():
    script_path = SKILLS_ROOT / "stage-analysis" / "scripts" / "stage_scan.py"
    spec = importlib.util.spec_from_file_location("stage_scan_cache", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_daily_bars_and_market_signals_share_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """peak_drawdown은 종목마다, stage_scan은 종목마다 같은 네이버 일봉을 다시 받는다."""
    stage = _load_stage_scan_module()
    monkeypatch.setattr(stage.http_cache, "CACHE_ROOT", tmp_path)

    body = "[['날짜'],\n['20260903', 1, 2, 3, 4, 5, 0.0]]".encode("utf-8")
    calls: list[str] = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        return _FakeResponse(body)

    monkeypatch.setattr(stage.urllib.request, "urlopen", fake_urlopen)
    first, err1 = datafeed_naver.fetch_bars("000660", 400)
    second, err2 = datafeed_naver.fetch_bars("000660", 400)
    assert (err1, err2) == (None, None)
    assert first == second and len(calls) == 1

    signals = _load_market_signal_module()
    monkeypatch.setattr(signals.http_cache, "CACHE_ROOT", tmp_path)
    sig_calls: list[str] = []

    def fake_signal_urlopen(req, timeout=None):
        sig_calls.append(req.full_url)
        return _FakeResponse(b'{"indices": []}')

    monkeypatch.setattr(signals.urllib.request, "urlopen", fake_signal_urlopen)
    assert signals.fetch_api("indices") == ({"indices": []}, None)
    assert signals.fetch_api("indices") == ({"indices": []}, None)
    assert len(sig_calls) == 1


def test_no_cache_flag_forces_a_fresh_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """캐시 오염이 의심될 때 껐다 확인할 수 있어야 한다 — 네트워크를 타는 스크립트 전부에 붙인다."""
    module = _load_stock_info_module()
    monkeypatch.setattr(module.http_cache, "CACHE_ROOT", tmp_path)
    monkeypatch.delenv("INVAGENT_HTTP_CACHE", raising=False)

    calls: list[str] = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        return _FakeResponse(b'{"ok": 1}')

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    datafeed_stockeasy.fetch_stock_json("/stock-info/info-tab/064290")
    assert len(calls) == 1

    module.http_cache.disable()
    datafeed_stockeasy.fetch_stock_json("/stock-info/info-tab/064290")
    assert len(calls) == 2, "disable() 후에도 캐시가 응답했다"

    # 네트워크를 타는 스크립트 셋 모두 플래그를 노출하고 실제로 끈다.
    for name in ("analyze-stock/scripts/fetch_stock_info.py",
                 "stage-analysis/scripts/stage_scan.py",
                 "stage-analysis/scripts/turn_scan.py",
                 "daily-digest/scripts/peak_drawdown.py"):
        text = (SKILLS_ROOT / name).read_text(encoding="utf-8")
        assert "--no-cache" in text, f"{name}에 --no-cache가 없다"
        assert "http_cache.disable()" in text, f"{name}이 플래그를 캐시에 연결하지 않았다"


def test_briefing_prep_steps_have_one_entry_point() -> None:
    """1-1·1-3-1은 서로 독립인데 각각 별도 bash 왕복으로 돌았다."""
    skill = (SKILLS_ROOT / "daily-digest" / "SKILL.md").read_text(encoding="utf-8")

    assert "uv run invagent daily-prep" in skill
    # 개별 명령은 지우지 않는다 — 한 단계만 다시 돌릴 때 쓰고, 해석 기준의 정본이다.
    for script in ("fetch_market_signals.py", "peak_drawdown.py"):
        assert script in skill
    # 스냅샷이 없으면 낙폭 단계는 실패가 아니라 미실행이라는 점이 문서에 있어야 한다.
    assert "그 파일이 이미 있을 때만" in skill


def test_http_cache_ttl_is_documented_where_it_is_explained() -> None:
    """TTL은 시세 신선도와 맞물린 운영 선택이다 — 상수만 바꾸고 근거가 남으면 다음 사람이 못 읽는다."""
    module = _load_http_cache_module()
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    minutes = module.DEFAULT_TTL_SECONDS // 60
    assert f"{minutes}분" in agents, "AGENTS.md의 TTL 설명이 상수와 어긋난다"
    assert "INVAGENT_HTTP_CACHE" in agents
    # 모듈이 옮겨가면 설명도 따라와야 한다. 우연히 남은 옛 이름으로 통과하지 않도록 경로로 찾는다.
    anchor = "src/invagent/datafeed/cache.py"
    assert anchor in agents, "AGENTS.md가 캐시 모듈의 현재 위치를 가리키지 않는다"
    # 왜 일 단위가 아닌지가 이 설계의 핵심이다.
    assert "현재가" in agents.split(anchor)[1][:1200]


def test_stock_info_endpoints_point_at_stockdata_api() -> None:
    """종목 데이터는 /stockdata/api/v1 경로에서 받는다."""
    module = _load_stock_info_module()

    assert module.API_BASE == "https://stockeasy.intellio.kr/stockdata/api/v1"
    assert set(module.ENDPOINTS) == {"search", "info_tab", "analysis_tab", "news", "reports"}
    assert module.COOKIE_ENV == "STOCKEASY_COOKIE"


def test_stock_info_sends_cookie_to_info_tab_and_news(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """2026-08 이후 info-tab·news도 로그인 세션을 요구한다 — 쿠키를 함께 보낸다."""
    module = _load_stock_info_module()
    calls = _patch_stock_info_fetch(module, monkeypatch)

    assert module.main(["064290", "--news", "3"]) == 0
    capsys.readouterr()

    info_call = next(v for k, v in calls.items() if k.startswith("/stock-info/info-tab/"))
    news_call = next(v for k, v in calls.items() if k.startswith("/news/by-stock-code/"))
    assert info_call["cookie"] == "session=abc"
    assert news_call["cookie"] == "session=abc"


def test_stock_info_consensus_counts_latest_report_per_broker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """같은 증권사의 옛 목표가가 평균을 끌어올리면 안 된다 — 증권사별 최신 1건만 센다."""
    module = _load_stock_info_module()
    _patch_stock_info_fetch(module, monkeypatch)

    assert module.main(["064290"]) == 0
    out = capsys.readouterr().out

    # 메리츠 2건(2026-07-03 75,000 / 2026-04-22 41,000) → 최신 1건만
    assert "평균 목표가 75,000원 (최고 75,000 / 최저 75,000)" in out
    assert "상향 1 / 하향 0" in out
    # 전체 이력 단순평균은 참고로 남기되 blend 입력이 아님을 명시한다
    assert "전체 이력 2건 단순평균은 58,000원" in out
    assert "9단계 blend 입력이 아니다" in out


def test_stock_info_info_tab_401_points_at_cookie(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """info-tab이 401이면 원인은 쿠키다 — 사유를 stderr에 특정해 준다."""
    module = _load_stock_info_module()

    def fake_fetch(path, params=None, referer=module.PAGE_BASE, cookie=None):
        if path == module.ENDPOINTS["search"]:
            return [{"stock_code": "064290", "stock_name": "인텍플러스", "market": "KR"}], None
        return None, "HTTP 401"

    monkeypatch.setattr(datafeed_stockeasy, "fetch_stock_json", fake_fetch)
    monkeypatch.setattr(datafeed_stockeasy, "load_cookie", lambda: "session=stale")

    assert module.main(["064290"]) == 1
    err = capsys.readouterr().err
    assert "STOCKEASY_COOKIE 만료·무효" in err


def test_stock_info_price_fields_strip_direction_sign() -> None:
    """`cur_prc: "-30850"`의 부호는 등락 방향 마커다. 가격은 절대값으로 읽는다."""
    module = _load_stock_info_module()

    assert module.unsigned("-30850") == 30850
    assert module.unsigned("+54100") == 54100
    assert module.unsigned("") is None
    # 등락률·손익 지표의 부호는 의미가 있으므로 유지한다
    assert module.signed("-3.89") == -3.89
    assert module.signed("+261.83") == 261.83


def test_stock_info_main_renders_quote_and_consensus(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_stock_info_module()
    _patch_stock_info_fetch(module, monkeypatch)

    assert module.main(["064290"]) == 0

    out = capsys.readouterr().out
    assert "인텍플러스(064290)" in out
    assert "[시세] 30,850원 (-3.89%" in out  # 음수 가격으로 새지 않는다
    assert "고 54,100원(2026-07-06" in out
    assert "저 8,540원(2025-08-06" in out
    assert "메리츠증권" in out and "75,000원" in out and "상향" in out
    # 목표가 미제시(Not Rated) 건은 빠지고, 같은 증권사는 최신 1건(2026-07-03 75,000)만 센다.
    assert "평균 목표가 75,000원" in out
    assert "커버 1사 / 목표가 제시 2건" in out
    assert "추정 — 컨센서스, DART 데이터 아님" in out
    assert "2026.2Q | 확정" in out and "2026.4Q | **추정 E**" in out
    assert "2026Q4 2026-08-12: 443.43 → 427.55 (-3.58%)" in out


def test_stock_info_since_filters_news_and_flags_new_reports(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """승계 작성 시 --since 이후 뉴스만 남기고 신규 리포트를 표시한다."""
    module = _load_stock_info_module()
    _patch_stock_info_fetch(module, monkeypatch)

    assert module.main(["064290", "--since", "2026-08-01"]) == 0

    out = capsys.readouterr().out
    assert "[뉴스] 1건 (기준일 2026-08-01 이후)" in out
    assert "지난달 기사" not in out
    assert "🆕" not in out  # 리포트는 전부 기준일 이전


def test_stock_info_renders_report_summaries_with_cookie(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`securities-reports`는 로그인 쿠키를 붙여야 요약이 나온다."""
    module = _load_stock_info_module()
    calls = _patch_stock_info_fetch(module, monkeypatch, cookie="session=abc")

    assert module.main(["064290", "--since", "2026-08-01"]) == 0

    out = capsys.readouterr().out
    assert "[리포트 요약] 1건 (총 35건)" in out
    assert "2026-07-03 [메리츠증권] 김동관 · Buy 목표가 75,000원(상향)" in out
    assert "· CoWoS 검사 장비 본계약 임박" in out
    assert "· 생산능력 50% 확대 계획" in out  # HTML 태그 제거
    assert "파일: 20260703_인텍플러스_064290" in out  # 로컬 PDF 중복 판정용
    assert "본문:" not in out  # --detail-chars 0이면 본문 생략
    # 쿠키는 세 엔드포인트 모두에 붙고, 리포트에는 date_from으로 기준일이 넘어간다
    reports_call = calls[module.ENDPOINTS["reports"]]
    assert reports_call["cookie"] == "session=abc"
    assert reports_call["params"]["date_from"] == "2026-08-01"
    assert calls["/stock-info/info-tab/064290"]["cookie"] == "session=abc"
    # 쿠키 값은 stdout으로 새지 않는다
    assert "session=abc" not in out


def test_stock_info_detail_content_sections_flatten(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`detail_content`는 문자열이 아니라 card_news_sections dict로 온다."""
    module = _load_stock_info_module()
    _patch_stock_info_fetch(module, monkeypatch)

    assert module.main(["064290", "--detail-chars", "200"]) == 0
    assert "본문: 3Q26 전망: 수주잔고 인식 본격화" in capsys.readouterr().out


def test_stock_info_missing_cookie_only_skips_reports(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """쿠키가 없으면 리포트 섹션만 비고 나머지는 정상 출력된다 (비블로킹)."""
    module = _load_stock_info_module()
    _patch_stock_info_fetch(module, monkeypatch, cookie=None)

    assert module.main(["064290"]) == 0

    captured = capsys.readouterr()
    assert "[리포트 요약] 미수집 — STOCKEASY_COOKIE 미설정" in captured.out
    assert "[시세] 30,850원" in captured.out
    assert "STOCKEASY_COOKIE 미설정" in captured.err


def test_stock_info_expired_cookie_hints_refresh(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_stock_info_module()
    _patch_stock_info_fetch(module, monkeypatch, reports_error="HTTP 401")

    assert module.main(["064290"]) == 0
    assert "쿠키 만료" in capsys.readouterr().out


def test_stock_info_cookie_read_from_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """저장소는 dotenv를 쓰지 않으므로 수집 계층이 .env를 직접 훑는다."""
    from invagent.datafeed import env

    module = _load_stock_info_module()
    monkeypatch.delenv(module.COOKIE_ENV, raising=False)

    nested = tmp_path / "src" / "invagent" / "datafeed"
    nested.mkdir(parents=True)
    (tmp_path / ".env").write_text(
        "# comment\nTELEGRAM_API_ID=1\nSTOCKEASY_COOKIE='session=from-env-file'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env, "__file__", str(nested / "env.py"))

    assert datafeed_stockeasy.load_cookie() == "session=from-env-file"

    monkeypatch.setenv(module.COOKIE_ENV, "session=from-environ")
    assert datafeed_stockeasy.load_cookie() == "session=from-environ"


def test_stock_info_shows_after_hours_quotes_apart_from_the_regular_close(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """2026-09 개편된 info-tab은 KRX 시간외·NXT 체결가를 따로 싣는다.

    판정은 정규장 종가(`cur_prc`)로만 하므로 시간외가는 참고줄로 떼어 보여 주고, 차이는
    API의 `flu_rt`(전일 종가 대비)가 아니라 오늘 정규장가 대비로 다시 잰다.
    """
    module = _load_stock_info_module()
    _patch_stock_info_fetch(module, monkeypatch)
    payload = {
        **STOCK_INFO_PAYLOAD,
        "after_hours_quote": {
            "dt": "20260929", "cur_prc": "-31467", "pred_pre": "-633", "flu_rt": "-1.97",
            "cntr_tm": "195959", "market": "krx", "session": "after",
        },
        "nxt_quote": {
            "dt": "20260929", "cur_prc": "-30233", "pred_pre": "-1867", "flu_rt": "-5.82",
            "cntr_tm": "195959", "session": "after",
        },
    }
    fake = datafeed_stockeasy.fetch_stock_json

    def with_after_hours(path, params=None, referer=module.PAGE_BASE, cookie=None):
        if path.startswith("/stock-info/info-tab/"):
            return payload, None
        return fake(path, params, referer=referer, cookie=cookie)

    monkeypatch.setattr(datafeed_stockeasy, "fetch_stock_json", with_after_hours)

    assert module.main(["064290"]) == 0
    out = capsys.readouterr().out

    assert "[시세] 30,850원 (-3.89%" in out  # 판정 가격은 그대로 정규장가
    line = next(l for l in out.splitlines() if l.startswith("[시간외·NXT]"))
    # 30,850 → 31,467은 +2.0%, 30,233은 -2.0% (API 등락률 -1.97/-5.82%는 전일 종가 기준)
    assert "KRX 시간외 31,467원(정규장가 대비 +2.0%)" in line
    assert "NXT 애프터마켓 30,233원(정규장가 대비 -2.0%)" in line
    assert "2026-09-29 19:59:59" in line
    assert "판정 미사용" in line


def test_stock_info_omits_the_after_hours_line_when_none_is_served(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_stock_info_module()
    _patch_stock_info_fetch(module, monkeypatch)

    assert module.main(["064290"]) == 0
    assert "[시간외·NXT]" not in capsys.readouterr().out


ANALYSIS_TAB_PAYLOAD = {
    "stock_code": "064290",
    "disclosures": [
        {
            "id": 406219,
            "rcept_no": "20260818800754",
            # 실제 응답은 제목 뒤에 공백을 채워 온다
            "report_nm": "최대주주등소유주식변동신고서              ",
            "rcept_dt": "2026-08-18",
            "corp_name": "인텍플러스",
            "disclosure_type": "common_table",
        },
        {
            "id": 406100,
            "rcept_no": "20260714000123",
            "report_nm": "반기보고서 (2026.06)",
            "rcept_dt": "2026-07-14",
            "corp_name": "인텍플러스",
            "disclosure_type": "interim_results",
        },
    ],
    "reports": [],
    "news": [],
    "target_price_history": [],
    "status": "success",
}


def test_stock_info_lists_disclosures_from_the_analysis_tab(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """2026-09 개편 페이지의 소식 탭(`analysis-tab`)이 종목별 DART 공시 목록을 싣는다."""
    module = _load_stock_info_module()
    calls = _patch_stock_info_fetch(module, monkeypatch)
    fake = datafeed_stockeasy.fetch_stock_json

    def with_disclosures(path, params=None, referer=module.PAGE_BASE, cookie=None):
        if path == "/stock-info/analysis-tab/064290":
            calls[path] = {"params": params, "cookie": cookie}
            return ANALYSIS_TAB_PAYLOAD, None
        return fake(path, params, referer=referer, cookie=cookie)

    monkeypatch.setattr(datafeed_stockeasy, "fetch_stock_json", with_disclosures)

    assert module.main(["064290", "--since", "2026-08-01"]) == 0
    out = capsys.readouterr().out

    assert "[공시] 1건 (기준일 2026-08-01 이후) · API 최근 2건 중 필터" in out
    assert "  - 2026-08-18 최대주주등소유주식변동신고서 · rcept_no 20260818800754" in out
    assert "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260818800754" in out
    assert "반기보고서" not in out
    assert calls["/stock-info/analysis-tab/064290"]["cookie"] == "session=abc"


def test_stock_info_missing_disclosures_do_not_block(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_stock_info_module()
    _patch_stock_info_fetch(module, monkeypatch)  # analysis-tab → HTTP 404

    assert module.main(["064290"]) == 0
    out = capsys.readouterr().out
    assert "[공시] 미수집 — HTTP 404" in out
    assert "[시세] 30,850원" in out


def test_stock_info_ambiguous_name_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """후보가 여러 개면 스킬이 사용자에게 되묻도록 exit 2로 알린다."""
    module = _load_stock_info_module()
    _patch_stock_info_fetch(
        module,
        monkeypatch,
        search=[
            {"market": "KR", "stock_code": "007280", "stock_name": "한국특강", "exchange": "KOSPI"},
            {"market": "KR", "stock_code": "161890", "stock_name": "한국콜마", "exchange": "KOSPI"},
        ],
    )

    assert module.main(["한국"]) == 2
    assert "후보 다수" in capsys.readouterr().err


def test_stock_info_unknown_name_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_stock_info_module()
    _patch_stock_info_fetch(module, monkeypatch, search=[])

    assert module.main(["없는종목명"]) == 1
    assert "검색 결과 없음" in capsys.readouterr().err


PORTFOLIO_DUMP = """|  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
| :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| 구분 |  계좌 | 섹터 | 종목 | 보유 | 평단 | 현재가 | 매수금액 | 평가금액 | 수익률 | 손익 | 비중 |  | 잔고 | ₩210,220,000 | 수익률 | 25.53% |
| B | 삼성 | 반도체 | 알파전자 | 95 | ₩703,575 | ₩1,466,000 | ₩66,839,625 | ₩139,270,000 | 108.36% | ₩72,430,375 | 26.3% | 슈퍼 사이클 |  |  |  |  |
| B | 삼성 | 바이오 | 베타파마 | 900 | ₩68,845 | ₩57,500 | ₩61,960,500 | ₩51,750,000 | \\-16.48% | \\-₩10,210,500 | 9.8% | 신약 |  |  |  |  |
| C | 삼성 | 소비재 | 감마엔터 | 500 | ₩30,278 | ₩28,400 | ₩15,139,000 | ₩14,200,000 | \\-9.20% | \\-₩939,000 | 2.7% | 신작 |  |  |  |  |
| 0 | 은행 | 현금 | \\_현금 | 1 | ₩5,000,000 | ₩5,000,000 | ₩5,000,000 | ₩5,000,000 | 0.00% | ₩0 | 5.1% | 현금도 종목이다 |  |  |  |  |

|  |  |  |  |  |
| :-: | :-: | :-: | :-: | :-: |
|  | 섹터 | 비중 | 평가금액 | 수익률 |
|  | 반도체 | 42.3% | ₩139,270,000 | 108.36% |
|  | 바이오 | 24.1% | ₩51,750,000 | \\-16.48% |
|  |  | 0.0% | ₩0 | \\#DIV/0\\! |

|  |  |  |  |
| :-: | :-: | :-: | :-: |
| 종목 | 최초 투자 | 투자 종료 | 수익률 |
| 델타중공업 | 2024-01-02 | 2024-05-05 | 31.00% |
"""


def _load_portfolio_module():
    script_path = SKILLS_ROOT / "daily-digest" / "scripts" / "extract_portfolio.py"
    spec = importlib.util.spec_from_file_location("extract_portfolio", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_portfolio_parser_extracts_first_sheet_only() -> None:
    module = _load_portfolio_module()
    snapshot = module.build_snapshot(PORTFOLIO_DUMP, "2026-08-09")

    assert "# 포트폴리오 스냅샷 — 2026-08-09" in snapshot
    assert "잔고 ₩210,220,000 · 수익률 25.53%" in snapshot
    assert "## 보유 (3종목 + 현금)" in snapshot
    # 다음 시트(매매기록)는 잘라낸다
    assert "델타중공업" not in snapshot
    assert "최초 투자" not in snapshot
    # 섹터 표는 유지하되 빈 행·#DIV/0! 행은 버린다
    assert "| 반도체 | 42.3% | ₩139,270,000 | 108.36% |" in snapshot
    assert "DIV/0" not in snapshot


def test_portfolio_parser_unescapes_and_flags_rules() -> None:
    module = _load_portfolio_module()
    snapshot = module.build_snapshot(PORTFOLIO_DUMP, "2026-08-09")

    assert "-16.48%" in snapshot and "\\-16.48%" not in snapshot
    assert "| _현금 | 현금 |" in snapshot

    assert "매매규칙 6(-20% 전량 매도): 해당 없음" in snapshot
    assert "매매규칙 6(-15% 1차 분할 매도): 베타파마 -16.48% (비중 9.8%) ← **위반**" in snapshot
    # -9.20%는 아직 「매매규칙 7」의 -10% 선에 닿지 않았다.
    assert "매매규칙 7(-10% 비중 축소 고려): 해당 없음" in snapshot
    assert "매매규칙 11(24~30%↑ 익절 쿠션): 알파전자 +108.36% (비중 26.3%)" in snapshot
    assert "매매규칙 9(평가 비중 35% 상한): 해당 없음" in snapshot
    # 현금은 종목 수·룰 판정에서 제외
    assert "매매규칙 9(종목 수 5~12): 3종목 ← **미달**" in snapshot
    assert "현금 비중: 5.1% (₩5,000,000)" in snapshot


PORTFOLIO_CSV = (
    "구분, 계좌,섹터,종목,보유,평단,현재가,매수금액,평가금액,수익률,손익,비중,,잔고,\"₩21,000,000\",총손익,\"₩1,000,000\",투자금,\"₩20,000,000\",수익률,5.00%\r\n"
    "0,은행,현금,_현금,1,\"₩5,000,000\",\"₩5,000,000\",\"₩5,000,000\",\"₩5,000,000\",0.00%,₩0,23.8%,현금도 종목이다,,,,,,,,\r\n"
    "A,삼성,반도체,알파전자,10,\"₩1,000,000\",\"₩1,100,000\",\"₩10,000,000\",\"₩11,000,000\",10.00%,\"₩1,000,000\",52.4%,\"슈퍼 사이클, 비중 30% 실현\",,,,,,,,\r\n"
    "B,삼성,기판,오메가기판,50,\"₩100,000\",\"₩100,000\",\"₩5,000,000\",\"₩5,000,000\",-0.00%,₩0,23.8%,\"증설, 저평가\",,,,,,,,"
)


def _drive_download_response(csv_text: str) -> str:
    """Drive MCP `download_file_content`(exportMimeType=text/csv) 응답을 그대로 저장한 모양."""
    encoded = base64.b64encode(csv_text.encode("utf-8")).decode("ascii")
    return json.dumps({"content": encoded, "mimeType": "text/csv", "title": "주식 포트폴리오"})


def test_portfolio_parser_reads_drive_csv_download(tmp_path: Path) -> None:
    """read_file_content가 요약만 돌려주게 된 뒤(2026-09-28) 정본 입력은 CSV 내보내기 응답이다."""
    module = _load_portfolio_module()
    dump = tmp_path / "download.json"
    dump.write_text(_drive_download_response(PORTFOLIO_CSV), encoding="utf-8")

    snapshot = module.build_snapshot(module.load_content(dump), "2026-09-28")

    assert "잔고 ₩21,000,000 · 총손익 ₩1,000,000 · 투자금 ₩20,000,000 · 수익률 5.00%" in snapshot
    assert "## 보유 (2종목 + 현금)" in snapshot
    assert "| 삼성 | 오메가기판 | 기판 | 50 |" in snapshot
    # 셀 안의 쉼표(따옴표로 감싼 금액)가 칸을 가르지 않는다
    assert "| 알파전자 | 반도체 | 10 | ₩1,000,000 | ₩1,100,000 | ₩10,000,000 | 10.00% | 52.4% | ₩11,000,000 |" in snapshot
    assert "| _현금 | 현금 |" in snapshot


def test_portfolio_parser_reads_plain_csv(tmp_path: Path) -> None:
    module = _load_portfolio_module()
    dump = tmp_path / "portfolio.csv"
    dump.write_text(PORTFOLIO_CSV, encoding="utf-8")

    snapshot = module.build_snapshot(module.load_content(dump), "2026-09-28")

    assert "## 보유 (2종목 + 현금)" in snapshot
    assert "| 삼성 | 오메가기판 | 기판 | 50 |" in snapshot


def test_portfolio_parser_rejects_truncated_holdings(tmp_path: Path) -> None:
    """행이 잘린 입력은 스냅샷이 되지 않는다 — 2026-09-28 CSV 옮겨 적기에서 마지막 행이 빠져
    이수페타시스가 「전량 청산」으로 읽힐 뻔했다. 보유 행 합계를 시트의 잔고와 대조해 잡는다."""
    module = _load_portfolio_module()
    truncated = PORTFOLIO_CSV.rsplit("\r\n", 1)[0]  # 오메가기판 행 누락
    dump = tmp_path / "download.json"
    dump.write_text(_drive_download_response(truncated), encoding="utf-8")

    with pytest.raises(ValueError, match="잔고"):
        module.build_snapshot(module.load_content(dump), "2026-09-28")


STOP_TIER_DUMP = """|  |  |  |  |  |  |  |  |  |  |  |  |
| :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| 구분 |  계좌 | 섹터 | 종목 | 보유 | 평단 | 현재가 | 매수금액 | 평가금액 | 수익률 | 손익 | 비중 |
| B | 삼성 | 반도체 | 앱실론 | 10 | ₩10,000 | ₩7,800 | ₩100,000 | ₩78,000 | \\-22.00% | \\-₩22,000 | 12.0% | |
| B | 삼성 | 바이오 | 제타 | 10 | ₩10,000 | ₩8,400 | ₩100,000 | ₩84,000 | \\-16.00% | \\-₩16,000 | 11.0% | |
| B | 삼성 | 소비재 | 에타 | 10 | ₩10,000 | ₩8,900 | ₩100,000 | ₩89,000 | \\-11.00% | \\-₩11,000 | 10.0% | |
| B | 삼성 | 화학 | 세타 | 10 | ₩10,000 | ₩9,500 | ₩100,000 | ₩95,000 | \\-5.00% | \\-₩5,000 | 9.0% | |

|  |  |
| :-: | :-: |
| 종목 | 최초 투자 |
"""


def test_portfolio_grades_stop_loss_in_two_tiers() -> None:
    """「매매규칙 6」은 -15% 1차 분할 + -20% 전량 두 티어다. 깊은 티어가 얕은 티어를 흡수한다."""
    module = _load_portfolio_module()
    snapshot = module.build_snapshot(STOP_TIER_DUMP, "2026-09-05")

    assert "매매규칙 6(-20% 전량 매도): 앱실론 -22.00% (비중 12.0%) ← **위반**" in snapshot
    assert "매매규칙 6(-15% 1차 분할 매도): 제타 -16.00% (비중 11.0%) ← **위반**" in snapshot
    # -22%는 전량 티어에만 잡힌다 — 한 종목이 두 줄에 겹쳐 세어지지 않는다.
    assert "매매규칙 6(-15% 1차 분할 매도): 앱실론" not in snapshot
    # -11%는 축소 고려, -5%는 아무 데도 안 걸린다.
    assert "매매규칙 7(-10% 비중 축소 고려): 에타 -11.00% (비중 10.0%)" in snapshot
    assert "세타" not in snapshot.split("## 룰 자동 판정")[1]


WEIGHT_DUMP = """|  |  |  |  |  |  |  |  |  |  |  |  |
| :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| 구분 |  계좌 | 섹터 | 종목 | 보유 | 평단 | 현재가 | 매수금액 | 평가금액 | 수익률 | 손익 | 비중 |
| B | 삼성 | 반도체 | 이오타 | 10 | ₩10,000 | ₩12,000 | ₩100,000 | ₩120,000 | 20.00% | ₩20,000 | 36.0% | |
| B | 삼성 | 바이오 | 카파 | 10 | ₩10,000 | ₩11,000 | ₩100,000 | ₩110,000 | 10.00% | ₩10,000 | 31.0% | |

|  |  |
| :-: | :-: |
| 종목 | 최초 투자 |
"""


def test_portfolio_weight_cap_is_the_valuation_axis() -> None:
    """「매매규칙 9」의 35%는 **평가 비중** 상한이다. 매수원금 비중 상한(2%÷손절률)과 다른 축이라 라벨로 갈라 쓴다."""
    module = _load_portfolio_module()
    snapshot = module.build_snapshot(WEIGHT_DUMP, "2026-09-05")

    assert "매매규칙 9(평가 비중 35% 상한): 이오타 36.0% ← **위반**" in snapshot
    # 시트의 비중은 평가 비중이므로 매수원금 축 룰을 여기에 인용하지 않는다.
    assert "매수원금" not in snapshot.split("## 룰 자동 판정")[1]


def _my_rules() -> str:
    return (REPO_ROOT / "context" / "my_rules.md").read_text(encoding="utf-8")


def _credit_rows(values: list[float]) -> list[dict]:
    return [{"date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "margin_call_amount": v}
            for i, v in enumerate(values)]


def _monitor_for(credit: list[dict], closes: list[float]) -> list[dict]:
    return [{"일자": r["date"], "KOSPI": c, "KOSDAQ": c} for r, c in zip(credit, closes)]


def _deep_drawdown(credit: list[dict]) -> list[dict]:
    """마지막 날이 고점 대비 -20%인 지수 경로 — 낙폭 게이트를 통과한다."""
    return _monitor_for(credit, [100.0] * (len(credit) - 1) + [80.0])


def test_margin_call_climax_fires_beyond_two_sigma() -> None:
    """지수 급락의 반대매매 클라이맥스는 지수 레버리지 신규 매수가 허용되는 자리다."""
    module = _load_market_signal_module()

    calm = [100.0, 120.0, 90.0, 110.0, 95.0] * 30      # 150일 평온
    credit = _credit_rows(calm + [2000.0])
    verdict = module.margin_call_climax(credit, _deep_drawdown(credit))["KOSPI"]

    assert verdict["climax"] is True
    assert verdict["value"] == 2000.0
    assert verdict["threshold"] < 2000.0
    assert verdict["sigma"] == module.MARGIN_CALL_SIGMA


def test_margin_call_climax_stays_quiet_on_an_ordinary_day() -> None:
    module = _load_market_signal_module()
    calm = [100.0, 120.0, 90.0, 110.0, 95.0] * 30
    # 기준선 평균 103.0 · σ 10.77 → 임계 124.5. 그 안쪽 값은 클라이맥스가 아니다.
    credit = _credit_rows(calm + [115.0])
    verdict = module.margin_call_climax(credit, _deep_drawdown(credit))["KOSPI"]

    assert verdict["climax"] is False
    assert verdict["value"] < verdict["threshold"]


def test_margin_call_climax_excludes_today_from_its_own_baseline() -> None:
    """오늘 값이 평균·표준편차에 섞이면 큰 값일수록 자기 임계를 끌어올려 신호가 무뎌진다."""
    module = _load_market_signal_module()
    calm = [100.0] * 150
    credit = _credit_rows(calm + [500.0])

    spike = module.margin_call_climax(credit, _deep_drawdown(credit))["KOSPI"]
    # 당일을 포함했다면 σ가 0이 아니게 되어 임계가 500 근처까지 밀린다.
    assert spike["threshold"] == 100.0
    assert spike["climax"] is True


def _ladder_monitor(closes: list[float]) -> list[dict]:
    return [{"일자": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "KOSPI": c, "KOSDAQ": c}
            for i, c in enumerate(closes)]


def test_drawdown_ladder_reports_every_five_percent_from_ten() -> None:
    """고점 대비 -10%부터 5%마다 단을 둔다 — 어느 단까지 밟았는지가 분할 매수의 눈금이다."""
    module = _load_market_signal_module()

    assert module.DRAWDOWN_RUNGS[:5] == (-10.0, -15.0, -20.0, -25.0, -30.0)
    assert -35.0 in module.DRAWDOWN_RUNGS and -40.0 in module.DRAWDOWN_RUNGS

    ladder = module.drawdown_ladder(_ladder_monitor([100.0] * 60 + [78.0]), "KOSPI")

    assert round(ladder["drawdown"], 1) == -22.0
    assert ladder["breached"] == [-10.0, -15.0, -20.0]
    assert ladder["next_rung"] == -25.0
    # 다음 단까지 지수가 얼마여야 하는지 — 미리 알아야 주문으로 옮긴다.
    assert round(ladder["next_level"], 1) == 75.0


def test_drawdown_ladder_flags_the_rung_first_touched_today() -> None:
    """새로 밟은 단이 행동 시점이다. 2026-07-30 저점에서 KOSPI는 -35%를 T-1에 처음 밟았다."""
    module = _load_market_signal_module()

    # 어제 -22%(=-20% 단), 오늘 -27%(=-25% 단 신규)
    ladder = module.drawdown_ladder(_ladder_monitor([100.0] * 60 + [78.0, 73.0]), "KOSPI")

    assert ladder["newly"] == [-25.0]

    # 같은 단에 머무르면 신규가 아니다.
    same = module.drawdown_ladder(_ladder_monitor([100.0] * 60 + [78.0, 77.0]), "KOSPI")
    assert same["newly"] == []


def test_drawdown_ladder_does_not_re_announce_a_rung_it_just_left() -> None:
    """경계선을 왕복하면 같은 단이 며칠 간격으로 되풀이 발동한다.

    실측 재현에서 KOSPI는 2026-07-28에 -30%를 처음 밟고, 반등 뒤 08-03·08-06에 같은 단을
    다시 「신규」로 알렸다. 분할 매수 신호가 같은 자리에서 세 번 울리면 눈금 구실을 못 한다.
    """
    module = _load_market_signal_module()

    # -30% 밟음 → 회복 → 재이탈. 재이탈은 신규가 아니다.
    closes = [100.0] * 60 + [69.0, 75.0, 69.5]
    ladder = module.drawdown_ladder(_ladder_monitor(closes), "KOSPI")
    assert -30.0 in ladder["breached"]
    assert ladder["newly"] == [], "쿨다운 안에서 같은 단이 다시 신규로 잡혔다"

    # 쿨다운을 넘겨 오래 떠 있다가 다시 내려오면 그때는 신규다.
    long_gap = [100.0] * 60 + [69.0] + [75.0] * (module.RUNG_RECLAIM_DAYS + 1) + [69.5]
    assert module.drawdown_ladder(_ladder_monitor(long_gap), "KOSPI")["newly"] == [-30.0]


def test_drawdown_ladder_is_quiet_above_the_first_rung() -> None:
    """-10%에 못 미치면 사다리는 아직 시작도 안 했다."""
    module = _load_market_signal_module()
    ladder = module.drawdown_ladder(_ladder_monitor([100.0] * 60 + [95.0]), "KOSPI")

    assert ladder["breached"] == [] and ladder["next_rung"] == -10.0


def test_leverage_entry_window_needs_both_the_drawdown_and_the_climax() -> None:
    """「레버리지 규칙 2」 베팅 가능 구간 = 낙폭 -10% 이하 **그리고** 반대매매 2σ."""
    module = _load_market_signal_module()
    calm = [100.0, 120.0, 90.0, 110.0, 95.0] * 30
    credit = _credit_rows(calm + [2000.0])
    deep = _deep_drawdown(credit)

    window = module.leverage_entry_window(credit, deep)
    assert window["KOSPI"]["open"] is True
    assert window["KOSPI"]["ladder"]["breached"], "사다리 단이 함께 실려야 어디서 살지 정할 수 있다"

    # 반대매매가 잠잠하면 낙폭이 깊어도 구간이 아니다.
    quiet = _credit_rows(calm + [110.0])
    assert module.leverage_entry_window(quiet, _deep_drawdown(quiet))["KOSPI"]["open"] is False


def test_margin_call_climax_is_judged_per_index() -> None:
    """반대매매는 시장 전체 값이지만 낙폭 국면은 지수마다 다르다 — 레버리지 상품이 지수별이므로 따로 낸다.

    실측(2025-01~2026-09): 같은 반대매매 시리즈에 KOSPI 게이트는 8건, KOSDAQ 게이트는 9건이
    걸리고 겹치는 날은 3일뿐이다.
    """
    module = _load_market_signal_module()
    calm = [100.0, 120.0, 90.0, 110.0, 95.0] * 30
    credit = _credit_rows(calm + [2000.0])
    n = len(credit)
    # KOSPI만 깊게 밀린 경로
    monitor = [
        {"일자": r["date"], "KOSPI": k, "KOSDAQ": q}
        for r, k, q in zip(credit, [100.0] * (n - 1) + [80.0], [100.0] * n)
    ]

    result = module.margin_call_climax(credit, monitor)

    assert set(result) == set(module.LEVERAGE_MARKETS)
    assert result["KOSPI"]["climax"] is True
    assert result["KOSDAQ"]["climax"] is False
    # 급증 자체는 시장 공통이므로 두 축에 같은 값이 실린다.
    assert result["KOSPI"]["value"] == result["KOSDAQ"]["value"] == 2000.0
    assert result["KOSPI"]["spike"] is result["KOSDAQ"]["spike"] is True


def test_margin_call_climax_requires_the_index_to_be_deep_in_drawdown() -> None:
    """반대매매 급증만으로는 부족하다 — 2026-05-11은 지수 **신고가**에서 +2σ가 켜졌다.

    사용자 조건은 "지수 급락 → 반대매매 클라이맥스"다. 급락은 그날 하락률이 아니라
    국면(고점 대비 낙폭)으로 읽는다 — 실제 클라이맥스 2026-07-31은 그날 +18.05%였다.
    """
    module = _load_market_signal_module()
    calm = [100.0, 120.0, 90.0, 110.0, 95.0] * 30
    credit = _credit_rows(calm + [2000.0])

    at_high = module.margin_call_climax(credit, _monitor_for(credit, [100.0] * len(credit)))["KOSPI"]
    assert at_high["climax"] is False
    assert at_high["drawdown"] == 0.0

    deep = module.margin_call_climax(credit, _deep_drawdown(credit))["KOSPI"]
    assert deep["climax"] is True
    assert deep["drawdown"] < module.MARGIN_CALL_MIN_DRAWDOWN_PCT


def test_margin_call_climax_needs_a_full_baseline() -> None:
    """창을 못 채우면 판정 불가다 — 짧은 표본의 σ로 레버리지 진입을 허가하지 않는다."""
    module = _load_market_signal_module()
    credit = _credit_rows([100.0] * 10)
    verdict = module.margin_call_climax(credit, _deep_drawdown(credit))["KOSPI"]

    assert verdict["climax"] is False and verdict["insufficient"] is True


def test_margin_call_climax_is_an_index_only_carve_out() -> None:
    """룰 원문이 지수 한정 예외임을 못 박아야 한다 — 섹터로 새면 사용자 판단 영역을 침범한다."""
    rules = _my_rules()
    rule2 = [ln for ln in rules.splitlines() if ln.startswith("2. **지수·섹터 레버리지")][0]

    assert "반대매매" in rule2 and "2σ" in rule2
    assert "지수 레버리지에 한해" in rule2
    assert "섹터" in rule2
    # 기존 보유 물량 처리는 사용자 판단 영역이다 — 룰이 대신 정하지 않는다.
    assert "기존 레버리지 정리는 그대로 이행" not in rule2

    script = (SKILLS_ROOT / "daily-digest" / "scripts" / "fetch_market_signals.py").read_text(encoding="utf-8")
    assert "margin_call_climax" in script


def test_leverage_rule_three_constants_match_the_documented_table() -> None:
    """임계값은 스킬의 운영 선택이다 — 상수만 바꾸고 근거 표가 남으면 다음 사람이 못 읽는다."""
    module = _load_market_signal_module()
    skill = (SKILLS_ROOT / "daily-digest" / "SKILL.md").read_text(encoding="utf-8")
    rules = _my_rules()

    # 룰 원문이 준 수치는 원문에서 다시 읽어 대조한다.
    assert f"±{module.VOLATILE_DAY_PCT:.0f}% 이상 변동" in rules
    assert f"최근 {module.VOLATILE_WINDOW_DAYS}거래일" in rules
    assert f"{module.VOLATILE_DAY_TRIGGER}일 이상" in rules

    table = skill.split("「레버리지 규칙 3」 판정도")[1][:1600]
    assert f"| `VOLATILE_DAY_PCT` | {module.VOLATILE_DAY_PCT}%" in table
    assert f"| `VOLATILE_WINDOW_DAYS` | {module.VOLATILE_WINDOW_DAYS} |" in table
    assert f"| `VOLATILE_DAY_TRIGGER` | {module.VOLATILE_DAY_TRIGGER} |" in table
    assert "전부 양수" in table
    assert "섹터" in table


def test_drawdown_ladder_constants_are_documented() -> None:
    """단 간격과 쿨다운은 운영 선택이다 — 근거가 남아야 다음 사람이 바꿀 수 있다."""
    module = _load_market_signal_module()
    skill = (SKILLS_ROOT / "daily-digest" / "SKILL.md").read_text(encoding="utf-8")

    assert "`DRAWDOWN_RUNGS`" in skill
    assert f"`RUNG_RECLAIM_DAYS` | {module.RUNG_RECLAIM_DAYS}" in skill
    # 되풀이 발동 실측과 T-1 근거가 함께 남아야 한다.
    assert "08-06" in skill and "T-1" in skill
    assert "오늘 처음 밟은 단" in skill


def test_margin_call_window_rationale_is_documented() -> None:
    """창 길이는 이 스킬의 운영 선택이다 — 왜 120인지가 남아야 다음 사람이 바꿀 수 있다."""
    module = _load_market_signal_module()
    skill = (SKILLS_ROOT / "daily-digest" / "SKILL.md").read_text(encoding="utf-8")

    assert f"`MARGIN_CALL_WINDOW_DAYS` | {module.MARGIN_CALL_WINDOW_DAYS}" in skill
    assert f"`MARGIN_CALL_SIGMA` | {module.MARGIN_CALL_SIGMA:g}" in skill
    assert f"{module.MARGIN_CALL_MIN_DRAWDOWN_PCT:g}%" in skill
    # σ를 올리는 대안이 왜 실패했는지가 남아야 다음 사람이 되풀이하지 않는다.
    assert "2.5σ" in skill and "+18.05%" in skill
    # 60일 창이 왜 안 되는지가 이 선택의 핵심이다.
    assert "자기 σ를 부풀려" in skill
    assert "당일을 기준선에서 제외" in skill


def test_long_bull_candle_is_wired_into_the_entry_rules() -> None:
    """「매매규칙 12」는 진입가를 구속한다 — 스크립트가 날짜와 종가를 주는데 스킬이 안 쓰면 소용없다."""
    analyze = (SKILLS_ROOT / "analyze-stock" / "SKILL.md").read_text(encoding="utf-8")
    stage = (SKILLS_ROOT / "stage-analysis" / "SKILL.md").read_text(encoding="utf-8")

    assert "장대 양봉" in stage and "LONG_BULL_PCT" in stage
    # analyze-stock 10단계의 매매규칙 12 항목이 스크립트 판정을 인용해야 한다.
    block = analyze.split("「매매규칙 12」")[1][:300]
    assert "stage_scan" in block or "장대 양봉 줄" in block


def _load_market_data_fetch_module():
    script_path = SKILLS_ROOT / "market-data" / "scripts" / "fetch.py"
    spec = importlib.util.spec_from_file_location("market_data_fetch", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_market_data_fetch_prints_a_quote(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_market_data_fetch_module()
    monkeypatch.setattr(
        module.tickers, "resolve_stock", lambda q: ({"stock_code": "005930"}, None, 0)
    )
    monkeypatch.setattr(datafeed_stockeasy, "load_cookie", lambda: "session=abc")
    monkeypatch.setattr(
        module.stockeasy,
        "fetch_stock_json",
        lambda *a, **k: ({"stock_info": {"cur_prc": "-70000", "flu_rt": "-1.5"}}, None),
    )

    assert module.main(["quote", "삼성전자"]) == 0
    out = capsys.readouterr().out
    assert "005930" in out and "70,000" in out


def test_market_data_fetch_reports_a_failure_without_raising(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """수집 실패는 비블로킹이라는 공통 원칙을 이 CLI도 따른다 — 사유를 stderr에 남긴다."""
    module = _load_market_data_fetch_module()
    monkeypatch.setattr(module.daily, "fetch_daily_bars", lambda code, days, asof=None: ([], "HTTP 500", None))
    monkeypatch.setattr(
        module.tickers, "resolve_stock", lambda q: ({"stock_code": "005930"}, None, 0)
    )

    assert module.main(["bars", "삼성전자"]) == 1
    assert "HTTP 500" in capsys.readouterr().err


def test_market_data_fetch_bars_names_a_naver_fallback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """직통 조회도 판정용 일봉과 같은 경로다 — 네이버 대체면 시간외가 가능성을 밝힌다."""
    from invagent.datafeed import daily

    module = _load_market_data_fetch_module()
    note = daily.fallback_note("STOCKEASY_COOKIE 미설정")
    monkeypatch.setattr(
        daily,
        "fetch_daily_bars",
        lambda code, days, asof=None: ([{"date": "20260928", "close": 121700.0, "volume": 1.0}], None, note),
    )
    monkeypatch.setattr(
        module.tickers, "resolve_stock", lambda q: ({"stock_code": "353200"}, None, 0)
    )

    assert module.main(["bars", "대덕전자"]) == 0
    assert note in capsys.readouterr().out


def test_market_data_fetch_passes_an_ambiguous_name_back(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_market_data_fetch_module()
    monkeypatch.setattr(
        module.tickers, "resolve_stock", lambda q: (None, "종목명 후보 다수 — 가나(000001)", 2)
    )

    assert module.main(["quote", "가나"]) == 2
    assert "후보 다수" in capsys.readouterr().err


def test_every_skill_script_is_named_by_a_skill_document() -> None:
    """호출처 없는 스크립트는 있는지도 모른 채 낡는다.

    스크립트를 두는 것과 언제 쓰는지 적는 것은 한 쌍이다. SKILL.md 어디에도 이름이 없으면
    모델은 그것을 영영 부르지 않는다.
    """
    documents = "\n".join(
        path.read_text(encoding="utf-8") for path in SKILLS_ROOT.glob("*/SKILL.md")
    )
    undocumented = sorted(
        str(path.relative_to(SKILLS_ROOT))
        for path in SKILLS_ROOT.glob("*/scripts/*.py")
        if path.name not in documents
    )

    assert not undocumented, f"어느 SKILL.md에도 없는 스크립트: {undocumented}"


def test_collection_mechanics_are_documented_in_one_skill() -> None:
    """수집 방법은 `market-data`가 정본이다.

    쿠키 갱신 절차가 여러 SKILL.md에 복제돼 있으면 하나만 고쳐지고 나머지는 낡는다.
    각 스킬은 절차를 다시 적지 않고 참조한다.
    """
    reference = (SKILLS_ROOT / "market-data" / "SKILL.md").read_text(encoding="utf-8")
    # 정본에는 실제 절차가 있어야 한다.
    assert "DevTools" in reference and "STOCKEASY_COOKIE" in reference
    assert "INVAGENT_HTTP_CACHE" in reference

    for name in ("analyze-stock", "stage-analysis", "daily-digest", "advice"):
        skill = (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")
        assert "market-data" in skill, f"{name}이 수집 정본을 참조하지 않는다"
        assert "DevTools" not in skill, f"{name}이 쿠키 갱신 절차를 복제하고 있다"


def test_ticker_overrides_are_consulted_from_one_place() -> None:
    """`context/ticker_overrides.md`는 자신이 API보다 먼저 읽힌다고 규정한다.

    예전에는 `peak_drawdown`만 그 파일을 보고 `stage_scan`은 보지 않아, 같은 종목명이
    스킬마다 다른 티커로 풀렸다. 두 경로 모두 수집 계층의 해석기를 지나야 한다.
    """
    overrides = (REPO_ROOT / "context" / "ticker_overrides.md").read_text(encoding="utf-8")
    assert "API보다 이 파일을 먼저 본다" in overrides

    stage = (SKILLS_ROOT / "stage-analysis" / "scripts" / "stage_scan.py").read_text(
        encoding="utf-8"
    )
    peak = (SKILLS_ROOT / "daily-digest" / "scripts" / "peak_drawdown.py").read_text(
        encoding="utf-8"
    )

    assert "tickers.resolve_stock(" in stage
    assert "stockeasy.resolve_stock(" not in stage
    assert "from invagent.datafeed.tickers import" in peak


def test_every_mechanically_checkable_rule_has_a_script() -> None:
    """룰이 수치로 규정한 조건은 모델이 눈대중하지 않는다 — 스크립트가 확정한다."""
    checks = {
        "레버리지 규칙 3": ("daily-digest/scripts/fetch_market_signals.py", "leverage_liquidation"),
        "기술적 분석 규칙 1": ("stage-analysis/scripts/stage_scan.py", "MA_20WEEK"),
        # '바닥을 다진 후 고개를 들기 시작하는 초반' — 바닥 저점이 버틴 거래일 수로 확정한다.
        "매매규칙 2": ("stage-analysis/scripts/turn_scan.py", "BASE_MIN_AGE"),
        "기본 원칙 13": ("daily-digest/scripts/peak_drawdown.py", "ACCOUNT_MDD_BANDS"),
        "매매규칙 6": ("daily-digest/scripts/extract_portfolio.py", "STOP_FULL_PCT"),
    }
    for rule, (path, symbol) in checks.items():
        text = (SKILLS_ROOT / path).read_text(encoding="utf-8")
        assert symbol in text, f"{rule}을 계산하는 주체가 없다 ({path})"


def test_leverage_and_short_term_rules_are_wired_into_advice() -> None:
    """인용된 적 없는 룰은 조언에 절대 나타나지 않는다 — 있으나 마나 한 룰이 된다."""
    advice = (SKILLS_ROOT / "advice" / "SKILL.md").read_text(encoding="utf-8")

    # 「레버리지 규칙 1」 개별 종목 레버리지 금지 — 2·3만 인용되고 1만 빠져 있었다.
    assert "레버리지 규칙 1" in advice
    # 「기본 원칙 11」 단기 투자 금지 — 이벤트 매매(「매매규칙 14」)와 같은 자리에 온다.
    assert "기본 원칙 11" in advice

    briefing = (SKILLS_ROOT / "daily-digest" / "SKILL.md").read_text(encoding="utf-8")
    # 브리핑의 레버리지 리마인드는 스크립트 판정을 인용한다.
    assert "레버리지 규칙 3" in briefing
    assert "fetch_market_signals" in briefing


def test_rule_check_blocks_convert_every_line_my_rules_asks_for() -> None:
    """`my_rules.md`「적용 방법」은 -10/-15/-20/24/30 선을 전부 가격으로 환산하라고 요구한다.

    advice 룰 체크 블록과 보고서 템플릿이 그 목록을 빠짐없이 담아야 한다.
    """
    rules = _my_rules()
    assert "-10%/-15%/-20%/24%/30% 선을 구체적 가격으로 환산" in rules

    advice = (SKILLS_ROOT / "advice" / "SKILL.md").read_text(encoding="utf-8")
    block = advice.split("🛡️ 룰 체크")[1].split("---")[0]
    assert "「매매규칙 7」 -10%" in block
    assert "「매매규칙 6」 -15%" in block
    assert "「매매규칙 6」 -20%" in block
    assert "매수원금 비중 상한 = 2% ÷ 실효 손절폭" in block
    assert "평가 비중 상한 35%" in block
    assert "-8%" not in block

    template = (REPO_ROOT / "template" / "stock_analysis.md").read_text(encoding="utf-8")
    assert "실효 손절폭 max(하방, 20%)" in template
    assert "매수원금 비중 상한" in template
    assert "min(2%÷" not in template


def test_advice_resolves_rule_versus_rule_conflicts() -> None:
    """급락장에선 「매매규칙 6」 손절 예외(팔지 마라)와 「기본 원칙 13」(줄여라)이 동시에 켜진다.

    advice가 대가↔룰 충돌만 정리하고 룰↔룰 충돌을 비워 두면 매번 재논쟁이 난다.
    """
    advice = (SKILLS_ROOT / "advice" / "SKILL.md").read_text(encoding="utf-8")
    table = advice.split("대가 기준 vs 사용자 룰 충돌 처리")[1].split("## 중요한 원칙")[0]

    assert "룰 ↔ 룰 충돌" in table
    assert "기본 원칙 13" in table and "매매규칙 6" in table
    # 계좌 레벨 룰이 종목 레벨 예외보다 위다.
    assert "계좌 레벨" in table


def test_no_file_presents_minus_fifteen_as_the_whole_of_rule_six() -> None:
    """「매매규칙 6」을 인용하면서 -15%만 적으면 -20% 전량 매도 티어가 통째로 사라진다."""
    rules = _my_rules()
    assert "-15%, -20% 손절" in rules, "룰 6의 제목이 두 티어를 담고 있어야 한다"

    bad = []
    for path in sorted(SKILLS_ROOT.rglob("*")) + sorted((REPO_ROOT / "template").rglob("*.md")):
        if path.suffix not in {".md", ".py"} or "__pycache__" in path.parts:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "매매규칙 6" not in line:
                continue
            # 티어를 명시한 줄(-15% = 1차 분할)과 축을 구분하는 줄은 정상이다.
            allowed = ("-20%", "1차 분할", "평단", "고점")
            if "-15%" in line and not any(tok in line for tok in allowed):
                bad.append(f"{path.relative_to(REPO_ROOT)}:{i}")
    assert not bad, "「매매규칙 6」을 -15% 단독으로 인용한 곳: " + ", ".join(bad)


def test_master_versus_rule_table_sizes_off_the_terminal_stop() -> None:
    """손절폭 2배를 사이징으로 상쇄한다는 논리는 분모가 최종 이탈선(-20%)일 때만 성립한다."""
    advice = (SKILLS_ROOT / "advice" / "SKILL.md").read_text(encoding="utf-8")
    row = [ln for ln in advice.splitlines() if ln.startswith("| 손절폭 |")]
    assert row, "대가↔룰 표의 손절폭 행을 찾지 못했다"
    text = row[0]

    assert "-20%" in text, "1차 분할만 적고 최종 이탈선을 빼면 리스크 총량이 과소평가된다"
    assert "10%" in text
    assert "13.3%" not in text


def test_no_skill_still_cites_the_retired_minus_eight_percent_line() -> None:
    """「매매규칙 7」은 2026-09-02에 -10%가 됐다. -8%를 인용하는 곳이 남으면 안 된다."""
    targets = [
        SKILLS_ROOT / "advice" / "SKILL.md",
        SKILLS_ROOT / "advice" / "references" / "trend_following.md",
        SKILLS_ROOT / "daily-digest" / "SKILL.md",
        SKILLS_ROOT / "daily-digest" / "scripts" / "extract_portfolio.py",
        REPO_ROOT / "template" / "stock_analysis.md",
        REPO_ROOT / "template" / "daily_digest.md",
    ]
    for path in targets:
        text = path.read_text(encoding="utf-8")
        assert "매매규칙 7" not in text or "-8%" not in text, f"{path.name}에 -8% 인용이 남아 있다"

    # 브리핑 룰 리마인드도 두 손절 티어를 모두 안내해야 한다.
    briefing = (SKILLS_ROOT / "daily-digest" / "SKILL.md").read_text(encoding="utf-8")
    assert "매매규칙 7 (-10%" in briefing
    assert "-20% 전량" in briefing


def test_split_sell_rule_does_not_contradict_the_stop_tiers() -> None:
    """「매매규칙 5」의 즉시 전량 조건에 -15% 손절이 들어가면 「매매규칙 6」의 1차 분할과 정반대가 된다.

    2026-09-02에 규칙 6이 -15% 1차 분할 / -20% 전량 두 티어로 바뀌었으므로 규칙 5의
    즉시 전량 사유는 아이디어 훼손과 「레버리지 규칙 3」 둘만 남는다.
    """
    rules = _my_rules()
    rule5 = re.search(r"^5\. \*\*분할 매도\*\*.*$", rules, re.MULTILINE)
    assert rule5, "「매매규칙 5」를 찾지 못했다"
    text = rule5.group(0)

    immediate = text.split("즉시 전량 정리")[0]
    assert "매매규칙 6" not in immediate, "즉시 전량 조건이 여전히 「매매규칙 6」에 걸려 있다"
    assert "투자 아이디어가 깨졌거나" in immediate
    assert "레버리지 규칙 3(변동성 레버리지 청산)" in immediate
    # 손절 티어의 소유권은 규칙 6에 있다고 명시해 둔다.
    assert "「매매규칙 6」의 -15%/-20% 손절은 그 규칙이 정한 티어" in text


def test_portfolio_thresholds_match_my_rules() -> None:
    """임계값 정본은 `context/my_rules.md`다. 룰 숫자가 바뀌면 이 테스트가 먼저 깨진다.

    2026-09-02 「매매규칙 6·7」이 바뀌었는데 스크립트가 옛 -8%·단일 -15%에 머문 적이 있다.
    상수를 문자열로 못 박지 않고 룰 원문에서 파싱해 대조한다.
    """
    module = _load_portfolio_module()
    rules = _my_rules()

    trim = re.search(r"^7\. \*\*(-\d+)% 비중 축소\*\*", rules, re.MULTILINE)
    stop = re.search(r"^6\. \*\*(-\d+)%, (-\d+)% 손절\*\*", rules, re.MULTILINE)
    cushion = re.search(r"수익이 (\d+)-(\d+)% 정도 발생하면", rules)
    weight = re.search(r"최대 (\d+)%를 넘지 않도록", rules)
    holdings = re.search(r"종목 수는 (\d+)~(\d+)종목", rules)
    assert trim and stop and cushion and weight and holdings, "룰 원문 형식이 바뀌었다"

    assert module.TRIM_PCT == float(trim.group(1))
    assert module.STOP_LOSS_PCT == float(stop.group(1))
    assert module.STOP_FULL_PCT == float(stop.group(2))
    assert module.PROFIT_CUSHION_PCT == float(cushion.group(1))
    assert module.MAX_WEIGHT_PCT == float(weight.group(1))
    assert (module.MIN_HOLDINGS, module.MAX_HOLDINGS) == (
        int(holdings.group(1)),
        int(holdings.group(2)),
    )


def _load_find_reports_module():
    script_path = SKILLS_ROOT / "analyze-stock" / "scripts" / "find_reports.py"
    spec = importlib.util.spec_from_file_location("find_reports", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("stock", "expected"),
    [
        ("율촌화학", "ㅇ"),
        ("삼성전자", "ㅅ"),
        ("까뮤이앤씨", "ㄱ"),  # 쌍자음은 평자음 폴더로 접는다
        ("쌍용C&E", "ㅅ"),
        ("SK하이닉스", "A-Z"),
        ("3S", "A-Z"),
    ],
)
def test_find_reports_routes_by_chosung(stock: str, expected: str) -> None:
    module = _load_find_reports_module()

    assert module.chosung_dir(stock) == expected


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("20260327_[율촌화학] 배터리 포장.pdf", "2026-03-27"),
        ("202602_이수페타시스 26년 경영계획.pdf", "2026-02"),
        ("2020_반도체_후공정패키징_.pdf", "2020"),
        ("20261332_잘못된_월.pdf", None),
        ("24.9.12 iM증권 제약 CDMO.pdf", None),
    ],
)
def test_find_reports_parses_leading_date(filename: str, expected: str | None) -> None:
    module = _load_find_reports_module()

    assert module.parse_date(filename) == expected


def _build_archive(root: Path) -> None:
    stock = root / "ㅇ" / "율촌화학"
    (stock / "IR자료").mkdir(parents=True)
    for name in (
        "20260327_[율촌화학] 최신.pdf",
        "20240502_율촌화학_구형.pdf",
        "율촌화학_날짜없음.pdf",
    ):
        (stock / name).touch()
    (stock / "IR자료" / "202505_율촌화학 IR.pdf").touch()
    (root / "ㅅ" / "삼성전자").mkdir(parents=True)
    sector = root / "_산업분석" / "화학"
    sector.mkdir(parents=True)
    (sector / "20260101_포장재_율촌화학_비교.pdf").touch()


def test_find_reports_collects_sorted_with_aux_dirs(tmp_path: Path) -> None:
    _build_archive(tmp_path)
    module = _load_find_reports_module()

    items, dirs, kind = module.collect(tmp_path, "율촌화학")

    assert kind == "exact"
    assert [d.name for d in dirs] == ["율촌화학"]
    # IR자료 하위 + _산업분석 파일명 매칭까지 포함해 최신순
    assert [parsed for parsed, _ in items] == [
        "2026-03-27",
        "2026-01-01",
        "2025-05",
        "2024-05-02",
        None,
    ]
    assert items[2][1].parent.name == "IR자료"
    assert items[1][1].parent.parent.name == "_산업분석"


def test_find_reports_limit_and_missing_stock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_archive(tmp_path)
    module = _load_find_reports_module()
    monkeypatch.setenv("INVAGENT_REPORT_ARCHIVE", str(tmp_path))

    assert module.main(["율촌화학", "--limit", "2"]) == 0
    out = capsys.readouterr().out
    assert "선정 2건" in out
    assert "20260327_[율촌화학] 최신.pdf" in out
    assert "20240502_율촌화학_구형.pdf" not in out
    assert "미선정 3건" in out

    # 폴더가 없어도 블로킹하지 않는다
    assert module.main(["없는종목명"]) == 0
    assert "검색 결과 없음" in capsys.readouterr().out


def test_find_reports_archive_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """저장소·스킬 파일에 절대경로를 박지 않기 위한 환경변수 오버라이드."""
    module = _load_find_reports_module()
    monkeypatch.setenv("INVAGENT_REPORT_ARCHIVE", str(tmp_path))

    assert module.archive_root() == tmp_path


def _load_find_mentions_module():
    script_path = SKILLS_ROOT / "analyze-stock" / "scripts" / "find_mentions.py"
    spec = importlib.util.spec_from_file_location("find_mentions", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass가 `from __future__ import annotations` 하에서 타입을 해석하려면
    # 모듈이 sys.modules에 등록돼 있어야 한다.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


INDEX_MD = """# 월간 누적 컨텍스트 (인덱스)

## 반복 등장 신호

- **⭐ 알파전자 = 증설 사이클 진입 (8/9)** → [[themes/알파전자-증설]] · 캐파 2배 · 최근(2026-08-13): 가동률 92%
- **⛔ 베타파마 = 임상 실패, 회피** · 후속 신호 없음
- **감마엔터** · 신작 대기 · 최근(08-01): 예약 판매 호조
"""

THEME_MD = """# 알파전자 증설

- **알파전자 증설** (2026-06-01~): 캐파 2배 증설
  - 2026-06-20: 착공. 델타중공업이 EPC 수주.
  - 2026-08-13: **⭐ 가동률 92% 확인** — 알파전자 증설분 조기 램프업.
  - 2026-07-05: 장비 발주 완료. 알파전자 공시.
"""

DAILY_MD = """# 텔레그램 데일리 브리핑 — 2026-08-13

## 📂 섹터별 / 종목별 정리

### 반도체

- **알파전자 (123456), 2Q OP +48% 서프라이즈**
  - 매출 1.2조, OPM 18.4%
  - 투자 시사점: 증설 효과가 실적으로 확인됨
- **엡실론소재**, 원재료 단가 하락 수혜
  - 알파전자 납품 비중 40%
  - 투자 시사점: 전방 확인 필요

## 💡 오늘의 투자 조언

- 알전은 베이스 돌파 대기.
"""

BROKEN_MD = """알파전자 관련 메모지만 불릿 구조가 없다.
두 번째 줄.
"""


def _build_telegram_archive(root: Path) -> Path:
    archive = root / "daily-digest"
    (archive / "themes" / "archive").mkdir(parents=True)
    (archive / "2026-08").mkdir(parents=True)

    (archive / "monthly_context.md").write_text(INDEX_MD, encoding="utf-8")
    (archive / "monthly_context.md.bak").write_text(INDEX_MD, encoding="utf-8")
    (archive / "themes" / "알파전자-증설.md").write_text(THEME_MD, encoding="utf-8")
    (archive / "themes" / "archive" / "pruned-2026-08-09-정리전-인덱스-전체.md").write_text(
        THEME_MD, encoding="utf-8"
    )
    (archive / "2026-08" / "2026-08-13.md").write_text(DAILY_MD, encoding="utf-8")
    (archive / "2026-08" / "backup.md").write_text(DAILY_MD, encoding="utf-8")
    return archive


def test_find_mentions_parses_records_per_source(tmp_path: Path) -> None:
    module = _load_find_mentions_module()
    archive = _build_telegram_archive(tmp_path)

    result = module.collect(archive, ["알파전자"])

    # 인덱스 항목은 현재 유효 판정 — 마커와 테마 포인터를 열로 보존한다
    assert len(result.index_hits) == 1
    assert result.index_hits[0].marker == "⭐"
    assert result.index_hits[0].pointer == "알파전자-증설"

    # 테마 서브불릿은 파일 내 순서가 어긋나도 최신순으로 정렬된다
    assert [r.date for r in result.theme_hits] == ["2026-08-13", "2026-07-05", "2026-06-01"]

    # 일일 브리핑: 헤드 매치가 본문 매치보다 먼저 온다
    daily = result.daily_hits
    assert daily[0].match == "head"
    assert "알파전자" in daily[0].head
    assert daily[0].label == "반도체"
    assert [r.match for r in daily] == ["head", "body"]


def test_find_mentions_groups_indented_block_into_head_record(tmp_path: Path) -> None:
    """`- **종목**` 헤드에 딸린 들여쓴 하위 불릿은 같은 레코드의 본문이다."""
    module = _load_find_mentions_module()
    archive = _build_telegram_archive(tmp_path)

    result = module.collect(archive, ["엡실론소재"])
    record = result.daily_hits[0]

    assert record.match == "head"
    assert "알파전자 납품 비중 40%" in record.body
    assert "투자 시사점" in record.body


def test_find_mentions_excludes_duplicate_sources(tmp_path: Path) -> None:
    module = _load_find_mentions_module()
    archive = _build_telegram_archive(tmp_path)

    paths = {hit.path.name for hit in module.collect(archive, ["알파전자"]).all_hits}

    assert "monthly_context.md.bak" not in paths
    assert "backup.md" not in paths
    assert "pruned-2026-08-09-정리전-인덱스-전체.md" not in paths


def test_find_mentions_alias_expands_search(tmp_path: Path) -> None:
    module = _load_find_mentions_module()
    archive = _build_telegram_archive(tmp_path)

    plain = module.collect(archive, ["알파전자"]).daily_hits
    expanded = module.collect(archive, ["알파전자", "알전"]).daily_hits

    assert len(expanded) == len(plain) + 1
    assert any("알전은 베이스 돌파" in hit.head for hit in expanded)


def test_find_mentions_falls_back_to_line_scan(tmp_path: Path) -> None:
    """레코드 구조가 없는 파일도 놓치지 않는다."""
    module = _load_find_mentions_module()
    archive = _build_telegram_archive(tmp_path)
    (archive / "themes" / "깨진테마.md").write_text(BROKEN_MD, encoding="utf-8")

    hits = [h for h in module.collect(archive, ["알파전자"]).theme_hits if h.path.name == "깨진테마.md"]

    assert len(hits) == 1
    assert hits[0].line == 1


def test_find_mentions_output_is_locator_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_find_mentions_module()
    _build_telegram_archive(tmp_path)
    monkeypatch.setenv("INVAGENT_OUTPUT_DIR", str(tmp_path))

    assert module.main(["알파전자", "--top", "1"]) == 0
    out = capsys.readouterr().out

    assert "## 인덱스 현재 판정" in out
    assert "themes/알파전자-증설.md:5" in out  # 경로:줄번호 로케이터
    assert "미표시 2건" in out  # 테마 3건 중 1건만 표시 → 나머지는 월별 집계로
    assert "2026-07 1건" in out and "2026-06 1건" in out
    # 본문(들여쓴 하위 불릿)은 출력하지 않는다
    assert "OPM 18.4%" not in out

    assert module.main(["없는종목명", "--count-only"]) == 0
    assert "아카이브 언급 없음" in capsys.readouterr().out


def _load_find_prior_report_module():
    script_path = SKILLS_ROOT / "analyze-stock" / "scripts" / "find_prior_report.py"
    spec = importlib.util.spec_from_file_location("find_prior_report", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PRIOR_REPORT_FILES = (
    "종목/ㅇ/율촌화학_2026-08-14.md",
    "종목/ㅇ/율촌화학_2026-07-02.md",
    "종목/ㄱ/기가비스_2026-08-11.md",
    "산업/2026-08-11_기판검사장비_기가비스_인텍플러스_비교.md",
    "산업/2026-08-09_반도체부품주_섹터리포트_분석.md",
    "기타/2026-08_투자전략.md",
)


def _build_reports_dir(root: Path) -> Path:
    reports = root / "reports"
    reports.mkdir(parents=True)
    for name in PRIOR_REPORT_FILES:
        path = reports / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {name}\n", encoding="utf-8")
    # 루트에 날짜 디렉터리로 PDF가 쌓인다. 하위는 스캔 대상이 아니다.
    (reports / "2026-08-14").mkdir()
    (reports / "2026-08-14" / "2026-08-14_율촌화학_함정.md").write_text("x", encoding="utf-8")
    return reports


def test_find_prior_report_inherits_latest_own_report(tmp_path: Path) -> None:
    module = _load_find_prior_report_module()
    reports = _build_reports_dir(tmp_path)

    result = module.lookup(reports, "율촌화학", "2026-09-01")

    assert result.inherit is not None
    assert result.inherit.path.name == "율촌화학_2026-08-14.md"
    assert [r.path.name for r in result.previous] == ["율촌화학_2026-07-02.md"]
    assert result.since == "2026-08-14"
    assert result.target == reports / "종목" / "ㅇ" / "율촌화학_2026-09-01.md"
    assert result.same_path is False


def test_find_prior_report_treats_comparison_report_as_related(tmp_path: Path) -> None:
    """산업·기타 보고서는 다른 종목 내용도 담겼다 — 참고만 하고 원본을 유지한다."""
    module = _load_find_prior_report_module()
    reports = _build_reports_dir(tmp_path)

    result = module.lookup(reports, "기가비스", "2026-09-01")

    assert result.inherit is not None
    assert result.inherit.path.name == "기가비스_2026-08-11.md"
    assert [r.path.name for r in result.related] == [
        "2026-08-11_기판검사장비_기가비스_인텍플러스_비교.md"
    ]


@pytest.mark.parametrize(
    ("stock", "expected_dir"),
    [
        ("율촌화학", "ㅇ"),
        ("덕산네오룩스", "ㄷ"),
        ("까뮤이앤씨", "ㄱ"),  # 쌍자음은 평자음 폴더로 접는다
        ("OCI홀딩스", "A-Z"),
    ],
)
def test_find_prior_report_target_routes_by_chosung(
    tmp_path: Path, stock: str, expected_dir: str
) -> None:
    module = _load_find_prior_report_module()
    reports = _build_reports_dir(tmp_path)

    target = module.lookup(reports, stock, "2026-09-01").target

    assert target == reports / "종목" / expected_dir / f"{stock}_2026-09-01.md"


def test_find_prior_report_reads_new_filename_convention(tmp_path: Path) -> None:
    """`<종목명>_<날짜>.md`로 바뀐 뒤에도 기존 보고서를 승계 대상으로 잡아야 한다."""
    module = _load_find_prior_report_module()
    reports = _build_reports_dir(tmp_path)
    (reports / "종목" / "ㅇ").mkdir(parents=True, exist_ok=True)
    (reports / "종목" / "ㅇ" / "와이지-원_2026-08-20.md").write_text("# x\n", encoding="utf-8")

    result = module.lookup(reports, "와이지-원", "2026-09-01")

    assert result.inherit is not None
    assert result.inherit.path.name == "와이지-원_2026-08-20.md"
    assert result.inherit.date == "2026-08-20"
    assert result.since == "2026-08-20"


def test_find_prior_report_ignores_undated_and_pdf_date_dirs(tmp_path: Path) -> None:
    module = _load_find_prior_report_module()
    reports = _build_reports_dir(tmp_path)

    scanned = {report.path.name for report in module.scan(reports)}

    assert "2026-08_투자전략.md" not in scanned  # 일자 없음
    assert "2026-08-14_율촌화학_함정.md" not in scanned  # PDF 날짜 디렉터리


def test_find_prior_report_flags_same_path_rerun(tmp_path: Path) -> None:
    module = _load_find_prior_report_module()
    reports = _build_reports_dir(tmp_path)
    rerun = reports / "종목" / "ㅇ" / "에이피알_2026-09-01.md"
    rerun.parent.mkdir(parents=True, exist_ok=True)
    rerun.write_text("# x\n", encoding="utf-8")

    result = module.lookup(reports, "에이피알", "2026-09-01")

    assert result.same_path is True


def test_find_prior_report_main_reports_new_and_inherited(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_find_prior_report_module()
    _build_reports_dir(tmp_path)
    monkeypatch.setenv("INVAGENT_OUTPUT_DIR", str(tmp_path))

    assert module.main(["율촌화학", "--today", "2026-09-01"]) == 0
    out = capsys.readouterr().out
    assert "종목/ㅇ/율촌화학_2026-08-14.md" in out
    assert "reports/종목/ㅇ/율촌화학_2026-09-01.md" in out
    assert "증분 기준일(`--since`): 2026-08-14" in out
    assert "과거 중복 후보 1건" in out
    assert "승계 파일명을 목표 경로로 먼저 변경" in out
    assert "복사본을 만들지 마라" in out
    # 옛 보고서는 `## 12. 개정 이력`, 현 템플릿은 `## 11. 개정 이력` — 번호가 아니라 절 이름으로 찾는다.
    assert "승계본의 `개정 이력` 절을 반드시 읽어라" in out
    assert "템플릿 섹션 순서로 재배치" in out
    assert "§11에 한 줄로 옮긴 뒤 본문에서 뺀다" in out
    assert "`## 11. 개정 이력`에 `- YYYY-MM-DD: 구분 — 요약` 한 줄" in out
    assert "## 12." not in out.replace("옛 배치는 `## 12.`", "")

    assert module.main(["카카오", "--today", "2026-09-01"]) == 0
    new_out = capsys.readouterr().out
    assert "승계 대상: 없음 (신규 작성)" in new_out
    assert "reports/종목/ㅋ/카카오_2026-09-01.md" in new_out


def test_find_mentions_since_filter(tmp_path: Path) -> None:
    module = _load_find_mentions_module()
    archive = _build_telegram_archive(tmp_path)

    result = module.collect(archive, ["알파전자"], since="2026-08-01")

    assert all(hit.date >= "2026-08-01" for hit in result.all_hits)
    assert result.theme_hits and len(result.theme_hits) == 1


# --- peak_drawdown.py (전고점 낙폭 · 계좌 MDD) --------------------------------

PEAK_SNAPSHOT = """# 포트폴리오 스냅샷 — 2026-08-28

> 출처: Google Sheets '주식 포트폴리오' / 「포트폴리오」 시트 · 수집 2026-08-28T21:03:43
> 잔고 ₩90,000,000 · 총손익 ₩10,000,000 · 투자금 ₩80,000,000 · 수익률 12.50%

## 보유 (2종목 + 현금)

| 종목 | 섹터 | 보유 | 평단 | 현재가 | 수익률 | 비중 | 평가금액 | 투자 아이디어 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| _현금 | 현금 | 1 | ₩9,000,000 | ₩9,000,000 | 0.00% | 10.0% | ₩9,000,000 | 현금도 종목이다 |
| 알파전자 | 반도체 | 100 | ₩50,000 | ₩70,000 | 40.00% | 40.0% | ₩36,000,000 | 슈퍼사이클 |
| 베타파마 | 바이오 | 200 | ₩60,000 | ₩50,000 | -16.67% | 50.0% | ₩45,000,000 | 임상 |

## 룰 자동 판정

- 매매규칙 6(-15% 손절): 베타파마 -16.67% (비중 50.0%) ← **위반**
"""


def _load_peak_drawdown_module():
    script_path = SKILLS_ROOT / "daily-digest" / "scripts" / "peak_drawdown.py"
    spec = importlib.util.spec_from_file_location("peak_drawdown", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bars(closes: list[tuple[str, float]]) -> list[dict]:
    return [
        {"date": d, "open": c, "high": c, "low": c, "close": c, "volume": 1.0}
        for d, c in closes
    ]


@pytest.mark.parametrize(
    ("drawdown", "expected"),
    [
        (0.0, None),
        (-9.9, None),
        (-10.0, -10.0),
        (-14.9, -10.0),
        (-15.0, -15.0),
        (-20.0, -20.0),
        (-29.9, -20.0),
        (-30.5, -30.0),
        (None, None),
    ],
)
def test_peak_drawdown_bands_take_the_deepest_hit(drawdown, expected) -> None:
    module = _load_peak_drawdown_module()

    assert module.band_for(drawdown) == expected


def test_peak_drawdown_band_survives_float_boundary() -> None:
    """90,000/100,000-1 = -9.999999999999998 — 표에 -10.0%로 찍히면 밴드도 걸려야 한다."""
    module = _load_peak_drawdown_module()
    result = module.peak_drawdown(
        _bars([("20260601", 100_000.0), ("20260828", 90_000.0)])
    )

    assert f"{result['drawdown']:+.1f}%" == "-10.0%"
    assert module.band_for(result["drawdown"]) == -10.0


def test_peak_drawdown_parses_holdings_and_drops_cash() -> None:
    module = _load_peak_drawdown_module()

    holdings = module.parse_holdings(PEAK_SNAPSHOT)

    assert [h["종목"] for h in holdings] == ["알파전자", "베타파마"]
    assert holdings[0]["수익률"] == "40.00%" and holdings[0]["비중"] == "40.0%"
    assert module.parse_balance(PEAK_SNAPSHOT) == 90_000_000.0


def test_peak_is_highest_close_in_window_with_its_date() -> None:
    module = _load_peak_drawdown_module()
    # 창(250거래일) 밖의 더 높은 종가는 전고점으로 잡히지 않는다
    old = [(f"2024{i:04d}", 999_000.0) for i in range(1, 3)]
    window = [("20260101", 80_000.0), ("20260615", 100_000.0), ("20260828", 70_000.0)]

    result = module.peak_drawdown(_bars(old + window * 100)[-module.PEAK_WINDOW_DAYS:])

    assert result["peak"] == 100_000.0
    assert result["peak_date"] == "20260615"
    assert result["close"] == 70_000.0
    assert result["drawdown"] == pytest.approx(-30.0)


def test_peak_drawdown_unresolved_ticker_does_not_block_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_peak_drawdown_module()
    monkeypatch.setattr(
        datafeed_stockeasy,
        "resolve_stock",
        lambda name: (
            ({"stock_code": "000660"}, None, 0)
            if name == "알파전자"
            else (None, "종목 검색 결과 없음", 1)
        ),
    )
    monkeypatch.setattr(
        module.daily,
        "fetch_daily_bars",
        lambda code, days, asof=None: (_bars([("20260601", 100_000.0), ("20260828", 60_000.0)]), None, None),
    )

    results = module.analyze_holdings(module.parse_holdings(PEAK_SNAPSHOT), {})

    assert results[0]["band"] == -30.0 and results[0]["drawdown"] == pytest.approx(-40.0)
    assert "티커 미해석" in results[1]["error"]
    section = module.render(results, {"error": "잔고 이력 없음"})
    assert "베타파마(티커 미해석" in section
    assert "ticker_overrides.md" in section


def test_peak_drawdown_flags_holdings_measured_on_naver_fallback_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """52주 시장 축은 정규장 종가(StockEasy)로 잰다. 네이버로 대체된 종목은 섹션이 이름째 밝힌다."""
    from invagent.datafeed import daily

    module = _load_peak_drawdown_module()
    monkeypatch.setattr(
        datafeed_stockeasy, "resolve_stock", lambda name: ({"stock_code": "000660"}, None, 0)
    )
    note = daily.fallback_note("STOCKEASY_COOKIE 미설정")
    monkeypatch.setattr(
        daily,
        "fetch_daily_bars",
        lambda code, days, asof=None: (
            _bars([("20260601", 100_000.0), ("20260828", 60_000.0)]), None, note
        ),
    )

    results = module.analyze_holdings(module.parse_holdings(PEAK_SNAPSHOT), {})
    section = module.render(results, {"error": "잔고 이력 없음"})

    assert results[0]["drawdown"] == pytest.approx(-40.0)
    flagged = [line for line in section.splitlines() if note in line]
    assert flagged and "알파전자" in flagged[0]


def test_peak_drawdown_overrides_win_over_api(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_peak_drawdown_module()
    monkeypatch.setattr(
        datafeed_stockeasy, "resolve_stock", lambda name: ({"stock_code": "999999"}, None, 0)
    )

    assert module.resolve_code("알파전자", {"알파전자": "000660"}) == ("000660", None)
    assert module.resolve_code("베타파마", {}) == ("999999", None)


def test_peak_drawdown_overrides_file_ignores_comments(tmp_path: Path) -> None:
    module = _load_peak_drawdown_module()
    path = tmp_path / "ticker_overrides.md"
    path.write_text(
        "# 티커 오버라이드\n> 설명 줄\n- 불릿 줄\n\n알파전자 = 000660   # 표기 오타\n베타파마 = bad\n",
        encoding="utf-8",
    )

    assert module.load_overrides(path) == {"알파전자": "000660"}


def test_peak_drawdown_fetch_failure_is_non_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_peak_drawdown_module()
    monkeypatch.setattr(
        datafeed_stockeasy, "resolve_stock", lambda name: ({"stock_code": "000660"}, None, 0)
    )
    monkeypatch.setattr(module.daily, "fetch_daily_bars", lambda code, days, asof=None: ([], "HTTP 500", None))

    results = module.analyze_holdings(module.parse_holdings(PEAK_SNAPSHOT), {})

    assert all("시세 수집 실패 — HTTP 500" in r["error"] for r in results)
    section = module.render(results, {"error": "잔고 이력 없음"})
    assert "전고점 낙폭 판정(52주 시장 종가 기준): 해당 없음" in section


def test_account_mdd_uses_snapshot_balance_peak(tmp_path: Path) -> None:
    module = _load_peak_drawdown_module()
    for day, balance in (("2026-08-20", 100_000_000), ("2026-08-25", 95_000_000)):
        (tmp_path / f"{day}.md").write_text(f"> 잔고 ₩{balance:,} · 수익률 1.00%\n", encoding="utf-8")

    account = module.account_mdd(tmp_path, 88_000_000.0, "2026-08-28")

    assert account["peak"] == 100_000_000.0 and account["peak_date"] == "2026-08-20"
    assert account["mdd"] == pytest.approx(-12.0)
    assert account["band"] == -10.0
    rendered = module.render([], account)
    assert "계좌 MDD(「기본 원칙 13」)" in rendered
    assert "-12.0% ← **발동 (-10%)**" in rendered
    assert "입출금을 보정하지 않는다" in rendered


def test_stock_band_warns_before_it_is_hit() -> None:
    """종목도 밴드를 밟기 전에 경고한다 — trailing stop은 밟은 뒤 정하면 늦다."""
    module = _load_peak_drawdown_module()

    pending = module.pending_bands(-9.8, 112_700.0, module.DRAWDOWN_BANDS)
    near = module.approaching_band(pending, module.DRAWDOWN_WARN_MARGIN_PP)

    assert [p["band"] for p in pending] == [-10.0, -15.0, -20.0, -30.0]
    assert near["band"] == -10.0
    assert near["trigger"] == pytest.approx(101_430.0)  # 발동가를 금액으로 준다
    assert near["gap_pp"] == pytest.approx(0.2, abs=0.01)


def test_stock_band_already_hit_is_not_an_approach() -> None:
    """발동한 밴드는 임박 대상이 아니고, 다음 밴드로 넘어간다."""
    module = _load_peak_drawdown_module()

    pending = module.pending_bands(-13.0, 100_000.0, module.DRAWDOWN_BANDS)

    assert [p["band"] for p in pending] == [-15.0, -20.0, -30.0]  # -10%는 이미 밟음
    assert module.approaching_band(pending, 2.0)["band"] == -15.0
    # 표의 경보 칸 = 발동이 임박을 가리지 않는다
    assert module.alert_label(-10.0, {"band": -15.0}) == "🟡 -10%"
    assert module.alert_label(None, {"band": -10.0}) == "⚠️ -10%"
    assert module.alert_label(None, None) == "—"


def test_stock_approach_uses_displayed_precision() -> None:
    """-12.969%는 표에 -13.0%로 찍히므로 -15%까지 2.0%p로 읽힌다 — 경고도 그 값으로 판단한다."""
    module = _load_peak_drawdown_module()
    drawdown = (406_000 / 466_500 - 1) * 100  # 에이피알 실제값 = -12.969%

    pending = module.pending_bands(drawdown, 466_500.0, module.DRAWDOWN_BANDS)
    near = module.approaching_band(pending, module.DRAWDOWN_WARN_MARGIN_PP)

    assert f"{drawdown:+.1f}%" == "-13.0%"
    assert near is not None and near["band"] == -15.0
    assert near["trigger"] == pytest.approx(396_525.0)


def test_stock_deepest_band_leaves_nothing_to_warn() -> None:
    module = _load_peak_drawdown_module()

    assert module.pending_bands(-45.3, 2_919_000.0, module.DRAWDOWN_BANDS) == []
    assert module.approaching_band([], 2.0) is None


def test_render_lists_stock_approaches_per_axis(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_peak_drawdown_module()
    monkeypatch.setattr(
        datafeed_stockeasy, "resolve_stock", lambda name: ({"stock_code": "000660"}, None, 0)
    )
    # 52주 축 = -9.0%(-10% 임박) / 기록 축 = -9.5%(-10% 임박)
    monkeypatch.setattr(
        module.daily,
        "fetch_daily_bars",
        lambda code, days, asof=None: (_bars([("20260601", 100_000.0), ("20260828", 91_000.0)]), None, None),
    )
    history = {"알파전자": [("2026-08-10", 77_348.0)]}  # 현재가 ₩70,000 → -9.5%

    results = module.analyze_holdings(
        module.parse_holdings(PEAK_SNAPSHOT), {}, history, "2026-08-28"
    )
    section = module.render(results, {"error": "잔고 이력 없음"})

    assert "⚠️ 임박(52주 시장 종가 기준): 알파전자 -9.0% → -10% 발동가 ₩90,000 (1.0%p 남음)" in section
    assert "⚠️ 임박(계좌 스냅샷 기록 기준): 알파전자 -9.5% → -10% 발동가 ₩69,613" in section
    # 미발동이지만 임박한 종목은 표의 경보 칸에도 표시된다
    assert "| -9.0% | ⚠️ -10% |" in section


def test_render_omits_approach_line_when_nothing_is_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_peak_drawdown_module()
    monkeypatch.setattr(
        datafeed_stockeasy, "resolve_stock", lambda name: ({"stock_code": "000660"}, None, 0)
    )
    monkeypatch.setattr(
        module.daily,
        "fetch_daily_bars",
        lambda code, days, asof=None: (_bars([("20260601", 100_000.0), ("20260828", 99_000.0)]), None, None),
    )

    section = module.render(
        module.analyze_holdings(module.parse_holdings(PEAK_SNAPSHOT), {}, {}, "2026-08-28"),
        {"error": "잔고 이력 없음"},
    )

    assert "임박" not in section


def _account(tmp_path: Path, today_balance: float, peak: float = 100_000_000.0) -> dict:
    """스냅샷 고점이 `peak`인 계좌에서 오늘 잔고가 `today_balance`일 때의 MDD 판정."""
    (tmp_path / "2026-08-20.md").write_text(f"> 잔고 ₩{peak:,.0f} · 수익률 1.00%\n", encoding="utf-8")
    return _load_peak_drawdown_module().account_mdd(tmp_path, today_balance, "2026-08-28")


def test_account_mdd_warns_before_band_is_hit(tmp_path: Path) -> None:
    """-15% 밟은 뒤 알리면 「기본 원칙 13」 대응을 준비할 시간이 없다 — 도달 전에 경고한다."""
    module = _load_peak_drawdown_module()

    account = _account(tmp_path, 86_500_000.0)  # MDD -13.5%

    assert account["mdd"] == pytest.approx(-13.5)
    assert account["band"] == -10.0  # -10%는 이미 발동
    assert account["approaching"]["band"] == -15.0  # -15%는 임박
    assert account["approaching"]["gap_pp"] == pytest.approx(1.5)
    assert account["approaching"]["trigger"] == pytest.approx(85_000_000.0)
    assert account["approaching"]["gap_won"] == pytest.approx(1_500_000.0)

    rendered = module.render([], account)
    assert "⚠️⚠️ **계좌 MDD -15% 임박 — 남은 거리 1.5%p(₩1,500,000).**" in rendered
    assert "잔고가 ₩85,000,000 아래로 내려가면 발동한다" in rendered
    assert "5거래일 신규 매수 금지" in rendered
    # 이미 밟은 -10%는 임박 대상이 아니다
    assert "계좌 MDD -10% 임박" not in rendered


def test_account_mdd_stays_quiet_outside_warn_margin(tmp_path: Path) -> None:
    module = _load_peak_drawdown_module()

    account = _account(tmp_path, 87_500_000.0)  # MDD -12.5% = -15%까지 2.5%p

    assert account["approaching"] is None
    rendered = module.render([], account)
    assert "임박" not in rendered
    # 경고가 없어도 발동선과 남은 거리는 매일 보인다
    assert "계좌 MDD -15% 발동선: ₩85,000,000 (현 잔고에서 2.5%p · ₩2,500,000 남음)" in rendered


def test_account_mdd_warn_margin_boundary_counts_as_approaching(tmp_path: Path) -> None:
    """경계값(정확히 2.0%p)은 경고에 포함한다 — 한 발 늦는 쪽으로 반올림하지 않는다."""
    module = _load_peak_drawdown_module()

    account = _account(tmp_path, 87_000_000.0)  # MDD -13.0% = -15%까지 정확히 2.0%p

    assert account["approaching"]["band"] == -15.0
    assert account["approaching"]["gap_pp"] == pytest.approx(module.ACCOUNT_MDD_WARN_MARGIN_PP)


def test_account_mdd_deepest_band_hit_has_nothing_left_to_warn(tmp_path: Path) -> None:
    module = _load_peak_drawdown_module()

    account = _account(tmp_path, 80_000_000.0)  # MDD -20%

    assert account["band"] == -15.0
    assert account["pending"] == [] and account["approaching"] is None
    rendered = module.render([], account)
    assert "임박" not in rendered and "발동선" not in rendered


def test_account_mdd_shows_distance_to_both_bands_when_healthy(tmp_path: Path) -> None:
    """오늘 같은 -6.8% 구간 = 경고는 없지만 두 발동선까지의 거리는 항상 찍힌다."""
    module = _load_peak_drawdown_module()

    account = _account(tmp_path, 93_200_000.0)  # MDD -6.8%

    assert account["approaching"] is None
    assert [p["band"] for p in account["pending"]] == [-10.0, -15.0]
    rendered = module.render([], account)
    assert "계좌 MDD -10% 발동선: ₩90,000,000 (현 잔고에서 3.2%p · ₩3,200,000 남음)" in rendered
    assert "계좌 MDD -15% 발동선: ₩85,000,000 (현 잔고에서 8.2%p · ₩8,200,000 남음)" in rendered


def test_peak_drawdown_fetches_holdings_concurrently_keeping_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """보유 10종목이면 네이버 왕복 10회가 직렬로 쌓인다. 병렬로 돌리되 출력 순서는 입력 순서다."""
    module = _load_peak_drawdown_module()
    monkeypatch.setattr(datafeed_stockeasy, "resolve_stock", lambda name: ({"stock_code": "000660"}, None, 0))

    import threading
    import time

    live, peak_live = 0, 0
    lock = threading.Lock()

    def slow_fetch(code, days, asof=None):
        nonlocal live, peak_live
        with lock:
            live += 1
            peak_live = max(peak_live, live)
        time.sleep(0.05)
        with lock:
            live -= 1
        return [{"date": "20260903", "close": 100.0}], None, None

    monkeypatch.setattr(module.daily, "fetch_daily_bars", slow_fetch)

    holdings = [
        {"종목": f"종목{i}", "섹터": "반도체", "현재가": "90", "수익률": "0%", "비중": "1%"}
        for i in range(6)
    ]
    results = module.analyze_holdings(holdings, {}, {}, "2026-09-05")

    assert [r["종목"] for r in results] == [h["종목"] for h in holdings], "출력 순서가 흐트러졌다"
    assert peak_live > 1, "여전히 한 번에 하나씩 받고 있다"
    assert peak_live <= module.MAX_FETCH_WORKERS


def test_market_signals_fetch_endpoints_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    """4개 엔드포인트를 순서대로 기다릴 이유가 없다 — 서로 독립이다."""
    module = _load_market_signal_module()

    import threading
    import time

    live, peak_live = 0, 0
    lock = threading.Lock()

    def slow_api(name):
        nonlocal live, peak_live
        with lock:
            live += 1
            peak_live = max(peak_live, live)
        time.sleep(0.05)
        with lock:
            live -= 1
        return {"indices": []}, None

    monkeypatch.setattr(module, "fetch_api", slow_api)
    data, errors = module.fetch_all()

    assert set(data) == set(module.ENDPOINTS) and not errors
    assert peak_live > 1, "엔드포인트를 아직 직렬로 받고 있다"


def test_account_mdd_action_text_matches_my_rules(tmp_path: Path) -> None:
    """경고에 붙는 대응 문구는 「기본 원칙 13」 원문에서 온다 — 룰이 바뀌면 같이 바뀌어야 한다."""
    module = _load_peak_drawdown_module()
    rules = (REPO_ROOT / "context" / "my_rules.md").read_text(encoding="utf-8")

    assert set(module.ACCOUNT_MDD_ACTION) == set(module.ACCOUNT_MDD_BANDS)
    assert "레버리지를 모두 정리하고 신규 매수를 중단" in rules
    assert "현금 비중을 30% 이상 확보" in rules
    assert "5거래일 동안 신규 매수를 금지" in rules
    assert "복기한 뒤에만 매매를 재개" in rules
    assert "레버리지 전량 정리" in module.ACCOUNT_MDD_ACTION[-10.0]
    assert "5거래일 신규 매수 금지" in module.ACCOUNT_MDD_ACTION[-15.0]


def test_peak_drawdown_append_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_peak_drawdown_module()
    monkeypatch.setattr(
        datafeed_stockeasy, "resolve_stock", lambda name: ({"stock_code": "000660"}, None, 0)
    )
    monkeypatch.setattr(
        module.daily,
        "fetch_daily_bars",
        lambda code, days, asof=None: (_bars([("20260601", 100_000.0), ("20260828", 90_000.0)]), None, None),
    )
    snapshot = tmp_path / "2026-08-28.md"
    snapshot.write_text(PEAK_SNAPSHOT, encoding="utf-8")

    assert module.main([str(snapshot), "--append"]) == 0
    assert module.main([str(snapshot), "--append"]) == 0
    capsys.readouterr()

    text = snapshot.read_text(encoding="utf-8")
    assert text.count(module.SECTION_TITLE) == 1
    # 기존 룰 자동 판정 섹션은 그대로 남는다
    assert "매매규칙 6(-15% 손절)" in text
    assert "🟡 -10%" in text
    # 계좌 기록 축도 같은 섹션에 함께 쓰인다
    assert "계좌 기록 고점(일자)" in text


def test_peak_drawdown_exits_1_on_unparsable_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_peak_drawdown_module()
    broken = tmp_path / "2026-08-28.md"
    broken.write_text("# 스냅샷\n\n표가 없다\n", encoding="utf-8")

    assert module.main([str(broken)]) == 1
    assert "스냅샷 파싱 실패" in capsys.readouterr().err


def test_daily_digest_documents_peak_drawdown_step() -> None:
    skill = (SKILLS_ROOT / "daily-digest" / "SKILL.md").read_text(encoding="utf-8")

    assert "### 1-3-1단계: 전고점 낙폭·계좌 MDD 판정" in skill
    assert "peak_drawdown.py" in skill
    # 밴드 4개와 룰 매핑이 모두 문서화돼 있어야 한다
    for band in ("-10%", "-15%", "-20%", "-30%"):
        assert band in skill
    assert "매매규칙 3(추세 기반 매도)" in skill
    assert "매매규칙 15" in skill
    assert "기본 원칙 13" in skill
    # 평단 축과 고점 축을 섞지 말라는 경고
    assert "평단 기준 룰과 다른 축이다" in skill
    assert "ticker_overrides.md" in skill
    # 고점 축이 둘(52주 시장 / 계좌 기록)이라는 사실과 혼용 금지가 문서화돼 있어야 한다
    assert "계좌 기록 고점" in skill
    assert "52주 시장 고점" in skill
    assert "어느 축에서 걸렸는지를 반드시 밝힌다" in skill


def test_daily_digest_checks_breakout_watchlist_every_day() -> None:
    skill = (SKILLS_ROOT / "daily-digest" / "SKILL.md").read_text(encoding="utf-8")

    assert "### 1-3-3단계: 돌파선 감시 리스트 점검" in skill
    assert "output/watchlist/돌파선_감시.md" in skill
    step = skill.split("### 1-3-3단계", 1)[1].split("\n### ", 1)[0]
    # 판정은 turn_scan 종가로만, 결과는 파일에 다시 써서 다음 날이 이어 본다
    assert "turn_scan.py" in step
    assert "종가" in step
    assert "갱신" in step
    # 섹터 확인 그룹은 개별 돌파만으로 매수 검토로 올리지 않는다
    assert "섹터 확인" in step
    assert "보유_트리거.md" in step


def test_daily_digest_documents_image_reading_step() -> None:
    skill = (SKILLS_ROOT / "daily-digest" / "SKILL.md").read_text(encoding="utf-8")

    assert "### 1-4단계: 이미지 판독 (OCR·차트 해석)" in skill
    # 이미지는 fetch 단계가 내려받고 판독은 에이전트가 파일 읽기 툴로 한다
    assert "output/daily-digest/media/" in skill
    assert "파일 읽기 툴로 직접 읽는다" in skill
    # raw 파일에 NUL 바이트가 섞여 있어 grep -a 없이는 마커를 못 찾는다
    assert "grep -an" in skill
    # 판독 결과 3줄 스키마
    for field in ("> 유형:", "> 텍스트:", "> 해석:"):
        assert field in skill
    # 실패 경로가 브리핑을 막지 않는다는 계약
    assert "[이미지 판독 실패]" in skill
    assert "(이미지 미확인)" in skill
    # 7단계가 미디어까지 정리한다
    assert "### 7단계: 과거 raw·미디어 정리" in skill
    assert "output/daily-digest/media \\" in skill
    assert "output/daily-digest/media/{today}/**" in skill


def test_image_pending_marker_matches_the_fetcher_constant() -> None:
    """SKILL.md가 찾는 마커와 fetch.py가 쓰는 마커가 같아야 한다.

    한쪽만 바꾸면 스킬이 판독 대상을 하나도 못 찾고 조용히 넘어간다.
    """
    from invagent.telegram.fetch import PENDING_IMAGE_MARKER

    skill = (SKILLS_ROOT / "daily-digest" / "SKILL.md").read_text(encoding="utf-8")

    assert PENDING_IMAGE_MARKER == "[분석 대기]"
    # grep 명령에 이스케이프된 형태로, 멱등성 설명에 그대로 등장한다
    assert "\\[분석 대기\\]" in skill
    assert f"`{PENDING_IMAGE_MARKER}`로 남은 항목만 처리" in skill


def test_link_summary_marker_matches_the_fetcher_constant() -> None:
    """SKILL.md가 찾는 요약 대기 마커와 fetch.py가 쓰는 마커가 같아야 한다.

    한쪽만 바꾸면 스킬이 요약할 링크를 하나도 못 찾고, 원문 파일만 쌓인 채 조용히 넘어간다.
    """
    from invagent.telegram.fetch import PENDING_LINK_SUMMARY_MARKER

    skill = (SKILLS_ROOT / "daily-digest" / "SKILL.md").read_text(encoding="utf-8")

    assert PENDING_LINK_SUMMARY_MARKER == "[요약 대기]"
    assert "\\[요약 대기\\]" in skill
    assert f"`{PENDING_LINK_SUMMARY_MARKER}`로 남은 항목만 처리" in skill


def test_daily_digest_documents_link_summary_step() -> None:
    skill = (SKILLS_ROOT / "daily-digest" / "SKILL.md").read_text(encoding="utf-8")

    assert "### 1-5단계: 링크 본문 요약" in skill
    # 원문은 fetch 단계가 파일로 빼 두고, 요약은 파일 8개당 서브에이전트 하나가 병렬로 한다
    assert "output/daily-digest/links/" in skill
    assert "서브에이전트" in skill
    assert "8개" in skill
    assert "1,000자" in skill
    # 실패 경로와 2단계 출처 표기
    assert "[요약 실패]" in skill
    assert "(링크 요약)" in skill
    # 7단계가 원문 파일도 정리하고 오늘 것은 남긴다
    assert "output/daily-digest/links \\" in skill
    assert "output/daily-digest/links/{today}/**" in skill


def _load_apply_link_summaries_module():
    script_path = SKILLS_ROOT / "daily-digest" / "scripts" / "apply_link_summaries.py"
    spec = importlib.util.spec_from_file_location("apply_link_summaries", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_apply_link_summaries_replaces_marker_with_sidecar(tmp_path) -> None:
    """요약 대기 마커를 원문 옆 요약 파일 내용으로 바꾸고 파일 줄은 남긴다"""
    module = _load_apply_link_summaries_module()
    body = tmp_path / "example.com_43ecaebf74.md"
    body.write_text("URL: https://example.com/report\n\n원문\n", encoding="utf-8")
    (tmp_path / "example.com_43ecaebf74.summary.md").write_text(
        "요약: 예시 「리포트」\n- 핵심: 매출 +10%\n- 결론: 유지\n", encoding="utf-8"
    )
    raw = tmp_path / "raw.md"
    raw.write_text(
        "링크:\n> URL: https://example.com/report\n"
        f"> 파일: {body}\n> [요약 대기]\n\n다음 메시지\n",
        encoding="utf-8",
    )

    assert module.main([str(raw)]) == 0

    assert raw.read_text(encoding="utf-8") == (
        "링크:\n> URL: https://example.com/report\n"
        f"> 파일: {body}\n"
        "> 요약: 예시 「리포트」\n> - 핵심: 매출 +10%\n> - 결론: 유지\n\n다음 메시지\n"
    )


def test_apply_link_summaries_reads_sidecar_next_to_pdf_body(tmp_path) -> None:
    """PDF로 저장된 링크(`X.pdf`)도 옆의 `X.summary.md`로 마커를 바꾼다"""
    module = _load_apply_link_summaries_module()
    body = tmp_path / "vo.la_167396fe47.pdf"
    body.write_bytes(b"%PDF-1.7\n")
    (tmp_path / "vo.la_167396fe47.summary.md").write_text("요약: 메리츠 「인텍플러스」\n", encoding="utf-8")
    raw = tmp_path / "raw.md"
    raw.write_text(f"> URL: https://vo.la/zapQlKL\n> 파일: {body}\n> [요약 대기]\n", encoding="utf-8")

    assert module.main([str(raw)]) == 0

    assert raw.read_text(encoding="utf-8") == (
        f"> URL: https://vo.la/zapQlKL\n> 파일: {body}\n> 요약: 메리츠 「인텍플러스」\n"
    )


def test_apply_link_summaries_leaves_marker_when_summary_is_missing(tmp_path) -> None:
    """요약 파일이 아직 없으면 마커를 그대로 두어 다시 돌려도 안전하다"""
    module = _load_apply_link_summaries_module()
    body = tmp_path / "news.example_0123456789.md"
    body.write_text("URL: https://news.example/a\n\n원문\n", encoding="utf-8")
    original = f"> 파일: {body}\n> [요약 대기]\n"
    raw = tmp_path / "raw.md"
    raw.write_text(original, encoding="utf-8")

    assert module.main([str(raw)]) == 0

    assert raw.read_text(encoding="utf-8") == original


def test_apply_link_summaries_reports_counts_without_summary_text(tmp_path, capsys) -> None:
    """출력은 반영·대기 건수와 대기 중인 원문 경로뿐이고 요약 내용은 찍지 않는다"""
    module = _load_apply_link_summaries_module()
    done = tmp_path / "done.example_aaaaaaaaaa.md"
    done.write_text("원문", encoding="utf-8")
    (tmp_path / "done.example_aaaaaaaaaa.summary.md").write_text(
        "요약: 비밀 요약 문장\n", encoding="utf-8"
    )
    pending = tmp_path / "wait.example_bbbbbbbbbb.md"
    pending.write_text("원문", encoding="utf-8")
    raw = tmp_path / "raw.md"
    raw.write_text(
        f"> 파일: {done}\n> [요약 대기]\n\n> 파일: {pending}\n> [요약 대기]\n",
        encoding="utf-8",
    )

    assert module.main([str(raw)]) == 0

    assert capsys.readouterr().out == f"반영 1건 · 대기 1건\n대기: {pending}\n"


def test_peak_drawdown_thresholds_match_documented_bands() -> None:
    module = _load_peak_drawdown_module()

    assert module.PEAK_WINDOW_DAYS == 250
    assert module.DRAWDOWN_BANDS == (-10.0, -15.0, -20.0, -30.0)
    assert module.ACCOUNT_MDD_BANDS == (-10.0, -15.0)


# --- 계좌 기록 고점 축 --------------------------------------------------------


def _record_snapshot(date: str, prices: dict[str, int]) -> str:
    """합성 스냅샷 마크다운. 현금 행은 기록 축에서 제외돼야 한다."""
    rows = "\n".join(
        f"| {name} | 반도체 | 10 | ₩1,000 | ₩{price:,} | 1.00% | 10.0% | ₩10,000 | 아이디어 |"
        for name, price in prices.items()
    )
    return (
        f"# 포트폴리오 스냅샷 — {date}\n\n"
        f"> 잔고 ₩90,000,000 · 수익률 1.00%\n\n"
        f"## 보유 ({len(prices)}종목 + 현금)\n\n"
        "| 종목 | 섹터 | 보유 | 평단 | 현재가 | 수익률 | 비중 | 평가금액 | 투자 아이디어 |\n"
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |\n"
        "| _현금 | 현금 | 1 | ₩1,000 | ₩1,000 | 0.00% | 1.0% | ₩1,000 | 현금도 종목이다 |\n"
        f"{rows}\n"
    )


def test_record_history_skips_unparsable_snapshot(tmp_path: Path) -> None:
    """`## 보유` 표가 없는 손글씨 스냅샷(실제 2026-08-15)이 섞여도 나머지로 계속 계산한다."""
    module = _load_peak_drawdown_module()
    (tmp_path / "2026-08-10.md").write_text(
        _record_snapshot("2026-08-10", {"알파전자": 355_000}), encoding="utf-8"
    )
    (tmp_path / "2026-08-15.md").write_text(
        "# 포트폴리오 스냅샷 — 2026-08-15\n\n| 종목 | 비중 | 비고 |\n|---|---:|---|\n"
        "| 알파전자 | 26.0% | 반도체 |\n",
        encoding="utf-8",
    )
    (tmp_path / "2026-09-03.md").write_text(
        _record_snapshot("2026-09-03", {"알파전자": 283_000}), encoding="utf-8"
    )

    history, meta = module.load_record_history(tmp_path)

    assert [d for d, _ in history["알파전자"]] == ["2026-08-10", "2026-09-03"]
    assert meta["valid_files"] == 2 and meta["total_files"] == 3
    assert meta["from"] == "2026-08-10" and meta["to"] == "2026-09-03"
    # 현금 행은 기록 축에 들어오지 않는다
    assert "_현금" not in history


def test_record_drawdown_uses_history_peak() -> None:
    module = _load_peak_drawdown_module()
    history = {"알파전자": [("2026-08-01", 300_000.0), ("2026-08-10", 355_000.0)]}

    result = module.record_drawdown("알파전자", 283_000.0, "2026-09-03", history)

    assert result["peak"] == 355_000.0 and result["peak_date"] == "2026-08-10"
    assert result["drawdown"] == pytest.approx(-20.28, abs=0.01)
    assert result["band"] == -20.0
    assert result["samples"] == 3


def test_record_drawdown_today_value_overrides_disk_entry() -> None:
    """오늘 일자 항목은 디스크 값을 버리고 방금 파싱한 값을 쓴다(`account_mdd`와 같은 규약)."""
    module = _load_peak_drawdown_module()
    history = {"알파전자": [("2026-09-03", 999_000.0), ("2026-08-10", 355_000.0)]}

    result = module.record_drawdown("알파전자", 283_000.0, "2026-09-03", history)

    assert result["peak"] == 355_000.0
    assert result["current"] == 283_000.0
    assert result["samples"] == 2


def test_record_drawdown_without_prior_history_is_flat() -> None:
    """편입 첫날 = 고점도 오늘, 현재가도 오늘 → 낙폭 0%. 마커 없이 숫자만 남는다."""
    module = _load_peak_drawdown_module()

    result = module.record_drawdown("신규종목", 100_000.0, "2026-09-03", {})

    assert result["error"] is None
    assert result["peak"] == result["current"] == 100_000.0
    assert result["drawdown"] == pytest.approx(0.0)
    assert result["band"] is None

    assert module.record_drawdown("신규종목", None, "2026-09-03", {}) == {
        "error": "스냅샷 기록 없음"
    }


def test_record_axis_survives_market_data_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """네트워크가 죽어 52주 축이 비어도 계좌 기록 축은 그대로 표에 남아야 한다."""
    module = _load_peak_drawdown_module()
    monkeypatch.setattr(
        datafeed_stockeasy, "resolve_stock", lambda name: (None, "종목 검색 결과 없음", 1)
    )
    history = {"알파전자": [("2026-08-10", 100_000.0)]}

    results = module.analyze_holdings(
        module.parse_holdings(PEAK_SNAPSHOT), {}, history, "2026-08-28"
    )

    assert results[0]["error"] is not None  # 52주 축은 실패
    assert results[0]["record"]["drawdown"] == pytest.approx(-30.0)  # 기록 축은 살아있다
    section = module.render(results, {"error": "잔고 이력 없음"})
    assert "₩100,000 (2026-08-10) | -30.0% ⛔" in section


def test_render_names_both_axes_and_forbids_summing(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_peak_drawdown_module()
    monkeypatch.setattr(
        datafeed_stockeasy, "resolve_stock", lambda name: ({"stock_code": "000660"}, None, 0)
    )
    monkeypatch.setattr(
        module.daily,
        "fetch_daily_bars",
        lambda code, days, asof=None: (_bars([("20260601", 100_000.0), ("20260828", 60_000.0)]), None, None),
    )
    # 52주 축은 -40%(⛔), 기록 축은 -12.5%(🟡) — 두 축이 두 밴드만큼 어긋나는 상황
    history = {"알파전자": [("2026-08-10", 80_000.0)], "베타파마": [("2026-08-10", 80_000.0)]}

    results = module.analyze_holdings(
        module.parse_holdings(PEAK_SNAPSHOT), {}, history, "2026-08-28"
    )
    section = module.render(
        results,
        {"error": "잔고 이력 없음"},
        {"valid_files": 22, "total_files": 23, "from": "2026-08-09", "to": "2026-09-03"},
    )

    # 같은 종목이 두 축에서 두 밴드만큼 다르게 걸린다 — 합쳐 세면 안 되는 이유
    assert "전고점 낙폭 판정(52주 시장 종가 기준): 알파전자 -40.0% (⛔ -30%)" in section
    assert "기록 낙폭 판정(계좌 스냅샷 기록 기준):" in section
    assert "알파전자 -12.5% (🟡 -10%)" in section
    assert "같은 룰 번호로 합산하지 않고" in section
    # 각주 = 기록 구간·개수·제외 파일 수
    assert "스냅샷 22개(2026-08-09~2026-09-03)" in section
    assert "1개는 형식이 달라 제외" in section
    assert "매도 후 재매수 구간도 구분하지 않고" in section


def test_append_section_removes_legacy_title(tmp_path: Path) -> None:
    """옛 제목으로 쓰인 스냅샷에 재실행해도 섹션이 둘로 갈라지지 않는다."""
    module = _load_peak_drawdown_module()
    snapshot = tmp_path / "2026-08-28.md"
    snapshot.write_text(
        PEAK_SNAPSHOT
        + f"\n{module.LEGACY_SECTION_TITLES[0]}\n\n| 종목 | 낙폭 |\n| --- | ---: |\n"
        "| 알파전자 | -45.3% |\n",
        encoding="utf-8",
    )

    module.append_section(snapshot, f"{module.SECTION_TITLE}\n\n- 새 내용\n")
    text = snapshot.read_text(encoding="utf-8")

    assert text.count(module.SECTION_TITLE) == 1
    assert module.LEGACY_SECTION_TITLES[0] not in text
    assert "-45.3%" not in text  # 옛 섹션 본문까지 걷어낸다
    assert "매매규칙 6(-15% 손절)" in text  # 다른 섹션은 보존


def _load_entry_policy_module():
    script_path = SKILLS_ROOT / "analyze-stock" / "scripts" / "entry_policy.py"
    spec = importlib.util.spec_from_file_location("entry_policy", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _entry_payload(**overrides) -> dict:
    """진입 게이트가 전부 통과하는 기본 입력. 시험할 게이트만 덮어쓴다."""
    payload = {
        "asof": "2026-09-09",
        "valuation_grade": "buy_candidate",
        "account_pause": {
            "status": "ok",
            "active": False,
            "remaining_trading_days": 0,
            "review_complete": True,
        },
        "stage": {"status": "ok", "value": 2},
        "turn": {"status": "ok", "value": "turning"},
        "overhang": {"status": "ok", "value": "none"},
        "event": {"status": "ok", "enabled": False},
        "long_bull": {"status": "ok", "active": False},
        "earnings": {"status": "ok", "surprise": False},
        "instrument": {"status": "ok", "kind": "stock", "leveraged": False},
    }
    payload.update(overrides)
    return payload


def test_entry_policy_stage_two_stays_eligible() -> None:
    """가드 — 밸류 경로를 여는 동안 2단계 정상 진입이 깨지면 안 된다."""
    module = _load_entry_policy_module()

    result = module.decide(_entry_payload())

    assert result["action"] == "eligible"
    assert result["max_tranche_fraction"] is None


def test_entry_policy_leaves_overhang_out_of_the_decision() -> None:
    """오버행은 §6-A 참고 사항이다 — 미해소·미수집이어도 action과 1차 수량 상한을 바꾸지 않는다."""
    module = _load_entry_policy_module()

    unresolved = module.decide(_entry_payload(overhang={"status": "ok", "value": "unresolved"}))
    unknown = module.decide(_entry_payload(overhang={"status": "missing"}))
    absent = _entry_payload()
    del absent["overhang"]

    for result in (unresolved, unknown, module.decide(absent)):
        assert result["action"] == "eligible"
        assert result["max_tranche_fraction"] is None
        assert "overhang" not in result["checks"]


def test_entry_policy_withholds_when_stage_is_unknown() -> None:
    """가드 — 알 수 없음은 허가로 승격되지 않는다."""
    module = _load_entry_policy_module()

    result = module.decide(_entry_payload(stage={"status": "missing"}))

    assert result["action"] == "withhold"


VALUE_GATE_PASS = {
    "status": "ok",
    "downside_blocked": True,
    "reward_risk": 3.4,
    "first_tranche_fraction": 0.03,
    "target_weight_fraction": 0.10,
    "next_tranche_trigger": "직전 순환적 저점 회복 후 스윙 고점 돌파",
}


def test_entry_policy_opens_stage_one_for_a_passing_value_gate() -> None:
    """1단계는 매집 구간이다. 하방이 막힌 저평가면 1차 분할이 열려야 한다."""
    module = _load_entry_policy_module()

    result = module.decide(
        _entry_payload(stage={"status": "ok", "value": 1}, value_gate=dict(VALUE_GATE_PASS))
    )

    assert result["action"] == "watch"
    assert result["max_tranche_fraction"] == pytest.approx(0.03)


def test_entry_policy_withholds_stage_one_without_a_value_gate() -> None:
    """밸류 근거 없이 1단계에 들어가는 것은 여전히 막는다 — 진입 수량 0."""
    module = _load_entry_policy_module()

    result = module.decide(_entry_payload(stage={"status": "ok", "value": 1}))

    assert result["action"] == "withhold"
    assert result["max_tranche_fraction"] is None


def test_entry_policy_treats_stage_three_like_stage_one() -> None:
    """3단계도 밸류 게이트 통과 시 1차 분할만 예외로 연다. 미통과면 수량 0."""
    module = _load_entry_policy_module()

    opened = module.decide(
        _entry_payload(stage={"status": "ok", "value": 3}, value_gate=dict(VALUE_GATE_PASS))
    )
    closed = module.decide(_entry_payload(stage={"status": "ok", "value": 3}))

    assert opened["action"] == "watch"
    assert opened["max_tranche_fraction"] == pytest.approx(0.03)
    assert closed["action"] == "withhold"


def test_entry_policy_opens_stage_four_only_once_the_stock_turns_up() -> None:
    """가치주 바닥은 150일선상 4단계인 채로 온다. 단기로 고개를 들었으면 '하락하는 와중'이 아니다.

    2026-09-12 사용자 확정 — 4단계도 「고개 들기」 + 밸류 게이트면 1차 분할이 열린다.
    바닥을 다지는 중이거나 밸류 게이트가 없으면 여전히 닫혀 있다.
    """
    module = _load_entry_policy_module()
    stage4 = {"status": "ok", "value": 4}

    turned = module.decide(_entry_payload(stage=stage4, value_gate=dict(VALUE_GATE_PASS)))
    basing = module.decide(
        _entry_payload(stage=stage4, value_gate=dict(VALUE_GATE_PASS),
                       turn={"status": "ok", "value": "basing"})
    )
    no_gate = module.decide(_entry_payload(stage=stage4))

    assert turned["action"] == "watch"
    assert turned["max_tranche_fraction"] == pytest.approx(0.03)
    assert basing["action"] == "withhold"
    assert basing["max_tranche_fraction"] is None
    assert no_gate["action"] == "withhold"


def test_entry_policy_waits_for_the_turn_before_the_value_path_opens() -> None:
    """「매매규칙 2」는 바닥을 다진 '후' 고개 드는 초반에 산다 — 다지는 중이나 초입을 지난 뒤는 수량 0."""
    module = _load_entry_policy_module()

    for value in ("basing", "extended"):
        result = module.decide(
            _entry_payload(stage={"status": "ok", "value": 1}, value_gate=dict(VALUE_GATE_PASS),
                           turn={"status": "ok", "value": value})
        )
        assert result["action"] == "withhold", value
        assert result["max_tranche_fraction"] is None, value


ACCUMULATION_PASS = {"status": "ok", "eligible": True, "op_yoy_increased": True}


def test_entry_policy_opens_half_a_first_tranche_before_the_turn_for_a_growing_quiet_base() -> None:
    """경로 C — 「매매규칙 2」 단서. 바닥 30일 + 바닥 +10% 이내 + 영업이익 YoY 증가 + 밸류 게이트면 고개 들기 전에
    1차 트랜치의 절반을 연다. 4단계도 같다 (2026-09-25 백테스트, 사용자 확정)."""
    module = _load_entry_policy_module()

    for stage in (1, 3, 4):
        result = module.decide(
            _entry_payload(stage={"status": "ok", "value": stage}, value_gate=dict(VALUE_GATE_PASS),
                           turn={"status": "ok", "value": "basing"}, accumulation=dict(ACCUMULATION_PASS))
        )
        assert result["action"] == "watch", stage
        assert result["max_tranche_fraction"] == pytest.approx(0.015), stage
        assert "매매규칙 2" in result["checks"]["stage"]["rule"]


def test_entry_policy_keeps_the_base_closed_when_any_accumulation_condition_is_missing() -> None:
    """실적 감소, 가격 조건 미충족, 판정 못 받음, 밸류 게이트 없음 — 하나라도 빠지면 고개 들기를 기다린다."""
    module = _load_entry_policy_module()
    basing = {"status": "ok", "value": "basing"}
    cases = {
        "영업이익 감소": dict(value_gate=dict(VALUE_GATE_PASS), accumulation={**ACCUMULATION_PASS, "op_yoy_increased": False}),
        "실적 모름": dict(value_gate=dict(VALUE_GATE_PASS), accumulation={**ACCUMULATION_PASS, "op_yoy_increased": None}),
        "가격 조건 미충족": dict(value_gate=dict(VALUE_GATE_PASS), accumulation={**ACCUMULATION_PASS, "eligible": False}),
        "판정 없음": dict(value_gate=dict(VALUE_GATE_PASS), accumulation={"status": "missing"}),
        "밸류 게이트 없음": dict(accumulation=dict(ACCUMULATION_PASS)),
    }
    for label, extra in cases.items():
        result = module.decide(_entry_payload(stage={"status": "ok", "value": 4}, turn=basing, **extra))
        assert result["action"] == "withhold", label
        assert result["max_tranche_fraction"] is None, label


def test_entry_policy_never_accumulates_into_a_stock_that_is_still_falling() -> None:
    module = _load_entry_policy_module()

    result = module.decide(
        _entry_payload(stage={"status": "ok", "value": 4}, value_gate=dict(VALUE_GATE_PASS),
                       turn={"status": "ok", "value": "falling"}, accumulation=dict(ACCUMULATION_PASS))
    )

    assert result["action"] == "avoid"


def test_entry_policy_avoids_a_stock_that_is_still_falling() -> None:
    """방금 신저가를 쓴 종목은 밸류가 아무리 좋아도 하락하는 와중이다."""
    module = _load_entry_policy_module()

    result = module.decide(
        _entry_payload(stage={"status": "ok", "value": 1}, value_gate=dict(VALUE_GATE_PASS),
                       turn={"status": "ok", "value": "falling"})
    )

    assert result["action"] == "avoid"
    assert result["max_tranche_fraction"] is None


def test_entry_policy_withholds_the_value_path_when_the_turn_is_unknown() -> None:
    """가드 — 단기 판정을 못 받았으면 허가로 승격하지 않는다."""
    module = _load_entry_policy_module()

    missing = _entry_payload(stage={"status": "ok", "value": 3}, value_gate=dict(VALUE_GATE_PASS))
    del missing["turn"]
    errored = _entry_payload(stage={"status": "ok", "value": 3}, value_gate=dict(VALUE_GATE_PASS),
                             turn={"status": "error"})

    for payload in (missing, errored):
        result = module.decide(payload)
        assert result["action"] == "withhold"
        assert result["max_tranche_fraction"] is None


def test_entry_policy_stage_two_does_not_consult_the_turn() -> None:
    """2단계는 경로 A다 — 단기 판정은 1·3·4단계의 타이밍 게이트일 뿐 주도주 진입을 막지 않는다."""
    module = _load_entry_policy_module()

    payload = _entry_payload(turn={"status": "ok", "value": "extended"})
    result = module.decide(payload)

    assert result["action"] == "eligible"
    assert result["max_tranche_fraction"] is None


def test_value_gate_requires_a_blocked_downside() -> None:
    """하방 막힘이 필수 조건이다. 기대수익만 큰 종목으로 경로가 열리면 안 된다."""
    module = _load_entry_policy_module()

    result = module.decide(
        _entry_payload(
            stage={"status": "ok", "value": 1},
            value_gate={**VALUE_GATE_PASS, "downside_blocked": False},
        )
    )

    assert result["action"] == "withhold"
    assert result["max_tranche_fraction"] is None


def test_value_gate_requires_a_higher_reward_risk_than_the_general_rule() -> None:
    """추세 확인 없이 들어가는 대가로 손익비 문턱이 일반 2.0보다 높다.

    문턱은 2.5다 (사용자 확정 2026-09-22). 3.0은 15% 분모와 맞물리면 중심
    기대수익 45%를 요구해 「기본 원칙 2」 본문의 50%와 겹치고, 20% 분모 시절에는
    60%를 요구해 룰 원문보다 엄격했다.
    """
    module = _load_entry_policy_module()

    assert module.VALUE_GATE_MIN_REWARD_RISK == pytest.approx(2.5)
    below = module.decide(
        _entry_payload(
            stage={"status": "ok", "value": 1},
            value_gate={**VALUE_GATE_PASS, "reward_risk": 2.49},
        )
    )
    at_threshold = module.decide(
        _entry_payload(
            stage={"status": "ok", "value": 1},
            value_gate={**VALUE_GATE_PASS, "reward_risk": module.VALUE_GATE_MIN_REWARD_RISK},
        )
    )

    assert below["action"] == "withhold"
    assert at_threshold["action"] == "watch"


def test_value_gate_caps_the_first_tranche_at_a_third_of_target_weight() -> None:
    """경로 B는 1차 분할 한정이다. 목표 비중을 통째로 싣는 tranche는 통과시키지 않는다."""
    module = _load_entry_policy_module()

    oversized = module.decide(
        _entry_payload(
            stage={"status": "ok", "value": 1},
            value_gate={**VALUE_GATE_PASS, "first_tranche_fraction": 0.05},
        )
    )
    at_limit = module.decide(
        _entry_payload(
            stage={"status": "ok", "value": 1},
            value_gate={
                **VALUE_GATE_PASS,
                "first_tranche_fraction": 0.10 * module.VALUE_PATH_TRANCHE_RATIO,
            },
        )
    )

    assert oversized["action"] == "withhold"
    assert at_limit["action"] == "watch"


def test_value_gate_requires_a_next_tranche_trigger() -> None:
    """나머지 tranche를 무엇이 여는지 안 적었으면 1차도 열지 않는다 — 「매매규칙 4」 사전 계획."""
    module = _load_entry_policy_module()

    blank = module.decide(
        _entry_payload(
            stage={"status": "ok", "value": 1},
            value_gate={**VALUE_GATE_PASS, "next_tranche_trigger": "   "},
        )
    )
    missing = module.decide(
        _entry_payload(
            stage={"status": "ok", "value": 1},
            value_gate={k: v for k, v in VALUE_GATE_PASS.items() if k != "next_tranche_trigger"},
        )
    )

    assert blank["action"] == "withhold"
    assert missing["action"] == "withhold"


def test_analyze_stock_opens_a_value_first_entry_path() -> None:
    """드리프트 가드 — 매수는 가치, 매도는 추세. 1·3단계가 밸류 게이트로 열려야 한다."""
    skill = (SKILLS_ROOT / "analyze-stock" / "SKILL.md").read_text(encoding="utf-8")

    assert "경로 A (추세 확인 진입)" in skill
    assert "경로 B (가치 우선 진입)" in skill
    assert "밸류 게이트" in skill
    # 하방 막힘이 필수 조건이다 — 기대수익만으로 열리면 안 된다.
    assert "하방 막힘" in skill
    assert "손익비 ≥ 2.5" in skill
    assert "목표 비중 ÷ 3" in skill
    # 4단계는 밸류 예외가 없다.
    gate = skill.split("경로 A (추세 확인 진입)")[1].split("오버행 참고")[0]
    assert "밸류 예외 없음" in gate
    assert "기대수익이 크다는 것은 뒤집을 근거가 아니다" in gate


def test_stock_analysis_template_has_entry_path_and_trailing_stop() -> None:
    """드리프트 가드 — 진입 경로와 추세 이탈선은 §1에서 바로 읽혀야 한다."""
    template = (REPO_ROOT / "template" / "stock_analysis.md").read_text(encoding="utf-8")

    assert "| 진입 경로 |" in template
    assert "| 추세 이탈선 |" in template
    # 「매매규칙 15」가 요구하는 것 — 미리 정한 선 + 매주 재검토.
    assert "매매규칙 3·15" in template
    assert "매주 재검토" in template
    # §5에 밸류 게이트 체크리스트가 붙는다.
    assert "### 🚪 진입 경로 판정" in template
    assert "max_tranche_fraction" in template


def test_trailing_stop_uses_the_closing_price_basis() -> None:
    """드리프트 가드 — 발동 판정은 종가로만 한다(`my_rules.md` 「적용 방법」 2026-09-09 확정)."""
    rules = _my_rules()
    assert "가격 기준은 종가다" in rules

    skill = (SKILLS_ROOT / "analyze-stock" / "SKILL.md").read_text(encoding="utf-8")
    assert "추세 이탈선" in skill
    block = skill.split("추세 이탈선")[1][:500]
    assert "종가 기준" in block
    assert "매매규칙 3" in block and "매매규칙 15" in block


def test_advice_records_the_entry_stage_conflict_resolution() -> None:
    """드리프트 가드 — 대가 기준(2단계만 매수) vs 사용자 룰을 매번 재논쟁하지 않는다."""
    advice = (SKILLS_ROOT / "advice" / "SKILL.md").read_text(encoding="utf-8")

    table = advice.split("## 대가 기준 vs 사용자 룰 충돌 처리")[1]
    assert "진입 국면" in table
    assert "매수는 가치, 매도는 추세" in table
    assert "1차 분할 한정" in table


def _load_weekly_upsert_module():
    script_path = SKILLS_ROOT / "weekly-investment-review" / "scripts" / "weekly_upsert.py"
    spec = importlib.util.spec_from_file_location("weekly_upsert", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_weekly_upsert_guard_rejects_a_lost_heading() -> None:
    """2026-09-20 사고 고정점 — 본문 헤딩이 하나라도 사라지면 병합 결과를 거부한다."""
    module = _load_weekly_upsert_module()

    before = "## 19\\~20(주말)\n- 주간 투자 반성\n\n## 18(금)\n- 매매 없음\n"
    after = "## 19\\~20(주말)\n- 주간 투자 반성\n"

    with pytest.raises(ValueError) as excinfo:
        module.assert_no_heading_loss(before, after)

    assert "18(금)" in str(excinfo.value)


_WEEKLY_BODY = (
    "- 상단 원칙 불릿\n"
    "\n"
    "## 19\\~20(주말)\n"
    "- 주간 투자 반성\n"
    "\t- 옛 내용 한 줄\n"
    "\t\t- 옛 하위 불릿\n"
    "\n"
    "## 18(금) {toggle=\"true\"}\n"
    "\t- 매매 및 시황\n"
    "\t\t- 매매 없음\n"
    "\n"
    "## 14(월) {toggle=\"true\"}\n"
    "\t- 매도: 알테오젠(80주 전량)\n"
)


def test_weekly_upsert_replaces_only_the_weekly_block() -> None:
    """주말 헤딩 아래 블록만 갈아끼우고, 다른 날짜 헤딩은 전부 남는다."""
    module = _load_weekly_upsert_module()

    merged = module.upsert(_WEEKLY_BODY, "- 주간 투자 반성\n\t- 새 내용\n", "19~20")

    assert "새 내용" in merged
    assert "옛 내용 한 줄" not in merged
    assert "옛 하위 불릿" not in merged
    assert module.headings(merged) == ["19~20(주말)", '18(금) {toggle="true"}', '14(월) {toggle="true"}']
    assert "- 상단 원칙 불릿" in merged
    assert "매매 없음" in merged
    assert "매도: 알테오젠(80주 전량)" in merged


def test_weekly_upsert_inserts_when_no_prior_block_exists() -> None:
    """주말 헤딩은 있는데 반성 블록이 아직 없으면 헤딩 바로 뒤에 넣는다."""
    module = _load_weekly_upsert_module()
    body = "## 26\\~27(주말)\n\n## 25(금)\n\t- 매매 없음\n"

    merged = module.upsert(body, "- 주간 투자 반성\n\t- 첫 기록\n", "26~27")

    lines = merged.split("\n")
    assert lines[0].startswith("## 26")
    assert lines[1] == "- 주간 투자 반성"
    assert lines[2] == "\t- 첫 기록"
    assert module.headings(merged) == ["26~27(주말)", "25(금)"]
    assert "매매 없음" in merged


def test_weekly_upsert_refuses_a_missing_weekend_heading() -> None:
    """헤딩이 없으면 조용히 맨 뒤에 붙이지 않고 거부한다."""
    module = _load_weekly_upsert_module()

    with pytest.raises(ValueError) as excinfo:
        module.upsert("## 18(금)\n\t- 매매 없음\n", "- 주간 투자 반성\n", "19~20")

    assert "weekend heading not found" in str(excinfo.value)


def test_weekly_upsert_cli_writes_nothing_when_it_refuses(tmp_path: Path) -> None:
    """거부 시 exit 2이고 --prepared-out은 만들어지지 않는다(반쯤 쓴 본문 금지)."""
    module = _load_weekly_upsert_module()
    existing = tmp_path / "body.md"
    existing.write_text("## 18(금)\n\t- 매매 없음\n", encoding="utf-8")
    proposed = tmp_path / "week.md"
    proposed.write_text("- 주간 투자 반성\n", encoding="utf-8")
    out = tmp_path / "merged.md"

    code = module.main([
        "--existing", str(existing),
        "--proposed", str(proposed),
        "--weekend", "19~20",
        "--prepared-out", str(out),
        "--json",
    ])

    assert code == 2
    assert not out.exists()


def test_weekly_upsert_cli_reports_the_merge(tmp_path: Path, capsys) -> None:
    """성공 시 exit 0, 병합 본문 기록, 헤딩 수를 보고한다."""
    module = _load_weekly_upsert_module()
    existing = tmp_path / "body.md"
    existing.write_text(_WEEKLY_BODY, encoding="utf-8")
    proposed = tmp_path / "week.md"
    proposed.write_text("- 주간 투자 반성\n\t- 새 내용\n", encoding="utf-8")
    out = tmp_path / "merged.md"

    code = module.main([
        "--existing", str(existing),
        "--proposed", str(proposed),
        "--weekend", "19~20",
        "--prepared-out", str(out),
        "--json",
    ])

    assert code == 0
    assert "새 내용" in out.read_text(encoding="utf-8")
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["replaced"] is True
    assert payload["headings"] == 3


def _weekly_skill() -> str:
    return (SKILLS_ROOT / "weekly-investment-review" / "SKILL.md").read_text(encoding="utf-8")


def test_weekly_skill_records_the_notion_overwrite_hazard() -> None:
    """드리프트 가드 — replace_content가 본문 전체를 덮는다는 사실과 백업·검증 순서를 남긴다."""
    skill = _weekly_skill()

    assert "replace_content" in skill
    assert "2026-09-20" in skill
    assert "백업" in skill
    assert "weekly_upsert.py" in skill
    assert "전체 본문" in skill
    assert "헤딩" in skill


def test_weekly_skill_reads_the_week_daily_digests() -> None:
    """드리프트 가드 — 채점 축 2(브리핑 권고 대비 체결)의 입력 경로와 절 이름을 남긴다."""
    skill = _weekly_skill()

    assert "output/daily-digest/" in skill
    for section in ("룰 리마인드", "시장 상황", "의사 결정 조언", "투자 조언"):
        assert section in skill


def test_weekly_skill_requires_the_next_week_plan() -> None:
    """드리프트 가드 — 다음 주 방향 세 칸이 빠지지 않게 한다."""
    skill = _weekly_skill()

    assert "신규 진입" in skill
    assert "포지션 사이징" in skill
    assert "현금 확보" in skill
    assert "기본 원칙 4" in skill


def test_weekly_skill_records_all_three_notion_command_behaviours() -> None:
    """드리프트 가드 — 2026-09-20 실측 3종과 매칭 규칙을 남긴다."""
    skill = _weekly_skill()

    assert "insert_content" in skill
    assert "페이지 맨 끝" in skill
    assert "update_content" in skill
    assert "content_updates" in skill
    assert "블록의 맨 앞부터" in skill
    assert "평문" in skill
    assert "블록을 지우는 수단은 없다" in skill


def test_stock_info_splits_a_preferred_share_between_two_tickers(monkeypatch):
    """우선주는 시세만 자기 코드, 실적·컨센·뉴스·리포트는 본주 코드로 받는다."""
    from invagent.datafeed import stockeasy

    module = _load_stock_info_module()
    payloads = {
        "005935": {"stock_info": {"name": "삼성전자우", "cur_prc": "196900"}},
        "005930": {
            "stock_info": {"name": "삼성전자", "cur_prc": "200000"},
            "financials": {"quarterly": []},
            "target_price_history": [],
        },
    }
    paths: list[str] = []

    def fake_fetch(path, params=None, referer=None, cookie=None):
        paths.append(path)
        code = (params or {}).get("stock_code") or path.rstrip("/").split("/")[-1]
        return payloads.get(code, {"items": []}), None

    monkeypatch.setattr(stockeasy, "fetch_stock_json", fake_fetch)

    info, news, reports, own, analysis, errors = module.collect_stock_payloads(
        "005935", news_limit=5, summaries=0, since=None, cookie="x", disclosures=5
    )

    assert "/stock-info/info-tab/005930" in paths, "실적은 본주 코드로 받는다"
    assert "/stock-info/info-tab/005935" in paths, "시세는 우선주 코드로 따로 받는다"
    assert "/news/by-stock-code/005930" in paths, "뉴스는 본주 코드로 받는다"
    assert "/stock-info/analysis-tab/005930" in paths, "공시는 회사 것이라 본주 코드로 받는다"
    assert info["stock_info"]["name"] == "삼성전자", "info는 본주 페이로드다"
    assert own["stock_info"]["cur_prc"] == "196900", "own은 우선주 페이로드다"


def test_stock_info_summary_keeps_the_preferred_price_and_the_common_fundamentals():
    module = _load_stock_info_module()
    summary = module.build_summary(
        {"stock_code": "005935", "stock_name": "삼성전자우"},
        {"stock_info": {"name": "삼성전자", "cur_prc": "200000"},
         "financials": {"quarterly": [1]}, "primary_fs_type": "C"},
        None,
        None,
        own_info={"stock_info": {"name": "삼성전자우", "cur_prc": "196900"}},
    )

    assert summary["stock_info"]["cur_prc"] == "196900", "시세는 우선주 것이다"
    assert summary["financials"] == {"quarterly": [1]}, "실적은 본주 것이다"
    assert summary["preferred_of"] == "005930"
    assert summary["preferred_discount_pct"] == pytest.approx(-1.55, abs=0.01)


def test_stock_info_summary_is_unchanged_for_a_common_share():
    module = _load_stock_info_module()
    summary = module.build_summary(
        {"stock_code": "005930", "stock_name": "삼성전자"},
        {"stock_info": {"name": "삼성전자", "cur_prc": "200000"}},
        None,
        None,
    )

    assert summary["stock_info"]["cur_prc"] == "200000"
    assert summary["preferred_of"] is None
    assert summary["preferred_discount_pct"] is None


def test_stock_info_summary_keeps_after_hours_quotes_and_disclosures():
    """--json도 텍스트 출력과 같은 신규 필드를 싣는다 — 시간외가는 우선주면 자기 것이다."""
    module = _load_stock_info_module()
    summary = module.build_summary(
        {"stock_code": "005935", "stock_name": "삼성전자우"},
        {"stock_info": {"cur_prc": "200000"}, "after_hours_quote": {"cur_prc": "+201000"}},
        None,
        None,
        own_info={
            "stock_info": {"cur_prc": "150000"},
            "after_hours_quote": {"cur_prc": "+151000"},
            "nxt_quote": {"cur_prc": "+150500", "session": "after"},
        },
        analysis=ANALYSIS_TAB_PAYLOAD,
    )

    assert summary["after_hours_quote"]["cur_prc"] == "+151000"
    assert summary["nxt_quote"]["cur_prc"] == "+150500"
    assert [d["rcept_no"] for d in summary["disclosures"]] == [
        "20260818800754",
        "20260714000123",
    ]


def test_daily_digest_marks_importance_inline_instead_of_a_headline_section() -> None:
    """헤드라인 섹션은 섹터별 정리와 같은 내용을 두 번 쓰게 만들었다 (2026-09-21 제거).

    압축을 위 아래로 나누는 대신, 본문에서 중요한 항목에 🔥를 붙여 같은 자리에서 표시한다.
    표시는 ⭐가 아니라 🔥다 — 누적 컨텍스트가 테마 진척 강도에 ⭐/⭐⭐를 쓰고 있어, 같은 기호를
    쓰면 「오늘 중요」와 「테마 진척」이 한 화면에서 구분되지 않는다 (2026-09-21 사용자 결정).
    템플릿과 SKILL.md가 같이 바뀌어야 한다 — 한쪽만 고치면 모델이 없는 섹션을 채우려 든다.
    """
    template = (REPO_ROOT / "template" / "daily_digest.md").read_text(encoding="utf-8")
    skill = (SKILLS_ROOT / "daily-digest" / "SKILL.md").read_text(encoding="utf-8")

    # 헤드라인 섹션과 그 선별 단계가 두 파일 어디에도 남아 있으면 안 된다
    assert "헤드라인" not in template, "템플릿에 헤드라인 섹션이 남아 있다"
    assert "헤드라인" not in skill, "SKILL.md에 헤드라인 선별 단계가 남아 있다"
    assert "## 🎯" not in template

    # 대체 규칙: 본문 🔥 강조가 템플릿과 SKILL.md 양쪽에 문서화돼야 한다
    assert "🔥" in template, "템플릿이 🔥 강조 표기를 규정하지 않는다"
    assert "🔥" in skill, "SKILL.md가 🔥 강조 기준을 규정하지 않는다"
    assert "### 2-1단계: 오늘의 중요도 표시(🔥) 선별" in skill

    # 누적 컨텍스트 갱신 룰도 사라진 헤드라인이 아니라 🔥 항목을 입력으로 삼아야 한다
    assert "🔥를 붙인 항목 중" in skill

    # ⭐는 누적 컨텍스트 전용이다. 브리핑의 강조 기호로 되돌아오면 두 층이 다시 섞인다.
    # (금지 문구 안의 ⭐는 남아 있어야 하므로, 기호로 **쓰인** 자리만 본다.)
    assert "- ⭐" not in template, "템플릿이 ⭐를 강조 기호로 쓰고 있다"
    assert "`⭐`는 쓰지 않는다" in template, "템플릿에 ⭐ 금지 사유가 적혀 있지 않다"


def _load_valuation_decision_module():
    script_path = SKILLS_ROOT / "analyze-stock" / "scripts" / "valuation_decision.py"
    spec = importlib.util.spec_from_file_location("valuation_decision", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _downside_blocked_payload() -> dict:
    """하단 목표가가 현재가보다 높은(하방 막힌) 입력. 두 분모의 차이가 드러나는 자리다."""
    return {
        "asof": "2026-09-22",
        "current_price": 100.0,
        "consensus": {
            "low": 110.0, "average": 140.0, "high": 180.0,
            "coverage": 10, "latest_report_date": "2026-09-11",
            "recent_report_count": 5, "eps_revision": "flat",
        },
        "scenarios": {
            "bear": 100.0, "base": 140.0, "bull": 190.0,
            "probabilities": {"bear": 20, "base": 60, "bull": 20},
        },
    }


def test_reward_risk_denominator_is_separate_from_the_position_sizing_floor() -> None:
    """손익비 분모(15%)와 2%룰 분모(20%)는 다른 것을 잰다.

    20% 하한은 「매매규칙 6」의 전량 이탈선이라 1회 최대 손실을 정하고, 그래서
    「기본 원칙 4(2%룰)」의 분모로 남아야 한다. 손익비는 시나리오 하방 대비
    보상을 재는 값이므로 실행 손절선에 묶이면 기대수익의 재진술이 된다.
    """
    module = _load_valuation_decision_module()

    result = module.decide(_downside_blocked_payload())

    center_return = result["expected_returns"]["center"]
    risk = result["risk"]
    assert risk["downside"] < 0, "이 입력은 하방이 막혀 있어야 한다"
    assert risk["reward_risk_denominator"] == pytest.approx(module.MIN_REWARD_RISK_STOP)
    assert module.MIN_REWARD_RISK_STOP == pytest.approx(0.15)
    assert risk["reward_risk"] == pytest.approx(center_return / module.MIN_REWARD_RISK_STOP)
    # 2%룰과 실행 손절선은 그대로 20%에 묶여 있다.
    assert risk["effective_stop_loss"] == pytest.approx(module.MIN_STOP_LOSS)
    assert module.MIN_STOP_LOSS == pytest.approx(0.20)
    assert risk["max_purchase_fraction"] == pytest.approx(
        module.ACCOUNT_RISK_LIMIT / module.MIN_STOP_LOSS
    )
