# A-02 · `actions/schema.py` — Action pydantic model

## Overview

Implement `chloe/actions/schema.py` containing the `Action` and `ActionResult` Pydantic models from PRD §8.1. Include a `ulid()` helper for generating unique action IDs. These types are used everywhere the gate, audit feed, and tools interact.

## Context

The 1.0 codebase has no formal action type — outreach is triggered with ad hoc function calls and no persistence of what was proposed. Every 2.0 side effect is represented as an `Action` before it reaches the gate, giving a single canonical shape for all tools, all authorization levels, and all states.

## Implementation

```python
# chloe/actions/schema.py

from __future__ import annotations
from pydantic import BaseModel, Field, model_validator
from typing import Literal, Any
from datetime import datetime

AuthClass = Literal["free", "intimate", "kinetic", "kinetic-sensitive"]
State     = Literal[
    "proposed", "deliberating", "self_aborted", "suppressed_by_leash",
    "awaiting_confirmation", "confirmed", "denied", "executed", "failed", "reverted"
]

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
    at: datetime = Field(default_factory=datetime.utcnow)
    note: str | None = None

class DeliberationRecord(BaseModel):
    outcome: Literal["proceed", "revise", "abort"]
    reason: str
    revisions: dict | None = None
    model: str | None = None
    thinking_tokens: int = 0

class Action(BaseModel):
    id: str = Field(default_factory=lambda: ulid())
    tool: str
    verb: str
    args: dict[str, Any] = Field(default_factory=dict)
    intent: str                          # one-sentence why, in her voice
    preview: str                         # human-readable preview
    authorization: AuthClass
    cost_estimate: CostEstimate = Field(default_factory=CostEstimate)
    proposed_at: datetime = Field(default_factory=datetime.utcnow)
    state: State = "proposed"
    deliberation: DeliberationRecord | None = None
    result: dict | None = None
    error: str | None = None
    user_response: UserResponse | None = None
    becomes_memory_id: int | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)

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
```

## `ulid()` helper

```python
def ulid() -> str:
    """Generate a ULID (lexicographically sortable unique ID)."""
    from python_ulid import ULID
    return str(ULID())
```

Fallback if `python-ulid` is not installed: use `uuid4()` with a hex prefix for sortability.

```python
import uuid, time

def ulid() -> str:
    # Simple fallback: 13-char millisecond timestamp prefix + uuid4 hex
    ts = format(int(time.time() * 1000), "013x")
    uid = uuid.uuid4().hex[:13]
    return f"{ts}{uid}".upper()
```

Prefer `python-ulid` in `pyproject.toml` dependencies.

## JSON serialisation

`Action.model_dump_json()` must be lossless. Pay attention to:
- `datetime` fields: use `model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})`.
- `args` is `dict[str, Any]` — ensure nested structures serialise correctly.

## Dependencies

- F-01 (package structure).
- F-06 (schemas, for type patterns).

## Testing

### Unit tests — `tests/unit/test_action_schema.py`

```python
import pytest
from datetime import datetime, timezone
from chloe.actions.schema import Action, ActionResult, CostEstimate, ulid

def test_ulid_is_string():
    uid = ulid()
    assert isinstance(uid, str)
    assert len(uid) > 0

def test_ulid_unique():
    ids = {ulid() for _ in range(100)}
    assert len(ids) == 100

def test_action_roundtrip_json():
    a = Action(
        tool="spotify",
        verb="queue_track",
        args={"uri": "spotify:track:abc"},
        intent="I want to queue something calming",
        preview="Queue 'Bloom' by Beach House",
        authorization="kinetic",
    )
    json_str = a.model_dump_json()
    a2 = Action.model_validate_json(json_str)
    assert a2.tool == "spotify"
    assert a2.verb == "queue_track"
    assert a2.args["uri"] == "spotify:track:abc"
    assert a2.state == "proposed"

def test_action_default_id_set():
    a = Action(
        tool="notes", verb="create",
        intent="make a note", preview="Create note",
        authorization="kinetic",
    )
    assert a.id is not None
    assert len(a.id) > 0

def test_action_empty_intent_raises():
    with pytest.raises(Exception):
        Action(
            tool="notes", verb="create",
            intent="   ",
            preview="preview", authorization="kinetic",
        )

def test_action_invalid_authorization_raises():
    with pytest.raises(Exception):
        Action(
            tool="notes", verb="create",
            intent="test", preview="p",
            authorization="superkinetic",
        )

def test_action_invalid_state_raises():
    with pytest.raises(Exception):
        Action(
            tool="notes", verb="create",
            intent="test", preview="p",
            authorization="kinetic",
            state="flying",
        )

def test_action_result_defaults():
    ar = ActionResult()
    assert ar.executed is False
    assert ar.suppressed is False
    assert ar.awaiting is False

def test_cost_estimate_defaults():
    c = CostEstimate()
    assert c.tokens == 0
    assert c.reversibility == 1.0

def test_cost_estimate_reversibility_bounds():
    with pytest.raises(Exception):
        CostEstimate(reversibility=1.5)

def test_action_with_artifact_refs():
    from chloe.actions.schema import ArtifactRef
    a = Action(
        tool="spotify", verb="queue_track",
        intent="queue", preview="Queue track",
        authorization="kinetic",
        artifact_refs=[ArtifactRef(kind="spotify_track", ref="spotify:track:xyz")]
    )
    j = a.model_dump_json()
    a2 = Action.model_validate_json(j)
    assert len(a2.artifact_refs) == 1
    assert a2.artifact_refs[0].ref == "spotify:track:xyz"
```

## Acceptance criteria

- `Action(**valid_data).model_dump()` round-trips through JSON without loss.
- `ulid()` returns a non-empty string; 100 calls return 100 unique values.
- Invalid `authorization` and `state` values raise `ValidationError`.
- Empty `intent` raises `ValidationError`.
