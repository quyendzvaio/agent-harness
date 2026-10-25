"""Semantic citation check: does the cited evidence actually support the sentence?

The harness only defines the contract. Plug in any classifier (e.g. a Jev `check`/`classify`
call) by implementing `ClaimVerifier` and passing it as `Overrides.claim_verifier`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from agent_harness.domain.evidence import Evidence

Verdict = Literal["supported", "unsupported", "uncertain"]


@dataclass(frozen=True, slots=True)
class Claim:
    sentence: str
    #: The evidence the sentence cites (only labels that exist).
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class ClaimVerdict:
    claim: Claim
    verdict: Verdict
    #: Calibrated probability that the claim is supported, when the verifier provides one.
    probability: float | None = None
    note: str = ""


class ClaimVerifier(Protocol):
    async def verify(self, claims: list[Claim]) -> list[ClaimVerdict]:
        """Return one verdict per claim, in order. Batch all claims in one call where possible."""
        ...
