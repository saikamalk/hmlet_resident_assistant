"""FastAPI application factory, lifecycle management, and router composition."""
from __future__ import annotations

from contextlib import asynccontextmanager
import os

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

from app.actions.service import ActionService
from app.assistant.service import ResidentAssistant
from app.core.observability import RequestObservabilityMiddleware
from app.core.rate_limit import RateLimiter
from app.core.session_store import SessionStore
from app.routes import actions, bookings, chat, health, sessions


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Release shared clients on shutdown; service creation remains lazy for tests/dev."""
    try:
        yield
    finally:
        limiter = getattr(app.state, "rate_limiter", None)
        if limiter is not None:
            close = getattr(limiter, "close", None)
            if callable(close):
                close()


def create_app(
    *,
    assistant: ResidentAssistant | None = None,
    action_service: ActionService | None = None,
    session_store: SessionStore | None = None,
    rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    app = FastAPI(title="Hmlet Resident Assistant API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(RequestObservabilityMiddleware)
    app.state.assistant = assistant
    app.state.action_service = action_service
    app.state.session_store = session_store
    app.state.booking_service = None
    app.state.rate_limiter = rate_limiter or RateLimiter(
        int(os.getenv("RATE_LIMIT_REQUESTS", "30")),
        int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")),
    )
    app.include_router(health.router)
    app.include_router(sessions.router, prefix="/v1")
    app.include_router(bookings.router, prefix="/v1")
    app.include_router(chat.router, prefix="/v1")
    app.include_router(actions.router, prefix="/v1")
    return app


app = create_app()
