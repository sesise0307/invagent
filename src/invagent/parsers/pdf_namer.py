"""PDF filename categorization and normalization."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_SECTOR_KEYWORDS: tuple[str, ...] = (
    "2차전지",
    "ESS",
    "LNG",
    "건설",
    "건설부동산",
    "게임",
    "금융",
    "기계",
    "반도체",
    "바이오",
    "방산",
    "보험",
    "부동산",
    "소비재",
    "원전",
    "음식료",
    "유통",
    "인바운드",
    "자동차",
    "전력기기",
    "제약",
    "제약바이오",
    "조선",
    "지주",
    "철강",
    "카지노",
    "통신",
    "피부미용",
    "항공",
    "호텔",
    "화장품",
    "화학",
    "헬스케어",
    "엔터",
    "레저",
    "미디어",
    "인터넷",
)

_MARKET_KEYWORDS: tuple[str, ...] = (
    "daily",
    "weekly",
    "monthly",
    "biweekly",
    "monitor",
    "market",
    "today",
    "chart",
    "watch",
    "snapshot",
    "morning brief",
    "issue",
    "view",
    "qwer",
    "시황",
    "스냅샷",
    "주간",
    "데일리",
    "위클리",
    "마감시황",
)

_BROKERAGE_ALIASES: dict[str, str] = {
    "bnk": "BNK",
    "bnk투자": "BNK",
    "bnk투자증권": "BNK",
    "daishin": "대신",
    "daol": "다올",
    "daol투자": "다올",
    "daol투자증권": "다올",
    "ds": "DS",
    "ds투자": "DS",
    "ds투자증권": "DS",
    "hana": "하나",
    "hana증권": "하나",
    "ibk": "IBK",
    "ibk투자": "IBK",
    "ibk투자증권": "IBK",
    "kb": "KB",
    "kb증권": "KB",
    "kiwoom": "키움",
    "lg": "LG",
    "ls": "LS",
    "ls증권": "LS",
    "meritz": "메리츠",
    "mirae": "미래",
    "nh": "NH",
    "nh투자": "NH",
    "nh투자증권": "NH",
    "samsung": "삼성",
    "samsung증권": "삼성",
    "shinhan": "신한",
    "shinhan증권": "신한",
    "sk": "SK",
    "sk증권": "SK",
    "yuanta": "유안타",
    "교보": "교보",
    "교보증권": "교보",
    "대신": "대신",
    "대신증권": "대신",
    "다올투자": "다올",
    "다올투자증권": "다올",
    "리딩": "리딩",
    "리딩투자": "리딩",
    "리딩투자증권": "리딩",
    "메리츠": "메리츠",
    "메리츠증권": "메리츠",
    "미래": "미래",
    "미래에셋": "미래",
    "미래에셋증권": "미래",
    "삼성": "삼성",
    "삼성증권": "삼성",
    "상상인": "상상인",
    "상상인증권": "상상인",
    "신한": "신한",
    "신한증권": "신한",
    "신한투자": "신한",
    "신한투자증권": "신한",
    "유안타": "유안타",
    "유안타증권": "유안타",
    "유진": "유진",
    "유진투자": "유진",
    "유진투자증권": "유진",
    "키움": "키움",
    "키움증권": "키움",
    "하나": "하나",
    "하나증권": "하나",
    "한국투자": "한투",
    "한국투자증권": "한투",
    "한화": "한화",
    "한화투자": "한화",
    "한화투자증권": "한화",
    "현대차": "현대차",
    "현대차증권": "현대차",
}

_DATE_TOKEN_RE = re.compile(r"^(?:\d{6}|\d{8}|\d{4}-\d{2}-\d{2})$")
_TICKER_BRACKET_RE = re.compile(r"^(?P<name>.+?)[\[\(［](?P<code>\d{6})[\]\)］]$")
_LEADING_BRACKET_RE = re.compile(r"^\[(?P<name>[^\[\]]+)\](?:_(?P<rest>.*))?$")


@dataclass(frozen=True)
class _ParsedReport:
    category: str
    name: str
    brokerage: str | None = None
    title: str | None = None
    date: str | None = None


def _preprocess_filename(filename: str) -> str:
    """Normalize unicode and replace spaces/commas with underscores."""
    normalized = unicodedata.normalize("NFKC", filename)
    if not normalized.lower().endswith(".pdf"):
        return normalized

    stem = normalized[:-4]
    stem = re.sub(r"[\s,]+", "_", stem)
    stem = re.sub(r"_+", "_", stem).strip("_")
    return f"{stem}.pdf"


def _normalize_date(token: str) -> str | None:
    """Convert supported date tokens into yyyy-mm-dd."""
    cleaned = token.strip()
    if re.fullmatch(r"\d{6}", cleaned):
        return f"20{cleaned[:2]}-{cleaned[2:4]}-{cleaned[4:6]}"
    if re.fullmatch(r"\d{8}", cleaned):
        return f"{cleaned[:4]}-{cleaned[4:6]}-{cleaned[6:8]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        return cleaned
    return None


def _normalize_brokerage(token: str) -> str | None:
    """Map brokerage aliases and remove the trailing '증권'."""
    cleaned = token.strip("[]()")
    if not cleaned:
        return None

    normalized_key = cleaned.lower()
    if normalized_key in _BROKERAGE_ALIASES:
        return _BROKERAGE_ALIASES[normalized_key]

    if cleaned.endswith("증권"):
        stripped = cleaned[:-2]
        alias = _BROKERAGE_ALIASES.get(stripped.lower())
        return alias if alias else stripped

    return None


def _tokenize(stem: str) -> list[str]:
    return [token for token in stem.split("_") if token]


def _find_date(tokens: list[str]) -> tuple[str | None, int | None]:
    for index in range(len(tokens) - 1, -1, -1):
        date = _normalize_date(tokens[index])
        if date:
            return date, index
    return None, None


def _find_brokerage(tokens: list[str]) -> tuple[str | None, int | None]:
    for index in range(len(tokens) - 1, -1, -1):
        brokerage = _normalize_brokerage(tokens[index])
        if brokerage:
            return brokerage, index
    return None, None


def _looks_like_market(stem: str) -> bool:
    lowered = stem.lower().replace("_", " ")
    return any(keyword in lowered for keyword in _MARKET_KEYWORDS)


def _find_sector(tokens: list[str]) -> tuple[str | None, int | None]:
    for index, token in enumerate(tokens):
        exact_matches = []
        partial_matches = []
        for sector in _SECTOR_KEYWORDS:
            compact = sector.replace(" ", "")
            if compact == token:
                exact_matches.append(sector)
            elif compact in token:
                partial_matches.append(sector)
        if exact_matches:
            return max(exact_matches, key=len), index
        if partial_matches:
            return max(partial_matches, key=len), index
    return None, None


def _strip_known_parts(tokens: list[str], indices_to_drop: set[int]) -> list[str]:
    return [token for index, token in enumerate(tokens) if index not in indices_to_drop]


def _is_author_token(token: str) -> bool:
    return len(token) == 3 and all("\uAC00" <= char <= "\uD7A3" for char in token)


class PDFNamer:
    """Classify report filenames and convert them into a normalized format."""

    def get_filename(self, original_filename: str) -> str:
        """Return a normalized filename for PDF reports."""
        if not original_filename.lower().endswith(".pdf"):
            return original_filename

        filename = _preprocess_filename(original_filename)
        stem = filename[:-4]

        if stem.startswith("산업_"):
            industry_report = self._parse_industry_report(stem)
            if industry_report:
                return self._render_industry_filename(industry_report)

        company_report = self._parse_company_report(stem)
        if company_report:
            return self._render_company_filename(company_report)

        industry_report = self._parse_industry_report(stem)
        if industry_report:
            return self._render_industry_filename(industry_report)

        if _looks_like_market(stem):
            return f"시황_{filename}"

        return f"기타_{filename}"

    def _parse_industry_report(self, stem: str) -> _ParsedReport | None:
        tokens = _tokenize(stem)
        if not tokens:
            return None

        explicit_industry = tokens[0] == "산업"
        sector, sector_index = _find_sector(tokens[1:] if explicit_industry else tokens)
        if sector_index is not None and not explicit_industry:
            sector_index += 0

        if explicit_industry:
            if len(tokens) < 2:
                return None
            sector = tokens[1]
            sector_index = 1
        elif sector is None or sector_index is None:
            bracket_sector = self._extract_bracket_sector(tokens[0])
            if bracket_sector:
                sector = bracket_sector
                sector_index = 0
            else:
                return None

        date, date_index = _find_date(tokens)
        if not date:
            return None

        brokerage, brokerage_index = _find_brokerage(tokens)

        drop_indices = {date_index}
        if brokerage_index is not None:
            drop_indices.add(brokerage_index)

        if explicit_industry:
            drop_indices.add(0)
            drop_indices.add(1)
        elif sector_index is not None:
            drop_indices.add(sector_index)

        if tokens and _is_author_token(tokens[0]) and 0 not in drop_indices:
            drop_indices.add(0)

        title_tokens = _strip_known_parts(tokens, drop_indices)
        title = "_".join(token for token in title_tokens if token != sector).strip("_")
        if not title:
            title = "리포트"

        return _ParsedReport(
            category="산업",
            name=sector,
            brokerage=brokerage or "미상",
            title=title,
            date=date,
        )

    def _parse_company_report(self, stem: str) -> _ParsedReport | None:
        tokens = _tokenize(stem)
        if not tokens:
            return None

        if tokens[0] == "기업":
            return self._parse_explicit_company(tokens)

        bracket_match = _LEADING_BRACKET_RE.match(stem)
        if bracket_match:
            name = bracket_match.group("name")
            rest = bracket_match.group("rest") or ""
            date, _ = _find_date(_tokenize(rest))
            brokerage, _ = _find_brokerage(_tokenize(rest))
            return _ParsedReport(
                category="기업",
                name=name,
                brokerage=brokerage or "미상",
                date=date or "",
            )

        ticker_company = self._extract_ticker_company(tokens)
        if ticker_company:
            date, _ = _find_date(tokens)
            brokerage, _ = _find_brokerage(tokens)
            if not date:
                return None
            return _ParsedReport(
                category="기업",
                name=ticker_company,
                brokerage=brokerage or "미상",
                date=date,
            )

        if len(tokens) >= 3 and _normalize_date(tokens[0]):
            date = _normalize_date(tokens[0])
            brokerage = _normalize_brokerage(tokens[-1])
            if date and brokerage:
                return _ParsedReport(
                    category="기업",
                    name=tokens[1],
                    brokerage=brokerage,
                    date=date,
                )

        date, date_index = _find_date(tokens)
        if not date or date_index is None:
            return None

        brokerage, brokerage_index = _find_brokerage(tokens)
        if brokerage_index is not None and brokerage_index > date_index:
            brokerage_index = None
            brokerage = None

        if tokens[0] in {"산업", "시황", "기타"}:
            return None

        company_index = 0
        if _is_author_token(tokens[0]) and len(tokens) > 1:
            company_index = 2 if len(tokens) > 2 and re.fullmatch(r"[A-Za-z]+", tokens[1]) else 1

        company_token = tokens[company_index]
        ticker_match = _TICKER_BRACKET_RE.match(company_token)
        if ticker_match:
            company_token = ticker_match.group("name")

        if company_token in _SECTOR_KEYWORDS:
            return None
        if _looks_like_market(company_token):
            return None

        if brokerage_index is None:
            brokerage = "미상"

        return _ParsedReport(
            category="기업",
            name=company_token,
            brokerage=brokerage,
            date=date,
        )

    def _parse_explicit_company(self, tokens: list[str]) -> _ParsedReport | None:
        if len(tokens) < 3:
            return None

        date, _ = _find_date(tokens)
        if not date:
            return None

        brokerage, _ = _find_brokerage(tokens)
        company = tokens[1]

        if company in _SECTOR_KEYWORDS:
            return None

        return _ParsedReport(
            category="기업",
            name=company,
            brokerage=brokerage or "미상",
            date=date,
        )

    def _extract_ticker_company(self, tokens: list[str]) -> str | None:
        for index, token in enumerate(tokens):
            ticker_match = _TICKER_BRACKET_RE.match(token)
            if not ticker_match:
                continue

            leading_name = ticker_match.group("name")
            prefix_tokens = tokens[:index]
            if prefix_tokens:
                return "_".join([*prefix_tokens, leading_name]).strip("_")
            return leading_name
        return None

    def _extract_bracket_sector(self, token: str) -> str | None:
        bracket_match = _LEADING_BRACKET_RE.match(token)
        if not bracket_match:
            return None

        inside = bracket_match.group("name")
        for sector in _SECTOR_KEYWORDS:
            if sector in inside:
                return sector
        return None

    def _render_company_filename(self, report: _ParsedReport) -> str:
        return f"기업_{report.name}_{report.brokerage}_{report.date}.pdf"

    def _render_industry_filename(self, report: _ParsedReport) -> str:
        return f"산업_{report.name}_{report.brokerage}_{report.title}_{report.date}.pdf"
