"""FastAPI service boundary for the Hmlet resident assistant."""
from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()
import hashlib
import json
import logging
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from app.actions.intake import Draft, NaturalLanguageActionIntake
from app.actions.service import ActionError, ActionPreview, ActionService
from app.bookings.service import BookingService
from app.assistant.service import ResidentAssistant
from app.core.auth import get_current_actor
from app.core.config import Settings
from app.knowledge.retrieval import KnowledgeBase
from app.assistant.llm import GroqChatModel
from app.core.observability import RequestObservabilityMiddleware
from app.core.rate_limit import RateLimitExceeded, RateLimiter


class CreateSessionResponse(BaseModel):
    session_id: str
    created_at: str


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class MessageResponse(BaseModel):
    session_id: str
    status: str
    answer: str
    action: dict[str, Any] | None = None
    evidence: list[str] = Field(default_factory=list)
    request_id: str | None = None


class SessionStore:
    """Persistent session metadata and action-intake draft; message bodies are not stored."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or os.getenv("RESIDENT_SESSION_DB", "data/sessions.sqlite3")
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, created_at TEXT NOT NULL, last_active_at TEXT NOT NULL, draft_json TEXT)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_actor ON sessions(actor_id, last_active_at)")
            conn.commit()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def create(self, actor_id: str):
        sid, now = f"ses_{uuid.uuid4().hex}", self._now()
        with closing(self._connect()) as conn:
            conn.execute("INSERT INTO sessions VALUES(?,?,?,?,NULL)", (sid, actor_id, now, now))
            conn.commit()
        return {"session_id": sid, "created_at": now}

    def get(self, session_id: str, actor_id: str):
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_id=? AND actor_id=?",
                               (session_id, actor_id)).fetchone()
            if row:
                conn.execute("UPDATE sessions SET last_active_at=? WHERE session_id=?", (self._now(), session_id))
                conn.commit()
            return dict(row) if row else None

    def save_draft(self, session_id: str, actor_id: str, draft: Draft | None):
        payload = None if draft is None else json.dumps(
            {"action_type": draft.action_type, "fields": draft.fields, "waiting_for": draft.waiting_for},
            sort_keys=True)
        with closing(self._connect()) as conn:
            updated = conn.execute(
                "UPDATE sessions SET draft_json=?, last_active_at=? WHERE session_id=? AND actor_id=?",
                (payload, self._now(), session_id, actor_id)).rowcount
            conn.commit()
        if updated != 1:
            raise HTTPException(status_code=404, detail="Session not found.")

    @staticmethod
    def load_draft(row: dict[str, Any]) -> Draft | None:
        if not row.get("draft_json"):
            return None
        try:
            data = json.loads(row["draft_json"])
            if data.get("action_type") not in {"maintenance", "booking_cancellation", "booking_creation"}:
                return None
            return Draft(action_type=data["action_type"], fields=dict(data["fields"]), waiting_for=data["waiting_for"])
        except (ValueError, KeyError, TypeError):
            return None


def _request_id(request: Request) -> str | None:
    """Return the middleware-generated request ID when available."""
    value = getattr(request.state, "request_id", None)
    return str(value) if value is not None else None


def _preview(preview: ActionPreview):
    return {"action_id": preview.action_id, "action_type": preview.action_type, "status": preview.status,
            "summary": preview.summary, "expires_at": preview.expires_at}


def create_app(*, assistant: ResidentAssistant | None = None, action_service: ActionService | None = None,
               session_store: SessionStore | None = None, rate_limiter: RateLimiter | None = None) -> FastAPI:
    app = FastAPI(title="Hmlet Resident Assistant API", version="1.0.0")
    app.add_middleware(RequestObservabilityMiddleware)
    app.state.assistant, app.state.action_service, app.state.session_store = assistant, action_service, session_store
    app.state.booking_service = None
    app.state.rate_limiter = rate_limiter or RateLimiter(int(os.getenv("RATE_LIMIT_REQUESTS", "30")),
                                                         int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")))

    def get_assistant():
        if app.state.assistant is None:
            runtime = Settings()
            data_dir = Path(runtime.data_dir or "data")
            if not data_dir.is_absolute():
                data_dir = Path(__file__).resolve().parents[1] / data_dir
            app.state.assistant = ResidentAssistant(KnowledgeBase(data_dir), GroqChatModel())
        return app.state.assistant

    def get_actions():
        if app.state.action_service is None:
            app.state.action_service = ActionService()
        return app.state.action_service

    def get_sessions():
        if app.state.session_store is None:
            app.state.session_store = SessionStore()
        return app.state.session_store

    def get_bookings():
        if app.state.booking_service is None:
            actions = get_actions()
            if not hasattr(actions, "db_path"):
                raise HTTPException(status_code=503, detail="Booking demo adapter is not configured.")
            runtime = Settings()
            data_dir = Path(runtime.data_dir or "data")
            if not data_dir.is_absolute():
                data_dir = Path(__file__).resolve().parents[1] / data_dir
            app.state.booking_service = BookingService(actions, data_dir)
        return app.state.booking_service

    def enforce_limit(actor):
        try:
            app.state.rate_limiter.check(actor["actor_id"])
        except RateLimitExceeded as exc:
            raise HTTPException(status_code=429, detail="Too many requests. Please retry later.",
                                headers={"Retry-After": str(app.state.rate_limiter.window_seconds)}) from exc

    # Public, documented aliases for local/manual checks. Keep the original
    # orchestration-friendly paths as hidden aliases for backwards compatibility.
    @app.get("/health", tags=["Health"])
    @app.get("/health/live", include_in_schema=False)
    def live():
        return {"status": "ok"}

    @app.get("/ready", tags=["Health"])
    @app.get("/health/ready", include_in_schema=False)
    def ready():
        if not os.getenv("GROQ_API_KEY") and app.state.assistant is None:
            raise HTTPException(status_code=503, detail="LLM provider is not configured.")
        return {"status": "ready", "rate_limiter": "redis" if app.state.rate_limiter.distributed else "local-dev"}

    @app.get("/v1/bookings", tags=["Bookings"])
    def list_my_bookings(actor=Depends(get_current_actor), actions=Depends(get_actions)):
        enforce_limit(actor)
        service = get_bookings()
        return {"mode": "local-demo", "bookings": service.my_bookings(actor["actor_id"])}

    @app.get("/v1/availability", tags=["Bookings"])
    def search_availability(start_date: str | None = None, stay_months: int | None = None, city: str | None = None,
                            actor=Depends(get_current_actor)):
        enforce_limit(actor)
        service = get_bookings()
        try:
            return {"mode": "local-demo", "listings": service.available_listings(start_date, stay_months, city)}
        except ActionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/sessions", response_model=CreateSessionResponse, status_code=201)
    def create_session(actor=Depends(get_current_actor), sessions=Depends(get_sessions)):
        enforce_limit(actor)
        return sessions.create(actor["actor_id"])

    @app.post("/v1/sessions/{session_id}/messages", response_model=MessageResponse)
    def send_message(session_id: str, body: MessageRequest, request: Request, actor=Depends(get_current_actor),
                     sessions=Depends(get_sessions), actions=Depends(get_actions),
                     resident_assistant=Depends(get_assistant)):
        enforce_limit(actor)
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
                if hasattr(actions, "db_path") and get_bookings().is_booking_action(actor_id, action_id):
                    result = get_bookings().confirm(actor_id, action_id)
                else:
                    result = actions.confirm(actor_id, action_id)
                answer, state, action = result.result.get("message", "Action completed."), result.status, {
                    "action_id": result.action_id, "action_type": result.action_type, "status": result.status,
                    "replayed": result.replayed}
                sessions.save_draft(session_id, actor_id, None)
            elif text.startswith("/decline "):
                action_id = text[len("/decline "):].strip()
                if hasattr(actions, "db_path") and get_bookings().is_booking_action(actor_id, action_id):
                    get_bookings().decline(actor_id, action_id)
                else:
                    actions.decline(actor_id, action_id)
                answer, state, action = "Action declined. No change was made.", "action_declined", {
                    "action_id": action_id, "status": "declined"}
                sessions.save_draft(session_id, actor_id, None)
            elif intake.draft is not None:
                if intake.draft.action_type == "booking_creation":
                    service = get_bookings()
                    outcome = service.continue_draft(intake.draft, text, actor_id)
                    if isinstance(outcome, ActionPreview) or (
                            isinstance(outcome, str) and outcome.startswith("Okay, I discarded")):
                        intake.draft = None
                else:
                    outcome = intake.continue_draft(text, actor_id, actions);
                sessions.save_draft(session_id, actor_id, intake.draft)
                if isinstance(outcome, ActionPreview):
                    answer, state, action = "Review this exact preview. Nothing has been changed. Confirm with /confirm <action_id> or decline with /decline <action_id>.", "action_pending", _preview(
                        outcome)
                else:
                    answer, state, action = str(outcome), "action_collecting_details", None
            else:
                from app.bookings.service import BookingService as BookingIntent
                if BookingIntent.is_my_bookings_request(text):
                    service = get_bookings()
                    answer, state, action = service.my_bookings_answer(actor_id), "bookings_listed", None
                elif BookingIntent.is_availability_request(text):
                    service = get_bookings()
                    answer, state, action = service.availability_answer(text), "availability_searched", None
                elif BookingIntent.is_booking_request(text):
                    service = get_bookings()
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
            # Logs contain no resident message, generated answer, or raw actor identifier.
            logging.getLogger("hmlet.api").info(json.dumps(
                {"event": "assistant_turn", "request_id": _request_id(request),
                 "actor_id_hash": hashlib.sha256(actor_id.encode()).hexdigest()[:16], "session_id": session_id,
                 "status": state}, separators=(",", ":")))
            return MessageResponse(session_id=session_id, status=state, answer=answer, action=action,
                                   evidence=evidence_sources, request_id=_request_id(request))
        except ActionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/sessions/{session_id}/actions/{action_id}/confirm", response_model=MessageResponse)
    def confirm_action(session_id: str, action_id: str, request: Request, actor=Depends(get_current_actor),
                       sessions=Depends(get_sessions), actions=Depends(get_actions)):
        enforce_limit(actor)
        if sessions.get(session_id, actor["actor_id"]) is None: raise HTTPException(status_code=404,
                                                                                    detail="Session not found.")
        try:
            if hasattr(actions, "db_path") and get_bookings().is_booking_action(actor["actor_id"], action_id):
                result = get_bookings().confirm(actor["actor_id"], action_id)
            else:
                result = actions.confirm(actor["actor_id"], action_id)
        except ActionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return MessageResponse(session_id=session_id, status=result.status,
                               answer=result.result.get("message", "Action completed."),
                               action={"action_id": result.action_id, "action_type": result.action_type,
                                       "status": result.status, "replayed": result.replayed},
                               request_id=_request_id(request))

    @app.post("/v1/sessions/{session_id}/actions/{action_id}/decline", response_model=MessageResponse)
    def decline_action(session_id: str, action_id: str, request: Request, actor=Depends(get_current_actor),
                       sessions=Depends(get_sessions), actions=Depends(get_actions)):
        enforce_limit(actor)
        if sessions.get(session_id, actor["actor_id"]) is None: raise HTTPException(status_code=404,
                                                                                    detail="Session not found.")
        try:
            if hasattr(actions, "db_path") and get_bookings().is_booking_action(actor["actor_id"], action_id):
                get_bookings().decline(actor["actor_id"], action_id)
            else:
                actions.decline(actor["actor_id"], action_id)
        except ActionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return MessageResponse(session_id=session_id, status="action_declined",
                               answer="Action declined. No change was made.",
                               action={"action_id": action_id, "status": "declined"},
                               request_id=_request_id(request))

    return app


app = create_app()
