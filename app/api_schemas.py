"""HTTP request and response schemas."""
from typing import Any

from pydantic import BaseModel, Field


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
