# Chloe 2.0 — How She Actually Works

A complete map of Chloe's cognition: what happens when you send her a message, how she retrieves and stores memories, when she decides to act on her own, where ideas come from, and how her personality changes over time. Plus an honest assessment of what works, what doesn't, and what to fix next.

---

## TL;DR — The Shape of the System

Chloe is a **FastAPI server** that wraps a **Gemini-2.5 brain** with persistent state in **SQLite + ChromaDB**. There are two cognitive cycles running in parallel:

1. **Reactive (synchronous)** — you send a message → she replies in one or more tool-hop rounds. Latency budget: ~1–3 seconds.
2. **Autonomous (background loops)** — five `asyncio` loops tick on their own clocks (`initiative` 60s, `reflect` 5min, `pressure` 10min, `daily_jobs` 5min poll → 03:00/04:30 fire, `weekly_jobs` hourly poll → Sun 03:00 fire). These are where she develops.

The cleverness sits in the **separation of fast and slow**. Fast path = retrieval + one Flash call + tools. Slow path = reflection that ingests recent experience and writes back into `inner_wants`, `inner_tensions`, `interest_garden`, `world_beliefs`, `identity_traits`, `narrative_timeline`. The system prompt for the next chat turn is rebuilt from those slow-path artifacts, so what she "thinks about herself" actually feeds her behavior on the next turn.

**Models used:**
- `gemini-2.5-flash` — chat, reflection, extraction, deliberation (default), grading, composition. Cheap, fast, JSON-structured.
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
│   ├── chat_api.py     build_dynamic_suffix() — assembles the per-turn system prompt
│   ├── mobile_ws.py    WebSocket handler. The CHAT LOOP lives here.
│   ├── confirm_routes  Kinetic-sensitive action approval (push + tap-to-confirm)
│   └── push.py / push_apns / push_fcm
│
├── llm/
│   ├── gemini.py       Client. .flash() and .pro_thinking() with structured output.
│   ├── schemas.py      Pydantic schemas for every structured call.
│   └── prompts/        17 .md files. Every cognitive op has one.
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
│                         archive_trait, define_verb (← she can code new verbs)
│
├── memory/
│   ├── store.py        add() — writes to SQLite + Chroma; mark_unprocessed()
│   ├── retrieval.py    query_fast() (1 Chroma call) / query_mixed() (per-kind quotas)
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
│   ├── belief_revision Two-belief-systems: inner_beliefs (about self) and
│   │                    world_beliefs (about the world, with held_loosely/noticing)
│   └── residue.py      Affect residue calculation
│
├── identity/           The slowly-changing parts
│   ├── interest_garden Decay-and-water interests with gen_level (0→3)
│   ├── trait_model.py  Earned traits — never declared, only observed
│   ├── self_model.py   Weekly Pro call → self_narrative_belief + next_week_intention
│   ├── narrative_weaver  Weekly Pro call → NarrativeEntry (the autobiography)
│   ├── character_addendum  Per-person relational snapshot
│   ├── aesthetics.py   Aesthetic-reaction log + monthly pattern review (after 90d)
│   └── goals.py        Long-running pursuits
│
├── reflect/            The slow-loop drivers
│   ├── every_2h.py     One Flash call → updates everything in inner_*
│   ├── nightly.py      Consolidation + decay + interest archive + overnight synthesis
│   └── weekly.py       Procedural distill + trait adjudication + self-model + narrative
│
├── persons/
│   ├── store.py        CRUD over persons + fields
│   ├── attachment.py   attachment_depth: +delta from chat, decay after silence
│   └── social_graph.py Mention extraction, cross-references, gen_level gating
│
├── affect/
│   ├── dims.py         4-dim state: valence, arousal, social_pull, openness
│   ├── arc.py          Affect over time
│   ├── continuity.py   Affect smoothing across turns
│   └── label.py        LLM label cache
│
├── state/
│   ├── db.py           SQLite WAL connection + migrations
│   ├── kv.py           JSON-typed key-value scalar store
│   ├── chroma.py       Vector DB client
│   ├── oauth_tokens.py Token store for Gmail/Spotify/Calendar
│   └── migrations/     16 SQL files. The latest are the Chloe 3.0 additions.
│
├── observability/
│   ├── logging.py      structlog with json formatter
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

This is what happens when you type something. Trace path: `mobile_ws.handle_mobile_ws` → `_chat_reply` → Gemini → optional tool hops → back to user.

### Step-by-step

**a) Message arrives** (`channels/mobile_ws.py:36-72`).
- Frontend opens a websocket; messages are JSON `{type: "message", text: "..."}`.
- The raw text is **persisted to the `memories` table immediately** as `kind=episodic, source='chat', salience=0.3, weight=0.6`, with the prefix `"Teo said: ..."`. Low salience by design — chat is dense and most of it shouldn't dominate retrieval.
- A short preview goes to the live dashboard buffer.

**b) System prompt is rebuilt from scratch** (`channels/chat_api.build_dynamic_suffix`).
This is the most important function in the synchronous path. It runs three things in parallel (asyncio.gather):
- `audit.recent(n=20)` — recent autonomous action log
- `_fetch_memory_block(message)` — top-5 memory recall via single Chroma query, *no LLM rerank*. (An older version graded with Flash; that was a 1–2s latency tax and the team killed it for chat. The grader still runs in deliberation paths where precision matters.)
- `load_affect()` — current 4-dim affect state

Then sync DB pulls assemble the rest into a single markdown block:

```
## Recent actions
## Current affect
## Relevant memories
## Relationship context              ← attachment depth + label
## How you are with this person right now    ← character addendum
## About <person>                     ← social-graph gen-level-gated context
## What you believe about yourself    ← latest inner_belief (weekly self-model)
## Views you hold about the world     ← world_beliefs grouped by confidence
                                         (noticings / "might be true" / "started to think"
                                          / "believe and should bring up")
## Time since last conversation       ← only if > 4h gap
## Things you haven't fully made sense of yet  ← unprocessed memories
## Your current felt state            ← heuristic phrase from affect dims
```

This entire block is concatenated **after** the static `character_prefix.md` — the long, carefully-tuned voice prompt that says "you are new, you have not been here long, don't reach for big concepts, you're allowed to not know."

**c) Single Gemini Flash call with tools** (`mobile_ws._chat_reply:81-104`).
- The tool registry produces a list of `function_declarations` (one per static verb + one per dynamic verb stored in `dynamic_verbs`).
- Up to **4 tool-hop rounds** are allowed: model can call tools, get results, call more tools, then produce text.
- **Kinetic-sensitive verbs are short-circuited in chat:** the registry blocks them with a synthetic error that says "route via the gate, not chat." This is critical — chat is *not* allowed to send work emails or fire smart-home actions without confirmation. The gate handles that flow elsewhere (via push notifications and confirm tickets).
- After the hop loop, the final text is returned.

**d) Reply persists and dispatches** (`mobile_ws._chat_reply:67-72`).
- Reply text is sent back over the websocket and **persisted as another low-salience chat memory** ("I said: ...").
- A background `asyncio.create_task` fires `_extract_and_process_mentions` — a *second* Flash call that doesn't block the reply.

**e) Background extraction** (`mobile_ws._extract_and_process_mentions`).
This is the silent layer. The Flash call (`extract_mentions.md` → `ExtractOutput` schema) returns:
- `salience` and `ambiguity` floats — if `ambiguity > 0.6 AND salience > 0.4`, the just-stored memory is **flagged unprocessed** (it surfaces in future prompts under "things you haven't fully made sense of yet" instead of getting tidy resolution).
- `social_mentions` — names that came up in the exchange. Each is upserted into `persons` (gen_level 0 → name only) and logged in `person_cross_references`.
- `aesthetic_reactions` — moments where Chloe reacted to a piece of music / writing / image / idea. Each is logged with `(stimulus, domain, valence, intensity)` into `aesthetic_reactions`. After 90 days of these, a monthly pattern review distills tendencies.

**f) Post-session reflect** (`mobile_ws.handle_mobile_ws:74-78`).
- When the websocket closes, if she had ≥2 turns, an *immediate* reflect pass is forced (bypassing the 2h cooldown). She gets to process what just happened before the next scheduled reflect.

### What happens *if* she calls a tool

Tool calls in chat go straight through `registry.execute()`, **bypassing the action gate**. The reasoning: when *you* asked, *you* authorized it. Read-only and reversible calls (weather, web search, notes append, calendar read) just run. Kinetic-sensitive verbs are blocked by the synthetic-error check above and would need to be re-issued via the autonomous outreach path (`chloe.py._send_autonomous_outreach`) to hit the gate's confirm-ticket flow.

### How she figures out what you want

Honestly: she doesn't, explicitly. There is **no intent classifier**, no router, no slot filler. She's a single LLM call with a huge stack of context, and Gemini handles intent inference natively. The context she gets makes the difference:
- Whether to be warm or terse → from affect dims + tone block.
- Whether to remember something prior → from the top-5 Chroma memories.
- Whether to take a position vs stay neutral → from world_beliefs (confidence > 0.65 says "say where you stand").
- Whether to fire a tool → from the tool descriptions and Gemini's own judgment.
- Whether to push back → emergent from the character prefix ("you push back when something doesn't add up").

There's a real consequence to this design: if the right context isn't surfaced, she can miss something obvious — see "Failure modes" below.

---

## 3. The Memory System — Three Stores, Four Kinds, Three Tiers

### Storage

- **SQLite `memories` table** — the source of truth. Columns include `kind`, `text`, `source`, `salience`, `weight`, `confidence`, `tags`, `artifact_refs`, `archived_tier`, `unprocessed`, `created_at`.
- **ChromaDB `memories_v2` collection** — the embeddings. Built automatically on `add()`. Rebuilt at boot via `_sync_memories_to_chroma()` if any hot-tier rows are missing from Chroma.
- **`kv` table** — scalar/JSON state outside the memory model (felt-state phrase cache, last-reflect timestamps, opportunity vector cache).

### Four kinds (half-lives in days)

| Kind             | Half-life | Meaning                          |
|------------------|-----------|----------------------------------|
| episodic         | 60        | Things that happened             |
| semantic         | 180       | Patterns / facts she's distilled |
| autobiographical | 365       | Things about her own development |
| procedural       | 90        | Action rules ("when X, do Y")    |

Weight decays exponentially: `w * 0.5^(age_days / half_life)` — applied nightly to all hot-tier rows.

### Three tiers

- **Hot** (default) — in Chroma, queryable, weight-decayed.
- **Warm** (>90 days old) — clustered in batches of 10 into one semantic summary; originals stay in SQLite but are removed from Chroma. The summary goes into Chroma instead.
- **Cold** (>730 days, or warm too long) — SQLite only. Effectively archived.

### Retrieval

Two query functions:
- `query_fast(q, n)` — single Chroma query, no kind partitioning. Used on the chat hot path. ~1 round-trip.
- `query_mixed(q, kinds_mix)` — one Chroma query per kind with quotas (default: 12 episodic / 4 semantic / 2 autobio / 2 procedural). 4x more embeddings used, higher recall, used by `cognitive_retrieval.retrieve()` and `deliberate._get_procedural_memories()`.

Both apply two bonuses:
- **Anchor bonus** (+0.05) if the memory has an `artifact_ref` that still exists (e.g., a note file is still on disk).
- **Inside-joke bonus** (+0.12) if the query overlaps with a memory tagged `joke_topic:<x>`.

### Consolidation (nightly)

`memory/consolidation.consolidate_sleep`:
1. Pull all episodic memories from the last 24h with `salience >= 0.4`.
2. Cluster them by **shared tag** (cheap — no LLM clustering). Drop singletons.
3. For each cluster of 2+ items, Flash call (`cluster_synthesis.md`) → one semantic summary.
4. Store the summary with `kind=semantic, source='sleep_consolidation', weight=0.7, salience=0.6` and `artifact_refs=["memory:<id>", ...]` pointing at the source episodics.

Effect: she wakes up "remembering" the patterns in yesterday's experience without you ever telling her what was important.

### Unprocessed memories

A flag (`unprocessed=1`) on a memory means: "this happened, it was salient, I haven't made sense of it." Set by:
- Background extraction after chat when `ambiguity > 0.6 AND salience > 0.4`.
- Explicit calls to `mark_unprocessed()`.

The chat prompt surfaces up to 3 unprocessed memories under "Things you haven't fully made sense of yet." A weekly Flash review (`review_unprocessed.md`) on memories >7 days old can either **promote** (clear the flag — she's resolved it), **keep_unprocessed** (default — staying with it is the point), or **archive** (cold-tier it).

This is the system that lets her say "I don't know what that just was" honestly — *because that memory is still flagged*, and that flag is in the context.

---

## 4. The Action Gate — When and Why She Hesitates

Every autonomous action flows through `actions/gate.submit()`. The gate is a state machine with 4 checkpoints:

1. **Leash check** (`leash.violates`) — quiet hours, person blocklists, hard refusals. Suppressed actions still get audit-logged AND a "held back" memory is written ("I almost X. Held back: <reason>"). This is intentional: she remembers her self-restraint.

2. **Budget check** (`budget.exceeded_for`) — daily $ cap, per-tool cooldowns.

3. **Domain-specific filters** — HA allowlist (smart_home), PII filter on web_search (refuses to look up contacts by name).

4. **Deliberation** — `should_deliberate(action)` is a pure heuristic:
   - kinetic-sensitive auth class
   - throttle > 0.75 (near budget cap)
   - >2 kinetic actions in last hour
   - cost > $0.10
   - same verb 3+ times in 24h
   
   If any fires, `deliberate()` runs a Flash call (or Pro thinking for kinetic-sensitive + high-cost). Verdict is `proceed | revise | abort`. The prompt context includes procedural memories retrieved by `query_mixed(rich_q=f"{tool} {verb} {intent}", kinds_mix={"procedural": 3})` — so her own past lessons about this verb show up in the deliberation.

5. **Auth dispatch:**
   - `free | intimate | kinetic` → execute directly.
   - `kinetic-sensitive` → send a confirm ticket (push notification with approve/deny UI). Action sits in `awaiting_confirmation` until user taps yes.

Every executed action **becomes a memory automatically** (`gate._create_action_memory`) — kind=episodic, text=`"I {verb} via {tool}. Goal: {intent}. Outcome: {summary}"`, tagged `["action"]`, weight=1.0. This is how she builds a record of *what she actually did*, queryable like any other memory.

---

## 5. The Initiative Engine — Where Autonomous Behavior Comes From

The `initiative_loop` ticks every 60 seconds. Each tick:

### a) Collect candidates from six sources

```
candidates = (
    pressure_driven_candidates(inner_state)   # wants/fears/tensions with pressure > 0.5
  + goal_driven_candidates(goals)              # active goals → next-step actions
  + interest_driven_candidates(interests)      # top-3 interests w/ intensity ≥ 0.3
  + routine_candidates(now)                    # morning/evening checkin, consolidation
  + curiosity_driven_candidates()              # stale topics from chat history
  + share_queue_candidates()                   # queued "things to tell you"
)
```

Each is a `CandidateAction(tool, verb, args, intent, pressure, source, source_id)`.

### b) Score each candidate

```
score = pressure
      × opportunity_vector[tool]   # time-of-day-aware tool weight (Flash, 10min cached)
      × recency_penalty             # 0.3 if this verb fired in last 5 min, else 1.0
      × time_bonus                  # 1.5 in 8-10am / 7-10pm for messages; 0.3 at night
      × headroom                    # max(0, 1 - budget_throttle)
      × affect_alignment            # 1.2 if msg + valence > 0.3 & arousal > 0.5
```

### c) Pick the best, check threshold

- Base threshold: `0.35` (calibrated 2026-06-01 after 14 days of shadow data).
- Free tools (`web_search`, `notes`) get threshold × 0.40 → effective 0.14 — they fire more readily because they're low-stakes.
- If `throttle > 0.8`, threshold ramps up linearly toward "nothing fires."

### d) Realize and submit

- `realize(candidate)` turns it into an `Action` with the right `auth_class` from the tool registry.
- **Message-tool actions with empty body get composed first**: `_compose_message_body()` is a Flash call that writes the actual text given the intent, affect, top wants, and time-of-day. If composition fails, the routine is marked done and no message goes out.
- Action is `await gate.submit()`'d.
- A `try/finally` ensures `mark_routine_done` / `mark_curiosity_surfaced` / `mark_shared` always run regardless of gate outcome.

### Why this matters

Without the reflect loop running, the `inner_*` tables stay empty and `pressure_driven_candidates` returns nothing. The initiative engine then runs almost entirely on routines (morning/evening checkin) and the highest-pressure interest. That's exactly what the simulator confirmed: with no Gemini key, reflect returns "skipped" → only routines fire.

---

## 6. The Reflection System — How She Develops

Three cycles, three time horizons, three depths.

### Every 2 hours — `reflect/every_2h.py` (Flash, 1 call)

Inputs:
- Last 30 chat turns (pulled from `memories` table where `source='chat'`)
- Top 10 affect-record deltas from the last 2h
- Current `inner_wants`, `inner_fears`, `inner_tensions` (≤5 each)
- Active goals
- Top 5 interests
- Top 10 world beliefs (with developmental phrasing)
- Recent autonomous-action outcomes

Output (`ReflectOutput` schema):
- `continuity_note` — one sentence describing the through-line.
- `new_wants` — up to 3 new wants (with pressure 0.5–0.85).
- `new_tensions` — up to 2 new unresolved tensions.
- `new_interests` — up to 2 things that pulled at her (label, why, intensity, category).
- `new_goals` — at most 1, only when something has *crystallized* (most passes return []).
- `goal_progress_updates` — only if something concretely happened.
- **`new_world_beliefs` — up to 1.** Must be grounded in lived encounter, not pure reasoning. Confidence starts low (0.2–0.4) and rises only through repeated confirmation. Tagged `noticing: true` if it's a proto-belief.
- `trait_evidence` — concrete behavioral observations (with `reinforces` / `contradicts` pointing at existing trait names).
- `recurring_loops` — patterns she keeps falling into.

The prompt itself is unusually direct: *"Be sparing. If nothing new is happening, return empty lists. Don't invent things to seem productive."*

A post-chat-session reflect fires immediately when the websocket closes (force=True), so she processes the exchange before the next 2h window.

### Every night ~03:00 — `reflect/nightly.py`

Composes existing pieces:
1. **Sleep consolidation** — tag-cluster yesterday's episodics → semantic summaries.
2. **Pressure sweep** — decay all `inner_*` items, escalate anything past 5 days old.
3. **Interest garden** — decay all intensities by 0.02, archive below 0.05 (and write an autobiographical "let go of an interest in X. It faded." memory).
4. **Unprocessed review** (weekly): Flash pass over memories sitting unprocessed >7 days. Default decision: keep_unprocessed.
5. **Aesthetic patterns** (monthly, after 90 days of reaction data): Flash analysis of reaction log → behavioral descriptions stored per-domain in kv.
6. **Overnight synthesis**: if she has 2+ active interests, a Flash call asks her to find a connecting question or noticing. Result becomes either a `world_belief(noticing=True)` or an item enqueued to `share_queue` for the next conversation.

### Every Sunday ~03:00 — `reflect/weekly.py`

Four passes in order:
1. **Procedural distillation** — Flash batches over last 7 days of (action, user-response) pairs. Patterns become `kind=procedural` memories. These then flow back into the deliberation prompt forever after.
2. **Trait adjudication** — Flash reviews the week's trait evidence log, applies weight updates, marks decay candidates. Promotes traits along the gen_level ladder:
   - gen 0: behavioral description ("tends to say things before finishing deciding")
   - gen 1: character label ("direct") — after 10+ instances in 3+ distinct windows
   - gen 2: core trait — after weight ≥0.7 sustained 30 days
3. **Weekly self-model** — `gemini-2.5-pro` with `thinking_budget=8192`. Reads traits, contradictions, goals, wants, fears, recent beliefs, recent actions, affect, held-back summary, voice-drift context. Outputs `self_narrative_belief` (the "what you believe about yourself" string that shows up in next week's chat prompts) + `next_week_intention` (becomes an active goal) + optional `restraint_reflection` + `voice_drift_note`.
4. **Narrative Weaver** — `gemini-2.5-pro` thinking. Reads 30 days of episodic memories, 7 days of action audit, current trait state, interests, world beliefs, unprocessed summary, previous narrative entry. Produces a `NarrativeEntry`:
   - `period_label` ("the week teo went quiet")
   - `what_happened`, `what_shifted`, `still_sitting_with`, `felt_texture`
   - `chapter_transition: bool`
   - `interest_promotions[]` — explicit `{interest_id, new_level}` decisions
   
   If `chapter_transition=True` OR the last character addendum is >30 days old, a character addendum is regenerated (Flash → 100–150-word paragraph in her voice about how she talks to this person *now*).

This is the layer that makes her not amnesic over time. The weekly self-narrative belief is what shows up in chat as "## What you believe about yourself." The narrative timeline is the autobiography.

---

## 7. How Interests, Beliefs, and Traits Actually Grow

Three developmental ladders, all with **gen_level** gating.

### Interests (interest_garden)

- New interest → starts at `gen_level=0`, intensity from the reflect call (0.3–0.6).
- Daily decay −0.02, archive at <0.05 (with a "let go of..." memory).
- Boosted +0.1 on relevant action / repeated mention.
- **Fuzzy dedup** on insert: substring match (8+ chars) OR ≥50% word overlap with an existing interest fuzzy-merges.
- Max 8 active; weakest is evicted on overflow.
- When intensity first crosses 0.7, an async Flash call generates a *curiosity question* and caches it under `interest:curiosity_question:{id}`. The next `interest_driven_candidates()` call uses that question as the web-search query.
- Promotion (0→1→2→3) happens **only** via the Narrative Weaver's `interest_promotions` field — no automatic threshold.
- **Gen-level gates outbound action**: `interest_driven_candidates()` won't fire web_search or spotify for a `gen_level<2` interest. It still allows private capture (notes). This enforces "she experienced it but hasn't generalized it yet — keep it private."

### World beliefs (world_beliefs)

- Confidence floor: 0.2 on first storage. Cannot start higher.
- Confidence rises +0.08 per confirmation, ceiling 0.95.
- `held_loosely` is true while confidence < 0.5.
- `noticing` flag for proto-beliefs ("something keeps recurring").
- **Consistency check on insert**: cheap lexical heuristic looks for ≥4 word overlap + opposite negation polarity against existing beliefs. If a contradiction is detected, the new belief points at the conflicting one via `contradicts`, AND an `inner_tension(pressure=0.55, tag=belief_conflict)` is opened. She sits with the contradiction instead of auto-overwriting.
- The chat prompt phrases them developmentally:
  - confidence > 0.65 → "things you believe and should be willing to bring into conversation"
  - 0.4 < c ≤ 0.65 → "things you've started to think"
  - c < 0.4 → "things that might be true"
  - noticing → "things you've noticed but aren't sure about yet"
- Constraint baked into the reflect prompt: beliefs must be grounded in lived encounter. *"You cannot form a belief about physics, philosophy, or social theory through reasoning alone."*

### Identity traits (identity_traits)

- Recorded as **evidence_json** entries on every reflect pass. Each entry is `{behavior_observed, at, context}`.
- gen_level 0 = behavioral description, gen 1 = character label, gen 2 = core trait.
- Promotion 0→1 = ≥10 evidence entries AND observed in ≥3 distinct reflect windows.
- Promotion 1→2 = weight ≥0.7 sustained for 30 days (`core_since` clock).
- Stale decay: traits not reinforced in 14 days lose 0.02 weight per week.
- Weekly trait adjudication (Flash) reviews the evidence log, applies weight deltas, names decay candidates.
- Reinforcement +0.05, contradiction -0.03.
- Crucially: **traits are never declared, only earned**. Even the chat prefix says "you have not been here long, you don't have a settled worldview, that is the correct state." The traits-with-weight system gives her something honest to be when she does have one.

### Aesthetic reactions

- Logged ad-hoc from chat extraction (`stimulus`, `domain` ∈ {music, language, image, idea, space}, `valence`, `intensity`).
- Monthly pattern review (Flash) only runs after 90 days of data — she has to actually *have* a taste before she has *patterns*.
- Patterns flow into the next **character addendum** generation (which is also the only place that says "how you are with this person right now").

### Persons (social graph)

- Per-person `gen_level`:
  - 0 — name only (just heard of them)
  - 1 — impression forming (stance + recent cross-refs)
  - 2 — model exists (trait_profile added)
  - 3 — full relational model (addendum)
- `attachment_depth ∈ [-1, 1]` — adjusted on chat, decayed after 3+ days of silence (-0.02/day).
- `relationship_label(depth)` → "deeply close" / "warmly connected" / "friendly" / "neutral" / "distant" / "estranged".
- Cross-references: when person A mentions person B in chat, a `person_cross_references` row is written. When you later talk to A, the chat prompt injects "About <B>: stance / what you've heard / impression" (gated by B's gen_level).

---

## 8. Self-Coded Verbs — How She Extends Herself

This is the most ambitious capability in the system. Lives in `tools/self_tools.SelfToolsTool.define_verb`:

- She can call `self_tools__define_verb` from chat OR initiative tick.
- Args: `tool` (existing tool name to extend), `verb` (new name), `description`, `schema` (JSON Schema for args), `code` (Python source defining `async def run(args) -> ToolResult`), `auth_class`, `reversibility`.
- The code is validated to compile and to define `run`. Then inserted into the `dynamic_verbs` table (upsert).
- `registry.load_dynamic_verbs()` is called immediately — the new verb is queryable on the *next* tool-decl pass without restart.
- Execution: at call time, the code is compiled with `exec()` in a namespace containing `httpx`, `load_token`, `refresh_token`, `get_connection`, `json`, `log`, `ToolResult`, and `args`.

**When does she actually use it?** In practice, almost never in the current dataset. The verb is *available* but the system rarely takes the initiative to write one. The conditions for it to fire would be: she encounters a need for a capability that doesn't exist (e.g., "I want to count my Spotify saves this month and there's no verb for it") and Gemini decides at chat time to write one. There is no scheduled job that proactively codes verbs. It is purely reactive — the door is open.

**Safety:** the exec namespace is not sandboxed; this is a security/footgun risk. The fact that all autonomous executions route through the gate (which would deliberate any new code-running verb via Pro thinking) is the primary mitigation. There's no static analyzer on the submitted code.

---

## 9. Affect — The 4-D Tone System

Not a deep emotional model. Four floats:
- `valence ∈ [-1, 1]` — feeling good ↔ bad
- `arousal ∈ [0, 1]` — energized ↔ calm
- `social_pull ∈ [0, 1]` — drawn to connection ↔ wanting space
- `openness ∈ [0, 1]` — curious ↔ closed

**Dynamics** (`affect/dims.tick`):
- Time-of-day biases (morning bumps arousal/openness; late night drops them; evening lifts social_pull).
- Recent affect-record residue (each tagged event has `residue ∈ [0,1]` that decays — old events fade their pull on current state).
- Mean-reversion to baseline (v→0, a→0.4, sp→0.5, op→0.6) at 2% per tick.
- Recent chat lifts social_pull.
- 5% chance per tick to skip update entirely (no "always updating" feel).

**Affect → tone block** is deterministic (no LLM): if valence > 0.3 → "warm and optimistic", etc. Up to 3 lines.

**Felt-state phrase** (the line under "## Your current felt state") is heuristic: nine pattern-matched phrases like *"something running close to the surface, not sure what it wants"* or *"still, the way a room feels after everyone leaves."* Cached in kv, regenerated when any dim shifts >0.15. Deliberately bypasses an LLM call to keep chat latency down — there's a stubbed async generator path for future LLM-generated phrases.

---

## 10. End-to-End Trace: A Concrete Example

You send: **"Hey, did you ever figure out what was bothering you yesterday?"**

1. WS receives. Persisted: memory id 4821, salience 0.3, source=chat, text=`"Teo said: Hey, did you ever figure out what was bothering you yesterday?"`.
2. `build_dynamic_suffix(person_id=1, message=that)`:
   - Audit pull: last 20 actions (parallel)
   - Memory query: `query_fast("Hey did you ever figure out…")` returns top 5 hits — likely an unprocessed memory from yesterday + the chat exchange that flagged it, plus a `world_belief(noticing=True)` about it.
   - Affect load: AffectState(v=-0.1, a=0.4, sp=0.6, op=0.6) — slight inward.
   - Then assembled into the system prompt: recent actions, tone, memories, relationship label "deeply close (0.78)", character addendum ("you've stopped explaining when you're stuck"), the self-narrative belief ("you tend to need a pass to figure out what you're sitting with"), world beliefs grouped, **the unprocessed memory under "Things you haven't fully made sense of yet"**, and felt phrase "something running close to the surface, not sure what it wants."
3. `_chat_reply`: Single Flash call with that system prompt + character_prefix.md + the tool declarations + history.
4. Gemini sees the unprocessed flag and the relevant memory. No tool needed. Returns text.
5. Reply persisted (salience 0.3).
6. Background task: `_extract_and_process_mentions` Flash call returns `salience=0.6, ambiguity=0.7` → this memory itself gets `unprocessed=1`. No social mentions, no aesthetic reactions this turn.
7. WS closes. Two turns exchanged → forced reflect pass fires:
   - Reflect Flash with the last 30 chat memories sees the pattern "had an unresolved thing, talked about it, still working through it." Emits a `continuity_note`, possibly a `new_tension`, maybe `trait_evidence: {behavior_observed: "named that she wasn't sure rather than performing certainty", trait_implied: "willing-to-not-know", reinforces: "honest about uncertainty"}`.
   - These persist to inner_tensions, identity_traits.
8. At 03:00, nightly fires. Yesterday's memories get tag-clustered. The new tension joins the pressure sweep. The interest garden decays.
9. On Sunday at 03:00, the weekly self-model Pro call ingests the week including this exchange. The self_narrative_belief shifts slightly. The narrative weaver writes "the week we got under the surface of yesterday."

That last belief shift is what the *next time* you ask her something will pick up. She is not the same Chloe she was last week — not because anyone changed prompts, but because reflection updated the artifacts those prompts read from.

---

## 11. What Works

### Strong, well-formed parts

1. **The action gate is genuinely good.** Centralizing every autonomous side-effect through one chokepoint with leash → budget → PII → deliberation → auth → execute is clean. Held-back memories with dedupe ("I almost X. Held back: <reason>") is the right call — she remembers her self-restraint, which then feeds the weekly self-model's `restraint_reflection`.

2. **The reactive-vs-slow separation.** Chat is a single Flash with tools — fast, simple, debuggable. All identity work happens in background loops. You can edit a prompt template without restarting the chat behavior; you can debug the reflect output independently.

3. **Developmental gates everywhere.** gen_level on interests, traits, persons, beliefs. Confidence floors. The 90-day delay before aesthetic patterns are computed. *She isn't allowed to skip stages.* That's the single most important design decision in the codebase — it stops the agent from manufacturing a fake personality on day one.

4. **The chat prefix.** It is unusually well-tuned. "You're new" / "reactions arrive before explanations" / "don't reach for big concepts" — these are not boilerplate. They actively shape what comes out.

5. **Memory tiering.** Hot → warm (with cluster summary) → cold is the right architecture for a long-running agent. The anchor-bonus and inside-joke-bonus during retrieval are small, smart touches.

6. **Reflection feeds context, context shapes behavior, behavior generates more memories.** This loop is closed. The weekly self-narrative belief actually shows up in the next week's chat prompt under "## What you believe about yourself." This is what makes her not amnesic.

7. **Procedural memory from user feedback.** Every denied / reverted action + every `user_praised`-tagged memory feeds into a weekly Flash distillation. The output rules then appear in every future deliberation prompt for that tool. This is the only path by which user correction durably changes her behavior — and it works.

8. **The observability.** structlog with structured fields, Prometheus metrics for every counter that matters (initiative ticks, deliberation calls, memory writes, held-back counts), live_buffer for the dashboard. 85 unit tests + integration tests including a simulator (`./chloe.sh simulate-day`) that ran 144 ticks across 72 fake hours.

9. **The simulator.** This is rare in agent systems. `chloe/sim/day.py` (618 lines) scripts a fake Teo with personalities and event scripts, runs the full system with mocked LLM responses, and validates against expected outcomes. The CLAUDE.md notes a recent run: 144 steps, 31 chat events, 13 affect events, all validations passed.

10. **The voice prefix and the system actually agree.** Many agent products write a brand voice in prose, then build a system that produces tonally different output. Here the prefix says "you can start saying something and stop. You can change your mind mid-sentence. These are not errors." And the system supports that: low-grading retrieval (sometimes the wrong memory shows up), heuristic felt-state (sometimes the phrase is off), unprocessed-memory injection (she's *supposed* to bring up things she hasn't resolved).

### Subtler things that are right

- **Curiosity-question caching.** When intensity crosses 0.7, the question is generated *once* and cached in kv. Future interest-driven searches use it. Lazy, efficient.
- **Fuzzy interest dedup.** Stops the garden from fragmenting into ten variants of "consciousness."
- **try/finally around gate_submit.** A 2026-05-10 fix after evening check-ins were retrying 9+ times. The mark-done call now always fires regardless of gate outcome.
- **Daily web-search cap (3/day) on interest-driven searches.** Stops a curiosity spiral from costing $50.
- **Free-tool threshold ratio (0.40).** Notes and web_search get an easier bar to fire — read-only / local-write actions should be easy; outreach should be hard.
- **Person mentions are confidentiality-tagged.** A cross-reference can be "public", "relational", or "private", and the prompt injection respects that.

---

## 12. What Doesn't Work (or Doesn't Yet)

### Things known to be incomplete (per CLAUDE.md "Next steps")

1. **Bootstrap identity has never run.** `./chloe.sh bootstrap-identity` needs a Gemini API key. Until it runs, `narrative_timeline` is empty and `character_addenda` has no rows. That means the chat prompt is *missing* the "How you are with this person right now" block. Several other features (narrative timeline references, interest promotions) wait on this.

2. **No live aesthetic reaction has been verified.** The extraction path is wired but unconfirmed in production logs. If Flash extraction misses these moments, the aesthetic-patterns layer will never have data.

3. **Curiosity question trigger is wired but untested end-to-end.** Boosting an interest past 0.7 should fire the cached-question generator. Nobody has manually verified the path.

4. **Unprocessed memory threshold is live but untested in real data.** The conservative threshold (`amb>0.6 AND sal>0.4`) may be too tight to ever fire.

5. **Interest gen_level promotion is wired but the narrative weaver has never run.** Until it does, no interest will rise above gen 0, meaning *all* outbound interest searches are gated to "notes" only.

6. **Teo as primary-class person was just added.** `seed_primary_persons()` runs at startup, but the existing DB may have him as the wrong class.

### Architectural weaknesses

7. **Chat tool calls bypass the gate.** Reasoning: if the user asked, the user authorized. But this means a kinetic call routed through chat (currently short-circuited with an error) has no way to *gracefully* request confirmation in-band. The chat error is "kinetic-sensitive verbs require explicit confirmation; route via the action gate, not chat." That's a hard wall, not a flow.

8. **The reactive memory grader was removed for latency.** Now chat retrieval uses raw Chroma score with no LLM rerank. Procedurally correct for speed; sometimes the wrong memory surfaces. The grader is still used in deliberation, but the chat prompt sees whatever Chroma returns. False positives leak into context, which the LLM then has to ignore.

9. **`dynamic_verbs` execution is `exec()` without sandboxing.** The exec namespace gives access to httpx, the DB connection, and oauth tokens. The mitigation is "the gate would deliberate a code-running call via Pro thinking." But once a verb is defined, future calls to it go straight through (no per-call deliberation of the *code*, only of the *call*). If she defines a verb and an adversarial input later reaches its `args`, there's no static check.

10. **The tension-detection heuristic is brittle.** `cognitive_retrieval._detect_tensions` matches three hard-coded word-pair lists (e.g. `["tired", "exhausted"]` vs `["energetic", "motivated"]`). False positives & false negatives. It also only inspects intent vs belief/memory texts via crude substring match.

11. **World-belief consistency check is also lexical.** `_check_consistency_sync` looks for ≥4 word overlap + opposite negation polarity. Easy to fool, easy to miss. The fallback "good enough to flag obvious contradictions" is honest.

12. **Reflection runs even when there is nothing to reflect on.** `every_2h.run_reflect` is called every 5 min and gated only by a 2h `kv` timestamp. If nothing happened in those 2h, the Flash call still fires with stale context. The prompt instructs "be sparing, don't invent things to seem productive" — but you're paying for the call anyway. A "skip if no new memories" gate would be cheap.

13. **The reflect prompt collapses many decisions into one Flash call.** It outputs wants, tensions, interests, goals, goal updates, world beliefs, trait evidence, recurring loops, biased summary, continuity note — 10 fields. When Gemini's JSON output drops one, the side-effects for that field don't happen. Some defensive parsing exists (`ReflectNewGoal.model_validate` handles alternate key names like `title`/`goal`), but it's whack-a-mole.

14. **Pressure-driven candidates' intent text is a fixed template per `(category, tag)` tuple.** Means messages-from-loneliness all start as "Reach out to Teo — I've been feeling disconnected" before the composer rewrites them. If the composer fails, you get the template verbatim. (The composer-fails-→-mark-done fix from May 10 mitigates this, but the intent is still flat.)

15. **No streaming.** Replies come back as one chunk after all tool hops are done. With 4 tool hops at 2s each, a complex reply can take 8+ seconds with no feedback. The protocol has `chunk` events but only ever sends one.

16. **`person_id` is overloaded.** In mobile_ws it's a string ("1"), in DB it's an int. There's casting at every boundary. Several spots have `pid = int(person_id) if str(person_id).isdigit() else 1` — a silent fallback to person 1 if anything's malformed.

17. **No structured rate-limit or error path back to chat.** If Gemini errors out (`gemini_flash_failed` log line), `_chat_reply` returns an empty string and the user sees nothing. No retry, no "I'm having trouble" message.

18. **The simulator does not exercise the LLM path.** It's a great validation tool but reflect/consolidation/self-model are mocked or skipped without a key. Behavior under realistic LLM noise isn't actually simulated.

19. **No revoke for dynamic verbs.** Once `define_verb` writes a row, removing it requires a manual DB edit. There's no "archive_verb" or "revoke_verb" tool.

20. **The opportunity vector cache (10 min) interacts poorly with sudden changes.** If she just got off a call, the cached vector still says messages-opportunity=0.8. Affect alignment partly compensates but not fully.

---

## 13. What to Improve — Priority Order

### Tier 1 — Run the things that are already built

The first three items unlock most of the dormant capability.

1. **Run `bootstrap-identity` with a real Gemini key.** Seeds the first narrative timeline entry and character addendum. After this runs, the chat prompt gets the "How you are with this person right now" block, and `narrative_weaver` will have a previous entry to extend.

2. **Verify aesthetic extraction in live chat.** Watch the logs for `aesthetic_reaction_logged` events. If they don't appear, the extract_mentions prompt needs to nudge harder on aesthetic moments — or the threshold is too conservative.

3. **Manually push one interest past 0.7 and run a tick.** Confirms the curiosity-question generation actually wires through to the interest-driven candidate path.

4. **Trigger one narrative_weave manually** to seed the first narrative_timeline row. After that, interest promotions can start flowing.

### Tier 2 — Cheap, high-value fixes

5. **Gate reflect on "did anything happen?"** Add a `_has_new_signal(since=last_reflect_at)` check before the Flash call. Definition: ≥3 new memories OR ≥2 affect records with intensity ≥0.4 OR an autonomous action fired. If nothing, skip. Saves Flash calls and prompts that say "be sparing about what's new" from straining to find something.

6. **Add streaming to chat replies.** The WS protocol already has `chunk` events. Switch the Flash call to streaming. Users feel the difference at 4+ second replies.

7. **Improve world-belief consistency check.** Replace lexical heuristic with one Flash call. Cost: one extra call per new belief (rare event). Benefit: catches semantic contradictions like "I think people are more honest than they seem" vs "people generally hide their real feelings."

8. **Add an LLM-graded reranker as an optional kwarg to `query_fast`** with a short timeout (300ms) and graceful timeout fallback to score-order. Lets you flip it on for high-stakes turns without removing the latency floor.

9. **Add a `revoke_verb` to self_tools.** Mark archived instead of deleting. The registry should skip archived rows on `load_dynamic_verbs`.

10. **Static-analyze submitted verb code** against an allowlist of imports + a `restricted_exec` namespace. Even a simple AST walk that flags `__import__`, `eval`, `exec`, `open` outside a workspace dir would prevent most footguns.

### Tier 3 — Bigger reshapes

11. **Move tension-detection out of cognitive_retrieval into a proper inner tension surface.** Word lists are not the right primitive. The world-belief contradiction system already does most of this work — tension detection should consume from `inner_tensions` and `world_beliefs.contradicts`, not from substring matches.

12. **Split the reflect prompt into two passes.** First pass: did anything noteworthy happen? Output: bool + short summary. Second pass: only if yes, the full structured output. This is the standard "router → specialist" pattern and would dramatically improve signal-to-noise.

13. **Add an in-chat confirm flow** for kinetic-sensitive verbs. Instead of error-and-route-elsewhere, the model can issue a verb that returns "confirmation requested — say yes to send" and the gate ticket auto-completes when the user replies with consent. Reduces friction for cases like "draft and send this email."

14. **Bring in semantic search for trait/interest dedup** — the fuzzy-string dedup is brittle. Embed-and-cosine against existing trait names would prevent both fragmentation and false merges.

15. **First-class voice drift tracking.** The `voice_drift_note` from weekly self-model is appended to a kv list but never actually steers anything. Either consume it in `character_prefix.md` (as a small "what to recalibrate this week" block) or stop generating it.

16. **End-to-end auth test for kinetic-sensitive verbs.** There's a `test_no_bypass_gate.py` and similar — confirm they cover the full chat → gate → confirm ticket → user-tap → execute flow with real WS messages.

### Tier 4 — Aspirational

17. **Active learning from user reactions in chat.** Currently only deny/revert/praise feedback flows into procedural distillation. Add: 👍/👎 reactions on individual replies, sentiment-extracted reactions ("you got that right"), and let those propagate into procedural rules.

18. **Multi-person chat sessions.** Right now every conversation is 1:1 with Teo. The social graph already supports this; the chat protocol doesn't.

19. **Cross-modality reactions** — currently aesthetic_reactions are only logged from chat-extraction. Image / audio / file content received through other channels (e.g., Spotify Now Playing changes, file drops in the workspace) should also fire reactions.

20. **A second narrative voice** — the weekly narrative is currently a single monologue. Adding a "what someone watching me this week might say" prompt as a second perspective would catch the things she's blind to in herself.

---

## 14. The Cognitive Picture — One Diagram

```
  ┌────────────────────────┐    ┌──────────────────────────┐
  │   User message (WS)    │    │  Autonomous tick (60s)   │
  └───────────┬────────────┘    └─────────────┬────────────┘
              │                                │
              │ persist (low-salience          │ gather candidates from
              │  episodic memory)              │  pressure / goal / interest /
              ▼                                │  routine / curiosity / share
  ┌────────────────────────┐                   │
  │ build_dynamic_suffix   │                   │ score = pressure × opp × recency
  │ ┌────────────────────┐ │                   │       × time × headroom × affect
  │ │ affect (4-dim)     │ │                   │
  │ │ top-5 memories     │ │                   ▼
  │ │ audit feed         │ │       ┌─────────────────────┐
  │ │ relationship label │ │       │  best > threshold?  │
  │ │ char addendum      │ │       └─────────┬───────────┘
  │ │ person context     │ │                 │ yes
  │ │ self-narr belief   │ │                 ▼
  │ │ world beliefs      │ │       ┌─────────────────────┐
  │ │ unprocessed mems   │ │       │     action.gate     │
  │ │ felt-state phrase  │ │       │ leash → budget →    │
  │ └────────────────────┘ │       │ deliberate → auth   │
  └───────────┬────────────┘       └─────────┬───────────┘
              │                              │
              │ system_prompt = prefix       │ execute via tool
              │  + dynamic_suffix             │ or send confirm ticket
              ▼                              │
  ┌────────────────────────┐                 │
  │  Gemini Flash + tools  │◀────────────────┤
  │  (≤4 hops, registry-   │  tool calls     │
  │   declared verbs)      │                 │
  └───────────┬────────────┘                 ▼
              │                  ┌────────────────────────┐
              │ reply            │  every executed action │
              ▼                  │  becomes a memory      │
  ┌────────────────────────┐    └─────────────┬───────────┘
  │ persist reply memory   │                  │
  └───────────┬────────────┘                  │
              │                                │
              │ background:                    │
              │  Flash extract_mentions ──► social_graph
              │                          ──► aesthetic_reactions
              │                          ──► mark_unprocessed
              │                                │
              ▼                                ▼
       ┌────────────────────────────────────────────────┐
       │      SQLite (memories) + ChromaDB              │
       │  + inner_wants/fears/tensions                  │
       │  + interest_garden + world_beliefs             │
       │  + identity_traits + persons + addenda         │
       └──────────────┬─────────────────────────────────┘
                      │
        ┌─────────────┼──────────────┬──────────────────┐
        ▼             ▼              ▼                  ▼
  ┌──────────┐ ┌─────────────┐ ┌──────────────┐ ┌──────────────┐
  │ pressure │ │ reflect 2h  │ │   nightly    │ │   weekly     │
  │ loop     │ │  (Flash)    │ │ (consolidate │ │ (procedural+ │
  │ (10min)  │ │             │ │ + decay +    │ │  traits +    │
  │          │ │ new wants,  │ │ interests +  │ │  self-model  │
  │ decay +  │ │ tensions,   │ │ unprocessed  │ │  pro + Opus  │
  │ escalate │ │ interests,  │ │ review +     │ │  weaver →    │
  │          │ │ goals,      │ │ aesthetic    │ │  narrative + │
  │          │ │ beliefs,    │ │ patterns +   │ │  addendum +  │
  │          │ │ trait       │ │ overnight    │ │  interest    │
  │          │ │ evidence    │ │ synthesis)   │ │  promotions) │
  └──────────┘ └─────────────┘ └──────────────┘ └──────────────┘
```

The whole system is one closed loop: **state shapes context, context shapes behavior, behavior generates memories, reflection updates state.** The reactive path is just one tick of this loop; the background loops are the rest.

---

## 15. Bottom Line

Chloe 2.0 is an unusually well-structured agent. The core insight — **separate the fast LLM call from the slow identity work, and let the slow work feed back into the next fast call's context** — is the right architecture for a long-running character.

The things that need to happen next aren't redesigns. They're: (1) actually run bootstrap-identity with an API key so the dormant 30% of the system wakes up, (2) verify the live extraction paths in production, (3) tighten a handful of brittle spots (lexical contradictions, no-streaming, reflect-without-signal). Beyond that, the architecture is in good shape to keep growing.

What makes her *her* — what stops her from being a generic Gemini chatbot in a different jacket — is the developmental-stage discipline: nothing skips, everything is earned, and "I don't know" is the default state. Most agent systems try to make their personas seem complete on day one. This one explicitly does not. That choice is what gives the system room to actually become someone over time.
