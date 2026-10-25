"""Rule R5: claims should cite evidence; citations must point at evidence that exists."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CitationPolicy:
    #: Sentences shorter than this (headings, bullets like "Kết luận:") are not counted as claims.
    min_claim_chars: int = 40
    #: Lines starting with these markers are the model labelling its own inference.
    inference_markers: tuple[str, ...] = ("suy luận", "diễn giải", "inference", "interpretation", "nhận định của agent")
