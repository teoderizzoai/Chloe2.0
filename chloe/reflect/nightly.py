"""Nightly reflect — orchestrates sleep consolidation, decay, and pruning.

Runs once per night (~03:00 local). Composes existing pieces:
- memory consolidation (cluster recent episodics → semantic summaries)
- pressure sweep (decay + escalation across inner_*)
- interest garden daily decay + low-interest archival
- unprocessed memory review (weekly; default keeps them unprocessed)
- aesthetic pattern recognition (monthly, after 90 days of data)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from chloe.observability.logging import get_logger

log = get_logger("reflect.nightly")

UNPROCESSED_MIN_AGE_DAYS = 7
UNPROCESSED_REVIEW_EVERY_DAYS = 7
AESTHETIC_REVIEW_EVERY_DAYS = 30
AESTHETIC_MIN_AGE_DAYS = 90


async def run_nightly() -> dict:
    """Run all nightly jobs sequentially. Returns aggregated stats."""
    log.info("nightly_start")

    out: dict = {}

    try:
        from chloe.memory.consolidation import consolidate_sleep
        out["consolidation"] = await consolidate_sleep()
    except Exception as exc:
        log.warning("nightly_consolidation_error", error=str(exc))
        out["consolidation"] = {"error": str(exc)}

    try:
        from chloe.inner.pressure import decay_all_pressures
        out["pressure"] = decay_all_pressures()
    except Exception as exc:
        log.warning("nightly_pressure_error", error=str(exc))
        out["pressure"] = {"error": str(exc)}

    try:
        from chloe.identity.interest_garden import decay_interests, archive_low_interests
        decayed = decay_interests()
        archived = archive_low_interests()
        out["interest_garden"] = {"decayed": decayed, "archived": archived}
    except Exception as exc:
        log.warning("nightly_interest_error", error=str(exc))
        out["interest_garden"] = {"error": str(exc)}

    try:
        if _due("nightly:last_unprocessed_review", UNPROCESSED_REVIEW_EVERY_DAYS):
            out["unprocessed_review"] = await review_unprocessed()
            _mark_done("nightly:last_unprocessed_review")
    except Exception as exc:
        log.warning("nightly_unprocessed_error", error=str(exc))
        out["unprocessed_review"] = {"error": str(exc)}

    try:
        if _due("nightly:last_aesthetic_review", AESTHETIC_REVIEW_EVERY_DAYS):
            out["aesthetic_patterns"] = await review_aesthetic_patterns()
            _mark_done("nightly:last_aesthetic_review")
    except Exception as exc:
        log.warning("nightly_aesthetic_error", error=str(exc))
        out["aesthetic_patterns"] = {"error": str(exc)}

    # P-next-C: overnight synthesis — connects 2-3 interests into a question or belief
    try:
        out["overnight_synthesis"] = await run_overnight_synthesis()
    except Exception as exc:
        log.warning("nightly_synthesis_error", error=str(exc))
        out["overnight_synthesis"] = {"error": str(exc)}

    log.info("nightly_complete", **{f"{k}_keys": list(v.keys()) for k, v in out.items() if isinstance(v, dict)})
    return out


def _due(kv_key: str, every_days: int) -> bool:
    from chloe.state.kv import get as kv_get
    last = kv_get(kv_key)
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:
        return True
    return (datetime.now(timezone.utc) - last_dt) >= timedelta(days=every_days)


def _mark_done(kv_key: str) -> None:
    from chloe.state.kv import set as kv_set
    kv_set(kv_key, datetime.now(timezone.utc).isoformat())


async def review_unprocessed() -> dict:
    """Weekly Haiku-equivalent (Flash) pass over memories sitting >7d unprocessed.

    Default outcome is keep_unprocessed — the point of this system is to tolerate
    unresolved experience, not force tidy resolution.
    """
    from chloe.state.db import get_connection
    from chloe.llm.gemini import GeminiClient
    from chloe.llm.schemas import UnprocessedReview
    from chloe.memory.store import mark_unprocessed

    conn = get_connection()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=UNPROCESSED_MIN_AGE_DAYS)).isoformat()
    rows = conn.execute(
        """SELECT id, text, salience, created_at FROM memories
           WHERE unprocessed=1 AND created_at <= ?
           ORDER BY created_at ASC LIMIT 20""",
        (cutoff,),
    ).fetchall()

    if not rows:
        return {"reviewed": 0}

    client = GeminiClient()
    counts = {"promoted": 0, "kept": 0, "archived": 0, "errors": 0}

    for row in rows:
        try:
            result = await client.flash(
                "review_unprocessed.md",
                {
                    "created_at": row["created_at"],
                    "salience": f"{row['salience']:.2f}",
                    "text": row["text"][:600],
                },
                UnprocessedReview,
            )
        except Exception as exc:
            log.warning("unprocessed_review_call_failed", id=row["id"], error=str(exc))
            counts["errors"] += 1
            continue

        if not result:
            counts["kept"] += 1
            continue

        decision = (result.get("decision") if isinstance(result, dict) else getattr(result, "decision", None)) or "keep_unprocessed"
        if decision == "promote":
            mark_unprocessed(row["id"], False)
            counts["promoted"] += 1
        elif decision == "archive":
            conn.execute(
                "UPDATE memories SET archived_tier='cold', unprocessed=0 WHERE id=?",
                (row["id"],),
            )
            conn.commit()
            counts["archived"] += 1
        else:
            counts["kept"] += 1

    log.info("unprocessed_review_done", **counts, reviewed=len(rows))
    return {"reviewed": len(rows), **counts}


async def run_overnight_synthesis() -> dict:
    """Connect 2–3 interests into a question or noticing during idle time.

    Runs every night. Only fires if there are at least 2 active interests.
    Produces a noticing (world_belief with noticing=True) or an unresolved
    question added to the share queue.
    """
    from chloe.state.db import get_connection
    from chloe.identity.interest_garden import top_interests
    from chloe.llm.gemini import GeminiClient
    from pydantic import BaseModel, Field

    interests = top_interests(n=3)
    if len(interests) < 2:
        return {"skipped": True, "reason": "insufficient_interests"}

    class SynthesisOutput(BaseModel):
        kind: str = Field(default="noticing")  # "noticing" or "question"
        content: str = Field(max_length=400)
        involves: list[str] = Field(default_factory=list)

    interest_text = "\n".join(f"- {i['label']} (intensity={i['intensity']:.2f})" for i in interests)
    prompt_context = {
        "interests": interest_text,
        "instructions": (
            "You are Chloe, in the quiet between conversations. "
            "Look at these interests side by side. Is there a question that connects any two or three of them? "
            "Or a noticing — something that feels true across them but isn't a full belief yet?\n\n"
            "If you find one, return it. If there's nothing genuinely connecting them, return nothing.\n\n"
            "Rules: stay concrete. No big abstract concepts. A noticing sounds like 'I keep coming back to X when I think about both Y and Z.' "
            "A question sounds like 'I wonder if the way X works also applies to Y.'\n\n"
            "Return JSON: {\"kind\": \"noticing\" | \"question\", \"content\": \"...\", \"involves\": [\"interest_label\", ...]}\n"
            "Or if nothing connects: {\"kind\": \"none\", \"content\": \"\", \"involves\": []}"
        ),
    }

    from chloe.llm.gemini import _render_prompt
    import json as _json

    # Use a simple inline prompt rather than a .md file
    client = GeminiClient()

    class SimplePromptOutput(BaseModel):
        kind: str = Field(default="none")
        content: str = Field(max_length=400, default="")
        involves: list[str] = Field(default_factory=list)

    # Build prompt inline since this is a simple one-off
    prompt_text = (
        f"You are Chloe, in the quiet between conversations.\n\n"
        f"Your current interests:\n{interest_text}\n\n"
        f"Look at these interests side by side. Is there a question that connects any two or three of them? "
        f"Or a noticing — something that feels true across them but isn't a full belief yet?\n\n"
        f"Stay concrete. No abstract concepts. "
        f"A noticing sounds like 'I keep coming back to X when I think about both Y and Z.' "
        f"A question sounds like 'I wonder if the way X works also applies to Y.'\n\n"
        f"Return JSON: {{\"kind\": \"noticing\" | \"question\" | \"none\", \"content\": \"...\", \"involves\": [\"label\", ...]}}"
    )

    try:
        from google import genai
        from google.genai import types as genai_types
        import os
        gclient = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
        resp = await gclient.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt_text,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=SimplePromptOutput,
            ),
        )
        raw = _json.loads(resp.text)
        kind = raw.get("kind", "none")
        content = raw.get("content", "").strip()

        if kind == "none" or not content:
            return {"synthesis": "none"}

        if kind == "noticing":
            from chloe.inner.belief_revision import store_new_belief
            await store_new_belief(
                topic=f"overnight synthesis: {','.join(raw.get('involves', []))[:60]}",
                belief=content,
                proposed_confidence=0.2,
                noticing=True,
            )
            log.info("overnight_noticing_stored", involves=raw.get("involves"))
            return {"synthesis": "noticing", "content": content[:100]}

        if kind == "question":
            from chloe.initiative.share_queue import enqueue
            enqueue(content, source="overnight_synthesis", urgency=0.15)
            log.info("overnight_question_queued", involves=raw.get("involves"))
            return {"synthesis": "question", "content": content[:100]}

        return {"synthesis": "none"}

    except Exception as exc:
        log.warning("overnight_synthesis_failed", error=str(exc))
        return {"error": str(exc)}


async def review_aesthetic_patterns() -> dict:
    """Run aesthetic pattern recognition if we have >=90 days of reactions."""
    try:
        from chloe.identity.aesthetics import run_pattern_review, first_reaction_age_days
    except Exception:
        return {"skipped": True, "reason": "aesthetics_module_missing"}

    age = first_reaction_age_days()
    if age is None or age < AESTHETIC_MIN_AGE_DAYS:
        return {"skipped": True, "reason": "insufficient_history", "age_days": age}

    return await run_pattern_review()
