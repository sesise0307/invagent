"""Validate shared Claude Code and Codex repository configuration."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

from tests._helpers import _load_http_cache_module
from tests.conftest import load_script

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
    assert "--inherit --expected-sha256" in skill
    assert "report_update.py" not in skill
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


def _my_rules() -> str:
    return (REPO_ROOT / "context" / "my_rules.md").read_text(encoding="utf-8")


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
    module = load_script("daily-digest", "fetch_market_signals")
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
    module = load_script("daily-digest", "fetch_market_signals")
    skill = (SKILLS_ROOT / "daily-digest" / "SKILL.md").read_text(encoding="utf-8")

    assert "`DRAWDOWN_RUNGS`" in skill
    assert f"`RUNG_RECLAIM_DAYS` | {module.RUNG_RECLAIM_DAYS}" in skill
    assert f"`PEAK_RESET_REBOUND_PCT` | {module.PEAK_RESET_REBOUND_PCT:g}%" in skill
    # 되풀이 발동 실측과 T-1 근거가 함께 남아야 한다.
    assert "08-06" in skill and "T-1" in skill
    assert "오늘 처음 밟은 단" in skill


def test_margin_call_window_rationale_is_documented() -> None:
    """창 길이는 이 스킬의 운영 선택이다 — 왜 120인지가 남아야 다음 사람이 바꿀 수 있다."""
    module = load_script("daily-digest", "fetch_market_signals")
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
    # 이름 검색은 네이버가 먼저다 — 스크립트가 StockEasy 검색을 직접 부르면 요청 한도를 다시 깎는다.
    stock_info = (SKILLS_ROOT / "analyze-stock" / "scripts" / "fetch_stock_info.py").read_text(
        encoding="utf-8"
    )
    assert "tickers.resolve_stock(" in stock_info
    assert "stockeasy.resolve_stock(" not in stock_info
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
    module = load_script("daily-digest", "extract_portfolio")
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


def test_account_mdd_action_text_matches_my_rules(tmp_path: Path) -> None:
    """경고에 붙는 대응 문구는 「기본 원칙 13」 원문에서 온다 — 룰이 바뀌면 같이 바뀌어야 한다."""
    module = load_script("daily-digest", "peak_drawdown")
    rules = (REPO_ROOT / "context" / "my_rules.md").read_text(encoding="utf-8")

    assert set(module.ACCOUNT_MDD_ACTION) == set(module.ACCOUNT_MDD_BANDS)
    assert "레버리지를 모두 정리하고 신규 매수를 중단" in rules
    assert "현금 비중을 30% 이상 확보" in rules
    assert "5거래일 동안 신규 매수를 금지" in rules
    assert "복기한 뒤에만 매매를 재개" in rules
    assert "신규 매수 중단은 지수가 더 하락하지 않고 반등하기 전까지 유지" in rules
    assert "레버리지 전량 정리" in module.ACCOUNT_MDD_ACTION[-10.0]
    assert "신규 매수 중단(지수 하락 멈추고 반등 전까지)" in module.ACCOUNT_MDD_ACTION[-10.0]
    assert "5거래일 신규 매수 금지" in module.ACCOUNT_MDD_ACTION[-15.0]
    assert "5거래일 매수 금지는 개별 종목에만 적용" in rules
    assert "레버리지가 아닌 지수 ETF는 중단·금지 기간에도 바텀피싱" in rules
    assert "개별 종목 한정" in module.ACCOUNT_MDD_ACTION[-15.0]
    assert "지수 ETF 바텀피싱 분할 매수 허용" in module.ACCOUNT_MDD_ACTION[-15.0]
    assert "-10% 신규 매수 중단과 -15% 5거래일 매수 금지는 개별 종목에만 적용" in rules
    assert "개별 종목 신규 매수 중단(지수 하락 멈추고 반등 전까지)" in module.ACCOUNT_MDD_ACTION[-10.0]


FLAT_ARCHIVE_PATH_RE = re.compile(
    r"portfolio/(?:\$\(date \+%Y-%m-%d\)|[<{](?:yyyy-mm-dd|today|날짜|어제|오늘)[>}]|\*\.md|20\d\d-\d\d-\d\d)"
    r"|daily-digest/(?:[<{](?:yyyy-mm|YYYY-MM)[>}]|20\d\d-\d\d/)"
)


def test_docs_use_the_year_month_archive_layout() -> None:
    """스냅샷·브리핑은 `<yyyy>/<mm>/<날짜>.md`다 — 평면 경로를 안내하는 문서가 남으면 파일을 못 찾는다."""
    docs = (
        sorted(SKILLS_ROOT.rglob("*.md"))
        + sorted(SKILLS_ROOT.glob("*/scripts/*.py"))
        + sorted((REPO_ROOT / "template").rglob("*.md"))
        + sorted((REPO_ROOT / "src").rglob("*.py"))
        + [REPO_ROOT / "AGENTS.md"]
    )
    stale = [
        f"{path.relative_to(REPO_ROOT)}:{n}: {line.strip()}"
        for path in docs
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if FLAT_ARCHIVE_PATH_RE.search(line)
    ]

    assert stale == []
    for name in ("daily-digest", "analyze-stock", "advice", "weekly-investment-review"):
        skill = (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")
        assert "<yyyy>/<mm>/" in skill or "{yyyy}/{mm}/" in skill, name


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


def _weekly_skill() -> str:
    return (SKILLS_ROOT / "weekly-investment-review" / "SKILL.md").read_text(encoding="utf-8")


def test_review_skills_file_results_by_year_and_month() -> None:
    """주간·월간 리뷰도 스냅샷·브리핑처럼 `output/reviews/<yyyy>/<mm>/`에 쌓는다."""
    weekly = (SKILLS_ROOT / "weekly-investment-review" / "SKILL.md").read_text(encoding="utf-8")
    monthly = (SKILLS_ROOT / "monthly-investment-review" / "SKILL.md").read_text(encoding="utf-8")

    assert "output/reviews/<yyyy>/<mm>/<yyyy-mm-dd>_weekly.md" in weekly
    # 주간 파일 날짜는 그 주 일요일 (사용자 결정 2026-10-04)
    assert "그 주 **일요일**" in weekly
    assert "2026-09-20_weekly.md" in weekly
    assert "output/reviews/<yyyy>/<mm>/<yyyy-mm>_monthly.md" in monthly
    # 병합본도 같은 폴더에 둔다 — output/ 루트에 흩어지지 않게
    assert "--proposed output/weekly-review.md" not in weekly
    assert "--prepared-out output/2026-09_merged.md" not in weekly
    assert "_weekly_merged.md" in weekly
    assert "_monthly_merged.md" in monthly


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


def test_analyze_stock_skill_envelope_example_validates():
    module = load_script("analyze-stock", "validate_inputs")
    skill = (SKILLS_ROOT / "analyze-stock" / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r"\*\*입력 봉투\*\*.*?```json\n(.*?)```", skill, re.S)
    assert match, "SKILL.md 9단계에 「입력 봉투」 json 예시 블록이 없다"

    result = module.validate_document(json.loads(match.group(1)))

    assert result["valid"] is True, result["errors"]
    assert result["observations"]["consensus_average"]["kind"] == "estimate"


def test_market_regime_constants_match_my_rules_and_skill() -> None:
    """「시장 국면」 급락 정의는 룰 원문·스크립트 상수·SKILL.md 표가 같아야 한다."""
    module = load_script("daily-digest", "fetch_market_signals")
    rules = (REPO_ROOT / "context" / "my_rules.md").read_text(encoding="utf-8")
    skill = (SKILLS_ROOT / "daily-digest" / "SKILL.md").read_text(encoding="utf-8")

    assert f"{module.CRASH_STREAK_DAYS}거래일 연속 하락한 뒤 그날 종가가 {module.CRASH_DROP_PCT:g}% 넘게 하락" in rules
    assert f"+{module.CRASH_MARGIN_SIGMA:g}σ 이상" in rules
    for name, value in (
        ("CRASH_STREAK_DAYS", module.CRASH_STREAK_DAYS),
        ("CRASH_DROP_PCT", f"{module.CRASH_DROP_PCT:g}%"),
        ("CRASH_MARGIN_SIGMA", f"+{module.CRASH_MARGIN_SIGMA:g}σ"),
    ):
        assert f"| `{name}` | {value} |" in skill, name
