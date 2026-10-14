"""Explicit confirmation and decline endpoints for proposed actions."""
from fastapi import APIRouter, Depends, HTTPException, Request

from app.actions.service import ActionError
from app.api_dependencies import (
    confirm_pending_action, decline_pending_action, enforce_limit, get_actions, get_sessions,
)
from app.api_schemas import MessageResponse
from app.core.auth import get_current_actor
from app.routes.chat import _request_id

router = APIRouter()


@router.post("/sessions/{session_id}/actions/{action_id}/confirm", response_model=MessageResponse)
def confirm_action(session_id: str, action_id: str, request: Request, actor=Depends(get_current_actor),
                   sessions=Depends(get_sessions), actions=Depends(get_actions)):
    enforce_limit(request, actor)
    if sessions.get(session_id, actor["actor_id"]) is None: raise HTTPException(status_code=404,
                                                                                detail="Session not found.")
    try:
        result = confirm_pending_action(request, actions, actor["actor_id"], action_id)
    except ActionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return MessageResponse(session_id=session_id, status=result.status,
                           answer=result.result.get("message", "Action completed."),
                           action={"action_id": result.action_id, "action_type": result.action_type,
                                   "status": result.status, "replayed": result.replayed},
                           request_id=_request_id(request))


@router.post("/sessions/{session_id}/actions/{action_id}/decline", response_model=MessageResponse)
def decline_action(session_id: str, action_id: str, request: Request, actor=Depends(get_current_actor),
                   sessions=Depends(get_sessions), actions=Depends(get_actions)):
    enforce_limit(request, actor)
    if sessions.get(session_id, actor["actor_id"]) is None: raise HTTPException(status_code=404,
                                                                                detail="Session not found.")
    try:
        decline_pending_action(request, actions, actor["actor_id"], action_id)
    except ActionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return MessageResponse(session_id=session_id, status="action_declined",
                           answer="Action declined. No change was made.",
                           action={"action_id": action_id, "status": "declined"}, request_id=_request_id(request))
