"""Shared FastAPI dependencies and lazy service initialization."""
from pathlib import Path

from fastapi import HTTPException, Request

from app.actions.dispatch import confirm_actor_action, decline_actor_action
from app.actions.service import ActionService
from app.assistant.llm import GroqChatModel
from app.assistant.service import ResidentAssistant
from app.bookings.service import BookingService
from app.core.config import Settings
from app.core.rate_limit import RateLimitExceeded
from app.core.session_store import SessionStore
from app.knowledge.retrieval import KnowledgeBase


def _data_dir() -> Path:
    runtime = Settings()
    path = Path(runtime.data_dir or "data")
    return path if path.is_absolute() else Path(__file__).resolve().parents[1] / path


def get_assistant(request: Request):
    app = request.app
    if app.state.assistant is None:
        app.state.assistant = ResidentAssistant(KnowledgeBase(_data_dir()), GroqChatModel())
    return app.state.assistant


def get_actions(request: Request):
    app = request.app
    if app.state.action_service is None:
        app.state.action_service = ActionService()
    return app.state.action_service


def get_sessions(request: Request):
    app = request.app
    if app.state.session_store is None:
        app.state.session_store = SessionStore()
    return app.state.session_store


def get_bookings(request: Request):
    app = request.app
    if app.state.booking_service is None:
        actions = get_actions(request)
        # Explicit adapter contract instead of inferring capabilities from db_path.
        if not isinstance(actions, ActionService):
            raise HTTPException(status_code=503, detail="Booking demo adapter is not configured.")
        app.state.booking_service = BookingService(actions, _data_dir())
    return app.state.booking_service


def enforce_limit(request: Request, actor):
    limiter = request.app.state.rate_limiter
    try:
        limiter.check(actor["actor_id"])
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please retry later.",
            headers={"Retry-After": str(limiter.window_seconds)},
        ) from exc


def _action_registry(request: Request, actions):
    # BookingService is created only when the explicit booking adapter is supported.
    bookings = get_bookings(request) if isinstance(actions, ActionService) else None
    from app.actions.dispatch import build_action_registry
    return build_action_registry(actions, bookings)


def confirm_pending_action(request: Request, actions, actor_id: str, action_id: str):
    """Resolve then execute via the shared typed registry; services enforce ownership."""
    return _action_registry(request, actions).confirm(actor_id, action_id)


def decline_pending_action(request: Request, actions, actor_id: str, action_id: str) -> None:
    """Resolve then decline via the shared typed registry; services enforce ownership."""
    _action_registry(request, actions).decline(actor_id, action_id)
