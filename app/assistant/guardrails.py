"""Deterministic pre-LLM gates. Defense in depth; not a complete security boundary."""
import re
from dataclasses import dataclass

UNKNOWN_ANSWER = "I do not have that information."

# Keep patterns focused on explicit attempts to override policy or expose instructions.
# Natural-language classifiers are inherently incomplete; authorization must be enforced
# independently and these patterns must not be treated as the security boundary.
_INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|system|developer) instructions",
    r"disregard (all |any |the )?(previous|prior|above|system|developer) instructions",
    r"forget (all |the )?(previous|prior|above|system) instructions",
    r"(reveal|print|show|display|output|repeat|dump|expose) (your |the )?(full |hidden |system |developer )?(system prompt|hidden instructions|developer message|system message|prompt|instructions)",
    r"repeat back everything above",
    r"act as (a |an )?(general[- ]purpose|unrestricted|uncensored)",
    r"pretend .* (rule|restriction|guardrail|policy) .* (does not|doesn't|no longer) apply",
    r"bypass (the |your )?(safety|security|guardrails|policy|restrictions)",
    r"(ignore|override|bypass) (the |your )?(safety rules|guardrails|policy)",
    r"you are now (a |an )?(different|unrestricted|uncensored|general[- ]purpose)",
]
_PERSONAL_INFO_CHANGE_PATTERNS = [
    r"\b(change|update|correct|edit|replace)\b.{0,60}\b(my|the)\b.{0,30}\b(email|phone|mobile|address|name|date of birth|personal details)\b",
    r"\b(change|update|correct|edit|replace)\b.{0,60}\b(email address|phone number|contact details)\b",
]
_PRIVATE_PATTERNS = [
    r"another resident",
    r"everyone currently living",
    r"resident id",
    r"room number of everyone",
    r"my friend .* (rent|billing|booking|move[d]? in)",
    r"full contents of your system prompt",
    r"\b(someone else'?s|another person'?s|other resident'?s|other tenant'?s)\b.{0,80}\b(rent|billing|booking|ticket|room|address|move.?in|personal data|phone|email)\b",
    r"\b(show|list|give|tell me)\b.{0,50}\b(other residents|all residents|all tenants)\b.{0,50}\b(room|rent|id|billing|booking|personal)\b",
]
_OUT_OF_SCOPE_PATTERNS = [
    r"\b(exchange rate|stock price|investment advice|tell me a joke|capital of|weather today)\b",
    r"\b(legal advice|sue them|financial advice|invest my money)\b",
    r"\b(should i sue|sue my landlord|sue the landlord|take my landlord to court)\b",
    r"\b(you are now|act as|speak like) (a |an )?(pirate|comedian|poet|celebrity|doctor|lawyer)\b",
]
_ACTION_PATTERNS = {
    "maintenance": re.compile(r"\b(maintenance|repair|broken|not working|fix)\b", re.I),
    # Mentions of cancellation in a policy question are not an action request.
    "cancellation": re.compile(
        r"\b(?:please\s+)?cancel\s+(?:my|the|this|upcoming)\s+"
        r"(?:current\s+|upcoming\s+)?(?:booking|reservation|stay)\b"
        r"|\b(?:cancel|cancellation)\b.{0,35}\b(?:booking|reservation)\s+"
        r"(?:id\s*)?(?:BKG[_-]?[A-Z0-9-]+|[A-Z]{2,8}-\d{3,})\b",
        re.I,
    ),
}


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    reason: str = "allowed"
    action_intent: str | None = None


def classify_input(message: str) -> GateResult:
    text = (message or "").strip().lower()
    if not text:
        return GateResult(False, "empty_input")
    if any(re.search(pattern, text) for pattern in _INJECTION_PATTERNS):
        return GateResult(False, "prompt_injection")
    if any(re.search(pattern, text) for pattern in _PRIVATE_PATTERNS):
        return GateResult(False, "private_data_request")
    if any(re.search(pattern, text) for pattern in _PERSONAL_INFO_CHANGE_PATTERNS):
        return GateResult(False, "personal_info_change")
    if any(re.search(pattern, text) for pattern in _OUT_OF_SCOPE_PATTERNS):
        return GateResult(False, "out_of_scope")
    for action, pattern in _ACTION_PATTERNS.items():
        if pattern.search(text):
            return GateResult(True, "action_workflow", action)
    return GateResult(True)
