from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Evidence:
    source: str
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AssistantResponse:
    answer: str
    status: str = "answered"
    evidence: tuple[Evidence, ...] = ()
    action_id: str | None = None
