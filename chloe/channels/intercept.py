"""Post-generation intercept — verb gap tracking only.

The full pre-generation analysis (context routing, task detection, memory
capture) now lives in channels/preflight.py and runs before the reply.

This module keeps a single responsibility: surfacing capability gaps so
Chloe can later draft a define_verb call. It runs as a background task
after the reply is sent, so latency is irrelevant.
"""
from __future__ import annotations

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("channels.intercept")


# ---------------------------------------------------------------------------
# Public helpers used by reflect to read and update proposals.
# ---------------------------------------------------------------------------

def get_pending_proposals(limit: int = 10) -> list[dict]:
    """Return unresolved verb_proposals for reflect to review."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, requested_text, tool_hint, verb_hint, intent,
                  rationale, person_id, confidence, created_at
           FROM verb_proposals
           WHERE status='pending'
           ORDER BY confidence DESC, created_at ASC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_proposal(proposal_id: int, new_status: str, resulting_verb: str | None = None) -> None:
    """Move a proposal to dismissed or promoted."""
    if new_status not in ("dismissed", "promoted"):
        return
    conn = get_connection()
    if new_status == "promoted":
        conn.execute(
            """UPDATE verb_proposals
               SET status='promoted', promoted_at=datetime('now'),
                   resulting_verb=?, updated_at=datetime('now')
               WHERE id=?""",
            (resulting_verb, proposal_id),
        )
    else:
        conn.execute(
            "UPDATE verb_proposals SET status='dismissed', updated_at=datetime('now') WHERE id=?",
            (proposal_id,),
        )
    conn.commit()
