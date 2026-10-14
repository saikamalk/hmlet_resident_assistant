"""Liveness and dependency-readiness probes."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/health", tags=["Health"])
@router.get("/health/live", include_in_schema=False)
def live():
    """Liveness is process-only and must not depend on external services."""
    return {"status": "ok"}


def _check_sqlite_store(service, name: str) -> str | None:
    """Check an initialized SQLite store without creating missing database files."""
    db_path = getattr(service, "db_path", None)
    if not db_path or db_path == ":memory:":
        return None
    try:
        path = Path(db_path)
        if not path.exists():
            return f"{name} storage is unavailable"
        with closing(sqlite3.connect(f"file:{path.resolve()}?mode=rw", uri=True, timeout=1)) as conn:
            conn.execute("SELECT 1").fetchone()
        return None
    except (OSError, sqlite3.Error, ValueError):
        return f"{name} storage is unavailable"


def _check_model(assistant) -> tuple[bool, str]:
    """Return provider state without treating an injected wrapper as healthy by default."""
    if assistant is None:
        return False, "not_initialized"
    model = getattr(assistant, "model", None)
    health_check = getattr(model, "health_check", None)
    if not callable(health_check):
        return False, "unverified"
    try:
        healthy = bool(health_check())
    except Exception:
        healthy = False
    return healthy, "healthy" if healthy else "unhealthy"


@router.get("/ready", tags=["Health"])
@router.get("/health/ready", include_in_schema=False)
def ready(request: Request):
    """Check provider and storage readiness; never use this endpoint for liveness."""
    app = request.app
    assistant = app.state.assistant
    if assistant is None:
        from app.core.config import settings
        if not settings.groq_api_key:
            raise HTTPException(status_code=503, detail={"status": "not_ready", "llm": "not_configured"})
        # Initialize the lazy provider on the first readiness probe so a scheduler
        # cannot deadlock by withholding traffic from an unready instance.
        try:
            from app.api_dependencies import get_assistant
            assistant = get_assistant(request)
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail={"status": "not_ready", "llm": "initialization_failed"}
            ) from exc

    model_ok, model_state = _check_model(assistant)
    if not model_ok:
        raise HTTPException(status_code=503, detail={"status": "not_ready", "llm": model_state})

    limiter = app.state.rate_limiter
    try:
        if limiter.health_check() is False:
            raise RuntimeError("rate limiter health check returned false")
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"status": "not_ready", "rate_limiter": "unhealthy"}) from exc

    storage_errors = []
    for service, name in (
        (app.state.action_service, "Action"),
        (app.state.session_store, "Session"),
        (app.state.booking_service, "Booking"),
    ):
        error = _check_sqlite_store(service, name)
        if error:
            storage_errors.append(error)
    if storage_errors:
        raise HTTPException(status_code=503, detail={"status": "not_ready", "errors": storage_errors})

    return {
        "status": "ready",
        "llm": model_state,
        "rate_limiter": "redis" if limiter.distributed else "local-dev",
        "storage": "healthy" if any((app.state.action_service, app.state.session_store)) else "lazy",
    }
