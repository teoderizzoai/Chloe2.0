"""Trait model — earned through accumulated behavioral evidence.

Traits are never declared; they emerge from patterns observed across reflect
windows. Gen-levels gate how a trait is labeled and whether it influences
Chloe's self-model:

  0 = behavioral description  "tends to say things before finishing deciding"
  1 = character label         "direct"  (after 10+ instances, 3+ windows)
  2 = core trait              (sustained weight > 0.7 for 30+ days)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TypedDict

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("identity.trait_model")

EVIDENCE_THRESHOLD = 10
WINDOW_THRESHOLD = 3
CORE_WEIGHT_THRESHOLD = 0.7
CORE_SUSTAINED_DAYS = 30
WEIGHT_INITIAL = 0.1
WEIGHT_REINFORCE = 0.05
WEIGHT_CONTRADICT = 0.03
WEIGHT_DECAY_STALE_DAYS = 14
WEIGHT_DECAY_AMOUNT = 0.02


class TraitEvidence(TypedDict):
    behavior_observed: str
    at: str
    context: str


def record_trait_evidence(
    behavior_observed: str,
    trait_implied: str,
    reinforces: str | None = None,
    contradicts: str | None = None,
    context: str = "",
) -> None:
    """Called once per trait_evidence item coming out of reflect.

    Creates the trait if it doesn't exist, updates weight and evidence/
    contradictions lists, increments windows_observed if the most recent
    reinforcement was in a different window, and checks for gen_level promotion.
    """
    if not behavior_observed.strip():
        return

    conn = get_connection()
    now_ts = datetime.now(timezone.utc).isoformat()

    trait_name = (reinforces or contradicts or trait_implied or "").strip()
    if not trait_name:
        return

    # Snap to approved personality vocabulary; empty return means it was an emotional word — skip.
    trait_name = _snap_to_approved_trait(trait_name)
    if not trait_name:
        return
    trait_name = _resolve_trait_name(trait_name, conn)

    row = conn.execute(
        "SELECT * FROM identity_traits WHERE name = ?", (trait_name,)
    ).fetchone()

    entry: TraitEvidence = {
        "behavior_observed": behavior_observed[:300],
        "at": now_ts,
        "context": context[:200],
    }

    if row is None:
        # First observation — create at gen_level 0 with behavioral description
        conn.execute(
            """INSERT INTO identity_traits
               (name, weight, status, gen_level, evidence_json, contradictions_json,
                first_observed_at, last_reinforced, windows_observed, created_at, updated_at)
               VALUES (?, ?, 'emerging', 0, ?, '[]', ?, ?, 1, ?, ?)""",
            (
                trait_name, WEIGHT_INITIAL,
                json.dumps([entry] if reinforces else []),
                now_ts, now_ts, now_ts, now_ts,
            ),
        )
        conn.commit()
        log.info("trait_first_observed", name=trait_name, weight=WEIGHT_INITIAL)
        _register_trait_embedding(trait_name)
        return

    row = dict(row)
    gen_level = int(row.get("gen_level") or 0)
    weight = float(row.get("weight") or WEIGHT_INITIAL)
    last_reinforced = row.get("last_reinforced")

    # Determine if this is a new reflect window (>= 1h gap from last reinforcement)
    new_window = False
    if last_reinforced:
        try:
            last_dt = datetime.fromisoformat(last_reinforced)
            if datetime.now(timezone.utc) - last_dt >= timedelta(hours=1):
                new_window = True
        except Exception:
            new_window = True
    else:
        new_window = True

    windows_observed = int(row.get("windows_observed") or 0)
    if new_window:
        windows_observed += 1

    if reinforces:
        evidence = _parse_json_list(row.get("evidence_json"))
        evidence.append(entry)
        new_weight = min(1.0, weight + WEIGHT_REINFORCE)
        conn.execute(
            """UPDATE identity_traits
               SET weight=?, evidence_json=?, last_reinforced=?, windows_observed=?, updated_at=?
               WHERE name=?""",
            (new_weight, json.dumps(evidence), now_ts, windows_observed, now_ts, trait_name),
        )
    elif contradicts:
        contras = _parse_json_list(row.get("contradictions_json"))
        contras.append(entry)
        new_weight = max(0.0, weight - WEIGHT_CONTRADICT)
        conn.execute(
            """UPDATE identity_traits
               SET weight=?, contradictions_json=?, last_reinforced=?, windows_observed=?, updated_at=?
               WHERE name=?""",
            (new_weight, json.dumps(contras), now_ts, windows_observed, now_ts, trait_name),
        )
    else:
        return

    conn.commit()

    # Reload to check promotion
    updated = dict(conn.execute(
        "SELECT * FROM identity_traits WHERE name=?", (trait_name,)
    ).fetchone())
    _maybe_promote(updated, conn)


def apply_stale_decay() -> int:
    """Reduce weight of traits not reinforced for WEIGHT_DECAY_STALE_DAYS. Returns count."""
    conn = get_connection()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=WEIGHT_DECAY_STALE_DAYS)).isoformat()
    rows = conn.execute(
        """SELECT name, weight, last_reinforced FROM identity_traits
           WHERE status NOT IN ('archived') AND (last_reinforced IS NULL OR last_reinforced < ?)""",
        (cutoff,),
    ).fetchall()
    count = 0
    for row in rows:
        new_w = max(0.0, float(row["weight"]) - WEIGHT_DECAY_AMOUNT)
        conn.execute("UPDATE identity_traits SET weight=?, updated_at=? WHERE name=?",
                     (new_w, datetime.now(timezone.utc).isoformat(), row["name"]))
        count += 1
    if count:
        conn.commit()
    log.info("trait_stale_decay", decayed=count)
    return count


def get_active_traits(min_gen_level: int = 0) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT name, weight, gen_level, status, windows_observed
           FROM identity_traits
           WHERE status NOT IN ('archived') AND gen_level >= ?
           ORDER BY weight DESC""",
        (min_gen_level,),
    ).fetchall()
    return [dict(r) for r in rows]


def _maybe_promote(row: dict, conn) -> None:
    name = row["name"]
    gen_level = int(row.get("gen_level") or 0)
    weight = float(row.get("weight") or 0.0)
    evidence_count = len(_parse_json_list(row.get("evidence_json")))
    windows = int(row.get("windows_observed") or 0)
    now_ts = datetime.now(timezone.utc).isoformat()

    if gen_level == 0 and evidence_count >= EVIDENCE_THRESHOLD and windows >= WINDOW_THRESHOLD:
        conn.execute(
            "UPDATE identity_traits SET gen_level=1, status='active', updated_at=? WHERE name=?",
            (now_ts, name),
        )
        conn.commit()
        log.info("trait_promoted_to_label", name=name, evidence=evidence_count, windows=windows)
        return

    if gen_level == 1 and weight >= CORE_WEIGHT_THRESHOLD:
        core_since = row.get("core_since")
        if core_since is None:
            # Start the clock
            conn.execute(
                "UPDATE identity_traits SET core_since=?, updated_at=? WHERE name=?",
                (now_ts, now_ts, name),
            )
            conn.commit()
        else:
            try:
                since_dt = datetime.fromisoformat(core_since)
                if datetime.now(timezone.utc) - since_dt >= timedelta(days=CORE_SUSTAINED_DAYS):
                    conn.execute(
                        "UPDATE identity_traits SET gen_level=2, status='core', updated_at=? WHERE name=?",
                        (now_ts, name),
                    )
                    conn.commit()
                    log.info("trait_promoted_to_core", name=name, weight=weight,
                             sustained_days=CORE_SUSTAINED_DAYS)
            except Exception:
                pass


def _parse_json_list(raw) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Approved trait vocabulary — LLM is instructed to pick from this list;
# _snap_to_approved_trait() enforces it as a Python-level safety net.
# ---------------------------------------------------------------------------

_APPROVED_TRAITS: frozenset[str] = frozenset({
    # Personality traits only — stable character patterns.
    # Emotional states (Anxious, Curious as a mood, etc.) are not here;
    # they are captured as current_emotions in reflect_inner_state.
    "Accommodating", "Adventurous", "Ambitious", "Amiable", "Analytical", "Articulate",
    "Assertive", "Authentic", "Balanced", "Benevolent", "Bold", "Caring", "Cautious",
    "Charismatic", "Clever", "Committed", "Compassionate", "Conscientious", "Considerate",
    "Cooperative", "Courageous", "Creative", "Curious", "Daring", "Decisive", "Dependable",
    "Determined", "Diplomatic", "Discerning", "Disciplined", "Dreamer", "Energetic",
    "Faithful", "Flexible", "Focused", "Frank", "Friendly", "Funny", "Generous", "Gracious",
    "Grateful", "Humble", "Humorous", "Idealistic", "Impartial", "Impulsive", "Independent",
    "Ingenious", "Innovative", "Insightful", "Inspiring", "Intelligent", "Inquisitive",
    "Introverted", "Judicious", "Kind", "Logical", "Methodical", "Meticulous", "Modest",
    "Motivated", "Nonjudgmental", "Nurturing", "Objective", "Observant", "Open-minded",
    "Organized", "Passionate", "Patient", "Pensive", "Perceptive", "Persevering", "Persistent",
    "Pioneering", "Practical", "Proactive", "Quiet", "Rational", "Realistic", "Reflective",
    "Relaxed", "Reliable", "Reserved", "Resourceful", "Respectful", "Self-critical", "Selfless",
    "Sensible", "Serene", "Serious", "Smart", "Spontaneous", "Steadfast", "Stoic", "Strategic",
    "Supportive", "Tactful", "Tenacious", "Thoughtful", "Timid", "Trustworthy", "Understanding",
    "Versatile", "Visionary", "Warm-hearted", "Whimsical", "Wise", "Witty",
})

_APPROVED_TRAITS_LOWER: dict[str, str] = {t.lower(): t for t in _APPROVED_TRAITS}

# Emotional states — belong in current_emotions, not in personality trait_evidence.
# If the LLM sends one of these as trait_implied, reject it (return "") rather than
# fuzzy-mapping it to a wrong personality word.
_EMOTIONAL_WORDS: frozenset[str] = frozenset({
    "affectionate", "alarmed", "amused", "angry", "annoyed", "anxious", "apathetic",
    "appreciative", "ashamed", "bewildered", "bitter", "bored", "calm", "cheerful",
    "confident", "confused", "content", "defeated", "delighted", "depressed",
    "despairing", "disappointed", "disgusted", "eager", "ecstatic", "elated",
    "embarrassed", "empathetic", "enraged", "envious", "excited", "fearless",
    "frightened", "frustrated", "grateful", "grumpy", "guilty", "happy", "hopeful",
    "hopeless", "horrified", "humiliated", "impatient", "impressed", "irritated",
    "imaginative", "indifferent", "jealous", "jolly", "joyful", "lonely", "loving",
    "melancholic", "mischievous", "nervous", "optimistic", "overjoyed", "passionate",
    "patient", "peaceful", "pessimistic", "perceptive", "pleased", "proud", "relieved",
    "romantic", "sad", "satisfied", "scared", "sensitive", "shocked", "shy", "surprised",
    "sympathetic", "terrified", "thankful", "thoughtful", "tolerant", "trusting",
    "unhappy", "warm", "worried",
})


def _snap_to_approved_trait(candidate: str) -> str:
    """Map any trait string to the nearest approved vocabulary word.

    Tries exact match (case-insensitive) first, then falls back to difflib
    closest-match. Returns the candidate unchanged only if no reasonable match
    exists (similarity < 0.6), which means 'log and pass through' for Chroma
    dedup to handle.
    """
    if not candidate:
        return candidate
    normalized = candidate.strip().lower()
    # Reject emotional states — they belong in current_emotions, not trait_evidence.
    if normalized in _EMOTIONAL_WORDS:
        log.info("trait_rejected_emotional", candidate=candidate)
        return ""
    if normalized in _APPROVED_TRAITS_LOWER:
        return _APPROVED_TRAITS_LOWER[normalized]
    import difflib
    matches = difflib.get_close_matches(normalized, _APPROVED_TRAITS_LOWER.keys(), n=1, cutoff=0.7)
    if matches:
        snapped = _APPROVED_TRAITS_LOWER[matches[0]]
        if snapped != candidate:
            log.info("trait_snapped", original=candidate, snapped=snapped)
        return snapped
    log.warning("trait_not_in_vocabulary", candidate=candidate)
    return candidate


# ---------------------------------------------------------------------------
# Semantic dedup helpers
# ---------------------------------------------------------------------------

_TRAIT_EMBED_COLLECTION = "trait_labels_v1"
_TRAIT_DEDUP_THRESHOLD = 0.88  # slightly tighter than interest dedup


def _resolve_trait_name(candidate: str, conn) -> str:
    """Return the canonical trait name for `candidate`.

    Checks the trait embedding collection first. If a stored trait is
    semantically close (≥0.88 cosine similarity), return its name instead of
    the candidate so evidence accumulates on the same trait rather than
    splitting into near-duplicates like "direct" vs "straightforward".

    Falls back to the candidate unchanged if Chroma is unavailable.
    """
    try:
        from chloe.state.chroma import get_collection
        collection = get_collection(_TRAIT_EMBED_COLLECTION)
        if collection.count() == 0:
            return candidate
        result = collection.query(
            query_texts=[candidate],
            n_results=1,
            include=["distances", "metadatas"],
        )
        distances = result.get("distances", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        if not distances:
            return candidate
        similarity = 1.0 / (1.0 + distances[0])
        if similarity >= _TRAIT_DEDUP_THRESHOLD and metas:
            canonical = metas[0].get("trait_name")
            if canonical and canonical != candidate:
                log.info(
                    "trait_semantic_merged",
                    candidate=candidate,
                    canonical=canonical,
                    similarity=round(similarity, 3),
                )
                return str(canonical)
    except Exception as exc:
        log.debug("trait_semantic_resolve_failed", error=str(exc))
    return candidate


def _register_trait_embedding(name: str) -> None:
    """Register a new trait name in the embedding collection for future dedup."""
    try:
        from chloe.state.chroma import get_collection
        collection = get_collection(_TRAIT_EMBED_COLLECTION)
        collection.upsert(
            ids=[name],
            documents=[name],
            metadatas=[{"trait_name": name}],
        )
    except Exception as exc:
        log.debug("trait_embedding_register_failed", error=str(exc))
