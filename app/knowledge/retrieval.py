"""Approved-source retrieval with query-facet expansion and conservative evidence scoring.

Structured listing facts remain authoritative. Policy text is split into small sections.
Private support tickets are intentionally never loaded into the global knowledge index.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.schemas import Evidence

_WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "can", "could",
    "do", "does", "for", "from", "give", "i", "in", "is", "it", "me", "my", "of",
    "on", "or", "please", "should", "tell", "that", "the", "their", "them", "there",
    "this", "to", "what", "whats", "when", "where", "which", "who", "why", "will",
    "with", "would", "you", "your", "how", "much", "long", "about", "still", "after", "exact",
    "before", "supposed", "currently", "doesnt", "dont", "following", "next", "year", "me", "first",
    "without", "asking", "please", "month", "months", "night", "nights", "stay", "bring", "paid",
}
_SYNONYMS = {
    "rent": {"rental", "monthly", "price", "cost", "fee"},
    "deposit": {"security", "bond"},
    "refund": {"refundable", "refunded", "reimbursement", "return"},
    "available": {"availability", "vacant", "vacancy", "open"},
    "minimum": {"min", "shortest", "least"},
    "cancel": {"cancellation", "cancelled", "terminate"},
    "maintenance": {"repair", "broken", "fix", "service"},
    "guest": {"visitor", "visitors", "overnight"},
    "late": {"overdue", "grace"},
    "pet": {"pets", "dog", "cat", "animal", "petfriendly"},
    "wifi": {"internet", "wireless"},
    "moveout": {"departure", "move", "moving"},
    "room": {"studio", "twin", "single", "double"},
    "stay": {"lease", "contract"},
}
_ALIAS_TO_CANONICAL = {alias: canonical for canonical, aliases in _SYNONYMS.items() for alias in aliases}


def tokenize(text: str) -> set[str]:
    raw = set(_WORD_RE.findall(text.lower())) - _STOP_WORDS
    return {_ALIAS_TO_CANONICAL.get(token, token) for token in raw}


def split_question_facets(question: str, property_names: tuple[str, ...] = ()) -> list[str]:
    """Split clearly distinct asks while retaining the original query for context.

    This is intentionally conservative. It does not try to infer answers or intents;
    it creates retrieval queries only. The original question is always included.
    """
    parts = [p.strip(" \t\n-•") for p in re.split(r"[?;\n]+", question) if p.strip(" \t\n-•")]
    # Split coordinated asks such as "... available, and what is the rent?".
    expanded: list[str] = []
    for part in parts:
        clauses = re.split(r",?\s+and\s+(?=(?:what|when|where|how much|how long|is|are|does|do|can|will|which)\b)",
                           part, flags=re.I)
        if len(clauses) > 1:
            # Carry named entities / room descriptors into elliptical follow-up clauses.
            prefix = clauses[0].strip()
            expanded.append(prefix)
            entity_match = re.search(
                r"(?:at|in|for)\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})", prefix
            )
            room_match = re.search(
                r"\b(studio|shared twin room|twin room|private single room|single room|double room)\b", prefix, re.I)
            carry = " ".join(x.group(0) for x in (entity_match, room_match) if x)
            for clause in clauses[1:]:
                # If the follow-up clause already names its own property/room, do not
                # contaminate it with entities from the preceding question.
                clause_has_entity = any(name.lower() in clause.lower() for name in property_names)
                clause_has_room = bool(re.search(r"\b(studio|twin|single|double|private|shared)\b", clause, re.I))
                expanded.append(
                    clause.strip() if (clause_has_entity or clause_has_room) else f"{carry} {clause.strip()}".strip())
        else:
            expanded.append(part)
    # Keep exact original as a retrieval query; unique normalized strings only.
    result = [question.strip()]
    for part in expanded:
        if part and part.lower() not in {x.lower() for x in result}:
            result.append(part)
    return result


def _listing_text(item: dict[str, Any]) -> str:
    parts = [
        f"Property: {item.get('property_name')}",
        f"Room type: {item.get('room_type')}",
        f"City: {item.get('city')}",
        f"Status: {item.get('status')}",
        f"Available from: {item.get('available_from') or 'not specified'}",
        f"Minimum stay: {item.get('min_stay_months')} months",
        f"Amenities: {', '.join(item.get('amenities', []))}",
    ]
    for key, value in item.items():
        if key.startswith("monthly_rent_"):
            parts.append(f"Monthly rent: {value} {key.rsplit('_', 1)[-1].upper()}")
        elif key.startswith("deposit_"):
            parts.append(f"Deposit: {value} {key.rsplit('_', 1)[-1].upper()}")
    return " | ".join(parts)


def _join_wrapped_lines(lines: list[str]) -> str:
    """Join Markdown soft-wrapped lines so facts split by formatting remain searchable."""
    if not lines:
        return ""
    normalized: list[str] = []
    for line in lines:
        line = line.strip()
        if normalized and line and not line.startswith(("* ", "- ", "## ")):
            normalized[-1] = normalized[-1].rstrip() + " " + line
        else:
            normalized.append(line)
    return "\\n".join(normalized).strip()


class KnowledgeBase:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.listings: list[dict[str, Any]] = json.loads(
            (self.data_dir / "listings.json").read_text(encoding="utf-8")
        )
        self.policy_chunks = self._load_policy_chunks()
        # support_tickets.json is deliberately never loaded here.

    def available_listings(self) -> list[dict[str, Any]]:
        """Return published listings marked available, ordered by move-in date and ID.

        This is based on the bundled listings dataset, not a live inventory API.
        Listings with status other than ``available`` or with no move-in date are
        excluded because they cannot be reliably offered as bookable options.
        """
        from datetime import date

        available: list[dict[str, Any]] = []
        for item in self.listings:
            if str(item.get("status", "")).strip().casefold() != "available":
                continue
            move_in = item.get("available_from")
            if not move_in:
                continue
            try:
                date.fromisoformat(str(move_in))
            except ValueError:
                continue
            available.append(item)
        return sorted(
            available,
            key=lambda item: (str(item.get("available_from") or "9999-12-31"),
                              str(item.get("listing_id") or "")),
        )

    def listing_evidence(self, listing: dict[str, Any]) -> Evidence:
        """Create a citation-ready evidence record for a structured listing."""
        return Evidence(
            source=f"listings.json:{listing.get('listing_id', 'unknown')}",
            text=_listing_text(listing),
            score=1.0,
            metadata={"listing_id": listing.get("listing_id"), "status": listing.get("status")},
        )

    def _load_policy_chunks(self) -> list[str]:
        raw = (self.data_dir / "house_rules.md").read_text(encoding="utf-8")
        chunks: list[str] = []
        current_heading = "General"
        paragraph_lines: list[str] = []
        for line in raw.splitlines():
            if line.startswith("## "):
                if paragraph_lines:
                    chunks.append(f"Section: {current_heading}\n" + _join_wrapped_lines(paragraph_lines))
                    paragraph_lines = []
                current_heading = line.removeprefix("## ").strip()
            elif line.strip():
                paragraph_lines.append(line.strip())
            elif paragraph_lines:
                chunks.append(f"Section: {current_heading}\n" + _join_wrapped_lines(paragraph_lines))
                paragraph_lines = []
        if paragraph_lines:
            chunks.append(f"Section: {current_heading}\n" + _join_wrapped_lines(paragraph_lines))
        return chunks

    @staticmethod
    def _score(query_tokens: set[str], text: str) -> float:
        """Coverage weighted by token rarity; output is a conservative 0..1 score."""
        doc_tokens = tokenize(text)
        if not query_tokens or not doc_tokens:
            return 0.0
        overlap = query_tokens.intersection(doc_tokens)
        # Query coverage prevents generic documents from scoring highly; rarity weights
        # make entity/attribute terms more valuable than common domain vocabulary.
        weights = {t: (2.0 if len(t) >= 7 else 1.0) for t in query_tokens}
        total = sum(weights.values())
        matched = sum(weights[t] for t in overlap)
        precision = len(overlap) / max(1, len(doc_tokens))
        return min(1.0, 0.82 * matched / total + 0.18 * precision * 8)

    def _retrieve_one(self, query: str, top_k: int) -> list[Evidence]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        property_names = {str(item.get("property_name", "")) for item in self.listings}
        lower_query = query.lower()
        matched_property_names = {name for name in property_names if name and name.lower() in lower_query}
        room_terms = {
            "studio": {"studio"},
            "shared twin room": {"shared", "twin"},
            "twin room": {"twin"},
            "private single room": {"private", "single"},
            "single room": {"single"},
            "double room": {"double"},
        }
        wanted_room_tokens: set[str] = set()
        for phrase, tokens in room_terms.items():
            if phrase in lower_query:
                wanted_room_tokens.update(tokenize(phrase))
        # Avoid letting a property name alone retrieve every record at that property.
        entity_tokens: set[str] = set()
        for name in matched_property_names:
            entity_tokens.update(tokenize(name))
        intent_tokens = query_tokens - entity_tokens
        if not intent_tokens:
            return []
        # The policy only says that pets are allowed at pet-friendly properties; it
        # does not identify which properties qualify. Do not let generic policy text
        # answer a property-specific pet question without an explicit listing attribute.
        if matched_property_names and "pet" in intent_tokens:
            if not any("pet_friendly" in item for item in self.listings
                       if item.get("property_name") in matched_property_names):
                return []

        results: list[Evidence] = []
        for listing in self.listings:
            if matched_property_names and listing.get("property_name") not in matched_property_names:
                continue
            listing_room_tokens = tokenize(str(listing.get("room_type", "")))
            if wanted_room_tokens and not wanted_room_tokens.issubset(listing_room_tokens):
                # Only enforce the room filter if a listing has room-type information.
                continue
            text = _listing_text(listing)
            score = self._score(intent_tokens, text)
            if score >= 0.10:
                results.append(Evidence(
                    source=f"listings.json:{listing.get('listing_id', 'unknown')}",
                    text=text,
                    score=score,
                    metadata={"listing_id": listing.get("listing_id"), "status": listing.get("status")},
                ))
        for idx, chunk in enumerate(self.policy_chunks, start=1):
            score = self._score(intent_tokens, chunk)
            if score >= 0.10:
                results.append(Evidence(source=f"house_rules.md:chunk-{idx}", text=chunk, score=score))
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:top_k]

    def retrieve(self, query: str, top_k: int = 6) -> list[Evidence]:
        """Retrieve across facets and merge with reciprocal-rank fusion.

        A single broad query can dilute the relevance of each part of a multi-part
        question. Per-facet retrieval increases recall; RRF combines rankings without
        pretending the lexical scores are calibrated probabilities.
        """
        facets = split_question_facets(query, tuple(str(x.get("property_name", "")) for x in self.listings))
        rankings: dict[str, tuple[Evidence, float]] = {}
        for facet in facets:
            for rank, evidence in enumerate(self._retrieve_one(facet, top_k=max(top_k, 4)), start=1):
                rrf = 1.0 / (60 + rank)
                old = rankings.get(evidence.source)
                if old:
                    best_evidence = evidence if evidence.score > old[0].score else old[0]
                    rankings[evidence.source] = (best_evidence, old[1] + rrf)
                else:
                    rankings[evidence.source] = (evidence, rrf)
        # Best per-facet relevance is primary; RRF is a small tie-breaker so a broad
        # original query cannot crowd a strong result from a distinct sub-question out.
        ordered = sorted(rankings.values(), key=lambda pair: pair[0].score + pair[1] * 0.1, reverse=True)
        return [item for item, _ in ordered[:top_k]]
