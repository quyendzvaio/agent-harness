"""Deterministic intent detection (FR-5). The model never decides whether an export was requested."""

from __future__ import annotations

import re
import unicodedata

from agent_harness.domain.conversation import Intent
from agent_harness.domain.language import VIETNAMESE_CHARS

_URL = re.compile(r"https?://[^\s<>\"'`)\]]+", re.I)

_EXPORT_PHRASES = (
    # vi
    r"xu[aấ]t\s+(ra\s+)?(file|t[eệ]p|markdown|md|b[aá]o\s+c[aá]o)",
    r"l[uư]u\s+(l[aạ]i\s+)?(th[aà]nh\s+|ra\s+|v[aà]o\s+)?(file|t[eệ]p|markdown|md)",
    r"t[aạ]o\s+(file|t[eệ]p)",
    r"(ghi|vi[eế]t)\s+ra\s+(file|t[eệ]p)",
    r"t[aả]i\s+v[eề]\s+(file|d[uư][oớ]i\s+d[aạ]ng)",
    r"file\s+\.?md\b",
    # en
    r"\bexport\b",
    r"\bsave\s+(it\s+|this\s+|the\s+\w+\s+)?(as|to|into)\s+(a\s+)?(file|markdown|md)",
    r"\bdownload(able)?\b.*\b(file|markdown|md)\b",
    r"\b(write|generate|create)\s+(a\s+)?(markdown|md)\s+file",
)
_DIAGRAM_PHRASES = (
    # vi
    r"v[eẽ]\s+(ra\s+|l[aạ]i\s+|gi[uú]p\s+)?((cho\s+)?(t[oô]i|m[iì]nh)\s+)?(s[oơ]\s*[dđ][oồ]|bi[eể]u\s*[dđ][oồ]|ki[eế]n\s+tr[uú]c|lu[oồ]ng|diagram|h[iì]nh)",
    r"s[oơ]\s*[dđ][oồ]\s+(ki[eế]n\s+tr[uú]c|h[eệ]\s+th[oố]ng|lu[oồ]ng|th[aà]nh\s+ph[aầ]n|t[oổ]ng\s+th[eể])",
    # en
    r"\bdiagram\b",
    r"\bmermaid\b",
    r"\bdraw\b.{0,40}\b(architecture|flow|system|components?)\b",
)
_NEGATIONS = (r"kh[oô]ng\s+(c[aầ]n\s+|ph[aả]i\s+)?", r"[dđ][uừ]ng\s+", r"kh[oỏ]i\s+", r"ch[uư]a\s+c[aầ]n\s+",
              r"don'?t\s+", r"do\s+not\s+", r"no\s+need\s+to\s+", r"without\s+")  # fmt: skip
_EXPORT = re.compile("|".join(f"(?:{p})" for p in _EXPORT_PHRASES), re.I)
_DIAGRAM = re.compile("|".join(f"(?:{p})" for p in _DIAGRAM_PHRASES), re.I)
_NEGATED = re.compile(r"(?:" + "|".join(_NEGATIONS) + r")(?:\w+\s+){0,2}$", re.I)


def parse_intent(message: str) -> Intent:
    text = unicodedata.normalize("NFC", message)
    urls = tuple(dict.fromkeys(u.rstrip(".,;:!?") for u in _URL.findall(text)))
    without_urls = _URL.sub(" ", text)
    return Intent(
        urls=urls,
        explicit_export=_asks(_EXPORT, without_urls),
        wants_diagram=_asks(_DIAGRAM, without_urls),
        language=_language(without_urls),
    )


def _asks(pattern: re.Pattern[str], text: str) -> bool:
    for match in pattern.finditer(text):
        preceding = text[max(0, match.start() - 30) : match.start()]
        if not _NEGATED.search(preceding):
            return True
    return False


def _language(text: str) -> str:
    return "vi" if VIETNAMESE_CHARS.search(text) or not re.search(r"[a-z]{3,}", text, re.I) else "en"
