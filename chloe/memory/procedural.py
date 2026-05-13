from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from chloe.state.db import get_connection
from chloe.llm.gemini import GeminiClient
from chloe.llm.schemas import ProceduralRule
from chloe.observability.logging import get_logger

log = get_logger("procedural")
_gemini = GeminiClient()

BATCH_SIZE = 10
MAX_BATCHES = 3


async def distill_procedural() -> list[int]:
    """
    Run weekly procedural distillation.
    Returns list of new memory IDs created.
    """
    pairs = _load_feedback_pairs()
    if not pairs:
        log.info("procedural_no_pairs")
        return []

    batches = [pairs[i:i + BATCH_SIZE] for i in range(0, len(pairs), BATCH_SIZE)][:MAX_BATCHES]
    new_memory_ids: list[int] = []

    for batch_idx, batch in enumerate(batches):
        rules = await _extract_rules_from_batch(batch, batch_idx)
        for rule in rules:
            memory_id = _store_rule(rule)
            if memory_id:
                new_memory_ids.append(memory_id)
                log.info("procedural_rule_stored", memory_id=memory_id, tool=rule.tool)

    log.info("procedural_distillation_complete", new_rules=len(new_memory_ids))
    return new_memory_ids


def _load_feedback_pairs() -> list[dict]:
    """
    Load (action, user_response) pairs from the last 7 days.
    Includes: denied confirmations, reverted actions, user_praised tagged actions,
    and 👍/👎 reply reactions for active learning.
    """
    conn = get_connection()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    denied = conn.execute(
        """
        SELECT id, tool, verb, args, intent, proposed_at,
               'deny' as response_kind
        FROM actions
        WHERE state = 'denied'
          AND proposed_at >= ?
        """,
        (cutoff,),
    ).fetchall()

    reverted = conn.execute(
        """
        SELECT id, tool, verb, args, intent, proposed_at,
               'revert' as response_kind
        FROM actions
        WHERE state = 'reverted'
          AND proposed_at >= ?
        """,
        (cutoff,),
    ).fetchall()

    praised = conn.execute(
        """
        SELECT a.id, a.tool, a.verb, a.args, a.intent, a.proposed_at,
               'praise' as response_kind
        FROM actions a
        JOIN memories m ON m.source_ref = a.id
        WHERE m.tags LIKE '%user_praised%'
          AND a.proposed_at >= ?
        """,
        (cutoff,),
    ).fetchall()

    pairs = []
    for row in [*denied, *reverted, *praised]:
        pairs.append({
            "action_id": row["id"],
            "tool": row["tool"],
            "verb": row["verb"],
            "args": json.loads(row["args"]) if isinstance(row["args"], str) else (row["args"] or {}),
            "intent": row["intent"],
            "proposed_at": row["proposed_at"],
            "response_kind": row["response_kind"],
        })

    # Include reply reactions (👍/👎) as lightweight feedback pairs
    try:
        reactions = conn.execute(
            """
            SELECT rr.reaction, rr.created_at,
                   m.text as reply_text
            FROM reply_reactions rr
            LEFT JOIN memories m ON m.id = rr.reply_memory_id
            WHERE rr.created_at >= ?
            ORDER BY rr.created_at DESC
            LIMIT 50
            """,
            (cutoff,),
        ).fetchall()
        for row in reactions:
            reply_preview = (row["reply_text"] or "")[:200]
            pairs.append({
                "action_id": None,
                "tool": "chat",
                "verb": "reply",
                "args": {"reply_preview": reply_preview},
                "intent": f"sent a reply: {reply_preview[:80]}",
                "proposed_at": row["created_at"],
                "response_kind": row["reaction"],  # 'thumbs_up' or 'thumbs_down'
            })
    except Exception:
        pass  # reply_reactions table may not exist yet in older DBs

    return pairs


async def _extract_rules_from_batch(batch: list[dict], batch_idx: int) -> list[ProceduralRule]:
    """Call Flash to extract procedural rules from a batch of feedback pairs."""
    payload = {
        "feedback_pairs": batch,
        "batch_index": batch_idx,
        "instructions": (
            "Analyze these action-feedback pairs. "
            "For each pattern you notice (same tool being denied/reverted repeatedly, "
            "or a type of action consistently praised), extract a concise procedural rule. "
            "Also examine reply reactions: 'thumbs_up' means the reply resonated — extract "
            "what made it work ('When X, say/do Y'). 'thumbs_down' means it missed — "
            "extract what to avoid ('When X, avoid Y'). "
            "Rules should be actionable: 'When X, do/avoid Y.' "
            "Include the tool name and be specific about context."
        ),
    }

    try:
        result = await _gemini.flash(
            prompt_file="procedural_distillation.md",
            context=payload,
            schema=list[ProceduralRule],
        )
        if result is None:
            return []
        if isinstance(result, list):
            out = []
            for r in result:
                if isinstance(r, dict):
                    out.append(ProceduralRule(**r))
                elif isinstance(r, ProceduralRule):
                    out.append(r)
            return out
        return []
    except Exception as exc:
        log.warning("procedural_flash_error", error=str(exc), batch_idx=batch_idx)
        return []


def _store_rule(rule: ProceduralRule) -> int | None:
    """Store a ProceduralRule as a procedural memory. Returns memory_id."""
    from chloe.memory.store import add as memory_add

    tags = ["procedural", rule.tool] + (rule.tags or [])
    try:
        memory_id = memory_add(
            kind="procedural",
            text=rule.rule_text,
            source="distillation",
            tags=tags,
            weight=0.8,
        )
        return memory_id
    except Exception as exc:
        log.error("procedural_store_error", error=str(exc))
        return None
