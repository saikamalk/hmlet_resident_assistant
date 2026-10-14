"""Liveness and dependency-readiness probes."""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/health", tags=["Health"])
@router.get("/health/live", include_in_schema=False)
def live():
    """Liveness must not depend on external services."""
    return {"status": "ok"}


def _check_sqlite_store(service, name: str) -> str | None:
    """Check an initialized SQLite-backed service without creating new resources."""
    db_path = getattr(service, "db_path", None)
    if not db_path or db_path == ":memory:":
        return None
    try:
        # sqlite3.connect() creates a missing file by default, which would make a
        # broken store look healthy. Check existence first and open read/write only.
        from pathlib import Path
        if not Path(db_path).exists():
            return f"{name} storage is unavailable"
        with closing(sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=rw", uri=True, timeout=1)) as conn:
            conn.execute("SELECT 1").fetchone()
        return None
    except (OSError, sqlite3.Error, ValueError):
        return f"{name} storage is unavailable"


@router.get("/ready", tags=["Health"])
@router.get("/health/ready", include_in_schema=False)
def ready(request: Request):
    """Check configured dependencies; do not make a live LLM request per probe."""
    app = request.app
    assistant = app.state.assistant
    llm_configured = assistant is not None or bool(os.getenv("GROQ_API_KEY"))
    if not llm_configured:
        raise HTTPException(status_code=503, detail="LLM provider is not configured.")

    limiter = app.state.rate_limiter
    try:
        limiter.health_check()
    except (AttributeError, RuntimeError):
        raise HTTPException(status_code=503, detail="Rate limiter is unavailable.")

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
        "llm": "injected" if assistant is not None else "configured",
        "rate_limiter": "redis" if limiter.distributed else "local-dev",
        "storage": "healthy" if any((app.state.action_service, app.state.session_store)) else "lazy",
    }
