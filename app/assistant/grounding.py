"""Application-level citation validation for generated answers.

Citations improve traceability but are not a mathematical proof of entailment. The
LLM must cite source IDs actually supplied to it; unsupported or malformed answers
fail closed to the assignment's unknown-answer response.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.assistant.guardrails import UNKNOWN_ANSWER
from app.schemas import Evidence

_SOURCE_LINE = re.compile(r"^\s*Sources\s*:\s*(.*?)\s*$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class GroundingResult:
    valid: bool
    answer: str
    cited_sources: tuple[str, ...] = ()
    reason: str = "valid"


def validate_grounding(answer: str, evidence: list[Evidence]) -> GroundingResult:
    if not answer or not answer.strip():
        return GroundingResult(False, UNKNOWN_ANSWER, reason="empty_answer")
    cleaned = answer.strip()
    if cleaned.casefold() == UNKNOWN_ANSWER.casefold():
        return GroundingResult(True, UNKNOWN_ANSWER, reason="model_abstained")
    match = _SOURCE_LINE.search(cleaned)
    if not match:
        return GroundingResult(False, UNKNOWN_ANSWER, reason="missing_citations")
    cited = tuple(dict.fromkeys(x.strip() for x in re.split(r"[,;]", match.group(1)) if x.strip()))
    allowed = {item.source for item in evidence}
    if not cited:
        return GroundingResult(False, UNKNOWN_ANSWER, reason="empty_citations")
    if any(source not in allowed for source in cited):
        return GroundingResult(False, UNKNOWN_ANSWER, reason="unknown_citation")
    body = _SOURCE_LINE.sub("", cleaned).strip()
    if not body:
        return GroundingResult(False, UNKNOWN_ANSWER, reason="missing_answer_body")
    return GroundingResult(True, body, cited_sources=cited)
