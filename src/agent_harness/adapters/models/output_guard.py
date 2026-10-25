"""Detect model output that must never reach the user: leaked chain-of-thought and repetition loops.

Some reasoning models (gpt-oss behind OpenAI-compatible gateways) lose their output format in long
tool-calling contexts: the planning text lands in `content` ("We need to produce a summary...") and
can loop on one sentence for thousands of tokens. Plain functions, no framework.
"""

from __future__ import annotations

import re
from collections import Counter

#: First line of a message that reads as the model planning its answer instead of answering.
_LEAKED_PLAN = re.compile(
    r"^(the user (asks|asked|wants|just|is|gave|said|wrote)\b"
    r"|we (need|should|must|have) (to )?(produce|write|answer|respond|summari[sz]e|provide|mention|include|check|read|output|give)\b"
    r"|we have (a lot|already|read|enough|now)\b"
    r"|let me (think|check|look|first)\b"
    r"|ok(ay)?, (so|let'?s|we|now)\b)",
    re.I,
)
_TRIVIAL = re.compile(r"^[\s|:\-–—=*_#>`.,;!?()\[\]{}]*$")  # table rules, separators, bullets

LEAK_MIN_CHARS = 150
LOOP_WINDOW_LINES = 80
LOOP_MIN_LINE_CHARS = 20
LOOP_REPEATS = 6


def check(text: str) -> str | None:
    """Why `text` (a message so far, or complete) is degenerate, or None when it looks fine."""
    stripped = text.lstrip()
    if len(stripped) >= LEAK_MIN_CHARS:
        first_line = stripped.split("\n", 1)[0].strip()
        if _LEAKED_PLAN.match(first_line):
            return f"leaked reasoning: {first_line[:80]!r}"
    lines = [line.strip() for line in text.splitlines()[-LOOP_WINDOW_LINES:]]
    counts = Counter(line for line in lines if len(line) >= LOOP_MIN_LINE_CHARS and not _TRIVIAL.match(line))
    if counts:
        line, repeats = counts.most_common(1)[0]
        if repeats >= LOOP_REPEATS:
            return f"repetition loop: {line[:80]!r} x{repeats}"
    return None
