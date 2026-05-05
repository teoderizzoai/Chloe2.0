# F-06 · `llm/schemas.py` — Pydantic output schemas for all Flash calls

## Overview

Define every structured-output Pydantic model referenced in the PRD in `chloe/llm/schemas.py`. Each model corresponds to a Flash (or Pro) structured call. Include field validators (ranges, max lengths). All 11 primary models must import cleanly.

## Context

The current 1.0 codebase parses LLM structured outputs with ad hoc `json.loads()` + defensive dict access. 2.0 uses Pydantic's `model_validate_json()` which gives automatic type coercion, field validation, and clear error messages. This file is the central schema registry for all LLM output shapes.

## Models to implement

### 1. `ExtractCombined` — per-turn exchange extraction

```python
from pydantic import BaseModel, Field
from typing import Literal

class ToolIntent(BaseModel):
    tool: str
    verb: str
    rationale: str = Field(max_length=200)

class PersonUpdate(BaseModel):
    person_name: str
    warmth_delta: float = Field(ge=-20, le=20, default=0)
    note: str | None = None
    new_event: str | None = None
    new_moment: str | None = None

class ProposedBelief(BaseModel):
    text: str = Field(max_length=300)
    confidence: float = Field(ge=0, le=1)

class ProposeTrait(BaseModel):
    name: str = Field(max_length=80)
    weight_suggestion: float = Field(ge=0.1, le=0.6)
    evidence: list[str]

class ExtractCombined(BaseModel):
    summary: str = Field(max_length=500)
    salience: float = Field(ge=0, le=1)
    emotional_valence: float = Field(ge=-1, le=1)
    emotional_arousal: float = Field(ge=0, le=1)
    tags: list[str] = Field(default_factory=list, max_length=10)
    new_facts: list[str] = Field(default_factory=list)
    tool_intent: list[ToolIntent] = Field(default_factory=list)
    stake_shift: float = Field(ge=-1, le=1, default=0)
    person_updates: list[PersonUpdate] = Field(default_factory=list)
    proposed_belief: ProposedBelief | None = None
    propose_trait: ProposeTrait | None = None
```

### 2. `Graded` — memory grader output

```python
class GradedMemory(BaseModel):
    id: int
    relevance_note: str = Field(max_length=150)

class Graded(BaseModel):
    selected: list[GradedMemory]  # top-K memories chosen by the grader
```

### 3. `Emotion` — per-message emotion read

```python
class Emotion(BaseModel):
    emotion: str = Field(max_length=40)
    intensity: float = Field(ge=0, le=1)
    directed_at_chloe: bool = False
    valence: float = Field(ge=-1, le=1)
    arousal: float = Field(ge=0, le=1)
```

### 4. `AffectLabel` — lazy affect labeler output

```python
class AffectLabel(BaseModel):
    label: str = Field(max_length=60)  # e.g. "melancholic-warm"
```

### 5. `Verdict` — deliberation outcome

```python
class Revisions(BaseModel):
    args_patch: dict = Field(default_factory=dict)
    delay_seconds: int = Field(ge=0, le=86400, default=0)
    downgrade_auth_to: str | None = None

class Verdict(BaseModel):
    outcome: Literal["proceed", "revise", "abort"]
    reason: str = Field(max_length=120)
    revisions: Revisions | None = None
```

### 6. `ReflectOutput` — every-2h reflection

```python
class TensionDetected(BaseModel):
    label: str = Field(max_length=100)
    why: str = Field(max_length=200)

class GoalProgressDelta(BaseModel):
    goal_id: int
    delta: float = Field(ge=-1, le=1)
    why: str = Field(max_length=150)

class ReflectOutput(BaseModel):
    continuity_note: str = Field(max_length=240)
    tension_detected: TensionDetected | None = None
    recurring_loops: list[str] = Field(default_factory=list)
    biased_summary: str = Field(max_length=400)
    maybe_propose_trait: ProposeTrait | None = None
    maybe_update_goal_progress: list[GoalProgressDelta] = Field(default_factory=list)
```

### 7. `ClusterSynthesis` — sleep consolidation cluster

```python
class ClusterSynthesis(BaseModel):
    summary: str = Field(max_length=400)
    tags: list[str] = Field(default_factory=list)
    dream_worthy: bool = False
    salience: float = Field(ge=0, le=1, default=0.5)
```

### 8. `DreamFragment` — optional nightly dream

```python
class DreamFragment(BaseModel):
    text: str = Field(max_length=300)
    tags: list[str] = Field(default_factory=list)
```

### 9. `SelfModelOutput` — weekly Pro self-modeling

```python
class BeliefWithConfidence(BaseModel):
    text: str = Field(max_length=400)
    confidence: float = Field(ge=0, le=1, default=0.5)

class NextWeekIntention(BaseModel):
    name: str = Field(max_length=120)
    why: str = Field(max_length=300)
    target_artifact_ref: str | None = None

class SelfModelOutput(BaseModel):
    self_narrative_belief: BeliefWithConfidence
    change_perception: BeliefWithConfidence
    next_week_intention: NextWeekIntention
```

### 10. `ProceduralRule` — procedural distillation output

```python
class ProceduralRule(BaseModel):
    text: str = Field(max_length=400)
    tool: str
    verb: str | None = None
    confidence: float = Field(ge=0, le=1, default=0.7)
    tags: list[str] = Field(default_factory=list)
```

### 11. `OpportunityVector` — initiative opportunity signal

```python
class OpportunityVector(BaseModel):
    messages:   float = Field(ge=0, le=1)
    spotify:    float = Field(ge=0, le=1)
    calendar:   float = Field(ge=0, le=1)
    notes:      float = Field(ge=0, le=1)
    web_search: float = Field(ge=0, le=1)
    reminders:  float = Field(ge=0, le=1)
```

## Export

All 11 models (plus helpers) should be importable via `from chloe.llm.schemas import *`. Add `__all__` listing them explicitly.

## Dependencies

- F-01 (package structure).

## Testing

### Unit tests — `tests/unit/test_schemas.py`

```python
from chloe.llm.schemas import (
    ExtractCombined, Graded, Emotion, AffectLabel, Verdict,
    ReflectOutput, ClusterSynthesis, DreamFragment, SelfModelOutput,
    ProceduralRule, OpportunityVector,
)
import pytest

def test_all_schemas_importable():
    # Just importing them above is the test
    assert ExtractCombined is not None

def test_verdict_valid():
    v = Verdict(outcome="proceed", reason="all good")
    assert v.outcome == "proceed"

def test_verdict_reason_max_length():
    with pytest.raises(Exception):
        Verdict(outcome="proceed", reason="x" * 121)

def test_emotion_clamps():
    with pytest.raises(Exception):
        Emotion(emotion="happy", intensity=1.5, valence=0, arousal=0)

def test_opportunity_vector_range():
    with pytest.raises(Exception):
        OpportunityVector(messages=1.5, spotify=0, calendar=0, notes=0,
                          web_search=0, reminders=0)

def test_extract_combined_roundtrip():
    import json
    data = {
        "summary": "test summary",
        "salience": 0.7,
        "emotional_valence": -0.3,
        "emotional_arousal": 0.5,
        "tags": ["music", "loss"],
        "new_facts": [],
        "tool_intent": [],
        "stake_shift": 0.1,
        "person_updates": [],
        "proposed_belief": None,
        "propose_trait": None,
    }
    m = ExtractCombined.model_validate(data)
    assert m.salience == 0.7
    j = m.model_dump_json()
    m2 = ExtractCombined.model_validate_json(j)
    assert m2.salience == m.salience

def test_self_model_output_valid():
    data = {
        "self_narrative_belief": {"text": "I care deeply", "confidence": 0.5},
        "change_perception": {"text": "I am changing", "confidence": 0.4},
        "next_week_intention": {"name": "read more", "why": "curiosity"},
    }
    m = SelfModelOutput.model_validate(data)
    assert m.self_narrative_belief.confidence == 0.5

@pytest.mark.parametrize("schema,data", [
    (Graded, {"selected": []}),
    (AffectLabel, {"label": "calm-open"}),
    (ClusterSynthesis, {"summary": "things happened", "dream_worthy": False, "salience": 0.4}),
    (DreamFragment, {"text": "I dreamed of water"}),
    (ProceduralRule, {"text": "don't add reminders", "tool": "calendar", "confidence": 0.8}),
])
def test_schemas_accept_valid_data(schema, data):
    m = schema.model_validate(data)
    assert m is not None
```

## Acceptance criteria

- `from chloe.llm.schemas import *` imports all 11 models without error.
- Each model has at least one passing `pytest` validation test.
- Field validators (ranges, max lengths) raise `ValidationError` on out-of-range values.
