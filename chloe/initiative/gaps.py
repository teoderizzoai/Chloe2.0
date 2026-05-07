from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("initiative.gaps")

GAP_STALE_PERSON_DAYS = 30
GAP_STALE_BELIEF_DAYS = 14
GAP_STALE_GOAL_DAYS   = 7


@dataclass
class KnowledgeGap:
    subject: str
    description: str
    priority: float
    kind: Literal["person", "belief", "goal"]
    reference_id: str
    suggested_framing: str


def detect_gaps() -> list[KnowledgeGap]:
    gaps: list[KnowledgeGap] = []
    gaps.extend(_person_gaps())
    gaps.extend(_belief_gaps())
    gaps.extend(_goal_gaps())
    gaps.sort(key=lambda g: g.priority, reverse=True)
    log.debug("gaps_detected", total=len(gaps))
    return gaps


# ---------------------------------------------------------------------------
# Person gaps
# ---------------------------------------------------------------------------

PERSON_FIELDS = [
    ("schedule",       "schedule/routine",       0.7, "What does your week usually look like?"),
    ("food_preferences","food preferences",       0.5, "Any foods you're really into lately?"),
    ("sleep_pattern",  "sleep pattern",           0.4, "How's your sleep been?"),
    ("work_context",   "work situation",          0.6, "How's work going?"),
    ("mood_baseline",  "recent emotional state",  0.5, "How are you doing overall?"),
]


def _person_gaps() -> list[KnowledgeGap]:
    conn = get_connection()
    cutoff = (datetime.utcnow() - timedelta(days=GAP_STALE_PERSON_DAYS)).isoformat()
    persons = conn.execute("SELECT id, name FROM persons WHERE is_active = 1").fetchall()

    gaps = []
    for person in persons:
        pid = person["id"]
        pname = person["name"]

        for field_name, label, base_priority, framing in PERSON_FIELDS:
            row = conn.execute(
                "SELECT updated_at FROM person_fields WHERE person_id = ? AND field_name = ?",
                (pid, field_name),
            ).fetchone()

            if row is None:
                gaps.append(KnowledgeGap(
                    subject=f"{pname}: {label}",
                    description=f"No {label} recorded for {pname}.",
                    priority=base_priority,
                    kind="person",
                    reference_id=str(pid),
                    suggested_framing=framing,
                ))
            elif row["updated_at"] < cutoff:
                gaps.append(KnowledgeGap(
                    subject=f"{pname}: {label}",
                    description=f"{label.capitalize()} for {pname} is over {GAP_STALE_PERSON_DAYS} days old.",
                    priority=base_priority * 0.7,
                    kind="person",
                    reference_id=str(pid),
                    suggested_framing=framing,
                ))

    return gaps


# ---------------------------------------------------------------------------
# Belief gaps
# ---------------------------------------------------------------------------

def _belief_gaps() -> list[KnowledgeGap]:
    conn = get_connection()
    cutoff = (datetime.utcnow() - timedelta(days=GAP_STALE_BELIEF_DAYS)).isoformat()

    rows = conn.execute(
        """
        SELECT id, text, confidence, updated_at
        FROM inner_beliefs
        WHERE confidence < 0.4
          AND archived = 0
          AND updated_at < ?
        ORDER BY confidence ASC
        LIMIT 5
        """,
        (cutoff,),
    ).fetchall()

    gaps = []
    for row in rows:
        gaps.append(KnowledgeGap(
            subject=f"uncertain belief: {row['text'][:60]}",
            description=f"Belief (confidence={row['confidence']:.2f}) hasn't been revisited in {GAP_STALE_BELIEF_DAYS}+ days.",
            priority=0.3 + (0.4 - row["confidence"]) * 0.5,
            kind="belief",
            reference_id=str(row["id"]),
            suggested_framing="Pay attention for information that might confirm or revise this.",
        ))

    return gaps


# ---------------------------------------------------------------------------
# Goal gaps
# ---------------------------------------------------------------------------

def _goal_gaps() -> list[KnowledgeGap]:
    conn = get_connection()
    cutoff = (datetime.utcnow() - timedelta(days=GAP_STALE_GOAL_DAYS)).isoformat()

    rows = conn.execute(
        """
        SELECT id, name, last_action_at, missing_context
        FROM inner_goals
        WHERE status NOT IN ('done', 'failed', 'stale')
          AND (last_action_at IS NULL OR last_action_at < ?)
          AND missing_context IS NOT NULL
        """,
        (cutoff,),
    ).fetchall()

    gaps = []
    for row in rows:
        gaps.append(KnowledgeGap(
            subject=f"goal '{row['name']}': missing context",
            description=row["missing_context"],
            priority=0.65,
            kind="goal",
            reference_id=str(row["id"]),
            suggested_framing=f"To move forward on '{row['name']}', I need: {row['missing_context']}",
        ))

    return gaps


# ---------------------------------------------------------------------------
# Candidate adapter (for initiative engine)
# ---------------------------------------------------------------------------

def gap_driven_candidates():
    """Surface knowledge gaps as low-pressure CandidateAction objects."""
    from chloe.initiative.candidates import CandidateAction
    gaps = detect_gaps()
    candidates = []
    for gap in gaps[:3]:
        candidates.append(CandidateAction(
            tool="gap_flag",
            verb="surface",
            args={
                "subject": gap.subject,
                "description": gap.description,
                "suggested_framing": gap.suggested_framing,
                "kind": gap.kind,
                "reference_id": gap.reference_id,
            },
            intent=f"Notice a knowledge gap: {gap.description}",
            pressure=gap.priority * 0.6,
            source="gap",
            source_id=f"gap:{gap.reference_id}:{gap.kind}",
        ))
    return candidates
