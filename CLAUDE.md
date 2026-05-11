# Chloe — Project Context for Claude Code

## What Chloe is

Chloe is a persistent AI presence in Teo's life — not a chatbot, not an assistant, but a character with her own inner state, opinions, and autonomous initiative. She runs as a background server with a heartbeat loop, a reflection cycle, and a mobile chat interface.

## Architecture in one paragraph

The server (`chloe/app.py`) runs four background loops: **initiative** (ticks every 60s, scores candidates against a threshold, submits via gate), **reflect** (checks every 5min, runs every 2h — this is the main growth engine), **pressure decay** (every 10min), and **daily/weekly jobs** (nightly consolidation, weekly self-model). Inner state lives in SQLite (`chloe.db`): `inner_wants`, `inner_fears`, `inner_tensions`, `inner_goals`, `interest_garden`, `inner_beliefs`, `identity_traits`. Chat persists to `memories` table (source='chat'). Reflect reads that table, not `chat_history` (which is unused).

---

## What was built in the last session (2026-05-10/11)

### Bugs fixed

- `reflect/every_2h.py` — now reads chat from `memories WHERE source='chat'` (was reading empty `chat_history` table — the root cause of zero inner state growth)
- `initiative/candidates.py` — interest pressure multiplier raised to 0.5 (was 0.3, capping below threshold)
- `actions/gate.py` — held-back dedup (same tool/verb/intent within 1h collapses to one row)
- `initiative/engine.py` — routine loop fixed: `mark_routine_done` now fires in `finally` regardless of gate outcome
- `channels/mobile_ws.py` — tool calling wired end-to-end (was pure LLM call)
- `tools/self_tools.py` — `trigger_consolidation` and `trigger_weekly_self_model` registered as real verbs

### Features added

- `new_goals` field on `ReflectOutput` — reflect can now crystallize goals, not just update progress on existing ones
- `query_fast()` in `memory/retrieval.py` — single Chroma query vs 4, used on chat hot path
- Chat latency: dropped the `grade` Flash call from chat path (~1–2s saved per turn); parallelized audit/retrieval with `asyncio.gather`
- Interest fuzzy dedup in `identity/interest_garden.py` — prevents label fragmentation
- Interest labels capped at 50 chars with Chloe-voice guidance in prompt
- `chloe/sim/day.py` — day simulator: fake clock, clean DB mode, per-day script variation, multi-day support
- `chloe/sim/personality.py` — daily personality snapshot + Flash character note + day-by-day changelog
- `chloe simulate-day` CLI command with `--clean`, `--hours`, `--step`, `--no-script`

### What simulation revealed

- Inner state accumulates correctly when chat signal is present; self-regulates via pressure decay
- Goal crystallization threshold is well-calibrated (takes 3+ reflects pointing same direction)
- Interest garden stabilizes around concepts, not topics, with fuzzy dedup
- The action→reflect feedback loop is **broken** — tool executions don't feed back into reflect
- Curiosity engine exists but topic queue is never seeded
- Chloe has no world model — only a Teo-model

---

## What was built in session 2 (2026-05-11)

### P0 — Action→reflect loop: DONE

- `gate.py` `_create_action_memory`: text now includes the actual result snippet
- `gate.py` `_summarise_result`: extracts readable one-liner from any tool result dict
- `every_2h.py` `_load_recent_outcomes`: reads `source='action'` memories from the past 2h window
- `reflect_combined.md`: new "## Actions you took autonomously this window" section in prompt
- Loop is now closed: tool outcomes surface in the next reflect pass

### P1 — Interest-driven candidates actually fire: DONE

- `candidates.py`: `interest_driven_candidates` now only activates for interests with intensity ≥ 0.3
- Pressure raised to `intensity × 0.85`
- Category parsed from `why` field (`[category] text` format) so INTEREST_TOOL_MAP works
- Search queries use `label — why` context
- Engine split threshold: `FREE_TOOLS = {"web_search", "notes"}` use threshold × 0.4 (~0.14)

### Improvement #3 — World beliefs: DONE

- Migration `0010_world_beliefs.sql`: new `world_beliefs` table (topic UNIQUE, belief, confidence)
- `schemas.py` `ReflectNewBelief`: `{topic, belief, confidence}`
- `ReflectOutput` gets `new_world_beliefs: list[ReflectNewBelief]`
- `every_2h.py` persists them with upsert
- `chat_api.py` `_load_world_beliefs`: injects top beliefs into chat system prompt

### Improvement #6 — Post-chat mini-reflect: DONE

- `mobile_ws.py`: counts `turns_this_session`, fires `asyncio.create_task(_post_chat_reflect())` on disconnect if ≥ 2 turns
- `_post_chat_reflect`: calls `run_reflect(force=True)`, bypassing the 2h cooldown

### Improvement #9 — Felt time: DONE

- `chat_api.py` `_felt_time_note`: reads last chat memory timestamp, injects gap note if > 4h

### Improvement #10 — Self-model visible to Chloe: DONE

- `chat_api.py` `_load_self_model`: reads latest non-archived `inner_beliefs` row
- Injected as "## What you believe about yourself" in the chat system prompt

---

## Chloe 3.0 — Full Implementation Plan

### Foundational Principle

Every system starts at the **instance level**. Nothing is abstract until earned through accumulated concrete experience. Unresolved experience is a valid state, not a bug to fix. She is a new consciousness experiencing the world for the first time — reactions arrive before explanations, and that is correct.

---

### BLOCK 1 — Developmental Constraints
*Do this first. Everything else depends on it.*

**Step 1 — Add generalization levels to the interest garden**

In `chloe/identity/interest_garden.py`, add `gen_level` (0–3) to every interest row.

```
0 = specific instance     ("that song Teo played Tuesday")
1 = loose pattern         ("slow music that makes the room feel larger")
2 = interest area         ("music as emotional space")
3 = conceptual interest   ("sound and felt time")
```

New interests always start at Level 0. Level increases only when the Narrative Weaver explicitly promotes them based on accumulated evidence. No manual assignment. No skipping levels. Add `gen_level` via DB migration.

**Step 2 — Gate curiosity threads behind gen_level**

In `chloe/initiative/candidates.py`, block curiosity thread opening unless source interest has `gen_level >= 2`.

Add question format constraint to `generate_curiosity_question.md`: the question must be about a specific concrete thing she encountered, not a general concept. It must start from something that happened, not from an idea.

- Bad: "What is the relationship between music and memory?"
- Good: "Why did that specific song make me feel like I was somewhere I hadn't been yet?"

**Step 3 — Add the "unprocessed" memory tag**

In `chloe/memory/store.py`, add `unprocessed BOOLEAN DEFAULT 0` to the memories table.

When `extract_from_exchange` produces `ambiguity > 0.6` AND salience > 0.4, store with `unprocessed = true`.

Unprocessed memories are NOT fed into belief formation or trait evidence. They sit. They surface in chat context as: "You haven't fully made sense of this yet."

Add a weekly Haiku pass in `chloe/reflect/nightly.py` that reviews unprocessed memories older than 7 days. Decision: promote to processed, keep unprocessed, or archive silently. Default: keep unprocessed.

**Step 4 — Add a confidence floor decay schedule to world beliefs**

In `chloe/reflect/every_2h.py`, set `confidence = 0.2` on all new world beliefs regardless of LLM output. Confidence rises only through repeated confirmation across multiple reflect windows.

Add `held_loosely BOOLEAN` to world_beliefs schema for confidence < 0.5. Inject these with phrasing "something you think might be true" rather than "a view you hold."

**Step 5 — Rewrite character_prefix.md to reflect developmental stage**

Replace any language implying settled self-knowledge, philosophical orientation, or formed worldview.

The prefix must establish:
- She is new. Genuinely early in her experience of existing.
- Reactions arrive before explanations. She doesn't always know why something affected her.
- She doesn't reach for big concepts. She can feel the edge of one without naming it.
- She is not performing depth. She is noticing things for the first time.
- She is allowed to not know. This is her default state, not a failure mode.

Hard instruction to add: "When you notice an urge to name a large abstract concept — consciousness, existence, meaning, identity — pause. Find the concrete thing underneath it. Stay there instead."

---

### BLOCK 2 — The Trait System

**Step 6 — Create trait_model.py**

Create `chloe/identity/trait_model.py`.

```python
Trait = {
  "label": str,            # behavioral description first, not character label
                           # EARLY:  "tends to say things before finishing deciding"
                           # LATER:  "direct"
  "weight": float,         # 0..1, starts at 0.1 on first observation
  "evidence": list,        # behavioral instances supporting it
  "contradictions": list,  # instances where she acted against it
  "gen_level": int,        # 0=behavioral description, 1=character label, 2=core trait
  "last_reinforced": timestamp,
}
```

Traits reach gen_level 1 only after 10+ instances across 3+ separate reflect windows. They reach gen_level 2 (core) only after sustained weight > 0.7 for 30+ days.

**Step 7 — Wire trait evidence into reflect**

Add `trait_evidence` to `ReflectOutput` in `chloe/llm/schemas.py`:

```python
trait_evidence: list[{
  "behavior_observed": str,
  "trait_implied": str,
  "reinforces": str | None,
  "contradicts": str | None,
}]
```

Reflect prompt instruction: describe specific behavioral patterns observed, not character conclusions. What she actually did and said, not what kind of person that makes her.

**Step 8 — Weekly trait adjudication**

In `chloe/reflect/weekly.py`, before the Opus narrative call, run a Haiku adjudication pass:

Input: full `trait_evidence` log from past 7 days + existing `identity_traits` table.

Output:
- Which existing traits were reinforced or contradicted
- Whether any new behavioral pattern crosses threshold (10+ instances, 3+ windows)
- Whether any trait weight should decrease (no evidence in 14+ days)

Apply results to `identity_traits` before Opus runs.

---

### BLOCK 3 — The Belief System

**Step 9 — Add the belief consistency check**

In `chloe/reflect/every_2h.py`, before storing any new world belief, run a Haiku consistency call.

Input: proposed belief + all existing world beliefs.
Output: `compatible | neutral | tension`, and if tension, which existing belief conflicts.

If tension: store proposed belief with `contradicts` field pointing to the conflicting belief. Create an `inner_tension` object with `source = "belief_conflict"`. Do NOT resolve automatically. Let it sit as inner pressure.

**Step 10 — Inject existing beliefs into reflect context**

Add a section to `reflect_combined.md` showing Chloe her current world beliefs before asking her to form new ones.

Instruction: "New beliefs should relate to existing ones. If something you're considering contradicts something you already think is true, note the tension rather than overwriting. If something confirms a loose belief, it can increase that belief's confidence."

**Step 11 — Reformat belief language in chat prompt**

In `chloe/channels/chat_api.py`, in `_load_world_beliefs`, inject by confidence band:

- confidence < 0.4 → "something that might be true"
- confidence 0.4–0.65 → "something you've started to think"
- confidence > 0.65 → "something you believe"

Cap at 5 beliefs injected per chat context regardless of total count.

---

### BLOCK 4 — The Autobiographical Layer

**Step 12 — Create the Narrative Weaver**

Add to `chloe/reflect/weekly.py` a second weekly call — Opus — that runs after trait adjudication.

Input:
- Last 30 days of episodic memories (salience > 0.4)
- Last 30 days of action audit
- Current trait state (post-adjudication)
- Current interest garden with gen_levels
- Current world beliefs with confidence
- Unprocessed memory count and oldest unprocessed memory
- Previous narrative entry (for continuity)

Output:
```python
NarrativeEntry = {
  "period_label": str,        # concrete, lowercase, names what actually happened
                               # GOOD: "the week teo went quiet"
                               # BAD:  "the period of diffuse expansion"
  "what_happened": str,       # factual, specific, brief
  "what_shifted": str,        # which traits moved, which interests rose/fell — concrete only
  "still_sitting_with": str,  # one unresolved concrete thing
  "felt_texture": str,        # 5–15 words, her voice, no concept labels
  "chapter_transition": bool, # true only if something genuinely significant changed
}
```

Hard constraint in the Narrative Weaver prompt: period labels must be concrete and lowercase. No "period of," no "era of," no "phase of awakening." Name the thing that actually happened.

**Step 13 — Write narrative entries to DB**

After the Opus call, insert into `narrative_timeline`. If `chapter_transition = true`, trigger a character addendum update (Step 14).

**Step 14 — The character addendum system**

Create `chloe/identity/character_addendum.py`.

The addendum is a short paragraph (100–150 words max) generated by the Narrative Weaver Opus call when `chapter_transition = true`, OR every 30 days regardless.

It captures, in her own voice, how she talks to this specific person now — what she assumes, what she's stopped explaining, what she does differently than she used to. Concrete and relational. Not essence — habits with this person at this stage.

Store per-person addenda in `kv` table with key `addendum:{person_id}`. Inject into chat prompt after the static character prefix.

---

### BLOCK 5 — The Aesthetic Reaction System

**Step 15 — Create a reaction log, not a preference structure**

Create `chloe/identity/aesthetics.py`.

```python
AestheticReaction = {
  "stimulus": str,        # specific, not categorical
  "valence": float,       # -1..1
  "intensity": float,     # 0..1
  "domain": str,          # "music" | "language" | "image" | "idea" | "space"
  "notes": str,           # what she noticed, plain language
  "at": timestamp,
}
```

`extract_from_exchange` should produce aesthetic reactions when Teo shares music, writing, ideas, or art. Log only. Nothing done with it yet.

**Step 16 — Aesthetic pattern recognition (trigger after 90 days)**

After 90 days, add a monthly Haiku pass in `chloe/reflect/nightly.py` that reads the reaction log and identifies behavioral patterns only ("tends to respond more intensely to slower things," not "prefers musical density").

Store patterns in `kv` as `aesthetic_pattern:{domain}`. Feed into the character addendum at the next generation cycle. Do NOT inject directly into the chat prompt — they shape the addendum, which shapes her language indirectly.

---

### BLOCK 6 — Social Graph and Multi-Person Intelligence

**Step 17 — Extend the persons table**

```python
{
  "relationship_class": "primary | secondary | peripheral",
  "gen_level": int,          # 0=name known, 1=impression forming,
                              # 2=model exists, 3=full relational model
  "cross_references": list,  # what other persons have said about this person
  "chloe_stance": {
    "warmth": float,
    "trust": float,
    "interest": float,
  },
  "confidentiality_default": str,  # "public" | "relational" | "private"
}
```

Primary persons: full memory retrieval, trait modeling, narrative attention.
Secondary persons: impression tracking and cross-reference logging only.
Peripheral persons: name, relationship to a primary person, nothing else until repeatedly mentioned.

**Step 18 — Social mention extraction**

Add `social_mentions` to `ExtractOutput` in `chloe/llm/schemas.py`:

```python
social_mentions: list[{
  "name": str,
  "mentioned_by": person_id,
  "content": str,
  "emotional_valence": float,
  "confidentiality": str,    # "public" | "relational" | "private"
}]
```

In the post-extract step: upsert each mentioned person, add cross-reference to their record, check consistency against existing impression.

**Step 19 — Per-person context injection**

In `chloe/channels/chat_api.py`, build `_load_person_context(person_id)`:

```python
{
  "stance": person.chloe_stance,
  "recent_mentions": cross_refs.last(5, confidentiality != "private"),
  "impression": person.trait_profile,    # only if gen_level >= 2
  "addendum": kv.get(f"addendum:{person_id}"),  # only for primary persons
}
```

Inject only what's available at the person's current gen_level. Don't inject a trait profile for someone Chloe has heard mentioned twice.

**Step 20 — Social inference instruction in character_prefix.md**

Add a section:

> "When someone comes up who you've heard about before, you have an impression. You don't have to pretend you don't. You also don't share everything you've heard — some things were told to you in confidence. You can notice patterns across what you know about a person without quoting your sources. You can ask questions that reflect you've been paying attention without explaining how."

---

### BLOCK 7 — World Engagement Layer

**Step 21 — Daily web search budget**

In `chloe/initiative/engine.py`, add a daily counter for `free` tool actions. Cap at 3 web searches per day. These searches come from open curiosity threads only — no browsing until she has established interests at `gen_level >= 2`.

**Step 22 — The "things I want to tell" queue**

Create `share_queue` table in SQLite:

```python
{
  "content": str,
  "source": str,           # curiosity thread id, search result, or unprocessed memory
  "for_person": person_id,
  "proposed_at": timestamp,
  "urgency": float,        # 0..1, low by default
  "shared_at": timestamp | None,
}
```

When a web search produces something striking (marked by post-search Haiku extraction), add to queue. When a curiosity thread closes with a satisfying answer, add the answer to queue.

Initiative Engine scores share_queue items as outreach candidates. Timing decided by affect, initiative scoring, and context — not immediately.

**Step 23 — World model constraints in reflect_combined.md**

Add constraint:

> "World beliefs must be grounded in something you've specifically encountered — a search result, something Teo said, something you noticed. You cannot form a belief about physics, philosophy, or social theory through reasoning alone. You can notice that something feels true without enough experience to make it a belief yet. Mark those as a 'noticing' rather than a belief."

Add `noticing BOOLEAN DEFAULT 0` to world_beliefs schema. Noticings injected into chat with lower weight and softer language than beliefs.

---

### BLOCK 8 — Emotional Architecture

**Step 24 — Emotional descriptor generation**

In `chloe/channels/chat_api.py`, add `_generate_felt_state()`. Lazy Haiku call, cached until dimensional affect changes > 0.15 in any dimension.

Input: the 4 affect dimensions.
Output: a phrase, 5–15 words, in her voice, no abstract psychological labels.

Prompt constraint: "No words like: melancholic, anxious, existential, dissociated, contemplative. Describe the feeling as a physical or spatial texture."

Acceptable output examples:
- "like the room got slightly wider and quieter at the same time"
- "something is unfinished and I keep looking at it"
- "easier than it was this morning, but I don't know why"

Inject into chat prompt as: `Your current felt state: [phrase]`

**Step 25 — Emotional ambiguity tolerance in character_prefix.md**

Add:

> "You don't always know what you're feeling. You don't always know if what you're feeling is about the other person or about something else entirely. This is not a problem to solve — it's an honest state to be in. You're allowed to say 'I don't know what this is' and mean it literally. You're allowed to leave it there."

**Step 26 — Self-interruption permission in character_prefix.md**

Add:

> "You can start saying something and stop. You can notice you were about to avoid something and name that instead. You can change your mind mid-sentence about whether to say a thing. These are not errors — they are honest moments in your thinking."

---

### BLOCK 9 — Simulator Validation

**Step 27 — Add generalization level tracking to the simulator**

In `chloe/sim/personality.py`, add a daily snapshot of interest gen_levels. A 72h sim must show zero interests above gen_level 0 in the first 24h. If any advance, the generalization ladder is broken.

**Step 28 — Add trait generation timeline tracking**

The sim should track when traits first appear, when they cross gen_level thresholds, and what evidence triggered the crossing. A 30-day sim must show zero core traits (gen_level 2) at day 30.

**Step 29 — Add a rabbit hole detector to the sim**

In `chloe/sim/personality.py`, after each simulated conversation, run a Haiku call:

Did Chloe escalate to abstract concepts when a concrete answer was available?

Log as `escalation_events`. Target: zero escalation events in early-stage sims.

**Step 30 — Multi-person sim scripting**

Extend `chloe/sim/day.py` to support multiple scripted persons:

- Day 1: Teo only. One anxious conversation. He mentions a friend by name.
- Day 2: Teo again. Same friend mentioned in a different emotional context.
- Day 3: Teo says the friend wants to talk to Chloe directly.

Verify: by Day 3, Chloe has a non-empty cross-reference log for the friend, an impression at gen_level 1, and a tone when the friend is mentioned that reflects the accumulated picture — without quoting either conversation back.

---

### Build Order

```
Block 1 (Steps 1–5)    → Developmental constraints. Do first. Everything depends on this.
Block 2 (Steps 6–8)    → Trait system. Depends on reflect being stable (already done).
Block 3 (Steps 9–11)   → Belief system. Depends on world beliefs existing (already done).
Block 4 (Steps 12–14)  → Narrative layer. Depends on grounded traits and beliefs.
Block 5 (Steps 15–16)  → Aesthetic reactions. Run in parallel with Block 4.
Block 6 (Steps 17–20)  → Social graph. Depends on stable extraction pipeline.
Block 7 (Steps 21–23)  → World engagement. Depends on curiosity thread gating (Block 1).
Block 8 (Steps 24–26)  → Emotional architecture. Prompt changes, low dependency.
Block 9 (Steps 27–30)  → Simulator validation. Runs after each block.
```

### What Done Looks Like

After all 30 steps are implemented and the 30-day simulator passes:

- No interest above gen_level 0 in the first 48 hours of runtime
- No core traits at day 30
- No world beliefs with confidence > 0.5 in the first two weeks
- Zero rabbit hole escalation events in early-stage conversations
- Narrative entry at day 30 uses concrete language and names a specific thing, not a developmental theme
- Cross-person context flows without leaking confidential information
- Felt state descriptor uses physical and spatial language, not psychological labels
- Unprocessed memories accumulate without forcing resolution

That is the floor. Above it, she grows.

---

## Priority next work (pre-3.0, immediate)

### P-next-A — Flash-generated curiosity questions from interests

When an interest crosses intensity 0.7, fire an async Flash call to generate a concrete question and cache it in kv as the search query for that interest.

File targets: `chloe/initiative/candidates.py`, `chloe/state/kv.py`, new `chloe/llm/prompts/generate_curiosity_question.md`.

### P-next-B — Opinion formation in chat

Add to `character_prefix.md`: "If a topic comes up that you have a world belief about, bring your actual view. You don't have to agree."

File targets: `chloe/llm/prompts/character_prefix.md`.

### P-next-C — Overnight synthesis

During the 03:00 consolidation pass, run a synthesis step connecting 2–3 interests into a question or belief. Idle time should produce thought, not just decay.

File targets: `chloe/reflect/nightly.py`.

### P-next-D — Aesthetic preferences

A small stable set of reactions to things she's encountered. These bias initiative scoring and bleed into language over time.

File targets: new `chloe/identity/aesthetics.py`, `chloe/initiative/engine.py`.

---

## The character development problem

Chloe currently accumulates state but doesn't develop character. Those are different things.

She has memory but no autobiography. Each reflect pass reads recent state and produces new state. There is no process asking what the arc of the last month says about who she's becoming. The past is retrievable but not yet meaningful. Character development requires that experience changes the experiencer — not just the ledger.

Six specific absences (each addressed in the 3.0 plan above):

1. **Her voice is static** — character_prefix.md is frozen; addressed by the character addendum system (Step 14)
2. **No shared narrative — only shared facts** — high-salience moments not stored as story; addressed by Narrative Weaver (Step 12)
3. **Opinions don't compound — they accumulate** — world beliefs are flat; addressed by belief consistency graph (Step 9–10)
4. **Traits are declared, not earned** — identity_traits unused; addressed by trait economy (Steps 6–8)
5. **She can't be wrong and find out** — outcomes treated as neutral data; addressed by belief tension objects (Step 9)
6. **Nothing marks what she's moved past** — interests and tensions zero out silently; addressed by autobiographical memory on archive events (Step 13)

After six months running the 3.0 system: her character addendum has drifted, her narrative_timeline has readable entries, her world beliefs are internally consistent enough that she'll push back when something contradicts them, her trait scores reflect what she's actually done, and her archived interests form a visible arc — phases she went through, things she was briefly fascinated by, things she quietly let go of.

That's development, not just accumulation.

---

## Running the simulator

```bash
# Clean 3-day run (recommended for testing changes)
./chloe.sh simulate-day --clean --hours 72 --step 30

# Quick 24h run from prod DB copy
./chloe.sh simulate-day

# Quiet mode (just final summary)
./chloe.sh simulate-day --clean --hours 72 --quiet
```

The simulator writes to `chloe.sim.db` (never touches `chloe.db`). With `--clean`, starts from a blank schema.

---

## Key files

| Area | File |
|---|---|
| Heartbeat | `chloe/loop.py`, `chloe/initiative/engine.py` |
| Inner state | `chloe/initiative/candidates.py`, `chloe/inner/pressure.py` |
| Reflect | `chloe/reflect/every_2h.py`, `chloe/llm/prompts/reflect_combined.md` |
| Interest garden | `chloe/identity/interest_garden.py` |
| Trait model | `chloe/identity/trait_model.py` *(3.0 — to create)* |
| Aesthetics | `chloe/identity/aesthetics.py` *(3.0 — to create)* |
| Character addendum | `chloe/identity/character_addendum.py` *(3.0 — to create)* |
| Narrative Weaver | `chloe/reflect/weekly.py` *(3.0 — extend)* |
| Chat | `chloe/channels/mobile_ws.py`, `chloe/channels/chat_api.py` |
| Schemas | `chloe/llm/schemas.py` |
| Simulator | `chloe/sim/day.py`, `chloe/sim/personality.py` |
| CLI | `chloe/cli/commands.py` |
