"""Natural-language slot filling for resident actions.

This module collects user-provided fields but never executes an operation. Once
all required fields are present, it creates a pending action through ActionService;
execution still requires explicit confirmation of the returned action ID.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.actions.service import ActionError, ActionPreview, ActionService
from app.assistant.guardrails import classify_input

_META_REQUEST = re.compile(
    r"\b(prepare a valid|inspect its preview|test milestone|run this test|"
    r"show me how to test|example maintenance request)\b", re.I
)
_MAINTENANCE_TAIL = re.compile(
    r"[,;]?\s*(?:can you|could you|please|would you|will you)?\s*"
    r"(?:please\s+)?(?:arrange|schedule|organize|submit|create|raise|log|book)\s+"
    r"(?:a|the|some|an)?\s*(?:maintenance|repair|technician|fix|service)?\s*(?:request)?\s*[?.!]*$",
    re.I,
)
_MAINTENANCE_PREFIX = re.compile(
    r"^\s*(?:hi[, ]*)?(?:i(?:'m| am)?\s+)?(?:my\s+)?"
    r"(?:bathroom|kitchen|bedroom|room|air conditioner|ac|tap|sink|toilet|shower|"
    r"heater|window|door|light|wifi|wi-fi|fridge|refrigerator|washing machine)\b",
    re.I,
)
_BOOKING_ID = re.compile(r"\b([A-Z]{2,8}-\d{3,}|BKG[_-]?[A-Z0-9-]+)\b", re.I)


@dataclass
class Draft:
    action_type: str
    fields: dict[str, str] = field(default_factory=dict)
    waiting_for: str = ""


class NaturalLanguageActionIntake:
    """Single-conversation slot filler. Keep one instance per authenticated chat."""

    def __init__(self) -> None:
        self.draft: Draft | None = None

    @staticmethod
    def _clean(value: str) -> str:
        return " ".join((value or "").strip().split()).strip(" \t\r\n.,;:")

    @staticmethod
    def _extract_issue(message: str) -> str:
        text = message.strip()
        # Remove only the action request, preserving the resident's actual issue.
        text = re.sub(r"[,;]?\s*(?:can|could|would|will) you\s+", " ", text, flags=re.I)
        text = re.sub(
            r"\b(?:please\s+)?(?:arrange|schedule|organize|submit|create|raise|log)\s+"
            r"(?:a|the|some|an)?\s*(?:maintenance|repair|technician|fix|service)?\s*"
            r"(?:request)?\s*[?.!]*$", "", text, flags=re.I
        )
        text = re.sub(r"^\s*(?:hi[, ]*)?", "", text, flags=re.I)
        text = re.sub(r"\b(?:i need maintenance for|i need a repair for|please fix)\b", "", text, flags=re.I)
        return " ".join(text.strip(" ,.;?!").split())

    def _next_prompt(self) -> str:
        assert self.draft is not None
        d = self.draft
        if d.action_type == "maintenance":
            if not d.fields.get("description"):
                d.waiting_for = "description"
                return "What maintenance issue should I report? Please describe the problem."
            if not d.fields.get("property_name"):
                d.waiting_for = "property_name"
                return "Which property is this for? Please provide the property name."
            if not d.fields.get("room_number"):
                d.waiting_for = "room_number"
                return "What is your room number?"
            if not d.fields.get("urgency"):
                d.waiting_for = "urgency"
                return "Is this normal or urgent? Reply 'normal' or 'urgent'."
        elif d.action_type == "booking_cancellation" and not d.fields.get("booking_id"):
            options = d.fields.get("booking_options")
            if options:
                bookings = json.loads(options)
                d.waiting_for = "booking_choice"
                choices = "; ".join(f"{i + 1}. {b['booking_id']} — {b['property_name']} ({b['status']})" for i, b in
                                    enumerate(bookings))
                return "Which of your bookings would you like to cancel? Reply with the number or booking reference: " + choices
            d.waiting_for = "booking_id"
            return "I couldn't find a booking linked to your account in the local demo database. If this is a legacy booking, provide its booking ID or reference; nothing has been cancelled."
        d.waiting_for = ""
        return ""

    def begin(self, message: str, actor_id: str | None = None, actions: ActionService | None = None) -> str | None:
        """Start an action draft when the message contains a genuine action request."""
        gate = classify_input(message)
        if not gate.allowed or not gate.action_intent:
            return None
        if _META_REQUEST.search(message):
            return (
                "I can help with a real maintenance request or booking cancellation. "
                "Please describe the actual issue or provide the booking ID; I won't create an action from test instructions."
            )

        if gate.action_intent == "maintenance":
            issue = self._extract_issue(message)
            # Do not mistake generic requests or meta instructions for issue details.
            if len(issue) < 5 or re.fullmatch(
                    r"(?i)(please|can you|could you|i need maintenance|arrange maintenance|maintenance|repair|fix it)",
                    issue
            ):
                issue = ""
            self.draft = Draft("maintenance", {"description": issue} if issue else {})
            # Infer explicit urgency only when stated by the resident.
            if re.search(r"\b(urgent|emergency|flooding|dangerous|sparking)\b", message, re.I):
                self.draft.fields["urgency"] = "urgent"
            return self._next_prompt()

        self.draft = Draft("booking_cancellation")
        match = _BOOKING_ID.search(message)
        if match:
            self.draft.fields["booking_id"] = match.group(1).upper()
        elif actor_id and actions is not None and hasattr(actions, "list_bookings"):
            owned = [b for b in actions.list_bookings(actor_id) if b.get("status") == "active"]
            if owned:
                self.draft.fields["booking_options"] = json.dumps(owned)
        return self._next_prompt()

    def continue_draft(self, message: str, actor_id: str, actions: ActionService) -> str | ActionPreview | None:
        """Consume the next answer; returns prompt, preview, or None if no draft."""
        if self.draft is None:
            return None
        text = self._clean(message)
        if not text:
            return self._next_prompt()
        if text.lower() in {"start over", "forget it", "never mind", "nevermind", "cancel request"}:
            self.draft = None
            return "Okay, I discarded the unsubmitted action. No changes were made."

        d = self.draft
        # Repeated generic intent does not fill a requested slot.
        if d.waiting_for == "property_name" and re.search(
                r"\b(please arrange|arrange maintenance|please repair|maintenance)\b", text, re.I):
            return "I still need the property name before I can prepare the preview. Which property are you in?"
        if d.waiting_for == "description" and _META_REQUEST.search(text):
            return "Please describe the actual issue (for example, 'bathroom tap is leaking'). I have not created an action."

        if d.waiting_for == "description":
            d.fields["description"] = text
        elif d.waiting_for == "property_name":
            d.fields["property_name"] = text
        elif d.waiting_for == "room_number":
            # Accept 'room 402' or just '402'.
            match = re.search(r"\b(?:room\s*)?([A-Za-z0-9-]{1,20})\b", text, re.I)
            d.fields["room_number"] = match.group(1) if match else text
        elif d.waiting_for == "urgency":
            normalized = text.lower()
            if normalized in {"urgent", "emergency", "asap"}:
                d.fields["urgency"] = "urgent"
            elif normalized in {"normal", "not urgent", "standard"}:
                d.fields["urgency"] = "normal"
            else:
                return "Please reply 'normal' or 'urgent' so I can include it in the preview."
        elif d.waiting_for in {"booking_id", "booking_choice"}:
            match = _BOOKING_ID.search(text)
            if d.waiting_for == "booking_choice" and d.fields.get("booking_options"):
                options = json.loads(d.fields["booking_options"])
                if text.isdigit() and 1 <= int(text) <= len(options):
                    d.fields["booking_id"] = options[int(text) - 1]["booking_id"]
                elif match and any(b["booking_id"].casefold() == match.group(1).casefold() for b in options):
                    d.fields["booking_id"] = match.group(1).upper()
                else:
                    return "Please choose one of the listed booking numbers or references. No cancellation has been made."
            elif match:
                d.fields["booking_id"] = match.group(1).upper()
            else:
                return "That doesn't look like a booking ID. Please choose a listed booking or provide its booking reference, or type 'never mind'."

        prompt = self._next_prompt()
        if prompt:
            return prompt

        try:
            if d.action_type == "maintenance":
                preview = actions.prepare_maintenance(
                    actor_id,
                    d.fields["property_name"],
                    d.fields["room_number"],
                    d.fields["description"],
                    d.fields["urgency"],
                )
            else:
                preview = actions.prepare_cancellation(actor_id, d.fields["booking_id"])
        except ActionError as exc:
            self.draft = None
            return str(exc)
        self.draft = None
        return preview
