from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
