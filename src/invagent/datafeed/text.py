"""종목명·파일명 비교용 문자열 정규화.

macOS 파일명은 NFD로 저장되고, 같은 종목이 시트·아카이브·API에서 서로 다른 띄어쓰기와
괄호로 적힌다. 비교 전에 항상 여기를 통과시킨다.
"""

from __future__ import annotations

import re
import unicodedata

_PUNCT_RE = re.compile(r"[\s._·\-()\[\]{},;:'\"]+")


def nfc(text: str) -> str:
    """macOS 파일명은 NFD로 저장된다. 비교 전 NFC로 정규화한다."""
    return unicodedata.normalize("NFC", text)


def norm(text: str) -> str:
    """비교용 키 — NFC + 소문자 + 공백/구두점 제거."""
    return _PUNCT_RE.sub("", nfc(text).lower())
