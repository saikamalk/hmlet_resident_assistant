"""Auditable, heuristic claim-to-evidence checks for evaluation and review.

This is a conservative heuristic, not a semantic entailment model. It should be used
for offline evaluation and triage; do not treat a passing result as proof of truth.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.schemas import Evidence

_SOURCE_LINE = re.compile(r"^\s*Sources\s*:\s*.*$", re.I | re.M)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_TOKEN = re.compile(r"[a-z][a-z0-9_-]*|\d+(?:[.,]\d+)*", re.I)
_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by", "can", "could",
    "did", "do", "does", "for", "from", "had", "has", "have", "he", "her", "here", "hers",
    "him", "his", "how", "i", "if", "in", "into", "is", "it", "its", "me", "my", "of", "on",
    "or", "our", "ours", "she", "should", "that", "the", "their", "them", "there", "these",
    "they", "this", "those", "to", "was", "we", "were", "what", "when", "where", "which",
    "who", "why", "will", "with", "would", "you", "your", "yours", "according", "listed",
    "provided", "source", "sources", "answer", "information", "property", "room", "monthly",
}


@dataclass(frozen=True)
class ClaimCheck:
    claim: str
    supported: bool
    overlap: float
    unsupported_numbers: tuple[str, ...] = ()
    reason: str = "supported_by_lexical_overlap"


@dataclass(frozen=True)
class GroundingAudit:
    passed: bool
    claims: tuple[ClaimCheck, ...]
    unsupported_claim_count: int


def _tokens(text: str) -> set[str]:
    return {x.casefold().replace(",", "") for x in _TOKEN.findall(text) if x.casefold() not in _STOP}


def _numbers(text: str) -> set[str]:
    # Normalize common numeric formatting: 1,450 and 1450 compare equally.
    return {x.replace(",", "").replace(".", "") for x in re.findall(r"(?<![A-Za-z])\d[\d,.]*(?![A-Za-z])", text)}


def _split_claims(answer: str) -> list[str]:
    body = _SOURCE_LINE.sub("", answer or "").strip()
    claims = []
    for line in body.splitlines():
        for part in _SENTENCE_SPLIT.split(line):
            part = re.sub(r"^\s*[-*•\d.)]+\s*", "", part).strip()
            if part and part.casefold() != "i do not have that information.":
                claims.append(part)
    return claims


def audit_claim_support(answer: str, evidence: Iterable[Evidence], *, min_overlap: float = 0.30) -> GroundingAudit:
    """Check each answer sentence against evidence using token overlap and number checks.

    `evidence` should contain only the evidence cited by the answer where possible.
    This function does not validate citation IDs; use app.grounding.validate_grounding
    for that separate check.
    """
    evidence_list = list(evidence)
    evidence_text = "\n".join(item.text for item in evidence_list)
    evidence_tokens = _tokens(evidence_text)
    evidence_numbers = _numbers(evidence_text)
    results: list[ClaimCheck] = []
    for claim in _split_claims(answer):
        claim_tokens = _tokens(claim)
        overlap = len(claim_tokens & evidence_tokens) / max(1, len(claim_tokens))
        claim_numbers = _numbers(claim)
        unsupported_numbers = tuple(sorted(claim_numbers - evidence_numbers))
        supported = bool(evidence_list) and overlap >= min_overlap and not unsupported_numbers
        if unsupported_numbers:
            reason = "unsupported_numeric_fact"
        elif not evidence_list:
            reason = "no_evidence"
        elif overlap < min_overlap:
            reason = "low_lexical_overlap_review_required"
        else:
            reason = "supported_by_lexical_overlap"
        results.append(ClaimCheck(claim, supported, round(overlap, 3), unsupported_numbers, reason))
    # Empty/abstaining answers are not unsupported claims, but should not count as a
    # successful factual answer-quality result in the quality evaluator.
    return GroundingAudit(
        passed=bool(results) and all(item.supported for item in results),
        claims=tuple(results),
        unsupported_claim_count=sum(not item.supported for item in results),
    )
