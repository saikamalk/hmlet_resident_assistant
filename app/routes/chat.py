"""Chat endpoint and resident action-intake orchestration."""
import hashlib
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.actions.intake import NaturalLanguageActionIntake
from app.actions.service import ActionError, ActionPreview
from app.api_dependencies import (
    confirm_pending_action, decline_pending_action, enforce_limit, get_actions, get_assistant, get_bookings, get_sessions,
)
from app.api_schemas import MessageRequest, MessageResponse
from app.bookings.service import BookingService
from app.core.auth import get_current_actor

router = APIRouter()


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return str(value) if value is not None else None


def _preview(preview: ActionPreview):
    return {"action_id": preview.action_id, "action_type": preview.action_type, "status": preview.status,
            "summary": preview.summary, "expires_at": preview.expires_at}


@router.post("/sessions/{session_id}/messages", response_model=MessageResponse)
def send_message(session_id: str, body: MessageRequest, request: Request, actor=Depends(get_current_actor),
                 sessions=Depends(get_sessions), actions=Depends(get_actions),
                 resident_assistant=Depends(get_assistant)):
    enforce_limit(request, actor)
    actor_id = actor["actor_id"]
    row = sessions.get(session_id, actor_id)
    if row is None: raise HTTPException(status_code=404, detail="Session not found.")
    text = body.message.strip()
    intake = NaturalLanguageActionIntake()
    intake.draft = sessions.load_draft(row)
    evidence_sources: list[str] = []
    try:
        if text.startswith("/confirm "):
            action_id = text[len("/confirm "):].strip()
            result = confirm_pending_action(request, actions, actor_id, action_id)
            answer, state, action = result.result.get("message", "Action completed."), result.status, {
                "action_id": result.action_id, "action_type": result.action_type, "status": result.status,
                "replayed": result.replayed}
            sessions.save_draft(session_id, actor_id, None)
        elif text.startswith("/decline "):
            action_id = text[len("/decline "):].strip()
            decline_pending_action(request, actions, actor_id, action_id)
            answer, state, action = "Action declined. No change was made.", "action_declined", {"action_id": action_id,
                                                                                                "status": "declined"}
            sessions.save_draft(session_id, actor_id, None)
        elif intake.draft is not None:
            if intake.draft.action_type == "booking_creation":
                service = get_bookings(request)
                outcome = service.continue_draft(intake.draft, text, actor_id)
                if isinstance(outcome, ActionPreview) or (
                        isinstance(outcome, str) and outcome.startswith("Okay, I discarded")):
                    intake.draft = None
            else:
                outcome = intake.continue_draft(text, actor_id, actions)
            sessions.save_draft(session_id, actor_id, intake.draft)
            if isinstance(outcome, ActionPreview):
                answer, state, action = "Review this exact preview. Nothing has been changed. Confirm with /confirm <action_id> or decline with /decline <action_id>.", "action_pending", _preview(
                    outcome)
            else:
                answer, state, action = str(outcome), "action_collecting_details", None
        else:
            if BookingService.is_my_bookings_request(text):
                service = get_bookings(request)
                answer, state, action = service.my_bookings_answer(actor_id), "bookings_listed", None
            elif BookingService.is_availability_request(text):
                service = get_bookings(request)
                answer, state, action = service.availability_answer(text), "availability_searched", None
            elif BookingService.is_booking_request(text):
                service = get_bookings(request)
                intake.draft, answer = service.begin(text)
                if not answer:
                    answer = service.continue_draft(intake.draft, "", actor_id)
                    if isinstance(answer, ActionPreview):
                        intake.draft = None
                        sessions.save_draft(session_id, actor_id, None)
                        state, action = "action_pending", _preview(answer)
                        answer = "Review this exact preview. Nothing has been changed. Confirm with /confirm <action_id> or decline with /decline <action_id>."
                    else:
                        sessions.save_draft(session_id, actor_id, intake.draft)
                        state, action = "action_collecting_details", None
                else:
                    sessions.save_draft(session_id, actor_id, intake.draft)
                    state, action = "action_collecting_details", None
            else:
                started = intake.begin(text, actor_id, actions)
                if started is not None:
                    sessions.save_draft(session_id, actor_id, intake.draft)
                    answer, state, action = started, "action_collecting_details", None
                else:
                    result = resident_assistant.respond(text)
                    answer, state, action = result.answer, result.status, None
                    evidence_sources = [item.source for item in result.evidence]
        logging.getLogger("hmlet.api").info(json.dumps({"event": "assistant_turn", "request_id": _request_id(request),
                                                        "actor_id_hash": hashlib.sha256(actor_id.encode()).hexdigest()[
                                                                         :16], "session_id": session_id,
                                                        "status": state}, separators=(",", ":")))
        return MessageResponse(session_id=session_id, status=state, answer=answer, action=action,
                               evidence=evidence_sources, request_id=_request_id(request))
    except ActionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
