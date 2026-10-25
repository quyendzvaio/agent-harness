from __future__ import annotations

import re

from agent_harness.domain.evidence import CITATION_PATTERN, CitationReport
from agent_harness.domain.policies import CitationPolicy

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_CODE_BLOCK = re.compile(r"```.*?```", re.S)
_INFERENCE_HEADING = re.compile(r"^#{1,6}\s")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:!?])")


def cited_sentences(text: str, policy: CitationPolicy | None = None) -> list[tuple[str, tuple[str, ...]]]:
    """Sentences (outside code blocks and inference sections) together with the labels they cite."""
    policy = policy or CitationPolicy()
    found: list[tuple[str, tuple[str, ...]]] = []
    in_inference_section = False
    for raw in _SENTENCE_SPLIT.split(_CODE_BLOCK.sub(" ", text)):
        sentence = raw.strip()
        if not sentence:
            continue
        if _INFERENCE_HEADING.match(sentence):
            in_inference_section = any(marker in sentence.lower() for marker in policy.inference_markers)
            continue
        labels = tuple(dict.fromkeys(CITATION_PATTERN.findall(sentence)))
        if labels and not in_inference_section:
            plain = _SPACE_BEFORE_PUNCT.sub(r"\1", re.sub(r"\s{2,}", " ", CITATION_PATTERN.sub("", sentence))).strip()
            found.append((plain, labels))
    return found


def validate_citations(text: str, known_labels: set[str], policy: CitationPolicy | None = None) -> CitationReport:
    policy = policy or CitationPolicy()
    cited = CITATION_PATTERN.findall(text)
    valid = tuple(dict.fromkeys(label for label in cited if label in known_labels))
    invalid = tuple(dict.fromkeys(label for label in cited if label not in known_labels))

    prose = _CODE_BLOCK.sub(" ", text)
    total = uncited = 0
    in_inference_section = False
    for raw in _SENTENCE_SPLIT.split(prose):
        sentence = raw.strip()
        if not sentence:
            continue
        lowered = sentence.lower()
        if _INFERENCE_HEADING.match(sentence):
            in_inference_section = any(marker in lowered for marker in policy.inference_markers)
            continue
        if len(sentence) < policy.min_claim_chars:
            continue
        if in_inference_section or any(lowered.lstrip("-* ").startswith(m) for m in policy.inference_markers):
            continue
        total += 1
        if not CITATION_PATTERN.search(sentence):
            uncited += 1
    return CitationReport(valid=valid, invalid=invalid, uncited_sentences=uncited, total_sentences=total)
