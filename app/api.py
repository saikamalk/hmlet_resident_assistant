"""FastAPI application factory and router composition."""
from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()
import os
from fastapi import FastAPI
from app.actions.service import ActionService
from app.assistant.service import ResidentAssistant
from app.core.observability import RequestObservabilityMiddleware
from app.core.rate_limit import RateLimiter
from app.core.session_store import SessionStore
from app.routes import actions, bookings, chat, health, sessions

# Backwards-compatible imports used by the existing tests and local tooling.


def create_app(*, assistant: ResidentAssistant | None = None, action_service: ActionService | None = None,
               session_store: SessionStore | None = None, rate_limiter: RateLimiter | None = None) -> FastAPI:
    app = FastAPI(title="Hmlet Resident Assistant API", version="1.0.0")
    app.add_middleware(RequestObservabilityMiddleware)
    app.state.assistant = assistant
    app.state.action_service = action_service
    app.state.session_store = session_store
    app.state.booking_service = None
    app.state.rate_limiter = rate_limiter or RateLimiter(int(os.getenv("RATE_LIMIT_REQUESTS", "30")),
                                                         int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")))
    app.include_router(health.router)
    app.include_router(sessions.router)
    app.include_router(bookings.router)
    app.include_router(chat.router)
    app.include_router(actions.router)
    return app

app = create_app()
