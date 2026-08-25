"""Validate shared Claude Code and Codex repository configuration."""

from __future__ import annotations

import importlib.util
import re
import sys
import tomllib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / ".agents" / "skills"
SKILL_NAMES = (
    "advice",
    "analyze-stock",
    "monthly-investment-review",
    "opendart",
    "summarize-telegram",
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

    assert "실효 손절폭  = max(하방, 15%)" in content
    assert "손익비      = 중심 기대수익 / 실효 손절폭" in content
    # 「기본 원칙 2」 단서(하방 막힘 시 30%)가 판정선에 들어와야 한다.
    assert "중심 30~50% & 하방 ≤ 0%" in content
    # 2%룰과 「매매규칙 9」 30% 상한 중 어느 쪽이 구속하는지 계산으로 갈린다.
    assert "포지션 상한 = min(2% / 실효 손절폭, 30%)" in content


def test_analyze_stock_wires_detected_signals_to_user_rules() -> None:
    """6단계가 잡은 서프라이즈·20주선 신호가 10단계 룰 환산까지 이어져야 한다."""
    content = (SKILLS_ROOT / "analyze-stock" / "SKILL.md").read_text(encoding="utf-8")

    assert "매매규칙 13" in content
    assert "기술적 분석 규칙 1" in content
    assert "확신도 판정" in content


def test_stock_analysis_template_has_target_price_block() -> None:
    """§5가 목표주가 산정의 정본이고, 12섹션 구조는 유지된다."""
    content = (REPO_ROOT / "template" / "stock_analysis.md").read_text(encoding="utf-8")

    assert "## 5. 밸류에이션 · 목표주가" in content
    for heading in ("### 5-A.", "### 5-B.", "### 5-C."):
        assert heading in content
    assert "종합 목표주가" in content
    assert "기대수익" in content
    assert "손익비" in content
    # §5-C·§1 모두 단일값이 아니라 하단/중심/상단을 요구한다.
    for point in ("하단", "중심", "상단"):
        assert point in content
    assert "범위 폭" in content
    assert "실효 손절폭" in content

    sections = re.findall(r"^## (\d+)\.", content, re.MULTILINE)
    assert [int(section) for section in sections] == list(range(1, 13))


def test_analyze_stock_inheritance_is_additive_in_one_rolling_file() -> None:
    """승계 보고서는 기존 파일명을 바꿔 이어 쓰며 별도 스냅샷을 만들지 않는다."""
    skill = (SKILLS_ROOT / "analyze-stock" / "SKILL.md").read_text(encoding="utf-8")
    template = (REPO_ROOT / "template" / "stock_analysis.md").read_text(encoding="utf-8")

    assert "### YYYY-MM-DD 업데이트" in skill
    assert "누적 파일 1개만 유지" in skill
    assert "파일명을 목표 경로로 변경" in skill
    assert "복사본을 만들지 않는다" in skill
    assert "불변 스냅샷" not in skill
    assert "기존 분석을 삭제·축약하지 않는다" in template
    assert "기존 분석 (보존)" in template


def test_project_codex_config_is_credential_free() -> None:
    config_path = REPO_ROOT / ".codex" / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))

    assert config["approval_policy"] == "on-request"
    assert config["sandbox_mode"] == "workspace-write"
    assert "mcp_servers" not in config


def _load_market_signal_module():
    script_path = SKILLS_ROOT / "summarize-telegram" / "scripts" / "fetch_market_signals.py"
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

    monkeypatch.setattr(module, "fetch_json", fake_fetch)
    monkeypatch.setattr(module, "load_cookie", lambda: cookie)
    return calls


def test_stock_info_endpoints_point_at_stockdata_api() -> None:
    """종목 데이터는 /stockdata/api/v1 경로에서 받는다."""
    module = _load_stock_info_module()

    assert module.API_BASE == "https://stockeasy.intellio.kr/stockdata/api/v1"
    assert set(module.ENDPOINTS) == {"search", "info_tab", "news", "reports"}
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

    monkeypatch.setattr(module, "fetch_json", fake_fetch)
    monkeypatch.setattr(module, "load_cookie", lambda: "session=stale")

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
    """저장소는 dotenv를 쓰지 않으므로 스크립트가 .env를 직접 훑는다."""
    module = _load_stock_info_module()
    monkeypatch.delenv(module.COOKIE_ENV, raising=False)

    scripts_dir = tmp_path / "skills" / "scripts"
    scripts_dir.mkdir(parents=True)
    (tmp_path / ".env").write_text(
        "# comment\nTELEGRAM_API_ID=1\nSTOCKEASY_COOKIE='session=from-env-file'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "__file__", str(scripts_dir / "fetch_stock_info.py"))

    assert module.load_cookie() == "session=from-env-file"

    monkeypatch.setenv(module.COOKIE_ENV, "session=from-environ")
    assert module.load_cookie() == "session=from-environ"


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
| 구분 |  계좌 | 섹터 | 종목 | 보유 | 평단 | 현재가 | 매수금액 | 평가금액 | 수익률 | 손익 | 비중 |  | 잔고 | ₩100,000,000 | 수익률 | 25.53% |
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
    script_path = SKILLS_ROOT / "summarize-telegram" / "scripts" / "extract_portfolio.py"
    spec = importlib.util.spec_from_file_location("extract_portfolio", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_portfolio_parser_extracts_first_sheet_only() -> None:
    module = _load_portfolio_module()
    snapshot = module.build_snapshot(PORTFOLIO_DUMP, "2026-08-09")

    assert "# 포트폴리오 스냅샷 — 2026-08-09" in snapshot
    assert "잔고 ₩100,000,000 · 수익률 25.53%" in snapshot
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

    assert "매매규칙 6(-15% 손절): 베타파마 -16.48% (비중 9.8%) ← **위반**" in snapshot
    assert "매매규칙 7(-8% 비중 축소 고려): 감마엔터 -9.20% (비중 2.7%)" in snapshot
    assert "매매규칙 11(24~30%↑ 익절 쿠션): 알파전자 +108.36% (비중 26.3%)" in snapshot
    assert "매매규칙 9(비중 30% 상한): 알파전자 26.3% ← 근접" in snapshot
    # 현금은 종목 수·룰 판정에서 제외
    assert "매매규칙 9(종목 수 5~12): 3종목 ← **미달**" in snapshot
    assert "현금 비중: 5.1% (₩5,000,000)" in snapshot


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
    archive = root / "telegram-daily"
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
    assert "기존 본문을 삭제·축약하지 않는다" in out

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
