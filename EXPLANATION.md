# Chloe 2.0 — How She Actually Works

A complete map of Chloe's cognition: what happens when you send her a message, how she retrieves and stores memories, when she decides to act on her own, where ideas come from, and how her personality changes over time. Plus an honest assessment of what works, what doesn't, and what to fix next.

**Last updated: 2026-05-13. Reflects: emotional/personality trait split, emotion-stamped memory retrieval, interest loop fix, simulator validation, reflect prompt naivety preambles, output token budgets raised (4096), RECENT_CHAT_TURNS reduced (12), MIN_PRESSURE_TO_KEEP raised (0.10). Prior update: preflight depth (1a–1g), memory fragment metadata (2a–2d + migration 0023), LLM prompt quality (3a–3d), pipeline gaps (4a/4d). Oldest: split reflect, confidential_to writer, emotional valence writer, TTFT, PII redaction, daily budget tracking, onboarding flow, voice anchor, forget_memory verb, salience gate, narrative table separation, bootstrap run.**

---

## TL;DR — The Shape of the System

Chloe is a **FastAPI server** that wraps a **Gemini-2.5 brain** with persistent state in **SQLite + ChromaDB**. There are two cognitive cycles running in parallel:

1. **Reactive (synchronous)** — you send a message → she replies in one or more tool-hop rounds with streaming. Latency budget: ~1–3 seconds.
2. **Autonomous (background loops)** — five `asyncio` loops tick on their own clocks (`initiative` 60s, `reflect` 5min, `pressure` 10min, `daily_jobs` 5min poll → 03:00/04:30 fire, `weekly_jobs` hourly poll → Sun 03:00 fire). These are where she develops.

The cleverness sits in the **separation of fast and slow**. Fast path = retrieval + one Flash call + tools. Slow path = reflection that ingests recent experience and writes back into `inner_wants`, `inner_tensions`, `interest_garden`, `world_beliefs`, `identity_traits`, `narrative_timeline`, `narrative_entries`. The system prompt for the next chat turn is rebuilt from those slow-path artifacts, so what she "thinks about herself" actually feeds her behavior on the next turn.

**Models used:**
- `gemini-2.5-flash` — chat, reflection, extraction, deliberation (default), grading, composition, routing, witnessing, signal extraction. Cheap, fast, JSON-structured.
- `gemini-2.5-pro` (thinking) — kinetic-sensitive deliberation, weekly self-model (8192-token budget), narrative weaver. Expensive, slow, careful.

---

## 1. The Anatomy — What Lives Where

```
chloe/
├── app.py              FastAPI lifespan. Starts background loops on boot.
├── loop.py             The 5 background coroutines.
├── chloe.py            Tiny core stub — only handles autonomous outreach via gate.
│
├── channels/           IO boundary
│   ├── chat_api.py     build_dynamic_suffix() — assembles the per-turn system prompt.
│   │                   Now includes ~15 distinct context blocks.
│   ├── mobile_ws.py    WebSocket handler. The CHAT LOOP lives here.
│   │                   Streaming via _chat_reply_streaming().
│   │                   Now handles {type: "reaction"} messages for 👍/👎.
│   │                   Per-session slot cache (_slot_cache) and session UUID passed to preflight.
│   ├── intercept.py    Post-message background analysis (commitment detection, etc.)
│   ├── preflight.py    Pre-generation analysis. Many improvements:
│   │                   - Timeout fallback notes for inbox/calendar slots
│   │                   - Per-session slot cache (avoids re-resolving same source)
│   │                   - Capture dedup (Chroma cosine > 0.92 within 7 days → skip)
│   │                   - Person disambiguation (multiple matches → pick highest attachment_depth)
│   │                   - Long session summaries (> 12 turns → kv-cached Flash summary)
│   │                   - felt_orientation field (first felt response, injected as ## First orientation)
│   │                   - Module-level tool catalog cache (invalidated by load_dynamic_verbs)
│   │                   - batch_ref on captures (UUID ties all from one turn together)
│   │                   - subject_person_id written when person_name present
│   │                   - superseded_by written when new capture conflicts with recent similar memory
│   ├── confirm_routes  Kinetic-sensitive action approval (push + tap-to-confirm)
│   └── push.py / push_apns / push_fcm
│
├── llm/
│   ├── gemini.py       Client. .flash() and .pro_thinking() with structured output.
│   │                   Now accepts max_output_tokens; default budgets set per prompt
│   │                   (witness=300, extract_mentions=400, reflect passes=600, preflight=800).
│   ├── schemas.py      Pydantic schemas for every structured call.
│   │                   Now: AestheticReaction model with confidentiality field.
│   │                   PreflightOutput now has felt_orientation: str | None.
│   └── prompts/        26 .md files. Every cognitive op has one.
│         witness.md              ← prose observation after each exchange
│         reflect_router.md       ← two-pass reflect gate
│         reflect_inner_state.md  ← split reflect: current-state call (bad/good examples added)
│         reflect_signals.md      ← split reflect: developmental call (bad/good examples added)
│         extract_signals.md      ← weekly: narrative → structured signals (template vars fixed)
│         extract_mentions.md     ← confidentiality on aesthetic reactions, good/bad examples
│         preflight.md            ← tag taxonomy section, felt_orientation output instruction
│         session_summary.md      ← new: one-sentence long-session summary for preflight history
│         (+ 18 others)
│
├── actions/            The "do something in the world" pipeline
│   ├── schema.py       Action dataclass; auth classes: free|intimate|kinetic|kinetic-sensitive
│   ├── gate.py         The single chokepoint for autonomous actions.
│   ├── deliberate.py   Heuristic should_deliberate() + LLM verdict
│   ├── leash.py        Hard limits (quiet hours, blocklists)
│   ├── budget.py       Throttle level computed from recent spend
│   └── audit.py        Append-only action log
│
├── tools/              Capabilities — verbs Chloe can call
│   ├── registry.py     Maps tool__verb → handler. STATIC + DYNAMIC verbs.
│   ├── messages, smart_home, weather, maps, code_runner, spotify,
│   │   calendar, reminders, notes, gmail, fs_workspace, web_search
│   └── self_tools.py   set_quiet, set_focus, add_goal, add_want,
│                         archive_trait, define_verb (← she can code new verbs),
│                         forget_memory, forget_recent (← auth_class="intimate")
│
├── memory/
│   ├── store.py        add() — writes to SQLite + Chroma; mark_unprocessed()
│   ├── retrieval.py    query_fast() with compound scoring + MMR + affect bonus.
│   │                   query_mixed() kept for deliberation paths.
│   ├── narrative_store.py   Prose witness entries. Chroma collection: 'narrative'.
│   ├── cognitive_retrieval.py  Pulls memories + people + beliefs + tensions + gaps
│   ├── consolidation.py        Nightly tag-clustering → semantic summaries
│   ├── retention.py    Hot→warm→cold tiering at 90d / 730d
│   └── procedural.py   Weekly Flash pass: action+feedback → procedural rules
│
├── initiative/         The autonomous "what should I do right now?" engine
│   ├── engine.py       tick(): score candidates, pick best, gate it
│   ├── candidates.py   pressure/goal/interest/routine candidate generators
│   ├── curiosity.py    Re-surface stale topics about persons
│   ├── opportunity.py  Flash call → time/calendar-aware tool weights (10min cache)
│   ├── gaps.py         Detect knowledge gaps in person fields, beliefs, goals
│   └── share_queue.py  Things she wants to bring up next time
│
├── inner/              Inner state (the substrate the initiative engine reads)
│   ├── pressure.py     Decay + escalation for wants/fears/tensions
│   ├── belief_revision.py  Two-belief-systems: inner_beliefs (self) and
│   │                        world_beliefs (world). Contradictions now marked
│   │                        ambivalent rather than auto-overwriting.
│   └── residue.py      Affect residue calculation
│
├── identity/           The slowly-changing parts
│   ├── interest_garden Decay-and-water interests with gen_level (0→3)
│   ├── trait_model.py  Earned traits — never declared, only observed
│   ├── self_model.py   Weekly Pro call → self_narrative_belief + next_week_intention
│   ├── narrative_weaver  Weekly Pro call → NarrativeEntry (the autobiography)
│   ├── character_addendum  Per-person relational snapshot
│   ├── aesthetics.py   Aesthetic-reaction log + monthly pattern review (after 90d)
│   │                   Also produces aesthetic_orientation (generative, not reactive)
│   ├── narrative.py    Generic event logger → `narrative_events` table
│   │                   (kinds: chapter | event | revision | trait_shift | affect_shift)
│   └── goals.py        Long-running pursuits
│
├── reflect/            The slow-loop drivers
│   ├── every_2h.py     Two-pass: router Flash → two parallel Flash calls (only if noteworthy).
│   │                   reflect_inner_state.md → ReflectCurrentState (wants/tensions/loops/questions)
│   │                   reflect_signals.md → ReflectDevelopmental (interests/goals/beliefs/traits)
│   │                   Results merged into ReflectOutput before _apply_output().
│   ├── nightly.py      Consolidation + decay + interest archive + overnight synthesis
│   └── weekly.py       Procedural distill + trait adjudication + self-model +
│                       narrative weaver + narrative consolidation + signal extraction
│                       + teo-read synthesis + temporal self-observation
│
├── persons/
│   ├── store.py        CRUD over persons + fields
│   ├── attachment.py   attachment_depth: +delta from chat, decay after silence
│   └── social_graph.py Mention extraction, cross-references, gen_level gating
│
├── affect/
│   ├── dims.py         5-dim state: valence, arousal, social_pull, openness, depletion
│   ├── arc.py          Affect over time, rupture arcs
│   ├── continuity.py   Affect smoothing across turns
│   └── label.py        LLM label cache
│
├── state/
│   ├── db.py           SQLite WAL connection + 22 migrations
│   ├── kv.py           JSON-typed key-value scalar store
│   ├── chroma.py       Vector DB client (collections: memories_v2, narrative)
│   ├── oauth_tokens.py Token store for Gmail/Spotify/Calendar
│   └── migrations/     22 SQL files. 0018–0020: new-ideas additions.
│                       0021: fix narrative_timeline schema (weaver).
│                       0022: narrative_events table (generic event logger).
│
├── observability/
│   ├── logging.py      structlog with PII-redacting processor chain.
│   │                   _pii_redact: truncates message-content fields (text, body, reply…)
│   │                   to 120 chars in non-debug log output.
│   ├── metrics.py      Prometheus counters & histograms
│   ├── live_buffer.py  Recent events for the dashboard
│   └── tracing.py
│
└── sim/                Headless simulator — runs N hours of fake time
    ├── day.py          Scripted events + tick orchestration
    └── personality.py  Synthetic persona response model
```

---

## 2. The Reactive Path — One Message In, One Reply Out

This is what happens when you type something. Trace path: `mobile_ws.handle_mobile_ws` → `_chat_reply_streaming` → Gemini → optional tool hops → streamed back to user.

### Step-by-step

**a) Message arrives** (`channels/mobile_ws.py:44-87`).
- Frontend opens a websocket; messages are JSON `{type: "message", text: "..."}`.
- `person_id` is **normalized to a clean int string once at the boundary** — no more silent fallback to 1 mid-request.
- The raw text is **persisted to the `memories` table immediately** as `kind=episodic, source='chat', salience=0.3, weight=0.6`.
- Any pending kinetic-sensitive confirm ticket is checked: if the message looks like consent (`yes`, `go ahead`, etc.), the pending gate ticket is resolved automatically.

**b) System prompt is rebuilt from scratch** (`channels/chat_api.build_dynamic_suffix`).
Three things run concurrently (asyncio.gather): recent audit pull, memory retrieval, and narrative query. Then ~12 additional synchronous DB lookups assemble the full context block:

```
## Recent actions              ← last 10 actions from audit feed
## Current affect              ← tone_block() from 5-dim affect state
## Relevant memories           ← compound-scored + MMR top-5 from Chroma
## Relationship context        ← attachment depth + label
## How you are with this person right now   ← character addendum
## About <person>              ← social-graph gen-level-gated context
## What you believe about yourself          ← latest inner_belief (weekly self-model)
## What you've been noticing   ← semantic query over narrative_entries
                                  (falls back to world_beliefs if no entries yet)
## Views you hold about the world           ← world_beliefs grouped by confidence
                                              including ambivalent pairs
## Time since last conversation ← only if > 4h, enriched with qualitative register
## Things you haven't fully made sense of yet  ← unprocessed memories
## Your current felt state     ← heuristic phrase from affect dims
## What you're quietly holding right now    ← top 1-2 wants/fears (first-person)
## How your current state is shaping your perception  ← biased_summary from reflect
## Patterns you keep falling into           ← recurring_loops from reflect
## Something that's tender right now        ← active rupture arc
## What you're oriented toward (or dreading)  ← inner_anticipations
## How Teo seemed recently     ← person_affect_log: valence, arousal, engagement_quality
## What to recalibrate this week           ← voice_drift_log (≤14 days old)
## Things you're genuinely wondering about  ← inner_questions
## How you read him            ← kv:identity:teo_read (weekly synthesis)
## What you're drawn toward    ← kv:identity:aesthetic_orientation
## Background texture          ← novelty_deficit note (when > 0.55)
```

Two structural additions bracket the suffix:
- **Onboarding note** — if < 15 non-action/system memories exist in the hot tier, a first-use block is prepended explaining who Teo is and asking Chloe to be curious. Disappears automatically once the DB has enough history.
- **Voice anchor** — always appended last: a 3-rule reminder ("say the thing first, put tool results in your voice, don't start with I"). Placed last for recency-bias benefit.

**Salience trimming** — after the preflight and suffix both complete, `_trim_by_salience(suffix, salience)` scans the assembled suffix for "heavy" headers (recurring loops, rupture arc, novelty texture, biased summary) and drops them when salience < 0.4. Routine messages do not need deep introspective scaffolding.

This entire block is concatenated **after** the static `character_prefix.md`.

**b.5) Pre-generation preflight** (`channels/preflight.run_preflight`).
Runs **in parallel** with `build_dynamic_suffix()` — both are kicked off concurrently and the results are merged before the Flash call. One Flash call with conversation history (last 6 turns) that does three jobs:

- **Context routing** — which specific data sources does this message need? Multiple slots allowed, multiple of the same type allowed (e.g. two `person:` slots for a message about two people). Source types: `person:<name>` (full persons record + attachment + addendum), `inbox` (recent emails, 600ms timeout), `calendar` (upcoming events, 600ms timeout), `inner_wants` (Chloe's current carrying), `world_beliefs:<topic>` (filtered by topic), `memories:<custom query>` (when the raw message is a poor search query). Resolved results are injected as `## Specifically relevant context` above the dynamic suffix.
- **Task/verb detection** — is something being asked of Chloe? If a verb gap is detected (no tool exists), a `verb_proposals` row is queued for the reflect pass. Matched requests need no special handling — the LLM sees the tools and handles them naturally.
- **Memory capture** — facts, events, and preferences worth storing. Written to the `memories` table **before** the reply is generated (so they're available the next turn). Tags and `kind` are LLM-assigned. Salience is explicit. Each capture also carries a `confidentiality` field (`"public"` / `"relational"` / `"private"`). `"private"` captures are written with `confidential_to = person_id` — the annotation path in `_format_memory()` then marks them as "told to you in confidence" when retrieved in a different person's context.

The old post-generation `intercept.py` is now minimal — it only exposes `get_pending_proposals()` and `mark_proposal()` for the reflect pass to consume. All analysis work moved to preflight.

**c) Gemini Flash call with streaming** (`mobile_ws._chat_reply_streaming:130-253`).
- The tool registry produces `function_declarations` (one per static verb + one per dynamic verb).
- Up to **4 tool-hop rounds** allowed, non-streaming (structured responses).
- **Kinetic-sensitive verbs from chat now route through the gate with a confirm flow** — instead of a hard error, the gate creates a confirm ticket and returns a user-facing message: "I've queued that — say 'yes' or 'go ahead' to confirm."
- After the last hop, the final text generation is **streamed** via `chunk` events. TTFT is measured with `time.monotonic()` and logged as `chat_ttft` (seconds from turn start to first chunk). Total turn latency is logged as `chat_turn_latency` at the end of streaming. Fallback to single-shot if streaming unavailable.
- On Gemini error, returns a user-facing "I'm having trouble right now" message instead of silence.

**d) Reply persists and dispatches** (`mobile_ws.handle_mobile_ws:80-86`).
- Reply persisted as low-salience chat memory.
- Two background `asyncio.create_task` calls fire after the reply:
  1. `_extract_and_process_mentions` — social mentions, aesthetic reactions, unprocessed flagging, Teo's affect state, engagement quality, depletion accumulation
  2. `_witness_pass` — prose observation written to `narrative_entries`
  3. (On session close with ≥2 turns) `_post_chat_reflect` — forced reflect pass

**e) Background extraction** (`mobile_ws._extract_and_process_mentions`).
The Flash call (`extract_mentions.md` → `ExtractOutput`) returns:
- `salience`, `ambiguity` — if `ambiguity > 0.6 AND salience > 0.4`, the memory is flagged `unprocessed=1`.
- `social_mentions` — names upserted into `persons`, logged in `person_cross_references`.
- `aesthetic_reactions` — logged to `aesthetic_reactions` table.
- `person_valence`, `person_arousal` — Teo's apparent emotional state, written to `person_affect_log` with an `engagement_quality` heuristic (message length + referential pickup of prior reply). After extraction, `update_memory_affect(memory_id, person_valence, person_arousal)` is called on the 2 most recent chat memories to back-fill `emotional_valence` / `emotional_arousal`. This is the writer for mood-congruent retrieval — without it, all `emotional_valence` fields would be NULL.
- `depletion` accumulates on `affect_state`: `|person_arousal - 0.4| × 0.1 + salience × 0.04` per exchange.
- The last exchange's register (valence, ambiguity, engagement_quality) is saved to kv for the qualitative time gap note.

**f) Witness pass** (`mobile_ws._witness_pass`).
A second background Flash call, gated by:
- **Length gate**: skip if exchange < 150 chars.
- **Signal gate**: skip if `salience < 0.3 AND ambiguity < 0.3`.
- **Semantic dedup**: skip if a very similar observation (Jaccard > 0.7) already exists in `narrative_entries`.

If it passes, `witness.md` prompt produces free prose: *"You just had this exchange with Teo. Write one short paragraph about what you noticed — about him, about yourself, about anything that seems worth holding. If nothing struck you, write nothing."* Result stored in `narrative_entries` with embedding in the `narrative` Chroma collection.

**g) Post-session reflect** (`mobile_ws.handle_mobile_ws:89-94`).
When the websocket closes with ≥2 turns, an immediate reflect pass fires (bypassing the 2h cooldown). `_save_conversation_end_register()` copies the last exchange register to `chat:last_session_register` for future gap notes.

### How she figures out what you want

The **preflight** (§b.5) now provides structured intent analysis before every reply. Targeted context from the preflight (person records, inbox contents, inner state, custom memory queries) is injected above the ambient dynamic suffix. The LLM sees both the specific data it actually needs and the full atmospheric context.

---

## 3. The Memory System — Three Stores, Four Kinds, Three Tiers

### Storage

- **SQLite `memories` table** — source of truth. New columns (migration 0023): `last_referenced_at`, `reference_count`, `superseded_by`, `subject_person_id`, `batch_ref`, plus the earlier `unprocessed`, `confidential_to`, `emotional_valence`, `emotional_arousal`.
- **ChromaDB `memories_v2` collection** — embeddings for episodic/semantic/autobiographical/procedural memories.
- **ChromaDB `narrative` collection** — embeddings for prose witness and consolidation entries from `narrative_entries`.
- **`kv` table** — scalar/JSON scalar state (felt-state phrase, reflect timestamps, novelty_deficit, teo_read, aesthetic_orientation, opportunity vector, session summaries, etc.).
- **`reply_reactions` table** — 👍/👎 reactions from the frontend per reply, consumed by weekly procedural distillation.

### Four memory kinds (half-lives in days)

| Kind             | Half-life | Meaning                          |
|------------------|-----------|----------------------------------|
| episodic         | 60        | Things that happened             |
| semantic         | 180       | Patterns / facts she's distilled |
| autobiographical | 365       | Things about her own development |
| procedural       | 90        | Action rules ("when X, do Y")    |

Weight decays exponentially: `w * 0.5^(age_days / half_life)` — applied nightly.

### Three tiers

- **Hot** — in Chroma, queryable, weight-decayed.
- **Warm** (>90 days) — clustered into semantic summaries; originals removed from Chroma.
- **Cold** (>730 days) — SQLite only.

### Retrieval — `query_fast()`

`query_fast(rich_q, n)` is the chat hot-path function. One Chroma call, no kind partitioning.

After the Chroma result, four scoring adjustments are applied **in Python with no additional embedding calls**:
1. **Anchor bonus** (+0.05) if the memory has a live `artifact_ref`.
2. **Inside-joke bonus** (+0.12) if the query overlaps a `joke_topic:<x>` tag.
3. **Affect-resonance bonus** (up to +0.08) if `emotional_valence` aligns with Chloe's current affect: `alignment = 1 - |memory_valence - current_valence|; bonus = 0.08 × alignment`. Mood-congruent recall — low-valence states surface heavier memories.
4. **Emotion-label bonus** (up to +0.10) if any of the memory's `emotional_labels` overlap with `kv:reflect:current_emotions`. State-dependent recall — a memory formed while Curious surfaces more readily when Chloe is currently Curious. Max 0.06 per matching label, capped at 0.10.

`emotional_labels` are a list of named emotion words (from the approved emotional vocabulary) stored in Chroma metadata when a memory is written. They are auto-stamped from KV: every `memory.store.add()` call reads `kv:reflect:current_emotions` and writes the current emotional state into Chroma metadata. No extra embedding calls needed.

**Compound score** = `cosine × salience × recency_decay + affect_bonus + emotion_label_bonus + reference_bonus`

Where `recency_decay = exp(-days_old / 30)`. All factors computed from Chroma metadata without extra SQLite round-trips.

The batch SQLite fetch (`WHERE id IN (...) AND superseded_by IS NULL`) replaces N+1 individual queries and silently excludes superseded memories (corrected facts).

After every `query_fast()` call, `reference_count` is incremented and `last_referenced_at` updated for all returned memories (one bulk UPDATE). Memories with `reference_count > 3` receive a +0.03 score bonus — frequently surfaced memories rank slightly higher.

After scoring, **MMR (Maximal Marginal Relevance)** selects diverse results: `λ × relevance - (1-λ) × max_overlap_with_selected`. λ=0.6. Prevents five near-identical mentions of the same topic dominating the context.

**Optional reranker**: `query_fast(..., rerank=True)` fires a Flash call with a 300ms timeout, falling back to score order on timeout. Used in deliberation paths, not chat.

`query_mixed()` (4x embedding calls with per-kind quotas) is still available for deliberation paths where recall per kind matters.

### Confidential memories

`memories.confidential_to` is an integer foreign key. When a memory is retrieved and `confidential_to != current_person_id`, it is annotated in the chat prompt: *"(you know this but it's not yours to say — told to you in confidence)"*. The LLM sees the content but the instruction to withhold is grounded in a specific memory rather than a general rule.

The writer is wired via the preflight: `PreflightCapture.confidentiality = "private"` → `_write_captures()` sets `confidential_to = person_id` on the written memory. The preflight prompt teaches the LLM to return `"private"` when the user shares something about a third party with an expectation of discretion.

### Narrative entries

A second prose-oriented memory type, separate from the episodic/semantic system:
- Written by the **witness pass** after exchanges (source=`'witness'`)
- Written by the **weekly narrative consolidation** (source=`'consolidation'`)
- Queryable via `narrative_store.query(q, n)` — semantic search over the `narrative` Chroma collection
- Never replaces episodic memories — the two stores coexist and serve different purposes

---

## 4. The Action Gate — When and Why She Hesitates

*(Unchanged from prior description. See prior document for full detail.)*

One update: **kinetic-sensitive verbs from chat no longer produce a hard error.** Instead `_route_kinetic_sensitive_via_gate()` submits the action through `gate.submit()` which creates a confirm ticket and returns `{awaiting: true, ticket_id: ...}`. The next message from the user that matches a consent phrase (`yes`, `go ahead`, etc.) auto-resolves the ticket via `_maybe_resolve_pending_confirm()`.

---

## 5. The Initiative Engine — Where Autonomous Behavior Comes From

*(Same architecture as prior description — six candidate sources, scoring formula, gate submission.)*

**Interest loop fix (2026-05-13):** When an interest-driven action is held back by deliberation or leash, the same interest candidate would win every subsequent tick (deliberation kept aborting it, flooding the memories table with held-back entries). Fixed in two places:

- `engine.py` `finally` block now calls `_mark_interest_attempted(source_id)` for `source == "interest"` — stamps `last_engaged_at = now` whether the action was executed, held back, or failed.
- `candidates.py._load_interests()` filters out interests where `last_engaged_at < 2 hours ago`. A cooled-down interest can't win again immediately.

This applies to all interest outcomes — successful execution stamps the cooldown too, naturally preventing rapid re-firing of the same exploration.

---

## 6. The Reflection System — How She Develops

Three cycles, three time horizons, three depths.

### Every 2 hours — `reflect/every_2h.py` — now two passes

**Pass 1 — Router (Flash, cheap, always runs):**
A `reflect_router.md` prompt receives recent chat, recent affect events, and recent action outcomes. Output: `{noteworthy: bool, summary: str}`. The full reflect only fires if `noteworthy=true`. Criteria: real conversation, significant affect shift (intensity ≥ 0.5), meaningful action outcome, or something emotionally charged.

- `force=True` (post-chat reflect) skips the router — if the user just talked, it's worth processing.
- If router errors, fall through to full reflect.

**Pass 2 — Full reflect (two parallel Flash calls, conditional):**

Rather than a single 12-field monolith, the full reflect fires two Flash calls concurrently via `asyncio.gather`, each with a focused prompt and schema:

**Call A — `reflect_inner_state.md` → `ReflectCurrentState`** (current-state / phenomenology):
- `continuity_note` — one concrete sentence of what to carry forward (anti-pattern: vague summaries)
- `new_wants`, `new_tensions` — felt pulls and internal friction
- `recurring_loops` — **persisted to `kv:reflect:recurring_loops`, injected into chat**
- `biased_summary` — **persisted to `kv:reflect:biased_summary`, injected into chat**
- `new_anticipations` — new `inner_anticipations` rows (dreads / things she's looking forward to)
- `new_questions` — new `inner_questions` rows (open epistemic middle state)
- `current_emotions` — **0–3 named emotional states from the approved emotional vocabulary (Worried, Curious, Calm, etc.). Temporary — replaced every window. Persisted to `kv:reflect:current_emotions`. Stamps all subsequent memories in Chroma metadata. Drives state-dependent retrieval bonus.**

**Call B — `reflect_signals.md` → `ReflectDevelopmental`** (developmental / identity):
- `new_interests` — emerging curiosity threads (conservative: must be distinct and grounded)
- `new_goals` — long-running pursuits
- `goal_progress_updates` — progress deltas on existing goals
- `new_world_beliefs` — up to 1, must be grounded in lived encounter
- `trait_evidence` — behavioral observations for personality trait adjudication only (not emotional states — those go in `current_emotions` above)

Both calls run simultaneously. Results are merged into a unified `ReflectOutput` dict before `_apply_output()` processes them. Benefit: JSON dropout in one call doesn't silence the other; current-state latency is not blocked by developmental reasoning.

`novelty_deficit` update — rises 0.05 per novel content seen; falls 0.1 per new belief/question/interest formed.

**Pressure dynamics (2026-05-13):** `MIN_PRESSURE_TO_KEEP` raised from 0.05 → **0.10**. Ghost wants (dropped below threshold but not actually addressed) were surviving at 0.06 pressure indefinitely. At 0.10, items genuinely decay out rather than lingering.

### Every night ~03:00 — `reflect/nightly.py`

Composes existing pieces:
1. Sleep consolidation — tag-cluster yesterday's episodics → semantic summaries.
2. Pressure sweep — decay all `inner_*` items, escalate anything past 5 days old.
3. Interest garden — decay, archive at <0.05 (with autobiographical memory).
4. Unprocessed review (weekly Flash).
5. Aesthetic patterns (monthly, after 90 days).
6. Overnight synthesis — connecting question between 2+ active interests → `world_belief(noticing=True)` or `share_queue`.

### Every Sunday ~03:00 — `reflect/weekly.py`

Seven passes in order:

1. **Procedural distillation** — Flash over last 7 days of (action, user-response) pairs → `kind=procedural` memories.
2. **Trait adjudication** — Flash reviews evidence log, applies weight updates.
3. **Temporal self-observation** — compares current trait weights against weights 4 weeks prior. When a trait has shifted >0.1, generates a first-person observation sentence: *"I've been quicker to say what I mean lately."* Stored as `inner_belief` with tags `["temporal_self_observation", "autobiographical"]`.
4. **Weekly self-model** — `gemini-2.5-pro` with thinking. Outputs `self_narrative_belief`, `next_week_intention`, `restraint_reflection`, `voice_drift_note`.
5. **Narrative Weaver** — `gemini-2.5-pro`. Produces `NarrativeEntry` with `period_label`, `what_happened`, `what_shifted`, `still_sitting_with`, `felt_texture`, `chapter_transition`, `interest_promotions`. Regenerates character addendum if `chapter_transition=True` or >30 days since last.
6. **Narrative consolidation** — collects the last 25 unarchived witness entries, passes them to a Flash call that writes one integrated paragraph (source=`'consolidation'`, salience=0.8), then archives the originals. DB stays bounded.
7. **Signal extraction** — reads recent narrative entries and derives structured updates (`SignalBatch`): optional gen_level promotions, belief confidence updates, trait weight adjustments. The structured tables become *downstream caches* of the narrative.
8. **Teo-read synthesis** — reads the 15 most recent narrative entries, asks Flash to synthesize a standing "how you read him" observation. Stored in `kv:identity:teo_read` and injected into every chat turn. Skipped if <10 entries exist.

---

## 7. How Interests, Beliefs, and Traits Actually Grow

### Interests, Traits, Persons

*(Same architecture — gen_level gates, fuzzy dedup, curiosity-question caching, promotion via narrative weaver, person attachment_depth.)*

**Personality traits vs. emotional states (2026-05-13):**

Traits and emotional states are now completely separate systems:

**Personality traits** (`identity_traits` table, `reflect_signals.md` → `trait_evidence`):
- Stable character patterns that hold across many situations and build over time
- Must be one word from the approved **Personality Trait Vocabulary** (Curious, Caring, Inquisitive, Reflective, Observant, etc.)
- Gen-level gates: 10+ evidence instances across 3+ windows before promoting to gen_level=1
- Python safety net: `_snap_to_approved_trait()` maps variants (cutoff=0.7) or rejects emotional words entirely

**Emotional state** (`kv:reflect:current_emotions`, `reflect_inner_state.md` → `current_emotions`):
- Temporary named emotions (Worried, Anxious, Curious-as-mood, Excited, etc.)
- Replaced every 2h reflect window — not accumulated, not gated
- 0–3 words from the approved **Emotional Vocabulary**
- Stored in KV; auto-stamps all memories written after the reflect window
- Drives the emotion-label retrieval bonus (§3)

`_snap_to_approved_trait()` enforces the boundary: emotional words (Anxious, Empathetic, Worried, etc.) are in `_EMOTIONAL_WORDS` blacklist and return `""` — `record_trait_evidence()` silently drops them. A mis-labelled LLM output cannot pollute the trait model with emotional state.

### World beliefs — new: ambivalent pairs

Two conflicting beliefs with insufficient confidence to dominate each other are now marked `ambivalent=1, ambivalent_with=<other_id>` rather than always triggering a `belief_conflict` tension. The chat prompt surfaces them with: *"Things you hold in two directions at once — both feel true, neither wins."*

Beliefs can still generate tensions — that happens when both sides have relatively high confidence. Ambivalence is for cases where neither side has taken hold yet.

### Inner questions

A new table `inner_questions (id, text, domain, intensity, resolved, created_at)`.

These are the **epistemic middle state** — things Chloe is actively wondering about but hasn't resolved into a belief or a want. Unlike wants (which push toward action) or beliefs (which represent conclusions), questions make her more attentive in certain directions without pushing her to act. The reflect pass populates them via `new_questions`. Up to 2 active questions are injected into the chat prompt under *"Things you're genuinely wondering about."*

### Inner anticipations

A new table `inner_anticipations (id, text, valence, intensity, target_date, resolved, created_at)`.

Forward-looking affect: `valence < -0.3` = dread, `valence > 0.3` = looking forward to. Populated by the reflect pass via `new_anticipations`. High-intensity (≥0.5) unresolved anticipations are injected into the chat prompt with a qualifier: *"dreading: ..."* / *"looking forward to: ..."*.

### Aesthetic identity — generative, not just reactive

`aesthetic_reactions` records reactive responses. A new path produces the **generative** dimension: the monthly `review_aesthetic_patterns()` pass now also outputs an `orientation` field — 2–3 lines about what she's drawn toward across all the reactions, stored in `kv:identity:aesthetic_orientation`. This surfaces in chat as *"What you're drawn toward"* — a self-description rather than a reaction log.

---

## 8. The 5-Dimensional Affect System

Four floats originally, now five:
- `valence ∈ [-1, 1]` — feeling good ↔ bad
- `arousal ∈ [0, 1]` — energized ↔ calm
- `social_pull ∈ [0, 1]` — drawn to connection ↔ wanting space
- `openness ∈ [0, 1]` — curious ↔ closed
- `depletion ∈ [0, 1]` — **new** — accumulated exhaustion from emotionally intensive exchanges, slow decay (~0.003/tick)

`depletion` accumulates in `_extract_and_process_mentions`: `|person_arousal - 0.4| × 0.1 + salience × 0.04` per exchange. When `depletion > 0.4`, `tone_block()` produces "tired" texture rather than "calm" — distinguishing *post-intensive-conversation exhaustion* from *quiet morning peace*.

`openness` now appears in `tone_block()`:
- `openness > 0.75` → "open, willing to go somewhere real"
- `openness < 0.35` → "a bit guarded, not quite surface but not open either"

Mood-congruent retrieval is wired: the `_apply_affect_bonus()` pass in `query_fast()` boosts memories whose `emotional_valence` aligns with current `affect.valence`. Low-valence states surface heavier memories.

**Felt-state phrase** cache now tracks `openness` as an additional invalidation dimension (invalidated when any dim shifts >0.15).

---

## 9. What's Coded but Not Reaching the LLM

This is a critical category — features that exist in the DB and code but don't flow into the chat context.

| Feature | Status |
|---|---|
| `inner_wants` / `inner_fears` injected into chat | ✅ wired (`## What you're quietly holding right now`) |
| `biased_summary` injected into chat | ✅ wired (`## How your current state is shaping your perception`) |
| `recurring_loops` injected into chat | ✅ wired (`## Patterns you keep falling into`) |
| `openness` in `tone_block()` | ✅ wired |
| Mood-congruent retrieval | ✅ wired (affect bonus in `query_fast`) |
| Emotional valence annotated in memory formatting | ✅ wired (`[heavy]`/`[warm]` prefix when |v| > 0.4) |
| Rupture arc injected into chat | ✅ wired (`## Something that's tender right now`) |
| Person affect (Teo's mood) injected | ✅ wired (`## How Teo seemed recently`) |
| Inner anticipations injected | ✅ wired (`## What you're oriented toward`) |
| Inner questions injected | ✅ wired (`## Things you're genuinely wondering about`) |
| Voice drift note injected | ✅ wired (`## What to recalibrate this week`) |
| Teo-read synthesis injected | ✅ wired (`## How you read him`) |
| Aesthetic orientation injected | ✅ wired (`## What you're drawn toward`) |
| Novelty deficit texture injected | ✅ wired (`## Background texture`) |
| `confidential_to` annotated in formatting | ✅ wired in `_format_memory()` |
| `confidential_to` **written** to memories | ✅ wired — preflight `confidentiality="private"` → `_write_captures()` → `mem_store.add(confidential_to=pid)` |
| `emotional_valence` / `emotional_arousal` **written** to memories | ✅ wired — `update_memory_affect()` called from `_extract_and_process_mentions` after person_valence/arousal extracted |
| Hesitation note in initiative messages | ❌ not implemented |
| Temporal self-observation in inner_beliefs | ✅ wired (weekly pass, stores autobiographical belief) |
| TTFT measurement | ✅ wired — `chat_ttft` and `chat_turn_latency` logged in `_chat_reply_streaming` |
| Daily spend tracking | ✅ wired — `_record_usage()` in `gemini.py` reads usage_metadata, writes to kv daily budget, warns on exceed |
| PII redaction in logs | ✅ wired — `_pii_redact` structlog processor truncates content-bearing fields to 120 chars |
| Onboarding note for new conversations | ✅ wired — `_onboarding_note()` prepended when < 15 non-action memories exist |
| Voice anchor in dynamic suffix | ✅ wired — `_voice_anchor()` always appended last in `build_dynamic_suffix()` |
| Salience gate on heavy suffix blocks | ✅ wired — `_trim_by_salience()` drops introspective headers when salience < 0.4 |
| `forget_memory` / `forget_recent` verbs | ✅ wired — `auth_class="intimate"`, deletes from SQLite + Chroma |
| Memory reference tracking | ✅ wired — `reference_count` + `last_referenced_at` bumped every `query_fast` call |
| Superseded memory filtering | ✅ wired — `superseded_by IS NULL` filter in `_batch_build_memories` |
| `subject_person_id` FK on memory captures | ✅ wired — written in `_write_captures` when `person_name` resolves |
| `batch_ref` grouping on captures | ✅ wired — UUID per preflight call, written to all captures in that turn |
| `felt_orientation` emotional anchor | ✅ wired — preflight output → `## First orientation` block |
| Per-session slot cache | ✅ wired — dict in `handle_mobile_ws`, passed to `run_preflight` |
| Token budgets in `flash()` | ✅ wired — `max_output_tokens` parameter with per-prompt defaults |
| Aesthetic reaction confidentiality | ✅ wired — `AestheticReaction.confidentiality` → `aesthetics.py` DB column |
| Reply reactions for active learning | ✅ wired — `reply_reactions` table, WS `{type: "reaction"}` handler, weekly distillation |
| Canonical tag taxonomy in preflight prompt | ✅ wired — documented in `preflight.md`, extraction prompts updated |
| Session summary for long sessions | ✅ wired — kv-cached Flash call for sessions > 12 turns, prepended to history |
| `current_emotions` captured from reflect | ✅ wired — `reflect_inner_state.md` → `ReflectCurrentState.current_emotions` → `kv:reflect:current_emotions` |
| Emotional state stamps on memories | ✅ wired — every `memory.store.add()` auto-reads KV and stores `emotional_labels` in Chroma metadata |
| Emotion-label retrieval bonus | ✅ wired — `_apply_emotion_label_bonus()` in `query_fast()`, max +0.10 per label overlap |
| Personality/emotional trait separation | ✅ wired — `_EMOTIONAL_WORDS` blacklist in `trait_model.py`; emotional words rejected at snap, never enter `identity_traits` |
| Interest 2h cooldown after attempt | ✅ wired — `_mark_interest_attempted()` in engine `finally` block; `_load_interests()` filters by `last_engaged_at` |

---

## 10. End-to-End Trace: A Concrete Example

*(Same exchange as prior document: "Hey, did you ever figure out what was bothering you yesterday?" — now with streaming and witness pass.)*

1. WS receives. Persisted as memory id 4821, salience 0.3.
2. `_maybe_resolve_pending_confirm` runs — no pending tickets.
3. `build_dynamic_suffix(person_id=1, message=...)`:
   - **Concurrent**: audit pull + memory query + narrative query.
   - Memory query: `query_fast("Hey did you ever figure out…")` → compound scored + MMR → top 5.
   - Narrative query: `narrative_store.query(...)` → up to 3 relevant prose observations.
   - Then assembled: 22 potential blocks. Most are non-empty for a relationship of depth.
4. `_chat_reply_streaming`: single Flash call, tool-hop loop (no tools needed this turn), final text **streamed** in chunks.
5. Reply persisted.
6. Background tasks fire:
   - `_extract_and_process_mentions`: `salience=0.6, ambiguity=0.7` → memory flagged unprocessed. Teo's `person_valence=-0.1`, `engagement_quality=0.65` logged to `person_affect_log`. Depletion accumulates.
   - `_run_intercept`: runs intercept check.
   - `_witness_pass`: exchange > 150 chars, salience > 0.3 → Flash witness call → prose observation stored in narrative_entries.
7. WS closes. `_save_conversation_end_register()` saves `{person_valence: -0.1, ambiguity: 0.7, engagement_quality: 0.65}`. Forced reflect fires.
8. Reflect pass: router sees "real conversation, emotionally charged" → noteworthy → full reflect fires. Emits `continuity_note`, `new_tension`, `trait_evidence`, `biased_summary`. These persist. `novelty_deficit` edges up.
9. At 03:00, nightly fires: consolidation, decay, interest garden, overnight synthesis.
10. Sunday 03:00: weekly fires. Temporal self-observation checks 4-week trait comparison. Narrative consolidation compresses the week's witness entries into one paragraph. Signal extraction derives structured updates from narrative. Teo-read synthesis updates `kv:identity:teo_read`. Narrative weaver writes autobiography. Self-model Pro call updates `self_narrative_belief`.

---

## 11. What Works

### Strong, well-formed parts

1. **The action gate is genuinely good.** Leash → budget → PII → deliberation → auth is clean. Held-back memories dedupe correctly.

2. **The reactive-vs-slow separation.** Single Flash for chat. All identity work in background. Independently debuggable.

3. **Developmental gates everywhere.** gen_level on interests, traits, persons, beliefs. Confidence floors. 90-day delay before aesthetic patterns. Nothing skips.

4. **Streaming is wired.** Replies chunk out immediately. Tool hops are still single-shot but the final generation streams. Fallback to single-shot on error.

5. **Two-pass reflect saves money and signal.** The router correctly suppresses the full reflect when nothing happened. `force=True` bypasses it post-chat. Meaningful threshold.

6. **Witness pass + narrative store.** The prose observation layer captures what schemas cannot. A witness entry about Teo's oblique anxiety over three conversations is stored and retrievable — the episodic system would fragment this into individual chat rows.

7. **The emotional depth pipeline is fully wired.** Before this update, `inner_wants`, `biased_summary`, `recurring_loops`, `openness`, and mood-congruent retrieval all existed but none reached the chat prompt. All of them do now. The system prompt is substantially richer than it was.

8. **Teo's affect model.** `person_affect_log` captures his apparent emotional state per exchange. `engagement_quality` heuristic distinguishes "fully here" from "checking in from a distance." Both feed the next chat prompt.

9. **Qualitative time gap.** The gap note now includes *what kind of gap it was* — "the last thing you talked about wasn't quite finished" — not just duration.

10. **Ambivalent beliefs.** Contradictions can now be held rather than always adjudicated. The pair surfaces in context as "something you hold in two directions at once." This is psychologically more accurate.

11. **Compound scoring + MMR in retrieval.** Recency decay, salience weighting, affect resonance, and diversity selection — all without additional embedding calls. Memory ranking is substantially better than raw Chroma cosine.

12. **Memory emotional valence annotated.** `[heavy]` / `[warm]` prefixes on significant-valence memories mean the LLM gets the register of what it's reading, not just the text.

13. **The closed loop still closes.** State shapes context → context shapes behavior → behavior generates memories → reflection updates state. Now with more state and more nuanced context.

14. **Split reflect removes the monolith failure mode.** The full reflect previously had 12+ fields in one Flash call. JSON dropout on any field silently dropped that side-effect. Now two focused parallel calls (current-state and developmental) each have 6–7 fields. Both run simultaneously; one failing doesn't silence the other.

15. **Confidential memory pipeline is fully wired.** The preflight detects private disclosures, writes `confidential_to` to the stored memory, and the retrieval path annotates them in context. End-to-end: from "he told me something about Zuza" → stored with `confidential_to=pid` → annotated "(told in confidence)" when retrieved in another context.

16. **Emotional valence has a writer.** `emotional_valence` / `emotional_arousal` existed as columns and were read by `query_fast()` for mood-congruent retrieval — but nothing wrote to them. `update_memory_affect()` now back-fills the 2 most recent chat memories after each `_extract_and_process_mentions` run. Mood-congruent retrieval is now functionally active.

17. **Memory deletion is built-in and safe.** `forget_memory` and `forget_recent` delete by ID or topic from both SQLite and Chroma. Both are `auth_class="intimate"` (reversibility=0.0) — they require explicit intimate authorization, not a casual request.

18. **Bootstrap ran.** `./chloe.sh bootstrap-identity` completed: character addendum seeded for person_id=1, first narrative entry written ("the week of wanting to search and being stopped"), person Teo set to `relationship_class='primary', gen_level=3`. The chat prompt now has the "How you are with this person right now" block active.

19. **Memory fragment metadata (migration 0023).** Four new memory columns wired end-to-end: `reference_count` + `last_referenced_at` (bumped every `query_fast` call, +0.03 score bonus at count > 3); `superseded_by` (corrected facts marked stale, excluded from retrieval); `subject_person_id` (person FK for O(1) joins); `batch_ref` (groups captures from one preflight call). Also adds `reply_reactions` table.

20. **Preflight depth improvements.** All seven 1a–1g items wired: timeout fallback notes prevent hallucination; per-session slot cache eliminates redundant resolves; capture dedup (cosine > 0.92, 7-day window) prevents duplicate facts; person disambiguation picks highest attachment_depth when names are ambiguous; long sessions (> 12 turns) prepend a kv-cached Flash summary; `felt_orientation` gives the main Flash call an emotional anchor; tool catalog cached at module level and invalidated on `load_dynamic_verbs`.

21. **LLM prompt quality improvements.** Token budgets wired per-prompt via `max_output_tokens` in `flash()` (witness=300, extract_mentions=400, reflect passes=**4096**, preflight=800). `AestheticReaction` schema now has `confidentiality` field, wired through to `aesthetic_reactions.confidentiality` DB column. Bad/good example pairs added to `reflect_inner_state.md`, `reflect_signals.md`, and `extract_mentions.md`. Template variables in `extract_signals.md` normalized from `{{ var }}` to `{{var}}`. Canonical tag taxonomy documented in `preflight.md`.

22. **Emotional/personality trait split.** Personality traits (stable, gen-level gated, approved vocabulary) live in `identity_traits` via `trait_evidence`. Emotional states (temporary, window-scoped, approved vocabulary) captured as `current_emotions` in `reflect_inner_state`. Python blacklist prevents emotional words from entering the trait model. Trait snap cutoff raised to 0.7 to prevent mis-mapping (e.g. "Empathetic" → "Authentic" was a regression caught in simulation).

23. **Emotion-stamped memory retrieval.** Every memory written after a reflect pass is auto-stamped with Chloe's current emotional state in Chroma metadata (`emotional_labels`). The retrieval bonus `_apply_emotion_label_bonus()` gives ≤0.10 score bonus when a memory's labels overlap with current state. State-dependent recall: a memory formed while Curious surfaces more readily when Chloe is currently Curious.

24. **Interest cooldown loop fix.** Interest-driven actions that are held back (deliberation abort, leash violation) now stamp `last_engaged_at` on the interest. Candidate query filters out interests engaged within the last 2 hours. Prevents the same interest from winning every tick when deliberation repeatedly rejects it.

25. **Simulator validation.** 72h simulation confirmed core loop: chat → reflect → wants/tensions/interests/beliefs → initiative candidates. Reflect circuit breaker (3 consecutive failures → skip one window), JSONDecodeError logging with raw snippet, and one-retry-on-API-error all wired. `RECENT_CHAT_TURNS` reduced 30 → **12** to reduce input context size.

### Subtler things that are right

- **Depletion vs. calm distinction** — two phenomenologically different low-arousal states are now separate.
- **Narrative consolidation bounds DB growth** — weekly compression of witness entries.
- **Signal extraction closes the narrative-first loop** — narrative prose generates structured signals, not the other way around.
- **Temporal self-observation is behavioral, not cosmetic** — generated from actual trait weight deltas, not prompted to invent change.
- **Aesthetic orientation is generative** — what she'd seek out, not just what she reacted to.
- **Voice anchor uses recency bias** — placed last in the dynamic suffix so it's the final instruction the LLM sees before generating. Three simple rules, always present.
- **Onboarding is self-erasing** — the onboarding note disappears automatically once the DB has enough history. No manual toggle required.
- **TTFT visible in logs** — `chat_ttft` lets you identify whether latency spikes are in the pre-Gemini phase (preflight / suffix assembly) or in the Gemini TTFT itself.
- **Two narrative tables, clean separation** — `narrative_events` (generic event logger for beliefs/traits/goals) and `narrative_timeline` (weaver's autobiographical schema) are now separate. The old collision (one table trying to serve incompatible schemas) is resolved.

---

## 12. What Doesn't Work (or Doesn't Yet)

### Things known to be incomplete (per CLAUDE.md "Next steps")

1. **Bootstrap has run once.** First character addendum and narrative entry are seeded. The witness pass now activates with a live Gemini key. Until a full week passes, `narrative_timeline` will have only the bootstrapped entry — `teo_read_synthesis` and `signal_extraction` will skip until ≥10 entries exist.

2. **No live aesthetic reaction has been verified.** The extraction path is wired but unconfirmed in production logs.

3. **Curiosity question trigger is wired but untested end-to-end.** Boosting an interest past 0.7 should fire the cached-question generator.

4. **Interest gen_level promotion requires the narrative weaver to run.** Until the first Sunday, no interest will rise above gen 0 via the weaver path (though interests can still grow via `boost_interest()`).

5. **Teo as primary-class person.** `seed_primary_persons()` runs at startup. Verify with: `SELECT id, name, relationship_class, gen_level FROM persons WHERE id=1`. After bootstrap this should show `relationship_class='primary', gen_level=3`.

### Coded but not connected

6. **Hesitation as an Internal Event is wired** — `_compose_message_body()` in `initiative/engine.py:279` uses `MessageBodyWithDeliberation` schema, which includes a deliberation field. The doc previously said "not implemented" — this was incorrect.

### Architectural weaknesses (still present)

7. **No intent-driven context assembly.** `build_dynamic_suffix()` always assembles the same ~22 blocks regardless of the message. **Partially addressed by preflight** — the preflight now routes targeted data (person records, inbox, calendar, inner_wants, custom memory queries) based on the message. But the ambient suffix is still assembled unconditionally. If you ask something that doesn't match a preflight slot type, you still get only top-5 semantic memories and the ambient context.

8. **`dynamic_verbs` execution is `exec()` with shallow AST safety.** `_ast_check()` walks the submitted code and blocks `__import__`, `eval`, `exec`, `compile`, `breakpoint`, `input`, and access to banned modules (`os`, `subprocess`, `socket`, `sys`, `importlib`, `shutil`). The gate deliberation adds a second layer. Deep sandboxing is still not present — the exec namespace still has access to httpx and oauth tokens.

9. **World-belief consistency check uses Flash as primary path.** `_check_consistency_async()` in `belief_revision.py:214` calls Flash (`BeliefConsistencyResult`) with a 500ms timeout; `_check_consistency_lexical()` is the fallback on timeout or error. The doc previously said "still lexical" — the Flash path is live, lexical is only the fallback.

10. **Streaming is partial.** Tool hops (up to 4 rounds) are still single-shot before the final streaming generation. Complex tool-heavy replies can still have multi-second latency before the first chunk.

11. **Teo-read synthesis and signal extraction require narrative entries.** Until ≥10 entries accumulate (requires witness pass to run with a live API key), both weekly passes always skip.

12. **The simulator does not exercise the LLM path.** reflect/consolidation/self-model are mocked or skipped without a key. Behavior under realistic LLM noise isn't actually simulated.

13. **`revoke_verb` exists** — already wired as a self_tools verb. The EXPLANATION.md previously said "no revoke"; this is corrected.

14. **The opportunity vector cache (10 min) interacts poorly with sudden changes.** Affect alignment partly compensates but not fully.

---

## 13. What to Improve — Priority Order

### Tier 1 — Verify the things just wired

1. **Verify aesthetic extraction in live chat.** Watch logs for `aesthetic_reaction_logged`. If absent, the extract_mentions prompt threshold is too conservative.

2. **Verify unprocessed memory threshold in live data.** `amb > 0.6 AND sal > 0.4` may be too tight. Check `SELECT * FROM memories WHERE unprocessed=1` after a few turns.

3. **Verify TTFT in logs.** After a live chat turn, check for `chat_ttft` events. Typical target: < 1s in warm conditions. Identify whether spikes are pre-Gemini (preflight/suffix) or Gemini TTFT.

4. **Verify confidential_to is being written.** After a turn where Teo mentions something private about someone else, check: `SELECT id, confidential_to FROM memories WHERE confidential_to IS NOT NULL LIMIT 5`. If empty, the preflight prompt is too conservative about returning `"private"`.

5. **Trigger one narrative_weave manually** to seed the second `narrative_timeline` row and unblock interest promotions and teo_read_synthesis.

### Tier 2 — Verify the preflight

6. **Verify preflight output in live logs.** Watch for `preflight_done` log lines. Check that `context_slots` are populated for messages that mention specific people or ask about email/calendar. Check that `preflight_capture_written` events appear for factual messages. Tune the prompt if too many empty slots or false-positive captures appear.

### Tier 3 — Cheap, targeted fixes

7. **Add hesitation scaffolding to initiative messages.** In `_compose_message_body()`, after the composer produces a draft, a short second Flash call asks "what did you consider saying that you held back?" Append the answer as a prompt note. Not shown to Teo — internal scaffolding only.

8. **Verify world-belief consistency check in live logs.** The Flash path is live but has a 500ms timeout; under load it may fall back to lexical frequently. Watch for `belief_consistency_fallback` log events after a few belief writes.

9. **`revoke_verb` already exists** — wired in self_tools (confirmed done).

10. **AST safety check already implemented** — `_ast_check()` in self_tools.py blocks dangerous builtins and module access (confirmed done).

### Tier 4 — Bigger reshapes

11. **Semantic dedup for trait/interest names.** Embed-and-cosine against existing names instead of fuzzy-string match. Prevents both fragmentation and false merges.

12. **First-class voice drift tracking.** `voice_drift_note` is now injected into chat. But the prompt instruction is just one line. A dedicated block comparing this week's tone sample against prior weeks would make the calibration more concrete.

13. **End-to-end auth test for kinetic-sensitive verbs.** Confirm coverage of the chat → gate → confirm ticket → user-tap → execute flow.

14. **Active learning from user reactions — now wired.** 👍/👎 reactions on individual replies are stored in `reply_reactions` and consumed by weekly procedural distillation (`_load_feedback_pairs` now includes them). Frontend sends `{type: "reaction", reaction: "thumbs_up"|"thumbs_down", reply_id: int}` over WebSocket.

15. **Multi-person chat sessions.** Social graph already supports it; the chat protocol doesn't.

---

## 14. The Cognitive Picture — One Diagram

```
  ┌────────────────────────┐    ┌──────────────────────────┐
  │   User message (WS)    │    │  Autonomous tick (60s)   │
  └───────────┬────────────┘    └─────────────┬────────────┘
              │                                │
              │ persist (low-salience          │ gather candidates from
              │  episodic memory)              │  pressure / goal / interest /
              │                                │  routine / curiosity / share
              │ ┌─────────────────────────────────────────────────────┐
              │ │         PARALLEL — both start immediately           │
              │ │                                                     │
              │ │  [A] preflight Flash (~300ms)   [B] build_dynamic  │
              │ │                                      _suffix        │
              │ │  ├─ context routing                (~100ms)        │
              │ │  │   person:<name> ──► persons                     │
              │ │  │   inbox ────────► gmail (600ms timeout)         │
              │ │  │   calendar ─────► calendar (600ms timeout)      │
              │ │  │   inner_wants ──► DB query                      │
              │ │  │   world_beliefs:<t>► filtered beliefs           │
              │ │  │   memories:<q> ──► query_fast(custom query)     │
              │ │  │                                                  │
              │ │  ├─ task detection                                  │
              │ │  │   verb gap ─────► verb_proposals queue           │
              │ │  │                                                  │
              │ │  └─ memory capture                                  │
              │ │      writes facts to memories table (pre-reply)    │
              │ └─────────────────────────────────────────────────────┘
              │              wait for max(A, B)
              │              ~300–360ms (local) / ~950ms (network)
              ▼                                │
  ┌─────────────────────────┐                 │ score = pressure × opp × recency
  │ build_dynamic_suffix    │                 │       × time × headroom × affect
  │ ┌─────────────────────┐ │                 │
  │ │ affect (5-dim)      │ │                 ▼
  │ │ top-5 memories      │ │     ┌─────────────────────┐
  │ │   (compound+MMR)    │ │     │  best > threshold?  │
  │ │ audit feed          │ │     └─────────┬───────────┘
  │ │ narrative entries   │ │               │ yes
  │ │ relationship label  │ │               ▼
  │ │ char addendum       │ │     ┌─────────────────────┐
  │ │ person context      │ │     │     action.gate     │
  │ │ self-narr belief    │ │     │ leash → budget →    │
  │ │ world beliefs       │ │     │ deliberate → auth   │
  │ │ inner wants/fears   │ │     └─────────┬───────────┘
  │ │ biased_summary      │ │               │
  │ │ recurring_loops     │ │               │ execute via tool
  │ │ rupture arc         │ │               │ or send confirm ticket
  │ │ anticipations       │ │               │
  │ │ teo affect state    │ │               │
  │ │ inner questions     │ │               │
  │ │ teo read            │ │               │
  │ │ aesthetic orient.   │ │               │
  │ │ unprocessed mems    │ │               │
  │ │ felt-state phrase   │ │               │
  │ │ novelty texture     │ │               │
  │ └─────────────────────┘ │               │
  └───────────┬─────────────┘               │
              │                             │
              │ system_prompt =             │
              │   character_prefix          │
              │ + preflight context block   │
              │ + dynamic_suffix (22 blocks)│
              ▼                             │
  ┌────────────────────────┐                │
  │  Gemini Flash + tools  │◀───────────────┘
  │  (≤4 hops, streamed    │
  │   final generation)    │
  └───────────┬────────────┘
              │ streamed chunks (~500ms to first character)
              ▼
  ┌────────────────────────┐
  │ persist reply memory   │
  └───────────┬────────────┘
              │
              │ background (2 tasks):
              │  ① Flash extract_mentions ──► social_graph
              │                          ──► aesthetic_reactions
              │                          ──► mark_unprocessed
              │                          ──► person_affect_log
              │                          ──► depletion accumulation
              │  ② Flash witness_pass ───► narrative_entries
              │                          ──► narrative Chroma
              │
              ▼
       ┌────────────────────────────────────────────────┐
       │      SQLite (memories, narrative_entries)      │
       │  + inner_wants/fears/tensions/questions/       │
       │    anticipations                               │
       │  + interest_garden + world_beliefs             │
       │  + identity_traits + persons + addenda         │
       │  + person_affect_log                           │
       │  + kv: novelty_deficit, teo_read, aesthetic_o  │
       └──────────────┬─────────────────────────────────┘
                      │
        ┌─────────────┼──────────────┬──────────────────┐
        ▼             ▼              ▼                  ▼
  ┌──────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────────────┐
  │ pressure │ │ reflect 2h   │ │   nightly    │ │   weekly             │
  │ loop     │ │ router→full  │ │ (consolidate │ │ (procedural +        │
  │ (10min)  │ │ persists:    │ │ + decay +    │ │  traits +            │
  │          │ │  biased_sum  │ │ interests +  │ │  temporal_self_obs + │
  │ decay +  │ │  rec_loops   │ │ unprocessed  │ │  self_model +        │
  │ escalate │ │  anticipat.  │ │ review +     │ │  narrative_weaver +  │
  │          │ │  questions   │ │ aesthetic    │ │  narrative_consol.   │
  │          │ │  novelty_def │ │ patterns +   │ │  signal_extraction + │
  │          │ │  trait_evid  │ │ overnight    │ │  teo_read_synthesis) │
  └──────────┘ └──────────────┘ └──────────────┘ └──────────────────────┘
```

---

## 15. The Pre-Generation Preflight — Implemented (Extended)

The preflight is the brain that runs before every reply. One Flash call with conversation history (last 6 turns, or a kv-cached session summary prepended if > 12 turns) that simultaneously routes to the right information, detects tasks, and captures memories — all before Chloe generates a word.

### Architecture

```
receive message
    │
    ├── [parallel A] preflight Flash call (~200–350ms)
    │       ├── context routing → slot resolution → "## First orientation\n..." (if felt)
    │       │                                    + "## Specifically relevant context" block
    │       │   ├── per-session slot cache: same source reused within a WS session
    │       │   ├── timeout fallback notes for inbox/calendar (not silent drop)
    │       │   └── person disambiguation: multiple LIKE matches → pick highest attachment_depth
    │       ├── task/verb detection → verb_proposals queue (background)
    │       └── memory capture → write to memories table (pre-reply)
    │           ├── dedup: cosine > 0.92 within 7 days → skip write (log preflight_capture_deduped)
    │           ├── batch_ref: UUID ties all captures from this turn together
    │           ├── subject_person_id: FK written when person_name resolves to a persons row
    │           └── supersede: similar recent memory (cosine > 0.82, tag overlap) → superseded_by=new_id
    │
    └── [parallel B] build_dynamic_suffix() (~50–150ms)
            └── ambient context: affect, top-5 memories, beliefs, inner state, etc.
    │
    ▼ (both complete)
system_prompt = character_prefix + preflight_context + dynamic_suffix
    │
    ▼
Gemini Flash + tools → streamed reply
```

### Context Routing — Multiple Slots, Multiple Types

The preflight can return **any number of slots**, including multiple of the same type:

| Slot format | Resolves to | Latency |
|---|---|---|
| `person:<name>` | Full persons record: relationship class, attachment depth, stance, impression, trait profile, character addendum, cross-references | ~5ms |
| `inbox` | Last 5 emails (subject + sender) | ~500ms network, 600ms timeout |
| `calendar` | Upcoming 7 days of events | ~300ms network, 600ms timeout |
| `inner_wants` | Top 3 wants + top 2 fears with pressure values | ~2ms |
| `world_beliefs:<topic>` | Beliefs filtered by topic substring, with confidence | ~3ms |
| `memories:<custom query>` | `query_fast()` with a targeted query instead of the raw message text | ~50ms |

Multi-slot examples the prompt explicitly teaches:
- "What do you think about Zuza and Marco?" → `[person:Zuza, person:Marco]`
- "Remind me what I said about the project and check Friday" → `[memories:project discussion, calendar]`
- "Do you miss me when I'm quiet?" → `[inner_wants, memories:absence quiet gap]`

Network slots (inbox, calendar) run with a 600ms hard timeout. If they miss, a fallback note is injected instead of silence: `*(inbox slot timed out — email content unavailable this turn)*`. The LLM knows it asked for something it didn't get, preventing hallucination of email content.

**`felt_orientation`** is a new optional output field in `PreflightOutput`. One short line — Chloe's first felt response to the message before cognitive processing. Non-null for emotionally charged messages ("relief — he's back"), null for factual queries. Injected above the slot block as `## First orientation`, giving the main Flash call an emotional anchor before it reads the 22-block context stack. Null for routine small talk and task instructions.

### Memory Capture — Written Before the Reply

Captured facts are written to `memories` before the reply is generated (not post-gen). This means:
- The fact "Marco moved to Berlin" is in the DB before Chloe responds
- It can be retrieved on the *next* turn immediately
- `kind` and `tags` are LLM-assigned per capture — using a canonical tag taxonomy (always `person:<lowercase_name>`, `topic:<word>`, `affect:<valence>`, etc.)
- `confidentiality` is LLM-assigned per capture: `"public"` (default), `"relational"` (about someone close, not secret), `"private"` (shared in confidence). `"private"` → `confidential_to = person_id` is written; retrieval annotates the memory in other person contexts.
- `batch_ref` (UUID) ties all captures from the same preflight call together — useful for audit and understanding which facts came from the same conversation turn.
- `subject_person_id` is written when a `person:<name>` tag is present, resolved to `persons.id` — enables direct SQL joins.

**Capture deduplication**: before writing, a Chroma cosine check (> 0.92) against memories created within the last 7 days skips near-identical captures. "Marco moved to Berlin" repeated three times across a week writes only once.

**Supersede on correction**: after writing a new capture, the five closest existing memories are checked. If one has overlapping tags, a cosine > 0.82, and is within 30 days, it is marked `superseded_by = new_id`. It will no longer surface in `query_fast()`.

**On multiple phrasings for better retrieval**: not implemented, not recommended. Writing "Marco moved to Berlin", "Marco is in Berlin now", and "Marco's location changed to Berlin" triples writes with marginal embedding benefit. The compound scoring formula (cosine × salience × recency + affect bonus) means salience and recency matter more than small angular differences in embedding space. Well-chosen tags do more work than paraphrase variants.

### Task Detection

Verb gaps (requests Chloe has no tool for) are queued as `verb_proposals`. Matched requests need no special handling — the LLM already sees the full tool catalog and calls verbs naturally. The preflight just flags gaps.

### Latency

The preflight and `build_dynamic_suffix()` run in parallel. Total wait before the Gemini reply call is `max(preflight_time, baseline_time)`, not the sum.

| Scenario | Pre-Gemini wait | + Gemini TTFT | = First character |
|---|---|---|---|
| Routine, no slots | ~300ms | ~500ms | **~800ms** |
| Local slots (person, beliefs, inner_wants) | ~360ms | ~500ms | **~860ms** |
| Network slots (inbox or calendar) | ~950ms | ~500ms | **~1450ms** |
| + each tool hop (non-streaming) | — | +~700ms each | **+700ms per hop** |
| **Before preflight existed** | ~100ms | ~500ms | **~600ms** |

Typical cost of the preflight on a non-trivial message: **~250ms**. Network slots (inbox, calendar) cost up to **~850ms extra** and only fire when the LLM explicitly requests them.

### What This Replaced

The post-generation `intercept.py` previously ran a Flash call after the reply to detect captures and requests. It ran too late to inform the reply. The module now only exposes `get_pending_proposals()` and `mark_proposal()` — helpers for the reflect pass to consume verb proposals. All analysis moved to preflight.

---

## 16. Bottom Line

Chloe 2.0 is a substantially more complete agent than the version described in the prior EXPLANATION.md. The major gaps identified in previous sessions have all been closed:

- **Streaming** is wired. TTFT is measured and logged.
- **Inner state reaching conversation** — `inner_wants`, `biased_summary`, `recurring_loops`, `openness`, `anticipations`, `questions`, rupture, Teo's affect state, novelty deficit — all injected.
- **Narrative system exists** — `narrative_entries` table, witness pass, weekly consolidation, signal extraction, teo-read synthesis. Bootstrap has run; first entry seeded.
- **Reflect efficiency** — two-pass with router, no more firing when nothing happened. Full reflect now uses two parallel calls (ReflectCurrentState + ReflectDevelopmental) — no more 12-field monolith.
- **Memory quality** — compound scoring, MMR, mood-congruent retrieval, emotional valence annotation.
- **Emotional valence writers wired** — `update_memory_affect()` back-fills the 2 most recent chat memories after each extraction pass. Mood-congruent retrieval is now functionally active.
- **Confidential memory pipeline complete** — preflight detects private disclosures, writes `confidential_to`, retrieval annotates them. The gap (field existed, writer absent) is closed.
- **Qualitative time gap** — not just duration, but what kind of gap.
- **Depletion vs. calm** — phenomenologically distinct low-arousal states.
- **Ambivalent beliefs** — contradictions held rather than always adjudicated.
- **Kinetic-sensitive from chat** — confirm flow instead of hard error.
- **person_id normalization** — clean at the boundary, no silent fallback.
- **PII redaction** — structlog processor truncates content-bearing fields to 120 chars. Safe to log in production.
- **Daily budget tracking** — per-day USD spend tracked in kv, warned on exceed. Prometheus gauge updated.
- **Onboarding flow** — early conversations get a first-use context block that erases automatically.
- **Voice anchor** — 3-rule voice reminder always last in the dynamic suffix. Recency bias works in its favor.
- **Salience gate + token budget** — routine messages skip heavy introspective blocks. Total suffix is also capped at ~2500 tokens (`_apply_token_budget`), dropping tail-first so lower-priority blocks (teo-read, aesthetic, novelty) fall off before higher-priority ones (affect, memories, inner state).
- **Two doc corrections**: hesitation scaffolding (`MessageBodyWithDeliberation`) and Flash-based belief consistency (`_check_consistency_async`) were both wired but EXPLANATION.md previously said they weren't.
- **Memory deletion** — `forget_memory` / `forget_recent` verbs, `auth_class="intimate"`, delete from SQLite + Chroma.
- **Two clean narrative tables** — `narrative_events` (event logger) and `narrative_timeline` (weaver's autobiography) are now separate with their own schemas and migrations.

The **preflight** (§15) is now fully implemented: a pre-generation Flash call with conversation history that routes to targeted data sources (person records, inbox, calendar, inner state, custom memory queries), captures facts to memory (with confidentiality) before the reply, and detects verb gaps. The system has both ambient context assembly and targeted structured lookup running in parallel before every reply.

**New in this session (2026-05-13):**
- Emotional and personality traits are now fully separate systems. Moods are window-scoped and temporary; character traits are slow and earned.
- Every memory carries an emotional fingerprint. State-dependent recall means memories from similar emotional moments surface more readily — a qualitatively different kind of memory coherence.
- The interest loop bug is fixed. A held-back action no longer floods the DB with identical attempts.
- Simulator validated the full 72h loop end-to-end including the reflect circuit breaker and new prompt naivety constraints.

The two things that make Chloe worth building — the developmental-stage discipline ("nothing is earned until it is earned") and the closed feedback loop ("reflection updates state, state shapes context, context shapes behavior") — are both intact and running deeper than before. Every feature that was coded is now connected.
