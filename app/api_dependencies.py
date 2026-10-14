"""Shared FastAPI dependencies and lazy service initialization."""
from pathlib import Path

from fastapi import HTTPException, Request

from app.actions.service import ActionService
from app.assistant.llm import GroqChatModel
from app.assistant.service import ResidentAssistant
from app.bookings.service import BookingService
from app.core.config import Settings
from app.core.rate_limit import RateLimitExceeded
from app.core.session_store import SessionStore
from app.knowledge.retrieval import KnowledgeBase


def get_assistant(request: Request):
    app = request.app
    if app.state.assistant is None:
        runtime = Settings()
        data_dir = Path(runtime.data_dir or "data")
        if not data_dir.is_absolute(): data_dir = Path(__file__).resolve().parents[1] / data_dir
        app.state.assistant = ResidentAssistant(KnowledgeBase(data_dir), GroqChatModel())
    return app.state.assistant


def get_actions(request: Request):
    app = request.app
    if app.state.action_service is None: app.state.action_service = ActionService()
    return app.state.action_service


def get_sessions(request: Request):
    app = request.app
    if app.state.session_store is None: app.state.session_store = SessionStore()
    return app.state.session_store


def get_bookings(request: Request):
    app = request.app
    if app.state.booking_service is None:
        actions = get_actions(request)
        if not hasattr(actions, "db_path"):
            raise HTTPException(status_code=503, detail="Booking demo adapter is not configured.")
        runtime = Settings()
        data_dir = Path(runtime.data_dir or "data")
        if not data_dir.is_absolute(): data_dir = Path(__file__).resolve().parents[1] / data_dir
        app.state.booking_service = BookingService(actions, data_dir)
    return app.state.booking_service


def enforce_limit(request: Request, actor):
    limiter = request.app.state.rate_limiter
    try:
        limiter.check(actor["actor_id"])
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail="Too many requests. Please retry later.",
                            headers={"Retry-After": str(limiter.window_seconds)}) from exc


def confirm_pending_action(request: Request, actions, actor_id: str, action_id: str):
    """Route all confirmation channels through the same action dispatcher."""
    from app.actions.dispatch import confirm_actor_action

    bookings = get_bookings(request) if hasattr(actions, "db_path") else None
    return confirm_actor_action(actions, bookings, actor_id, action_id)


def decline_pending_action(request: Request, actions, actor_id: str, action_id: str) -> None:
    """Route all decline channels through the same action dispatcher."""
    from app.actions.dispatch import decline_actor_action

    bookings = get_bookings(request) if hasattr(actions, "db_path") else None
    decline_actor_action(actions, bookings, actor_id, action_id)
