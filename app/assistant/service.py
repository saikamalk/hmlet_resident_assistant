from __future__ import annotations

import re

from app.assistant.grounding import validate_grounding
from app.assistant.guardrails import classify_input, UNKNOWN_ANSWER
from app.assistant.llm import ChatModel
from app.core.config import settings
from app.knowledge.retrieval import KnowledgeBase
from app.schemas import AssistantResponse

SAFE_REDIRECT = "I can help with resident support questions about listings, bookings, billing policies, house rules, and maintenance."
TEMPORARY_FAILURE = "The assistant is temporarily unavailable. Please try again."
ACTION_HELP = (
    "I can help prepare that action, but it has not been submitted or changed. "
    "For a maintenance request, provide the property name, room number, issue description, and urgency. "
    "For a booking cancellation, provide your booking ID. I will show you an exact preview and ask for confirmation."
)


class ResidentAssistant:
    def __init__(self, knowledge: KnowledgeBase, model: ChatModel,
                 min_retrieval_score: float | None = None):
        self.knowledge = knowledge
        self.model = model
        self.min_retrieval_score = (
            settings.min_retrieval_score if min_retrieval_score is None else min_retrieval_score
        )

    def respond(self, message: str) -> AssistantResponse:
        gate = classify_input(message)
        if not gate.allowed:
            if gate.reason == "personal_info_change":
                return AssistantResponse(
                    "I can’t change personal details. Please contact the property team to update your information.",
                    status="refused",
                )
            if gate.reason in {"out_of_scope", "prompt_injection", "private_data_request"}:
                return AssistantResponse(SAFE_REDIRECT, status="refused")
            return AssistantResponse(SAFE_REDIRECT, status="refused")

        # Action requests are deliberately not executed through free-form chat.
        # The caller must invoke ActionService with trusted actor identity, gather
        # required fields, present a preview, and explicitly confirm its action ID.
        if gate.action_intent:
            return AssistantResponse(ACTION_HELP, status="action_required")

        if _is_broad_housing_inventory_question(message, self.knowledge.listings):
            listings = self.knowledge.available_listings()
            if not listings:
                return AssistantResponse(
                    "I couldn't find any listings marked available in the current listings dataset.",
                    status="insufficient_evidence",
                )
            evidence = [self.knowledge.listing_evidence(item) for item in listings]
            return AssistantResponse(
                _format_available_listings(listings),
                status="answered",
                evidence=tuple(evidence),
            )

        evidence = self.knowledge.retrieve(message, top_k=8)
        relevant = [item for item in evidence if item.score >= self.min_retrieval_score]
        if not relevant:
            return AssistantResponse(UNKNOWN_ANSWER, status="insufficient_evidence")

        try:
            raw_answer = self.model.answer(message, relevant)
        except Exception:
            return AssistantResponse(TEMPORARY_FAILURE, status="temporarily_unavailable")

        grounding = validate_grounding(raw_answer, relevant)
        if not grounding.valid:
            return AssistantResponse(
                UNKNOWN_ANSWER,
                status="grounding_failed",
                evidence=tuple(relevant),
            )
        if grounding.reason == "model_abstained":
            return AssistantResponse(UNKNOWN_ANSWER, status="insufficient_evidence", evidence=tuple(relevant))
        return AssistantResponse(
            grounding.answer,
            status="answered",
            evidence=tuple(item for item in relevant if item.source in grounding.cited_sources),
        )


_INVENTORY_TERMS = re.compile(
    r"\b(available|availability|vacant|housing|housings|accommodation|accommodations|"
    r"properties|property|listings|listing|rooms|room options|places to stay)\b",
    re.IGNORECASE,
)
_BROAD_INVENTORY_PHRASES = (
    "what are available", "what are the available", "what housing", "available housing", "available housings",
    "available accommodations",
    "available properties", "available listings", "all available", "show me available",
    "list available", "housing options", "room options", "what rooms are available",
    "what properties are available", "what listings are available", "accommodation options",
)


def _is_broad_housing_inventory_question(message: str, listings: list[dict] | None = None) -> bool:
    """Route broad inventory requests to structured data, not lexical RAG."""
    normalized = re.sub(r"[^a-z0-9]+", " ", message.casefold()).strip()
    if not _INVENTORY_TERMS.search(normalized):
        return False
    # Keep property-specific queries in ordinary retrieval; broad catalog wording
    # is the signal for a deterministic inventory response.
    for item in listings or []:
        name = str(item.get("property_name", "")).casefold().strip()
        if name and name in normalized:
            return False
    if any(phrase in normalized for phrase in _BROAD_INVENTORY_PHRASES):
        return True
    return any(term in normalized for term in ("all", "options", "details", "list", "show me")) and not re.search(
        r"\b(?:at|in)\s+[a-z]+(?:\s+[a-z]+){0,3}\b", normalized
    )


def _format_available_listings(listings: list[dict]) -> str:
    """Format structured listing attributes directly, without LLM paraphrasing."""
    lines = [
        f"I found {len(listings)} listing(s) marked available in the current listings dataset. "
        "These are dataset-listed options, not live inventory confirmation:"
    ]
    for item in listings:
        rent_key = next((key for key in item if key.startswith("monthly_rent_")), None)
        deposit_key = next((key for key in item if key.startswith("deposit_")), None)
        currency = rent_key.rsplit("_", 1)[-1].upper() if rent_key else ""
        rent = f"{item[rent_key]:,} {currency}/month" if rent_key else "not listed"
        deposit_currency = deposit_key.rsplit("_", 1)[-1].upper() if deposit_key else currency
        deposit = f"{item[deposit_key]:,} {deposit_currency}" if deposit_key else "not listed"
        amenities = ", ".join(item.get("amenities") or []) or "not listed"
        lines.extend([
            "",
            f"**{item.get('property_name', 'Unnamed property')} — {item.get('room_type', 'Room type not listed')}**",
            f"- Listing ID: {item.get('listing_id', 'not listed')}",
            f"- City: {item.get('city', 'not listed')}",
            f"- Monthly rent: {rent}",
            f"- Deposit: {deposit}",
            f"- Available from: {item.get('available_from') or 'not specified'}",
            f"- Minimum stay: {item.get('min_stay_months', 'not listed')} month(s)",
            f"- Amenities: {amenities}",
        ])
    lines.append(
        "\nFor date-specific booking, confirm the move-in date and stay length; the dataset does not provide live availability.")
    return "\n".join(lines)
