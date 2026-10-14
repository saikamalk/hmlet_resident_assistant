import os

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/health", tags=["Health"])
@router.get("/health/live", include_in_schema=False)
def live(): return {"status": "ok"}


@router.get("/ready", tags=["Health"])
@router.get("/health/ready", include_in_schema=False)
def ready(request: Request):
    if not os.getenv("GROQ_API_KEY") and request.app.state.assistant is None:
        raise HTTPException(status_code=503, detail="LLM provider is not configured.")
    limiter = request.app.state.rate_limiter
    return {"status": "ready", "rate_limiter": "redis" if limiter.distributed else "local-dev"}
