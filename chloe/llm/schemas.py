from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MessageBody(BaseModel):
    body: str = Field(max_length=500)


class Verdict(BaseModel):
    decision: Literal["proceed", "abort", "revise"]
    reason: str


class OpportunityVector(BaseModel):
    messages: float = Field(ge=0.0, le=1.0)
    spotify: float = Field(ge=0.0, le=1.0)
    calendar: float = Field(ge=0.0, le=1.0)
    notes: float = Field(ge=0.0, le=1.0)
    web_search: float = Field(ge=0.0, le=1.0)
    gmail: float = Field(ge=0.0, le=1.0)
    reminders: float = Field(ge=0.0, le=1.0)


class AffectLabelResult(BaseModel):
    label: str = Field(max_length=60)


class GradeItem(BaseModel):
    id: int
    relevance_note: str


class GradeResult(BaseModel):
    selected: list[GradeItem] = Field(default_factory=list)


class ProceduralRule(BaseModel):
    rule_text: str = Field(max_length=500, description="Concise actionable rule: 'When X, do/avoid Y'")
    tool: str = Field(description="Primary tool this rule applies to")
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    valence: str = Field(default="avoid", description="'avoid' or 'prefer'")


class SelfModelOutput(BaseModel):
    self_narrative_belief: str = Field(max_length=1000)
    next_week_intention: str = Field(max_length=500)
    noted_contradictions: list[str] = Field(default_factory=list)
    affect_drift_note: str | None = None
    restraint_reflection: str | None = None
    voice_drift_note: str | None = None


class ClusterSynthesis(BaseModel):
    summary: str = Field(max_length=1000)


class ReflectNewWant(BaseModel):
    text: str = Field(max_length=240)
    tags: list[str] = Field(default_factory=list)
    pressure: float = Field(ge=0.0, le=1.0, default=0.6)


class ReflectNewTension(BaseModel):
    text: str = Field(max_length=240)
    tags: list[str] = Field(default_factory=list)
    pressure: float = Field(ge=0.0, le=1.0, default=0.6)


class ReflectNewInterest(BaseModel):
    label: str = Field(max_length=50)   # short, Chloe-voiced handle — enforces dedup
    why: str = Field(max_length=240, default="")
    intensity: float = Field(ge=0.0, le=1.0, default=0.4)
    category: str = Field(default="curiosity")


class ReflectGoalUpdate(BaseModel):
    goal_id: int
    delta: float = Field(ge=-1.0, le=1.0)
    why: str = Field(max_length=240, default="")


class ReflectNewGoal(BaseModel):
    name: str = Field(max_length=200, description="Short name for the goal, e.g. 'Learn music theory basics'")
    why: str = Field(max_length=400, default="", description="Why this goal matters now")

    @classmethod
    def model_validate(cls, obj, **kwargs):  # type: ignore[override]
        # The LLM sometimes returns alternate field names. Remap before validation.
        if isinstance(obj, dict):
            obj = dict(obj)
            for alt in ("title", "goal", "goal_name", "new"):
                if alt in obj and "name" not in obj:
                    obj["name"] = obj.pop(alt)
            for alt in ("reason", "description", "motivation", "goals"):
                if alt in obj and "why" not in obj:
                    obj["why"] = obj.pop(alt)
            obj.pop("new", None)   # discard stray 'new' key
            obj.pop("goals", None) # discard stray 'goals' key
        return super().model_validate(obj, **kwargs)


class ReflectNewBelief(BaseModel):
    topic: str = Field(max_length=80, description="Short topic label, e.g. 'consensus' or 'creative block'")
    belief: str = Field(max_length=300, description="The actual opinion or conclusion about the world")
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)


class ReflectOutput(BaseModel):
    continuity_note: str = Field(max_length=240, default="")
    new_wants: list[ReflectNewWant] = Field(default_factory=list)
    new_tensions: list[ReflectNewTension] = Field(default_factory=list)
    new_interests: list[ReflectNewInterest] = Field(default_factory=list)
    new_goals: list[ReflectNewGoal] = Field(default_factory=list)
    goal_progress_updates: list[ReflectGoalUpdate] = Field(default_factory=list)
    new_world_beliefs: list[ReflectNewBelief] = Field(default_factory=list)
    recurring_loops: list[str] = Field(default_factory=list)
    biased_summary: str = Field(max_length=240, default="")
