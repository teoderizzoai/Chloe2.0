from __future__ import annotations

import uuid
import time
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_serializer, model_validator


AuthClass = Literal["free", "intimate", "kinetic", "kinetic-sensitive"]
State = Literal[
    "proposed", "deliberating", "self_aborted", "suppressed_by_leash",
    "awaiting_confirmation", "confirmed", "denied", "executed", "failed", "reverted",
    "held_back",
]

_utcnow = lambda: datetime.now(timezone.utc)


def ulid() -> str:
    try:
        from python_ulid import ULID
        return str(ULID())
    except ImportError:
        ts = format(int(time.time() * 1000), "013x")
        uid = uuid.uuid4().hex[:13]
        return f"{ts}{uid}".upper()


class CostEstimate(BaseModel):
    tokens: int = 0
    usd: float = 0.0
    seconds: float = 0.0
    reversibility: float = Field(ge=0, le=1, default=1.0)


class ArtifactRef(BaseModel):
    kind: str
    ref: str
    snapshot: str | None = None


class UserResponse(BaseModel):
    kind: Literal["confirm", "deny", "revert"]
    at: datetime = Field(default_factory=_utcnow)
    note: str | None = None


class DeliberationRecord(BaseModel):
    outcome: Literal["proceed", "revise", "abort"]
    reason: str
    revisions: dict | None = None
    model: str | None = None
    thinking_tokens: int = 0


class Action(BaseModel):
    id: str = Field(default_factory=ulid)
    tool: str
    verb: str
    args: dict[str, Any] = Field(default_factory=dict)
    intent: str
    preview: str
    authorization: AuthClass
    cost_estimate: CostEstimate = Field(default_factory=CostEstimate)
    proposed_at: datetime = Field(default_factory=_utcnow)
    state: State = "proposed"
    deliberation: DeliberationRecord | None = None
    result: dict | None = None
    error: str | None = None
    user_response: UserResponse | None = None
    becomes_memory_id: int | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)

    @field_serializer("proposed_at")
    def serialize_proposed_at(self, v: datetime) -> str:
        return v.isoformat()

    @model_validator(mode="after")
    def intent_not_empty(self) -> "Action":
        if not self.intent.strip():
            raise ValueError("intent must not be blank")
        return self


class ActionResult(BaseModel):
    executed: bool = False
    suppressed: bool = False
    awaiting: bool = False
    reason: str | None = None
    ticket_id: str | None = None
    action_id: str | None = None
    error: str | None = None
