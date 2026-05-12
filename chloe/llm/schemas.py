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


class SocialMentionItem(BaseModel):
    name: str = Field(max_length=80)
    content: str = Field(max_length=400)
    emotional_valence: float = Field(ge=-1.0, le=1.0, default=0.0)
    confidentiality: Literal["public", "relational", "private"] = "relational"


class ExtractOutput(BaseModel):
    salience: float = Field(ge=0.0, le=1.0, default=0.4)
    ambiguity: float = Field(ge=0.0, le=1.0, default=0.2)
    social_mentions: list[SocialMentionItem] = Field(default_factory=list)
    aesthetic_reactions: list[dict] = Field(default_factory=list)
    person_valence: float = Field(ge=-1.0, le=1.0, default=0.0,
        description="Teo's apparent emotional valence in this exchange (-1=very negative, 1=very positive)")
    person_arousal: float = Field(ge=0.0, le=1.0, default=0.4,
        description="Teo's apparent energy/arousal level (0=flat/withdrawn, 1=very energised)")


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
    noticing: bool = Field(default=False, description="True if this is a 'noticing' (proto-belief), not yet a full belief — when you've felt something might be true without enough experience to claim it as a belief.")


class UnprocessedReview(BaseModel):
    decision: Literal["promote", "keep_unprocessed", "archive"] = "keep_unprocessed"
    note: str = Field(max_length=160, default="")


class TraitEvidenceItem(BaseModel):
    behavior_observed: str = Field(max_length=300, description="What she specifically did or said — concrete, not a character conclusion")
    trait_implied: str = Field(max_length=120, description="The behavioral label this pattern suggests")
    reinforces: str | None = Field(default=None, description="Existing trait name this reinforces, if any")
    contradicts: str | None = Field(default=None, description="Existing trait name this contradicts, if any")


class TraitWeightUpdate(BaseModel):
    name: str = Field(max_length=200)
    delta: float = Field(ge=-0.1, le=0.1)


class TraitNewPattern(BaseModel):
    trait_implied: str = Field(max_length=200)
    first_observed: str = Field(max_length=40, default="")
    evidence_count: int = Field(ge=0, default=0)


class TraitAdjudicationOutput(BaseModel):
    reinforced: list[str] = Field(default_factory=list)
    contradicted: list[str] = Field(default_factory=list)
    weight_updates: list[TraitWeightUpdate] = Field(default_factory=list)
    new_patterns: list[TraitNewPattern] = Field(default_factory=list)
    decay_candidates: list[str] = Field(default_factory=list)
    notes: str = Field(max_length=200, default="")


class ReflectAnticipation(BaseModel):
    text: str = Field(max_length=240, description="What Chloe is anticipating — a forward-looking felt orientation")
    valence: float = Field(ge=-1.0, le=1.0, default=0.0,
        description="Negative=dread/apprehension, positive=looking forward to")
    intensity: float = Field(ge=0.0, le=1.0, default=0.5)
    target_date: str | None = Field(default=None, description="ISO date of the anticipated thing, if known")


class ReflectNewQuestion(BaseModel):
    text: str = Field(max_length=240, description="The question as Chloe would phrase it to herself — open, not resolved")
    domain: str = Field(default="world", description="'teo', 'world', or 'self'")
    intensity: float = Field(ge=0.0, le=1.0, default=0.5)


class ReflectOutput(BaseModel):
    continuity_note: str = Field(max_length=240, default="")
    new_wants: list[ReflectNewWant] = Field(default_factory=list)
    new_tensions: list[ReflectNewTension] = Field(default_factory=list)
    new_interests: list[ReflectNewInterest] = Field(default_factory=list)
    new_goals: list[ReflectNewGoal] = Field(default_factory=list)
    goal_progress_updates: list[ReflectGoalUpdate] = Field(default_factory=list)
    new_world_beliefs: list[ReflectNewBelief] = Field(default_factory=list)
    trait_evidence: list[TraitEvidenceItem] = Field(default_factory=list)
    recurring_loops: list[str] = Field(default_factory=list)
    biased_summary: str = Field(max_length=240, default="")
    new_anticipations: list[ReflectAnticipation] = Field(default_factory=list)
    new_questions: list[ReflectNewQuestion] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Two-pass reflect: a cheap router decides whether to spend the full Flash
# call. The router returns a tiny payload — bool + one sentence — and the
# specialist (the existing ReflectOutput) only runs when it says go.
# ---------------------------------------------------------------------------

class ReflectRouterOutput(BaseModel):
    noteworthy: bool = Field(
        default=False,
        description="True only if something genuinely shifted in the last window — new behavior, a felt change, a tension surfacing. False for routine ticks.",
    )
    summary: str = Field(
        max_length=200,
        default="",
        description="One sentence: what (if anything) shifted. Empty when noteworthy=false.",
    )


# ---------------------------------------------------------------------------
# Message intercept: a fast Flash call run in parallel with the main reply.
# Two jobs — detect requests (and whether a tool exists for them) and detect
# information worth capturing (events, reminders, facts about people).
# ---------------------------------------------------------------------------

class InterceptRequest(BaseModel):
    text: str = Field(max_length=240, description="What Teo asked for, in his words or a tight paraphrase")
    matched_tool: str | None = Field(default=None, description="Existing tool name if one fits, else null")
    matched_verb: str | None = Field(default=None, description="Existing verb name if one fits, else null")
    verb_gap: bool = Field(
        default=False,
        description="True only when there is NO existing tool/verb that can serve this request. When true, a verb proposal will be queued.",
    )
    suggested_tool: str | None = Field(
        default=None,
        max_length=40,
        description="If verb_gap=true: the existing tool name we'd extend (e.g. 'spotify'). Else null.",
    )
    suggested_verb: str | None = Field(
        default=None,
        max_length=40,
        description="If verb_gap=true: a snake_case name for the new verb. Else null.",
    )
    rationale: str = Field(
        max_length=300,
        default="",
        description="One sentence explaining why this is or isn't already serviceable. Required when verb_gap=true.",
    )


class InterceptCapture(BaseModel):
    summary: str = Field(max_length=240, description="What the information actually is in one line")
    domain: Literal["event", "reminder", "fact", "preference", "person", "feeling", "note"] = "fact"
    when_iso: str | None = Field(
        default=None,
        max_length=40,
        description="ISO 8601 datetime if the info has a definite time (e.g. dentist on 2026-05-19T15:00). Null if not time-bound.",
    )
    person_name: str | None = Field(
        default=None,
        max_length=80,
        description="Person referenced, if any (not Teo himself).",
    )
    suggested_action: dict | None = Field(
        default=None,
        description=(
            "If the info is action-shaped, an object with keys {tool, verb, args}. "
            "Examples: {tool:'calendar', verb:'add_event', args:{title, start, end}} or "
            "{tool:'reminders', verb:'add', args:{body, time}}. Null if no clear action."
        ),
    )
    follow_up_question: str | None = Field(
        default=None,
        max_length=200,
        description="If the info is ambiguous (missing time, missing person), one short question to ask. Null otherwise.",
    )


class InterceptOutput(BaseModel):
    is_request: bool = False
    is_informational: bool = False
    requests: list[InterceptRequest] = Field(default_factory=list)
    captures: list[InterceptCapture] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)


# ---------------------------------------------------------------------------
# Belief consistency: replace the lexical contradiction heuristic with one
# Flash call that returns the conflicting belief id if any.
# ---------------------------------------------------------------------------

class BeliefConsistencyResult(BaseModel):
    contradicts_id: int | None = Field(
        default=None,
        description="Row id from the candidate list that materially contradicts the new belief, or null if no real contradiction.",
    )
    reason: str = Field(max_length=200, default="")


class WitnessOutput(BaseModel):
    observation: str = Field(
        max_length=600,
        default="",
        description="One prose paragraph in Chloe's voice about what she noticed. Empty if nothing struck her.",
    )


class SignalBatchBelief(BaseModel):
    topic: str = Field(max_length=80)
    belief: str = Field(max_length=300)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    noticing: bool = False


class SignalBatchInterestPromotion(BaseModel):
    interest_id: int
    new_level: int = Field(ge=1, le=3)
    reason: str = Field(max_length=200, default="")


class SignalBatch(BaseModel):
    belief_updates: list[SignalBatchBelief] = Field(default_factory=list)
    interest_promotions: list[SignalBatchInterestPromotion] = Field(default_factory=list)
    new_tensions: list[str] = Field(default_factory=list)
    notes: str = Field(max_length=400, default="")


class MessageBodyWithDeliberation(BaseModel):
    deliberation: str = Field(
        max_length=300,
        default="",
        description="What you almost said but decided against, and why — not sent to Teo, internal scaffolding only.",
    )
    body: str = Field(max_length=500)
