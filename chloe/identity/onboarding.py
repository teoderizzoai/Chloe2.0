"""Onboarding extraction — structured knowledge from Teo's initial Q&A.

Called by admin API (/onboarding/complete and /onboarding/re-extract) and
by the bootstrap-identity CLI command.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("identity.onboarding")

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "onboarding_extract.md"


async def run_extraction(qa_text: str, conn=None) -> dict:
    """Run Flash extraction over onboarding Q&A, write results to DB, return summary.

    Idempotent: clears previous onboarding_extract memories before re-writing.
    Also synthesises an initial teo_read and stores it in kv + persons.impression.
    """
    from chloe.llm.gemini import GeminiClient
    from chloe.memory import store as mem_store
    from chloe.state.kv import set as kv_set

    if conn is None:
        conn = get_connection()

    client = GeminiClient()
    extraction = None
    try:
        raw_prompt = _PROMPT_PATH.read_text().replace("{{qa_text}}", qa_text)
        raw = await asyncio.wait_for(
            client.flash_text(raw_prompt, max_output_tokens=4000),
            timeout=60,
        )
        if raw:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            extraction = json.loads(cleaned)
    except Exception as exc:
        log.warning("onboarding_extraction_failed", error=str(exc))

    if not extraction:
        return {"extraction": "failed"}

    now = datetime.now(timezone.utc).isoformat()

    # Clear previous extraction memories before re-writing (idempotent)
    conn.execute("DELETE FROM memories WHERE source='onboarding_extract'")

    # Knowledge statements → semantic memories tagged to Teo (subject_person_id=1)
    for stmt in extraction.get("knowledge_statements", []):
        if stmt.strip():
            mid = mem_store.add(
                kind="semantic",
                text=stmt.strip(),
                source="onboarding_extract",
                salience=0.85,
                weight=1.0,
                tags=["onboarding", "teo_profile", "knowledge"],
            )
            conn.execute("UPDATE memories SET subject_person_id=1 WHERE id=?", (mid,))
    conn.commit()

    # Biography → autobiographical memory tagged to Teo
    biography = (extraction.get("biography") or "").strip()
    if biography:
        mid = mem_store.add(
            kind="autobiographical",
            text=biography,
            source="onboarding_extract",
            salience=0.9,
            weight=1.0,
            tags=["onboarding", "teo_profile", "biography"],
        )
        conn.execute("UPDATE memories SET subject_person_id=1 WHERE id=?", (mid,))
        conn.commit()

    # Trait profile → persons row
    trait_profile = extraction.get("trait_profile", {})
    if trait_profile:
        conn.execute(
            "UPDATE persons SET trait_profile = ? WHERE id = 1",
            (json.dumps(trait_profile),),
        )

    # Aversions → inner_aversions (skip duplicates)
    for av in extraction.get("aversions", []):
        av = av.strip()
        if av:
            exists = conn.execute(
                "SELECT 1 FROM inner_aversions WHERE LOWER(text)=LOWER(?)", (av,)
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO inner_aversions (text, tags, resolved, created_at) VALUES (?, ?, 0, ?)",
                    (av, json.dumps(["teo", "onboarding"]), now),
                )

    # Open threads → inner_questions (skip duplicates)
    for thread in extraction.get("open_threads", []):
        thread = thread.strip()
        if thread:
            exists = conn.execute(
                "SELECT 1 FROM inner_questions WHERE LOWER(text)=LOWER(?) AND domain='teo'",
                (thread,),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO inner_questions (text, domain, intensity, resolved, created_at)"
                    " VALUES (?, 'teo', 0.5, 0, ?)",
                    (thread, now),
                )

    # Interests → semantic memories about Teo, NOT Chloe's interest_garden.
    # Chloe should develop her own interests through conversation, not inherit them at intake.
    for interest_label in extraction.get("interests", []):
        interest_label = interest_label.strip()
        if interest_label:
            mid = mem_store.add(
                kind="semantic",
                text=f"Teo is into {interest_label}",
                source="onboarding_extract",
                salience=0.75,
                weight=1.0,
                tags=["onboarding", "teo_profile", "interest"],
            )
            conn.execute("UPDATE memories SET subject_person_id=1 WHERE id=?", (mid,))
    conn.commit()

    # People → persons + person_notes + person_third_parties + subject memories
    people_created = []
    for person in extraction.get("people", []):
        name = (person.get("name") or "").strip()
        if not name:
            continue
        raw_rel = (person.get("relationship_class") or "acquaintance").strip().lower()
        rel_class = (
            "primary"   if raw_rel in ("primary", "family", "partner") else
            "secondary" if raw_rel in ("secondary", "friend", "close friend", "best friend") else
            "peripheral"
        )
        rel_desc = (person.get("relationship_desc") or "").strip()
        notes = (person.get("notes") or "").strip()
        nicknames = [
            n.strip() for n in (person.get("nicknames") or [])
            if n.strip() and n.strip().lower() != name.lower()
        ]

        # Build a one-line impression from rel_desc + first sentence of notes
        impression_parts = [rel_desc] if rel_desc else []
        if notes:
            first_sentence = notes.split(".")[0].strip()
            if first_sentence and first_sentence.lower() not in (rel_desc or "").lower():
                impression_parts.append(first_sentence)
        impression = ". ".join(impression_parts).strip(". ").strip()

        existing = conn.execute(
            "SELECT id FROM persons WHERE LOWER(name)=LOWER(?)", (name,)
        ).fetchone()
        if existing:
            pid = existing["id"]
            existing_aliases = json.loads(
                conn.execute("SELECT aliases FROM persons WHERE id=?", (pid,))
                .fetchone()["aliases"] or "[]"
            )
            merged = list(dict.fromkeys(
                [a for a in existing_aliases if a.lower() != name.lower()] + nicknames
            ))
            conn.execute(
                "UPDATE persons SET aliases=?, relationship_class=?, impression=? WHERE id=?",
                (json.dumps(merged), rel_class, impression or None, pid),
            )
        else:
            cursor = conn.execute(
                "INSERT INTO persons (name, aliases, relationship_class, warmth, distance, is_active, impression, created_at)"
                " VALUES (?, ?, ?, 50.0, 50.0, 1, ?, ?)",
                (name, json.dumps(nicknames), rel_class, impression or None, now),
            )
            pid = cursor.lastrowid
            people_created.append(name)

        # person_notes: one rich note combining everything known
        full_note_parts = [p for p in [rel_desc, notes] if p]
        full_note = ". ".join(full_note_parts).strip(". ").strip()
        if full_note:
            conn.execute(
                "INSERT INTO person_notes (person_id, text, created_at) VALUES (?, ?, ?)",
                (pid, full_note, now),
            )

        # Semantic memories tagged with subject_person_id — feeds "things she knows"
        facts = []
        if rel_desc:
            facts.append(f"{name} is {rel_desc}")
        if notes:
            # Split multi-sentence notes into individual facts
            for sentence in notes.replace("  ", " ").split("."):
                sentence = sentence.strip()
                if sentence and len(sentence) > 8:
                    facts.append(f"{name}: {sentence}")
        if nicknames:
            facts.append(f"{name} goes by {', '.join(nicknames)}")

        for fact in facts:
            mid = mem_store.add(
                kind="semantic",
                text=fact,
                source="onboarding_extract",
                salience=0.8,
                weight=1.0,
                tags=["onboarding", "person_profile", f"person:{name.lower()}"],
            )
            conn.execute("UPDATE memories SET subject_person_id=? WHERE id=?", (pid, mid))

        # Wire as third_party for Teo (person_id=1)
        existing_tp = conn.execute(
            "SELECT id FROM person_third_parties WHERE person_id=1 AND LOWER(name)=LOWER(?)",
            (name,),
        ).fetchone()
        if not existing_tp:
            conn.execute(
                "INSERT INTO person_third_parties (person_id, name, relation) VALUES (1, ?, ?)",
                (name, rel_desc or rel_class),
            )

    # Pets → knowledge memories + person_notes on Teo's record
    pets = extraction.get("pets", [])
    for pet in pets:
        pet_name = (pet.get("name") or "").strip()
        species = (pet.get("species") or "pet").strip()
        pet_notes = (pet.get("notes") or "").strip()
        if pet_name:
            desc = f"Teo has a {species} named {pet_name}"
            if pet_notes:
                desc += f". {pet_notes}"
            mem_store.add(
                kind="semantic",
                text=desc,
                source="onboarding_extract",
                salience=0.8,
                weight=1.0,
                tags=["onboarding", "teo_profile", "pet"],
            )

    conn.commit()

    # Synthesise teo_read from extracted knowledge
    knowledge = extraction.get("knowledge_statements", [])
    if knowledge:
        knowledge_text = "\n".join(f"- {s}" for s in knowledge)
        synthesis_prompt = (
            f"You are Chloe. Here is what you now know about Teo:\n\n{knowledge_text}\n\n"
            "Write one short paragraph in your voice about how you read him. "
            "Not a summary of facts — your felt sense of him: what kind of person he seems like, "
            "what feels most like him from what he shared. "
            "Write like you're thinking to yourself. Two to four sentences. Be concrete."
        )
        try:
            teo_read = await asyncio.wait_for(
                client.flash_text(synthesis_prompt),
                timeout=30,
            ) or ""
            teo_read = teo_read.strip()
            if teo_read:
                kv_set("identity:teo_read", teo_read)
                conn.execute("UPDATE persons SET impression = ? WHERE id = 1", (teo_read[:500],))
                conn.commit()
                log.info("teo_read_written", chars=len(teo_read))
        except Exception as exc:
            log.warning("teo_read_synthesis_failed", error=str(exc))

    log.info(
        "onboarding_extraction_complete",
        knowledge=len(knowledge),
        people_created=people_created,
        pets=len(pets),
        interests=len(extraction.get("interests", [])),
        aversions=len(extraction.get("aversions", [])),
        threads=len(extraction.get("open_threads", [])),
    )
    return {
        "knowledge_statements": len(knowledge),
        "biography": bool(biography),
        "people_found": [p.get("name") for p in extraction.get("people", [])],
        "people_created": people_created,
        "pets": [p.get("name") for p in pets if p.get("name")],
        "traits": trait_profile,
        "interests": extraction.get("interests", []),
        "aversions": extraction.get("aversions", []),
        "open_threads": extraction.get("open_threads", []),
    }
