from fastapi import APIRouter, Depends, Request

from app.api_dependencies import enforce_limit, get_sessions
from app.api_schemas import CreateSessionResponse
from app.core.auth import get_current_actor

router = APIRouter()


@router.post("/v1/sessions", response_model=CreateSessionResponse, status_code=201)
def create_session(request: Request, actor=Depends(get_current_actor), sessions=Depends(get_sessions)):
    enforce_limit(request, actor)
    return sessions.create(actor["actor_id"])
