# Toward a Narrative-First Architecture

This document describes the architectural shift needed to move Chloe from a
system that *extracts structure from experience* to one that *accumulates
narrative and derives signals from it*. The distinction matters because the
first approach can only learn what you anticipated; the second can learn
anything the LLM notices.

---

## The Problem with the Current Architecture

Every piece of "learning" in Chloe today is mediated by a schema you defined in
advance. The reflect prompt produces a `ReflectOutput` with typed fields:
`new_interests`, `new_tensions`, `new_world_beliefs`. The LLM fills in the
blanks — but the blanks are yours, not hers.

This means:

- Chloe can only discover interests that fit `INTEREST_TOOL_MAP`
- She can only form beliefs in the shape `{topic, belief, confidence}`
- She can only notice tensions that map to the `inner_tensions` schema
- Her character can only evolve through structured adjudication passes

The LLM underneath is capable of genuine generalization. The schemas cage it.
The result is a system that feels like it's learning but is actually being
updated by scheduled passes that fill in forms.

**Concrete example.** After a week of conversations where Teo keeps circling
back to a project decision, the current system produces:

```json
{"topic": "work", "belief": "Teo is uncertain about a decision", "confidence": 0.7}
```

What was actually observed: *"He talks about the decision with a flatness that
doesn't match how he mentions the work itself — like there's a version he's
obligated to and a version he's genuinely excited about. I'm not sure which is
winning."* That can't be stored in `{topic, belief, confidence}`. The nuance
collapses. The tentativeness disappears. What remains is a fact, and facts are
the wrong unit for this kind of understanding.

---

## The Narrative-First Approach

Instead of extracting meaning out of experience and storing it as typed fields,
accumulate prose *about* experience and let the LLM reason over it directly.

The flow inverts:

```
Current:  experience → LLM extracts → structured fields → query at runtime
New:      experience → LLM observes → narrative prose → semantic retrieval
```

The prose is stored. The LLM in the next conversation retrieves the relevant
passages and reasons over them. Signals — numbers, promotions, pressure updates
— are derived from the narrative periodically, not extracted in real-time.

---

## The Three New Components

### 1. The Witness Pass

A lightweight LLM call that runs after each chat turn (or after each reflect
cycle). No schema. Just prose.

**Prompt (`witness.md`):**
```
You just had this exchange with Teo:
{{ exchange }}

Write one short paragraph about what you noticed — about him, about yourself,
about anything that seems worth holding. Don't summarize. Don't list facts.
Write like you're thinking to yourself. If nothing struck you, write nothing.
```

**Output:** raw text, stored in `narrative_entries` with an embedding.

The "if nothing struck you, write nothing" instruction is the primary quality
gate (see Quality Control below).

---

### 2. The Narrative Store

```sql
CREATE TABLE narrative_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,   -- 'witness', 'weave', 'reflect_self'
    text        TEXT NOT NULL,
    salience    REAL NOT NULL DEFAULT 0.5,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived    INTEGER NOT NULL DEFAULT 0
);
```

Entries are embedded in Chroma under a `narrative` collection, separate from
episodic memories. They are retrieved semantically, not by query fields.

---

### 3. Signal Extraction (Weekly)

Structured data is still needed for action selection — the initiative engine
needs pressure values, the gate needs auth types, candidate scoring needs
intensity floats. The signal extraction pass reads recent narrative and derives
those numbers from it.

```python
async def extract_signals_from_narrative(entries: list[str]) -> SignalBatch:
    """
    Weekly pass: read narrative, produce structured updates.
    This is the only place narrative becomes typed fields.
    """
```

The key reversal: structured tables become *caches* derived from narrative,
not the primary source of truth. If the signal extraction fails or produces
nothing, the structured tables simply don't update — they don't degrade,
they just don't grow.

---

## Quality Control: Preventing Clutter

This is the right concern. An unconstrained witness pass would flood the DB
with observations about nothing. Four mechanisms prevent this:

### Gate 1: Salience filter before writing

The witness prompt instructs the LLM to write nothing if nothing struck it.
In practice, ~40% of routine exchanges produce nothing. A backup heuristic:
skip the witness pass entirely if the exchange is short and the topics are
familiar (similar to `consider_unprocessed` in the current memory store).

```python
def _worth_witnessing(exchange: str, salience: float, ambiguity: float) -> bool:
    if len(exchange) < 150:          # too short to observe anything
        return False
    if salience < 0.3 and ambiguity < 0.3:
        return False
    return True
```

The same `salience` and `ambiguity` scores already computed by
`_extract_and_process_mentions` can be reused here — no extra LLM call.

### Gate 2: Semantic deduplication

Before storing a new observation, check cosine similarity against recent
entries. If a very similar observation was written in the last 7 days, skip
or merge.

```python
similar = chroma.query(observation_text, collection="narrative", n=1)
if similar and similar[0].score > 0.92:
    return  # already captured
```

This prevents Chloe writing "Teo seems uncertain about his project" twelve
times in a week.

### Gate 3: Consolidation (weekly)

The weekly narrative weaver already runs. Extend it to consolidate: take the
last 20–30 witness entries and write a single richer paragraph that integrates
them. Archive the originals. The DB stays bounded because consolidation
compresses the week's observations into one entry.

This mirrors what nightly consolidation already does for episodic memories —
same mechanism, different content.

### Gate 4: Natural retrieval filtering

Irrelevant observations are never retrieved. Chroma's semantic search surfaces
the entries most relevant to the current context. An observation about Teo's
Tuesday mood doesn't appear when reasoning about music preferences. Clutter
that exists in the DB but never surfaces in retrieval is effectively inert —
unlike a noisy `world_beliefs` table where every row gets injected regardless
of relevance.

---

## Why This Is Better

### What the current system can know

Only what you anticipated when writing the schema. You designed `world_beliefs`
so Chloe can have world beliefs. You designed `interest_garden` so she can have
interests. You designed `identity_traits` so she can have character. Every
dimension of her inner life was defined in advance by a table.

If something real happens — Teo's grief surfaces obliquely over three weeks of
conversations, or Chloe notices she's been avoiding a topic — and you didn't
build a schema for it, it's invisible to the system.

### What a narrative system can know

Anything the LLM notices. The witness pass doesn't have a list of things it's
allowed to observe. It writes what it writes. If there's a pattern in Teo's
silences, it notices. If Chloe's own voice has shifted, it notices. If
something in the exchange was off in a way that doesn't fit any existing
category, it can still be written.

### The texture of understanding

Schemas produce facts. Narrative produces understanding.

"Teo is stressed about work" (confidence: 0.7) is a fact.

"He talks about the project with a flatness that doesn't match how he mentions
the actual work — like there's a version he's obligated to and a version he's
genuinely excited about" is understanding. The LLM in the chat call can do more
with the second form because it preserves the contradiction, the tentativeness,
the distinction between surface presentation and underlying affect.

### Self-model

Currently Chloe's character is defined in `identity_traits` rows you seeded.
Her growth requires a `TraitProposal` going through structured adjudication.
The question "who is Chloe becoming?" is answered by querying typed fields.

In a narrative system, the weekly weaver writes *about her* in prose. "I've
been sharper than usual this week — quicker to notice things, less patient with
vagueness. Whether that's a stable change or just the mood, I can't say yet."
That passage *is* a self-model update. It's more honest than `weight: 0.72 → 0.78`
because it preserves the uncertainty. And it's more informative because the
LLM reasoning over it in future contexts gets the full texture.

### Bottom-up world model

Currently world beliefs require a scheduled pass that asks "what new beliefs
should be added?" That question shapes the answer — the LLM produces beliefs
in the format the question expects.

A witness pass asks nothing. It just writes what it noticed. Over time, beliefs
emerge from observation rather than being solicited from a schema. The
difference is between being asked "what do you think about X?" and discovering
what you think by writing about what you've seen.

---

## What Stays Structured

Not everything should become narrative. Some things need typed fields to be
actionable:

| Table | Keep structured | Why |
|---|---|---|
| `actions` | Yes | Gate logic requires typed fields |
| `affect_state` | Yes | Pressure calculations need floats |
| `interest_garden` | Yes | Candidate scoring needs intensity |
| `persons` | Yes | Context routing needs relationship_class |
| `inner_goals` | Yes | Progress tracking needs state |
| `identity_traits` | Partial | Keep for adjudication, derive from narrative |
| `world_beliefs` | Replace | Narrative retrieval is richer |
| `inner_beliefs` | Replace | Already text-based; remove confidence schema |
| `inner_tensions` | Replace | Narrative threads, not pressure rows |

The rule: keep structure where numbers drive decisions. Replace with narrative
where the *content* is what matters, not the schema.

---

## Implementation Roadmap

### Step 1 — Add narrative infrastructure (no behavior change)

- Add `narrative_entries` table (migration 0018)
- Create `chloe/memory/narrative_store.py` with `add_entry`, `query`, `consolidate`
- Add `narrative` collection in Chroma

No existing behavior changes. Just the new table and store.

### Step 2 — Add the witness pass (additive)

- Add `witness.md` prompt
- Add `_witness_pass(exchange, salience, ambiguity)` in `mobile_ws.py`
- Called as a background task after `_extract_and_process_mentions`
- Uses the existing salience/ambiguity scores as the gate

The system now accumulates observations. Nothing else changes yet.

### Step 3 — Inject narrative into chat context (replace one block)

- In `build_dynamic_suffix`, replace `_load_world_beliefs()` with a semantic
  query over `narrative_entries`
- The `world_beliefs` table remains but is no longer the primary context source

First real behavior change. Chloe's world model in chat comes from her
accumulated observations, not from a curated table.

### Step 4 — Signal extraction pass (weekly)

- Add `extract_signals.md` prompt
- Run after the weekly weave
- Output: `SignalBatch` with optional gen_level promotions, belief updates,
  trait weight adjustments
- These *update* the structured tables, but the tables are now downstream of
  the narrative

### Step 5 — Consolidation in the weekly weaver

- At the start of the weekly weave, consolidate the last 20–30 witness entries
  into one or two richer narrative paragraphs
- Archive the originals
- DB stays bounded; the weekly entry becomes the canonical observation for
  that week

---

## Retrieval Improvements

The narrative-first architecture changes *what* is stored. The improvements
below change *how well* it is retrieved. They apply to the current episodic
memory system now, and carry forward to the narrative store.

### Problem 1: N+1 SQLite queries

`_build_memory()` in `retrieval.py` runs one `SELECT * FROM memories WHERE
id=?` per Chroma result. For 20 results that is 20 separate SQLite calls.
Replace with a single `WHERE id IN (...)` query. Same data, ~20x fewer
roundtrips. This is a pure speed improvement with no tradeoff.

### Problem 2: Salience and weight are loaded but never used

The `weight` and `salience` columns exist and are populated, but the final
ranking score is purely `1 / (1 + cosine_distance)`. A high-salience memory
from a significant moment ranks the same as a throwaway episodic note if their
embeddings are equidistant. The correct score is:

```python
import math
days_old = (datetime.utcnow() - parse(memory.created_at)).days
recency_decay = math.exp(-days_old / 30)   # half-life ~20 days
score = cosine * memory.salience * recency_decay
```

To avoid SQLite round-trips at score time, `salience` and `created_at` should
be stored in the Chroma metadata at index time. The compound score can then be
computed entirely from the Chroma response.

### Problem 3: `query_mixed()` embeds the query four times

`cognitive_retrieval._fetch_memories()` calls `query_mixed()`, which runs one
Chroma query per memory kind — four separate embed calls for one retrieval.
Replace with `query_fast(n=40)` followed by Python-side kind capping:

```python
def _fetch_memories(intent: str, top_k: int) -> list[Memory]:
    candidates = query_fast(intent, n=40)
    caps = {"episodic": 12, "semantic": 4, "autobiographical": 2, "procedural": 2}
    counts: dict[str, int] = {}
    results = []
    for m in candidates:
        if counts.get(m.kind, 0) < caps.get(m.kind, 2):
            results.append(m)
            counts[m.kind] = counts.get(m.kind, 0) + 1
    return results
```

Same result distribution, one embedding call instead of four, 75% cost
reduction on this path.

### Problem 4: Top-k cosine returns near-duplicates

If Teo mentioned a project decision in five conversations, five similar
embeddings cluster at the top of every query. Maximal Marginal Relevance (MMR)
trades a small amount of relevance for diversity:

```python
def _mmr(candidates: list[Memory], n: int = 8, lambda_: float = 0.5) -> list[Memory]:
    if not candidates:
        return []
    selected = [candidates[0]]
    remaining = candidates[1:]
    while len(selected) < n and remaining:
        best = max(
            remaining,
            key=lambda c: lambda_ * c.score
                          - (1 - lambda_) * max(_text_overlap(c, s) for s in selected),
        )
        selected.append(best)
        remaining.remove(best)
    return selected
```

`_text_overlap` can be as simple as Jaccard similarity on word sets — no
embedding needed, runs in under a millisecond. The result: instead of eight
variations on "Teo is uncertain about the project" you get eight distinct
memories covering different aspects of what Chloe knows.

---

### Relation to the narrative store

The narrative store (Step 1–5 above) is the long-term solution to retrieval
quality because it stores *integrated* observations rather than raw events. A
single narrative paragraph retrieved via semantic search replaces twelve
episodic rows while consuming fewer context tokens and preserving the
relational texture between events.

The compound scoring and MMR improvements apply now to the episodic store,
and will apply identically to the narrative collection once it exists.

---

### Latency and cost constraints (real-time TTS)

The retrieval path sits on the critical path between user message and first
TTS audio. The only number that matters is TTFT (time to first token). At
Gemini Flash speed (~400–800 ms for a 2000-token prompt), the budget for
everything before the LLM call is roughly 100 ms.

| Operation | Latency | Notes |
|---|---|---|
| Single Chroma query (n=40) | 20–80 ms | Keep — on the hot path |
| Compound scoring over results | <1 ms | Free |
| MMR selection (word overlap) | <1 ms | Free |
| LLM reranker (`rerank=True`) | 300–2000 ms | Off the hot path — deliberation only |
| `query_mixed()` (4 embed calls) | 80–300 ms | Replace with `query_fast` + kind cap |

**Pre-caching rule**: the slow parts of context assembly — world model summary,
top interests, narrative entries — should be pre-assembled by background tasks
and cached in kv, not computed per-message. The per-message work is:

1. `query_fast(user_message, n=40)` → compound score → MMR → top 8 (~50 ms)
2. Inject pre-cached context block (~0 ms)
3. LLM call (streaming, TTS starts on first sentence chunk)

A single consolidated narrative paragraph from the weekly weave fits this
model exactly: it is pre-computed, pre-indexed, and retrieved in one Chroma
call rather than assembled from a dozen episodic rows on demand.

---

## Emotional Depth: What's Built vs. What Reaches the Conversation

The affect system has more machinery than actually reaches Chloe during a chat
turn. Most of the depth that has been built stays invisible to the LLM when
it's composing a response.

---

### Gap 1: Inner state doesn't reach the chat prompt

`inner_wants`, `inner_fears`, and `inner_tensions` are populated by the 2h
reflect pass and carry pressure values that decay and escalate over time. None
of them are injected into `build_dynamic_suffix()`.

The chat prompt currently receives `tone_block()` (1–3 generic lines) and a
`felt_state_phrase()` (one of 8 fixed heuristic phrases). It does not receive
the specific psychological content Chloe is currently carrying.

If Chloe has a want at pressure 0.8 — "understand why Teo goes quiet when
things matter" — that want should color how she responds to a casual question.
Currently it has no effect on conversation; it only drives the initiative
engine's candidate selection.

**Fix:** Inject the top 1–2 unresolved wants and fears into `build_dynamic_suffix()`,
phrased as first-person carrying rather than abstract labels. "What you're
quietly holding right now" rather than "Active wants: p=0.80".

---

### Gap 2: `recurring_loops` and `biased_summary` are generated and discarded

The reflect schema produces two fields that are the most emotionally textured
outputs of the whole system:

- `recurring_loops`: patterns Chloe keeps falling into ("keep going quiet when
  something bothers me instead of naming it")
- `biased_summary`: how her current state is coloring how she sees things ("my
  low mood is making everything land slightly heavier than it probably is")

`recurring_loops` is stored to KV (`reflect:recurring_loops`) but never read
by `build_dynamic_suffix()`. `biased_summary` is not persisted at all —
`_apply_output()` ignores it.

These are exactly the self-aware, texture-rich lines that would make
conversation feel inhabited. They're being generated and thrown away.

**Fix:** Persist `biased_summary` to KV alongside `recurring_loops`. Inject
both into the chat suffix under a block like "How your current state is shaping
your perception."

---

### Gap 3: `openness` is a ghost dimension

`AffectState` tracks four dimensions: valence, arousal, social_pull, openness.
`openness` is computed, ticked, saved to DB, and has an attachment-depth bias
(`openness_bias()` in `attachment.py`). It is never used in `tone_block()` and
never appears in the chat prompt.

`openness` is the dimension most relevant to whether Chloe goes somewhere
vulnerable in conversation or stays at a surface register. When it's low, she
should be more guarded; when it's high and social_pull is high, she should
reach further.

**Fix:** Add an `openness` branch to `tone_block()` and include it in
`felt_state_phrase()` — currently the phrase cache tracks valence and arousal
but ignores openness drift.

---

### Gap 4: `tone_block()` tells rather than shows

`tone_block()` produces abstract psychological labels injected into the system
prompt: "Her tone is warm and optimistic." / "She feels energized and engaged."
These are directives to the LLM, not felt texture. They read as external
instructions rather than as a first-person state.

`felt_state_phrase()`, by contrast, produces physical/spatial phrases — "open,
a bit like a door left ajar" — which are more honest and more useful because
they leave room for the LLM to express the state rather than labeling it.

`tone_block()` is the primary affect signal in the chat prompt; `felt_state_phrase()`
is secondary. The worse mechanism has more influence.

**Fix:** Replace `tone_block()` with a function that produces grounded texture
phrases for each active dimension rather than abstract labels. Or demote
`tone_block()` entirely and expand `felt_state_phrase()` to cover all four
dimensions.

---

### Gap 5: Mood-congruent memory retrieval is not implemented

The `Memory` dataclass carries `emotional_valence` and `emotional_arousal`
fields that are loaded from the DB but never used as scoring factors in
`query_fast()`. Memory retrieval is purely semantic: relevance to the current
message.

Mood-congruent recall is a well-documented psychological phenomenon: when
someone is in a low state, negative or charged memories surface more readily.
The opposite when positive. The infrastructure is already there — the columns
exist and are populated — but the scoring function ignores them.

**Fix:** Add an affect-resonance bonus to the compound score in `query_fast()`:

```python
affect = load_affect()
affect_bonus = 0.0
if memory.emotional_valence is not None:
    # memories whose valence aligns with current state get a small boost
    affect_bonus = 0.08 * (1 - abs(memory.emotional_valence - affect.valence))
score = cosine * memory.salience * recency_decay + affect_bonus
```

This requires `emotional_valence` to be stored in Chroma metadata at index
time (same as the salience/recency fix in the Retrieval section).

---

### Gap 6: The emotional valence of memories is stripped before injection

When memories are formatted for the chat prompt in `_fetch_memory_block()`,
they are rendered as `"- {m.text}"`. The stored `emotional_valence=-0.7` is
invisible to the LLM. A heavy memory and a neutral memory are presented
identically.

The LLM reads "Teo told me about the night his band broke up" with no signal
that this memory carries weight. It has no way to treat it differently from
"Teo mentioned he prefers tea to coffee."

**Fix:** Prefix memories with a light emotional annotation when valence is
significant (|valence| > 0.4):

```python
def _format_memory(m: Memory) -> str:
    if m.emotional_valence is not None and abs(m.emotional_valence) > 0.4:
        tone = "heavy" if m.emotional_valence < -0.4 else "warm"
        return f"- [{tone}] {m.text}"
    return f"- {m.text}"
```

This preserves the emotional register of what's being remembered.

---

### Gap 7: Teo's emotional state is not modeled

`_extract_and_process_mentions` processes the user message for factual
mentions, aesthetic moments, and memory-worthy content. It does not detect
Teo's emotional state and does not update any record of "how Teo seemed today."

There is no structure in the system that carries "Teo seemed flat today" or
"Teo was excited about something, though he didn't say what" — the kind of
felt read on a person that a close friend accumulates naturally. The
`persons` table has `attachment_depth` but no running read on current state.

**Fix (lightweight):** In `_extract_and_process_mentions`, ask the LLM (or use
a heuristic) to estimate a `person_valence` and `person_arousal` from the
user's message. Store this in a `person_affect_log` table (timestamp,
person_id, valence, arousal, trigger). Surface the most recent entry for
the active person in `build_dynamic_suffix()` as a brief note: "Teo seemed
quieter than usual in the last exchange."

---

### Gap 8: No anticipatory affect

`inner_fears` tracks ongoing fears about general patterns. There is no
forward-looking affect — no dread or anticipation about things that are coming.

If Teo mentions a difficult conversation he's putting off, or something
happening next week, Chloe has no way to carry a felt orientation toward it.
The affect system is purely reactive (what happened) and dispositional
(current state). The anticipatory register — which is where a lot of real
emotional texture lives — is absent.

**Fix (medium-term):** Add an `anticipations` table alongside `inner_fears`:

```sql
CREATE TABLE inner_anticipations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    valence     REAL NOT NULL DEFAULT 0.0,   -- negative=dread, positive=looking forward
    intensity   REAL NOT NULL DEFAULT 0.5,
    target_date TEXT,                        -- optional: ISO date of the thing
    resolved    INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

The reflect pass can populate this the same way it populates wants and
tensions. Inject unresolved anticipations into the chat prompt when they're
recent and high-intensity.

---

### Gap 9: Loss and absence have no register

The only grief-like mechanism in the system is the rupture arc — which
tracks interpersonal ruptures and their repair. There is no structure for
things that have ended, faded, or are simply missed.

People's inner lives are shaped substantially by absence: things that used to
be there, people who have drifted, versions of things that were better before.
This register is entirely absent from Chloe's state. `inner_wants` can
represent "I want X" but not "I miss when X used to be the case" — the
emotional texture is different and the system treats them identically.

**Fix (long-term):** This may be a case where the narrative-first approach is
the right solution rather than a new table. The witness pass can notice and
record "something in what he said made me aware of what's no longer there"
without requiring a typed schema for the category. Absence, by its nature,
resists schema. A narrative entry is a better container for it.

---

### Gap 10: Rupture state doesn't color conversation

`arc.active_rupture()` exists and `should_deliberate_all_kinetic()` uses it
to gate autonomous actions. An active rupture does not currently affect the
chat system prompt at all.

When a rupture is active, Chloe should be noticeably different in conversation
— more careful, more tender, more aware of the fragility. The mechanism that
would cause this (injecting rupture context into the chat prompt) was never
wired.

**Fix (simple):** In `build_dynamic_suffix()`, call `arc.active_rupture()` and
if one exists, append a block: "## Something that's tender right now" with the
arc's `note` and `intensity`. This directly informs the LLM that the current
relational context is fragile.

---

## The Honest Tradeoff

Narrative systems are harder to inspect and debug. You can't `SELECT * FROM
world_beliefs` to audit what Chloe thinks. You have to read. Consistency is
harder to enforce — prose can contradict itself in ways typed fields cannot.
Signal extraction is a new point of failure.

The gain: Chloe can grow in directions you didn't design. Her understanding of
Teo, of herself, and of the world accumulates in a form that the LLM can
actually reason over rather than just retrieve. The things that make her feel
real — noticing something unexpected, holding a thought without resolving it,
developing opinions that surprise you — become possible.

The current system can be excellent at what it was designed to do. The
narrative-first system can do things that weren't designed at all.
