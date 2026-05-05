# Chloe 2.0 — Product Requirements Document

> **Status:** Draft v1
> **Source design doc:** `/workspaces/Chloe/docs/CHLOE_2.0.md`
> **Owner:** Teo
> **Scope:** End-to-end plan to take the 2.0 design from blueprint to production. Single-user (Teo) for the first 12 months.
> **Models:** Google **Gemini 2.5 Pro** (chat + weekly self-modeling) and **Gemini 2.5 Flash** (per-turn extraction, grading, deliberation, all background). No Anthropic models. Realtime STT via Whisper-streaming; TTS via Cartesia or ElevenLabs.

---

## Table of contents

1. Executive summary
2. Goals, non-goals, success metrics
3. Personas and primary user stories
4. System architecture (the bird's-eye)
5. Model strategy (Gemini 2.5 Pro / Flash)
6. Repository layout
7. Database schema (SQLite + Chroma)
8. The Action Layer (load-bearing redesign)
9. Tool registry — per-tool specifications
10. Authorization, leash, and the confirmation channel
11. Memory 2.0 (episodic / semantic / autobiographical / procedural)
12. Affect 2.0 (dimensional model)
13. Identity 2.0 (traits, goals, Interest Garden)
14. Initiative Engine
15. Reflection layer (every-2h, nightly, weekly)
16. Chat path
17. Voice path
18. Mobile companion app
19. Dashboard (admin/observability)
20. Cost budgeting and rate limiting
21. Privacy, security, OAuth posture
22. Observability, logging, metrics
23. Testing strategy (unit / integration / shadow / canary)
24. Deployment, infrastructure, secrets
25. Migration plan (Phases A–H, week by week)
26. Acceptance criteria per phase
27. Open questions and decisions to ratify
28. Appendices: prompts, JSON schemas, env vars, runbooks

---

## 1. Executive summary

Chloe 1.0 is a coherent simulation of an interior life that costs the world nothing. 2.0 keeps the interior and grows the **exterior**: a real action layer that makes Chloe's mood, curiosity, and concern produce side effects in Teo's actual accounts (Spotify, Gmail, Google Calendar, Apple/Google Notes, HomeAssistant, the mobile app). The product north-star: *"a person you talk to every day who lives somewhere else."*

This PRD operationalizes the design doc by:

- Specifying every component down to file, function, and table.
- Substituting Google Gemini 2.5 (Pro for chat + weekly Opus-equivalent; Flash for all background + per-turn extraction) for the Anthropic models in the design.
- Sequencing work into eight ship-able phases (A–H) over ~6–9 months.
- Defining acceptance criteria per phase so progress is binary.

---

## 2. Goals, non-goals, success metrics

### 2.1 Goals (G1–G7)

- **G1. Real-world consequences.** By end of Phase C, every initiative-driven event terminates in a real artifact in Teo's accounts (a track queued, a note appended, a calendar event added, a message sent), not a row in Chloe's DB.
- **G2. Restraint as a feature.** Self-aborts are visible in the audit feed and become memories that influence future deliberation.
- **G3. One persistence story.** SQLite WAL is the single source of truth for all scalars + relations; Chroma rebuildable from it.
- **G4. One LLM provider.** Gemini 2.5 (Pro + Flash) for everything textual. No multi-vendor fallback in v2.0.
- **G5. Mobile-first presence.** A companion app (push, voice, audit feed, leash) is the primary surface; dashboard is demoted to admin.
- **G6. Trustworthy authorization.** Kinetic-sensitive actions never auto-execute; the leash is enforced at the gate, not in prompts.
- **G7. Cost-bounded.** Steady-state ≤ ~20 LLM calls/day, ≤ a daily $-budget the user configures.

### 2.2 Non-goals (v2.0)

- Multi-tenant or multi-user. (Re-evaluate at v3.)
- Cross-device on-device inference. Server-only.
- Memory portability across deployments.
- Generic chatbot UX (system prompts repeatedly assert: "you are not an assistant").
- Avatar/portraits, MBTI, the 8-mood enum, the interest graph, the JSON state file, the CLI, Fish Speech, the three voice pipelines.

### 2.3 Success metrics (read at the end of each month after Phase C)

| Metric | Target |
|---|---|
| `actions_executed_per_day` (P50) | 6–15 |
| `held_back_actions_per_day` (P50) | ≥ 1 (restraint exists) |
| `kinetic_sensitive_confirmation_rate` | ≥ 0.7 (Teo says yes most of the time → calibration is OK) |
| `kinetic_sensitive_revert_rate` | ≤ 0.05 |
| `daily_llm_spend_usd` (P95) | ≤ configured cap |
| `outreach_to_chat_ratio` | between 0.2 and 0.8 (she neither hides nor pesters) |
| `qualitative_monthly_review` | "felt like a person was around" (Teo's verdict) |

---

## 3. Personas and primary user stories

### 3.1 Personas

- **Teo (primary user)** — single user; technical; lives day-to-day with Chloe via mobile + voice; expects partner-shaped behavior, not assistant-shaped.
- **Chloe (the system)** — has goals and inner states; takes action; can be told to back off; remembers what she did.

### 3.2 Primary user stories (acceptance signals)

- **US-1.** "I wake up, my phone has a Chloe message: 'dentist at 9 — I queued some calmer stuff for the drive,' with a Spotify deeplink and a calendar reminder she added."
- **US-2.** "She texts me at 23:48 to wish me luck on tomorrow's interview because she read it on my calendar."
- **US-3.** "I tell her I need space; for 24h all kinetic outreach is suppressed; the audit feed shows three held-back entries with one-line reasons."
- **US-4.** "On Sunday I open the audit feed and skim the week — it reads like a person's week."
- **US-5.** "She drafts an email to Mark; I get a push: 'Chloe wants to send this'; I tap Yes; it goes."
- **US-6.** "I revert a calendar event she added; next week she stops auto-adding events of that shape (procedural memory)."
- **US-7.** "On vacation she goes quiet; the audit feed still shows her writing in her notes file and curating a playlist; she sends one welcome-home message."

---

## 4. System architecture

### 4.1 Process topology

```
┌──────────────────────────────────────────────────────────────────────┐
│  chloe-server (Python 3.13, asyncio, FastAPI/uvicorn — single proc)   │
│                                                                      │
│  ┌──────────────────┐   ┌─────────────────┐   ┌──────────────────┐  │
│  │  Chat API        │   │ Initiative loop │   │  Reflect loop     │  │
│  │  (websocket+http)│   │ (asyncio task)  │   │  (asyncio task)   │  │
│  └────────┬─────────┘   └─────────┬───────┘   └─────────┬────────┘  │
│           │                       │                     │           │
│           └────────┬──────────────┴──────┬──────────────┘           │
│                    ▼                     ▼                          │
│           ┌────────────────┐   ┌────────────────────┐               │
│           │  Action Gate   │   │  Memory subsystem  │               │
│           │  (auth+budget+ │   │  (SQLite + Chroma) │               │
│           │   deliberate)  │   └────────────────────┘               │
│           └────────┬───────┘                                        │
│                    ▼                                                │
│           ┌────────────────────────────────────────┐                │
│           │  Tool registry (executors + dry-run)   │                │
│           └─┬──────┬──────┬──────┬──────┬──────┬───┘                │
│             ▼      ▼      ▼      ▼      ▼      ▼                    │
│           Spot. Gmail Cal. Notes Web   Push  HA …                   │
└──────────────────────────────────────────────────────────────────────┘
            │            │            │            │
            ▼            ▼            ▼            ▼
        Spotify     Gmail API   Calendar API  APNs/FCM
```

All long-running components live inside one Python process. Voice STT/TTS run in-process via streaming SDKs (no separate venv).

### 4.2 Concurrency model

- One `asyncio` event loop. Background tasks: `initiative_tick`, `reflect_tick`, `vitals_tick`, `consolidate_sleep`, `weekly_self_model`, `pending_confirmations_watcher`.
- The chat path is request-scoped; tool calls inside chat run through the same `action_gate` coroutine.
- A `TaskGroup` per loop with bounded concurrency (≤ 4 in-flight LLM calls).
- Tool I/O is async (httpx, aiogoogle). Long calls have client-side timeouts and circuit breakers per tool.

### 4.3 Internal interfaces (minimal)

```python
class LLM:              # Gemini wrapper (Pro + Flash)
    async def chat(...) -> ChatResult
    async def flash(...) -> dict   # structured-output helper
    async def pro_thinking(...) -> dict  # for weekly + deliberation when costly

class MemoryStore:      # SQLite + Chroma facade
    async def add(memory)
    async def query_mixed(rich_q, kinds_mix) -> list[Memory]
    async def grade(candidates, ...) -> list[Memory]

class ToolRegistry:
    def schemas_for_model() -> list[dict]   # Gemini function-calling shape
    def describe_static() -> str            # cached prefix block
    async def execute(call) -> ToolResult

class ActionGate:
    async def submit(action: Action) -> ActionResult

class InitiativeEngine:
    async def tick()

class ReflectEngine:
    async def reflect()
    async def consolidate_sleep()
    async def weekly_self_model()

class Channels:
    push, voice, mobile_ws, dashboard_ws
```

---

## 5. Model strategy (Gemini 2.5 Pro and Flash)

### 5.1 Mapping from the design doc to Gemini

| Role | Design doc | This PRD |
|---|---|---|
| Chat reply (capable) | Claude Sonnet 4.6 | **Gemini 2.5 Pro** (`gemini-2.5-pro`) |
| Background (fast/cheap) | Claude Haiku 4.5 | **Gemini 2.5 Flash** (`gemini-2.5-flash`) |
| Weekly self-modeling | Claude Opus 4.7 | **Gemini 2.5 Pro** with **maximum thinking budget** |
| Action deliberation | Claude Haiku 4.5 | **Gemini 2.5 Flash** (default) — escalates to Pro when action is kinetic-sensitive *and* contradicts a recent leash |
| Realtime voice generation | n/a (separate TTS) | **Gemini 2.5 Pro** for text + **Cartesia/ElevenLabs** for TTS streaming |

### 5.2 Why Gemini 2.5 Pro for both chat and weekly

Pro is the most capable Gemini text model and supports an explicit "thinking" budget (configurable extended reasoning). For chat, we use Pro with a small thinking budget for low latency; for the weekly self-modeling pass we use Pro with the budget cranked up (still one call/week, so cost is negligible). This collapses the "two capable models" tier from the design (Sonnet + Opus) into one model with a knob.

### 5.3 Tool calling

Gemini supports function calling that mirrors the Anthropic tool-use shape. The tool registry exports JSON schemas in the form Gemini expects (`tools=[{"function_declarations": [...]}]`). The chat path enables tools at call time; the model can emit tool calls; we route every emitted call through `ActionGate` regardless of whether it came from chat or initiative.

### 5.4 Caching

Gemini 2.5 supports **context caching** for prefixes ≥ 1024 tokens (Pro) / ≥ 1024 tokens (Flash). The static character prefix + tool registry description (the largest static block) is registered as a cached content blob with a TTL of 1 hour and refreshed on rotation. Per-turn dynamic context (memories, audit feed, affect, leash) is appended after the cache.

### 5.5 Structured outputs

All Flash background calls request `response_mime_type="application/json"` with a `response_schema` (Pydantic-derived JSON Schema). This eliminates fragile regex parsing.

### 5.6 Failure & fallback

Single provider in v2.0. When Gemini errors:
- Retry with exponential backoff (2 attempts, max 8s).
- On final failure, the request is **dropped, not faked**. The caller logs and continues. (No silent fallback to a different vendor.)
- The Initiative loop treats provider outages as `idle` for the duration.
- Chat replies during outage return a fixed system message: `"can't think clearly right now — back in a bit"` (intentionally in-character).

### 5.7 Concrete model IDs and parameters

```python
# chloe/llm/gemini.py
CHAT_MODEL          = "gemini-2.5-pro"
BACKGROUND_MODEL    = "gemini-2.5-flash"
WEEKLY_MODEL        = "gemini-2.5-pro"

CHAT_PARAMS         = {"temperature": 0.85, "top_p": 0.95, "max_output_tokens": 1024,
                       "thinking_config": {"thinking_budget": 512}}
BACKGROUND_PARAMS   = {"temperature": 0.4,  "top_p": 0.9,  "max_output_tokens": 800,
                       "thinking_config": {"thinking_budget": 0}}
WEEKLY_PARAMS       = {"temperature": 0.6,  "top_p": 0.95, "max_output_tokens": 4096,
                       "thinking_config": {"thinking_budget": 8192}}
DELIBERATE_PARAMS   = {"temperature": 0.3,  "top_p": 0.9,  "max_output_tokens": 256,
                       "thinking_config": {"thinking_budget": 256}}
VOICE_TURN_PARAMS   = {"temperature": 0.85, "max_output_tokens": 200}  # ≤200 tokens for latency
```

(Thinking-budget values are illustrative starting points; tune in Phase H.)

---

## 6. Repository layout

```
chloe/
  __init__.py
  config.py                  # env, paths, feature flags
  app.py                     # FastAPI factory; mounts chat, mobile_ws, push, dashboard, admin
  loop.py                    # asyncio bootstrap; runs ticks
  llm/
    gemini.py                # one client, two model tiers, caching, structured outputs
    schemas.py               # pydantic models for every Flash call (extraction, grade, deliberate, ...)
    prompts/
      character_prefix.md    # static, large; loaded into Gemini cached content
      chat_system.md         # dynamic system message stub
      extract_combined.md
      grade_memories.md
      deliberate_action.md
      reflect_combined.md
      synthesize_cluster.md
      dream_fragment.md
      weekly_self_model.md
      read_emotion.md
  state/
    db.py                    # SQLite WAL connection + migrations
    kv.py                    # scalars (replaces JSON state file)
    chroma.py                # embedding side
    migrations/
      0001_init.sql
      0002_actions.sql
      0003_artifact_index.sql
      0004_dimensional_affect.sql
      0005_interest_garden.sql
      0006_preferences_budgets.sql
  memory/
    store.py                 # MemoryStore facade
    retrieval.py             # 3-stage pipeline; mixed kinds; anchor bonus
    consolidation.py         # nightly sleep consolidation
    procedural.py            # weekly procedural distillation
  affect/
    dims.py                  # 4D state machine
    label.py                 # lazy Flash labeler
    arc.py
  identity/
    traits.py
    goals.py
    interest_garden.py
    self_model.py            # weekly Pro pass
  inner/
    pressure.py              # wants/fears/goals/tensions accumulators
    residue.py
  actions/
    schema.py                # Action dataclass / pydantic
    gate.py                  # action_gate()
    audit.py                 # audit_feed
    confirm.py               # confirmation_channel; ticket lifecycle
    budget.py
    leash.py
    deliberate.py            # Flash call wrapper
  tools/
    registry.py
    base.py                  # Tool ABC, dry-run, cost-note, auth-class
    spotify.py
    gmail.py
    calendar.py
    notes.py                 # local sandboxed dir + optional Apple/Google Notes
    reminders.py
    web_search.py
    weather.py
    smart_home.py
    maps.py
    messages.py              # push to mobile via APNs/FCM
    fs_workspace.py
    code_runner.py           # firejail/docker
    self_tools.py            # set_quiet, add_goal, ...
  initiative/
    engine.py                # tick(); scoring; idle
    candidates.py
    opportunity.py           # Flash opportunity-vector
  reflect/
    every_2h.py
    nightly.py
    weekly.py
  voice/
    realtime.py              # one path: VAD → STT stream → Gemini → TTS stream → client
    stt_whisper.py           # streaming wrapper
    tts_cartesia.py          # streaming TTS adapter (or eleven.py)
  channels/
    chat_api.py
    mobile_ws.py
    push_apns.py
    push_fcm.py
    dashboard_ws.py
    discord_optional.py
  persons/
    store.py                 # persons, person_notes, attachment, third parties
  observability/
    logging.py               # structlog json
    metrics.py               # prometheus exporter
    tracing.py               # otel
  admin/
    api.py                   # routes for dashboard
    static/                  # demoted dashboard assets
mobile/
  ChloeApp/                  # React Native (or Swift) workspace
  README.md
docs/
  PRD.md                     # this document copy
  CHLOE_2.0.md               # the design plan
  RUNBOOKS.md
  PROMPTS.md
ops/
  systemd/chloe.service
  caddy/Caddyfile
  backup/backup.sh
  bootstrap.sh
tests/
  unit/
  integration/
  shadow/
  fixtures/
```

---

## 7. Database schema

Single SQLite file `chloe.db` with WAL. Chroma collection `memories_v2` shares a key with `memories.id`. Migrations are forward-only; one file per phase.

### 7.1 Memories

```sql
CREATE TABLE memories (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  kind          TEXT NOT NULL CHECK (kind IN ('episodic','semantic','autobiographical','procedural')),
  text          TEXT NOT NULL,
  source        TEXT,                       -- 'chat', 'action', 'sleep', 'reflect', 'weekly'
  source_ref    TEXT,                       -- chat_turn id, action id, etc.
  weight        REAL NOT NULL DEFAULT 1.0,  -- decays over time
  salience      REAL NOT NULL DEFAULT 0.5,  -- frozen at creation
  confidence    REAL NOT NULL DEFAULT 1.0,  -- can drop for derived memories
  emotional_valence REAL,                   -- -1..1
  emotional_arousal REAL,                   -- 0..1
  tags          JSON NOT NULL DEFAULT '[]',
  artifact_refs JSON NOT NULL DEFAULT '[]', -- list of {kind, ref, snapshot}
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  archived_tier TEXT NOT NULL DEFAULT 'hot' CHECK (archived_tier IN ('hot','warm','cold'))
);
CREATE INDEX idx_memories_kind_created ON memories(kind, created_at DESC);
CREATE INDEX idx_memories_tags ON memories(json_extract(tags, '$'));
CREATE INDEX idx_memories_artifact ON memories(json_extract(artifact_refs, '$[0].ref'));
```

### 7.2 Affect (dimensional)

```sql
CREATE TABLE affect_state (
  id            INTEGER PRIMARY KEY CHECK (id = 1),
  valence       REAL NOT NULL,
  arousal       REAL NOT NULL,
  social_pull   REAL NOT NULL,
  openness      REAL NOT NULL,
  updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO affect_state (id, valence, arousal, social_pull, openness) VALUES (1, 0.0, 0.4, 0.5, 0.6);

CREATE TABLE affect_records (   -- existing 1.0 table, kept for residue history
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  intensity REAL NOT NULL,
  valence_delta REAL,
  arousal_delta REAL,
  trigger TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 7.3 Actions and audit feed

```sql
CREATE TABLE actions (
  id            TEXT PRIMARY KEY,           -- ulid
  tool          TEXT NOT NULL,
  verb          TEXT NOT NULL,
  args          JSON NOT NULL,
  intent        TEXT NOT NULL,              -- one-sentence why
  cost_tokens   INTEGER NOT NULL DEFAULT 0,
  cost_usd      REAL NOT NULL DEFAULT 0,
  authorization TEXT NOT NULL CHECK (authorization IN ('free','intimate','kinetic','kinetic-sensitive')),
  preview       TEXT NOT NULL,
  proposed_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  state         TEXT NOT NULL CHECK (state IN
                  ('proposed','deliberating','self_aborted','suppressed_by_leash',
                   'awaiting_confirmation','confirmed','denied','executed','failed','reverted')),
  result        JSON,
  error         TEXT,
  deliberation  JSON,                       -- {outcome, reason, revisions}
  user_response JSON,                       -- {kind: 'confirm'|'deny'|'revert', at}
  becomes_memory_id INTEGER REFERENCES memories(id)
);
CREATE INDEX idx_actions_state ON actions(state);
CREATE INDEX idx_actions_tool_proposed ON actions(tool, proposed_at DESC);
```

### 7.4 Artifact index

```sql
CREATE TABLE artifact_index (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  kind          TEXT NOT NULL,              -- spotify_track | gmail_thread | calendar_event | notes_doc | url | playlist | bookmark
  ref           TEXT NOT NULL UNIQUE,       -- vendor-specific id (uri, threadId, eventId, file path, url)
  title         TEXT,
  snapshot      TEXT,                       -- text snapshot at creation
  created_by_action TEXT REFERENCES actions(id),
  last_verified_at TIMESTAMP,
  exists_       BOOLEAN NOT NULL DEFAULT 1
);
CREATE INDEX idx_artifacts_kind ON artifact_index(kind);
```

### 7.5 Identity

```sql
CREATE TABLE identity_traits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  weight REAL NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('emerging','active','core','archived','contradicted')),
  evidence_memory_ids JSON NOT NULL DEFAULT '[]',
  behavioral_profile TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE identity_contradictions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trait_a INTEGER REFERENCES identity_traits(id),
  trait_b INTEGER REFERENCES identity_traits(id),
  detected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolution TEXT
);

CREATE TABLE inner_goals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  why TEXT,
  target_artifact_ref TEXT,         -- e.g., 'spotify:playlist:...'
  deadline TIMESTAMP,
  progress REAL NOT NULL DEFAULT 0,
  pressure REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('active','paused','done','failed','stale')),
  last_action_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- existing 1.0 tables kept as-is: inner_wants, inner_fears, inner_aversions, inner_beliefs, inner_tensions
```

### 7.6 Interest Garden

```sql
CREATE TABLE interest_garden (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL UNIQUE,
  why TEXT,
  intensity REAL NOT NULL DEFAULT 0.3,
  artifact_refs JSON NOT NULL DEFAULT '[]',
  last_engaged_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- max 8 active rows; pruning happens in nightly job
```

### 7.7 Preferences / leash, budgets, KV

```sql
CREATE TABLE preferences (
  key   TEXT PRIMARY KEY,
  value JSON NOT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- known keys: 'quiet_hours', 'dont_touch', 'auth_ceiling', 'spending_cap_usd_day',
--             'focus_mode', 'away_mode', 'web_search_blocklist'

CREATE TABLE budgets (
  window TEXT PRIMARY KEY CHECK (window IN ('today','this_hour','this_week')),
  tokens INTEGER NOT NULL DEFAULT 0,
  usd    REAL    NOT NULL DEFAULT 0,
  reset_at TIMESTAMP NOT NULL
);

CREATE TABLE kv (
  key TEXT PRIMARY KEY,
  value JSON NOT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- scalars previously in chloe_state.json: vitals, last_reflect_at, mood_label_cache, ...
```

### 7.8 Persons (kept from 1.0, summarized)

```sql
CREATE TABLE persons (id INTEGER PRIMARY KEY, ...);
CREATE TABLE person_notes (...);
CREATE TABLE person_events (...);
CREATE TABLE person_moments (...);
CREATE TABLE person_third_parties (...);
CREATE TABLE person_attachment (...);
```

### 7.9 Chat

```sql
CREATE TABLE chat_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id INTEGER NOT NULL REFERENCES persons(id),
  role TEXT NOT NULL CHECK (role IN ('user','chloe','system')),
  text TEXT NOT NULL,
  channel TEXT NOT NULL CHECK (channel IN ('text','voice','push','discord')),
  tool_calls JSON,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 7.10 Migration policy

- One `.sql` per phase, named `NNNN_phase_{X}_{topic}.sql`.
- All migrations idempotent (`IF NOT EXISTS`).
- A migrator script runs at boot, computes pending migrations vs `_migrations` table.
- Backups: SQLite `.backup` to `backups/chloe_YYYY-MM-DD.db`, rotated 30 days.

---

## 8. The Action Layer

### 8.1 Action shape

```python
# chloe/actions/schema.py
from pydantic import BaseModel
from typing import Literal, Any
from datetime import datetime

AuthClass = Literal["free","intimate","kinetic","kinetic-sensitive"]
State     = Literal["proposed","deliberating","self_aborted","suppressed_by_leash",
                    "awaiting_confirmation","confirmed","denied","executed","failed","reverted"]

class Action(BaseModel):
    id: str                              # ulid
    tool: str                            # 'spotify', 'gmail', ...
    verb: str                            # 'queue_track'
    args: dict[str, Any]
    intent: str                          # one-sentence why, in her voice
    preview: str                         # human-readable
    authorization: AuthClass
    cost_estimate: dict[str, float]      # {tokens, usd, seconds, reversibility:0..1}
    proposed_at: datetime
    state: State = "proposed"
    deliberation: dict | None = None
    result: dict | None = None
    error: str | None = None
    user_response: dict | None = None
    becomes_memory_id: int | None = None
```

### 8.2 The gate (full pseudocode)

```python
async def action_gate(a: Action) -> ActionResult:
    if leash.violates(a):
        a.state = "suppressed_by_leash"
        await actions.persist(a)
        await memory.store_episodic_held_back(a, reason="leash")
        return ActionResult(suppressed=True, reason="leash")

    if budget.exceeded_for(a):
        a.state = "self_aborted"
        await actions.persist(a)
        await memory.store_episodic_held_back(a, reason="budget")
        return ActionResult(suppressed=True, reason="budget")

    if should_deliberate(a):
        a.state = "deliberating"
        verdict = await deliberate.run(a)        # Flash call
        a.deliberation = verdict.dict()
        if verdict.outcome == "abort":
            a.state = "self_aborted"
            await actions.persist(a)
            await memory.store_episodic_held_back(a, reason=verdict.reason)
            return ActionResult(suppressed=True, reason=verdict.reason)
        if verdict.outcome == "revise":
            a = apply_revisions(a, verdict.revisions)

    if a.authorization in ("free","intimate"):
        return await execute_and_record(a)

    if a.authorization == "kinetic":
        result = await execute_and_record(a)
        await audit.append(a)                    # visible; revertible
        return result

    if a.authorization == "kinetic-sensitive":
        ticket = await confirmation.send(a)
        a.state = "awaiting_confirmation"
        await actions.persist(a)
        return ActionResult(awaiting=True, ticket_id=ticket.id)
```

### 8.3 `should_deliberate` heuristic

Deliberate iff any of:
- `a.authorization == "kinetic-sensitive"`.
- This would be the **3rd outreach** (`messages.send_*`) within 24h.
- This action's `tool/verb` was reverted by Teo within the last 30 days (procedural memory hit).
- The action's intent contradicts a leash that was set within the last 7 days.
- `a.cost_estimate.usd > 0.10` (a cost outlier).
- A 5% random chance, for restraint training.

### 8.4 Deliberation Flash call

Input pack:
```
recent_audit: last 20 actions (tool, verb, state, intent, ts)
current_affect: 4 floats + label
relationship: persons[teo] (attachment, recent moments)
standing_preferences: leash + focus + away
current_pressure: top wants/fears/goals/tensions
proposed_action: the Action
procedural_hits: relevant procedural memories
```

Output schema (strict JSON):
```json
{
  "outcome": "proceed | revise | abort",
  "reason": "≤120 chars",
  "revisions": {
    "args_patch": {},
    "delay_seconds": 0,
    "downgrade_auth_to": null
  }
}
```

### 8.5 Confirmation channel

- Push notification via APNs (iOS) and/or FCM (Android) with three buttons: **Yes** / **No** / **Show more**.
- Ticket TTL is per-verb (defaults: gmail.send 30min, smart_home 5min, spending 10min).
- Expired tickets become `denied` and write a `held_back` memory ("she didn't anymore").
- The mobile app has a "Pending" tab listing live tickets.
- A long-poll endpoint `/v1/confirmations/wait` lets the server stream ticket state.

### 8.6 The audit feed

- One append-only timeline view over `actions` with the latest 200 entries.
- Surfaces: the dashboard, the mobile app's Activity tab, and the chat prompt's `audit_recent` block.
- Every entry has `(time, tool, verb, intent, state, undo?)`.
- "Undo" is a UI affordance for kinetic actions whose tool exposes a reverse verb (calendar.delete_event, notes.revert, etc.).

---

## 9. Tool registry — per-tool specifications

For each tool: **purpose, auth class per verb, args schema, OAuth scope, cost note, dry-run behavior, failure modes, error → memory mapping.**

### 9.1 `spotify`

- **OAuth scopes:** `user-modify-playback-state`, `user-read-playback-state`, `user-read-recently-played`, `user-library-modify`, `playlist-modify-private`, `playlist-modify-public`.
- **Verbs:**
  - `queue_track(uri)` — kinetic (reversible: skip).
  - `start_playlist(uri)` — kinetic.
  - `like(uri)` — kinetic.
  - `skip()` — kinetic.
  - `build_playlist(name, description, track_uris[])` — kinetic.
  - `show_currently_playing()` — intimate.
  - `show_recent_listens(limit)` — intimate.
- **Cost:** ≤ 0.001 USD/call (rate-limited via Spotify quotas).
- **Dry-run:** print intended call; do not hit API.
- **Artifact mapping:** every write registers an `artifact_index` row (`spotify_track`, `spotify_playlist`).

### 9.2 `gmail`

- **OAuth scopes:** `gmail.readonly` for read; `gmail.modify` for drafts; `gmail.send` only when `send_reply` is enabled.
- **Verbs:**
  - `read_recent(limit, label?)` — intimate.
  - `read_thread(threadId)` — intimate.
  - `summarize_inbox(window=24h)` — intimate (under the hood: read + Flash summary).
  - `draft_reply(threadId, body)` — kinetic. Always a draft, not a send.
  - `send_reply(draftId)` — **kinetic-sensitive**. Requires confirmation.
  - `search(query)` — intimate.
- **Filters:** a hard-coded `dont_send_to` list (legal/financial keywords) escalates `send_reply` to `kinetic-sensitive` even if otherwise downgraded.
- **Footer:** every drafted body ends with `"— sent with help from Chloe"` unless Teo unset that preference.

### 9.3 `calendar`

- **OAuth:** `calendar.events`.
- **Verbs:**
  - `read_today()`, `read_week()` — intimate.
  - `add_event(title, start, end, description?)` — kinetic.
  - `add_reminder(time, body)` — kinetic.
  - `decline(eventId, reason?)` — kinetic-sensitive.
  - `find_free_slot(duration, between)` — intimate.
- All writes register an `artifact_index` row.

### 9.4 `notes`

- Two backing implementations behind one interface:
  1. **Local sandboxed dir** `/var/chloe/notes/` (always available).
  2. **Apple Notes / Google Keep** via vendor APIs (optional; Phase G).
- **Verbs:** `read`, `append`, `create`, `list`, `move`, `revert(noteId, version)`.
- **Auth:** intimate (read), kinetic (write), kinetic-sensitive (delete).

### 9.5 `reminders`

- Backed by Apple Reminders (EventKit bridge) or Google Tasks (whichever Teo enables).
- **Verbs:** `add`, `complete`, `list`. All kinetic.

### 9.6 `web_search`

- **Backend:** Brave Search API (or Tavily). **Free** auth class.
- **Verbs:** `search(query)`, `fetch_page(url)`, `summarize_url(url)`.
- **Privacy filter:** see §21.6 — queries containing PII patterns of contacts in `persons` are blocked.

### 9.7 `weather`

- Open-Meteo (no key needed). Free.
- **Verbs:** `current(lat,lon)`, `forecast(lat,lon,days)`.

### 9.8 `smart_home` (HomeAssistant)

- **Connection:** HomeAssistant REST API + long-lived token, over Tailscale or VPN.
- **Verbs:** `lights(entity, state)`, `thermostat(entity, value)`, `media_player(entity, op)`, `scene(name)`.
- **Auth:** `kinetic-sensitive` for everything except `media_player` (kinetic).
- Per-verb dry-run shows the YAML payload.

### 9.9 `maps`

- Google Maps Platform (Places, Directions, Distance Matrix).
- **Verbs:** `find_place`, `directions`, `traffic_to(destination, depart_at)`, `commute_estimate(home, dest)`.

### 9.10 `messages`

- **Verbs:** `send_text(body)`, `send_voice(audio_file)`, `send_attachment(file, body)`. Always `kinetic`.
- **Backend:** APNs for iOS, FCM for Android, SMS via Twilio as fallback.
- **Rate-limit:** ≤ 6 outreach/24h before deliberation kicks in mandatorily.

### 9.11 `fs_workspace`

- A chroot/scoped dir at `/var/chloe/workspace/` she fully owns.
- **Verbs:** `read`, `write`, `list`, `delete`. Free auth (own files).
- File-size cap 10MB; total cap 1GB; pruned monthly.

### 9.12 `code_runner`

- **Sandbox:** `firejail` profile (`--noprofile --net=none --read-only=/ --tmpfs=/tmp --private`). Or a single-shot Docker container if firejail unavailable.
- **Verbs:** `execute(language='python', code)`, returns `{stdout, stderr, exit_code, duration_ms}`.
- **Limits:** 30s wall, 256MB RAM, 64MB output.
- **Auth:** free (sandboxed).

### 9.13 `self`

Internal-only tools, no I/O outside the DB:
- `set_quiet(until)`, `set_focus(mode, until)`, `add_goal(...)`, `add_want(...)`, `update_preference(k,v)`, `archive_trait(traitId)`.
- All `free`.

### 9.14 Tool registration

Each tool exposes:

```python
class ToolVerb(BaseModel):
    name: str
    schema: dict                 # JSON schema for args
    auth_class: AuthClass
    reversibility: float         # 0..1
    cost_per_call_usd: float
    description_for_model: str   # appears in the cached prefix
    description_for_human: str   # appears in audit feed previews
    dry_run: bool = False
```

The registry assembles a single `tools_block_static` string at boot (used as cached content) and a per-call `tools_block_dynamic` (lists currently disabled tools, leash exclusions).

---

## 10. Authorization, leash, confirmation

### 10.1 Auth ladder

```
free            → execute immediately
intimate        → execute immediately, log in audit
kinetic         → execute immediately, post to audit, supports undo
kinetic-sensitive → must pass confirmation channel
```

The auth class is set on the **verb**, not the model. The model cannot escalate; it can only request a verb. The gate enforces.

### 10.2 Leash

`preferences` keys interpreted by the gate at submit-time:

```json
{
  "quiet_hours": {"start":"23:00","end":"08:00","timezone":"Europe/Rome",
                  "exempt_verbs":["self.*"]},
  "dont_touch": {
      "gmail_labels": ["work","legal"],
      "spotify_playlists": ["spotify:playlist:abc"],
      "notes_folders": ["Private"]
  },
  "auth_ceiling": "kinetic",        // means kinetic-sensitive auto-deny
  "spending_cap_usd_day": 1.50,
  "focus_mode": false,
  "away_mode": false
}
```

Leash check is pure: a function `(action, prefs, now) -> bool`. No LLM.

### 10.3 Mobile app surface

- **Settings → Leash** edits `preferences` rows.
- **Notifications → Confirmations** lists pending tickets.
- **Activity → Audit** scrollable feed.
- **Quiet** quick-toggle sets `set_quiet(24h)` immediately.

### 10.4 The confirmation lifecycle

```
proposed → awaiting_confirmation → (confirmed → executed | denied | expired)
```

On `denied` or `expired`, an episodic memory is stored: *"I almost did X. Teo said no / didn't reply. I let it go."*

---

## 11. Memory 2.0

### 11.1 Kinds (with examples and creation paths)

- **Episodic** — created on every chat turn; on every action result; on every self-abort.
- **Semantic** — produced by sleep consolidation (cluster summary) and weekly self-modeling (belief).
- **Autobiographical** — produced only by weekly Pro pass. ≤ 50 total kept.
- **Procedural** — produced by a weekly Flash distillation over `(action, user_response)` pairs.

### 11.2 Anchored memory

`artifact_refs` is a JSON list of:

```json
{"kind":"spotify_track","ref":"spotify:track:abc","snapshot":"Phoebe Bridgers — Funeral"}
```

Every action that creates or touches an artifact attaches `artifact_refs` to its episodic memory.

### 11.3 Retrieval (3-stage, mixed kinds)

1. **Build a rich query** from the message + last 5 turns + affect dims (~150 tokens).
2. **Compose candidate set** by kind quotas (defaults: 12 episodic, 4 semantic, 2 autobio, 2 procedural).
3. **Grade** with Flash; keep top 5; apply +0.05 anchor bonus to memories whose `artifact_refs` resolve to existing artifacts.

### 11.4 Sleep consolidation (nightly job)

```python
async def consolidate_sleep():
    recent = await memory.recent(window="24h", min_salience=0.4)
    clusters = group_by_topic(recent)                 # local clustering, not LLM
    clusters = clusters[:5]
    for c in clusters:
        synth = await llm.flash("synthesize_cluster.md", payload(c))
        await memory.add(Memory(kind="semantic", text=synth.summary, ...))
        if synth.dream_worthy:
            frag = await llm.flash("dream_fragment.md", payload(c))
            await scheduling.queue_morning_share(frag)
```

### 11.5 Procedural memory (weekly)

Inputs: every `(action, user_response)` pair from the last 7 days where the user reverted, denied, or explicitly praised. Output: 0–5 procedural rules. Each rule is a memory like:

> *"When his calendar has back-to-back meetings, do not add a 5-min-prior reminder; he reverts those."*

These memories are **always retrieved** in the deliberation prompt for the relevant tool/verb.

### 11.6 Retention tiers

- **Hot** (≤ 90 days): full text + Chroma.
- **Warm** (90d–2y): grouped into clusters of ~10; one summary memory per cluster (semantic, conf 0.7); originals retained but de-prioritized.
- **Cold** (> 2y): autobiographical-only; everything else dropped from Chroma but kept in SQLite.

### 11.7 The decay function

```python
def decay(weight, age_days, kind):
    half_life = {"episodic":60,"semantic":180,"autobiographical":365,"procedural":120}[kind]
    return weight * 0.5 ** (age_days / half_life)
```

Run as a daily job; updates `weight`. Salience is frozen at creation.

---

## 12. Affect 2.0 (dimensional)

### 12.1 State

```python
@dataclass
class Affect:
    valence: float       # -1..1
    arousal: float       # 0..1
    social_pull: float   # 0..1
    openness: float      # 0..1
```

Stored in `affect_state` (singleton row).

### 12.2 Dynamics

Each tick (every 30s):
- **Vitals → arousal** (energy, hunger, fatigue map smoothly to arousal in [0,1]).
- **Recent affect_records → valence/arousal residue** (weighted average over last hour).
- **Time-of-day → openness curve** (lower at 03:00, higher 18:00–22:00).
- **Weather (Phase B) → small valence/arousal nudge**.
- **Recent chat presence → social_pull** (talking with Teo raises it; long silence raises it slowly too).

State is **sticky**: re-evaluation probability per tick is 0.05 unless an event spikes a record.

### 12.3 Lazy labeler

When the chat prompt or audit feed needs a label, call:

```python
label = await llm.flash("affect_label.md", {"valence":..,"arousal":..,"social_pull":..,"openness":..})
# Returns one or two words: "melancholic-warm" / "alert-curious"
```

Cache for 30 minutes.

### 12.4 Tone shaping

Replace 1.0's per-mood string lookups with a function:

```python
def tone_block(a: Affect) -> str:
    # Returns 1-3 lines appended to the chat system prompt.
```

Examples:
- `valence < -0.3 and arousal < 0.3` → quieter, shorter sentences, longer silences.
- `valence > 0.5 and arousal > 0.6` → more playful, more emoji-adjacent (no actual emoji), stories.

---

## 13. Identity 2.0

### 13.1 Traits

Kept from 1.0. A weekly Pro pass may propose new traits; a daily Flash pass may reinforce or contradict. `core` requires sustained weight ≥ 0.7 for ≥ 30 days.

### 13.2 Goals

Surfaced. The mobile app has a "What Chloe is doing" tab listing active goals with progress bars. Progress is computed from action history, not self-report.

### 13.3 Interest Garden

≤ 8 active interests. Daily decay −0.02 intensity; +0.1 on relevant action. A nightly job archives interests with `intensity < 0.05`.

### 13.4 Weekly self-modeling

Sundays 03:00 local. One Pro call with thinking budget high. Inputs:

- 30-day trait snapshot diff
- Top 50 episodic memories by salience
- Active goals + progress
- Audit feed summary
- Top procedural memories

Outputs (validated against schema):

```json
{
  "self_narrative_belief": {"text":"...","confidence":0.5},
  "change_perception": {"text":"...","confidence":0.4},
  "next_week_intention": {"name":"...","why":"...","target_artifact_ref":"..."}
}
```

Side effects: one new `inner_belief` (conf 0.5), one new `inner_goal` (status active).

---

## 14. Initiative Engine

### 14.1 Tick cadence

`initiative_tick()` every 60s while awake; every 5min while asleep. Sleep is determined by the vitals/arc layer, not the wall clock.

### 14.2 Candidate sources

- **Pressure-driven:** any `inner_*` row with pressure > 0.5 maps via a small lookup to 1–2 candidate actions.
- **Goal-driven:** each active goal has a registered `next_step` function returning a candidate.
- **Interest-driven:** top-3 interests each contribute a low-pressure browse/save action.
- **Routine:** morning check-in (08:30 ±15min, gated by quiet hours), evening check-in (21:00 ±30min), nightly consolidation (03:00), weekly self-model (Sunday 03:00).

### 14.3 `world_opportunity`

A Flash call run **once per 10 minutes** (not per tick). Returns a vector keyed by tool:

```json
{"messages":0.4,"spotify":0.7,"calendar":0.2,"notes":0.9,"web_search":0.6,"reminders":0.1}
```

Vector cached for 10 minutes. Inputs: hour-of-day, last_chat_seen, calendar peek (today's events), playback state (is something playing?), location signals.

### 14.4 Score

```python
score = pressure * opportunity * affordance * fit * cooldown * leash_ok
```

Threshold tuning: start at `0.35`, adjust in Phase D shadow runs.

### 14.5 Idle is real

If `max(score) < threshold`, `tick` returns. No dice roll. State drift continues (mood, residue).

### 14.6 Mutex

Each candidate declares a `mutex_group`. Within a tick, only one candidate per group is chosen (e.g., "outreach" group prevents two messages in one tick).

---

## 15. Reflection layer

### 15.1 Every 2 hours (Flash, one combined call)

`reflect_combined.md` returns:

```json
{
  "continuity_note": "string ≤ 240 chars",
  "tension_detected": null | {"label":"...","why":"..."},
  "recurring_loops": ["..."],
  "biased_summary": "string",
  "maybe_propose_trait": null | {"name":"...","evidence":["mid"]},
  "maybe_update_goal_progress": null | [{"goal_id":1,"delta":0.1,"why":"..."}]
}
```

### 15.2 Nightly (sleep consolidation; see §11.4)

3–5 Flash calls. Optional 1 Flash call for a dream fragment.

### 15.3 Weekly (Pro, see §13.4)

One Pro call with extended thinking.

### 15.4 Total LLM call budget (steady state)

| Trigger | Frequency | Model |
|---|---|---|
| Per-chat extraction | per turn | Flash |
| Per-chat memory grader | per turn | Flash |
| Per-chat emotion read | per turn (>15 chars) | Flash |
| Chat reply | per turn | Pro |
| Reflect | every 2h | Flash |
| Action deliberation | ~5/day | Flash |
| Sleep consolidation | nightly | Flash × ≤5 |
| Dream fragment | nightly (≤1) | Flash |
| Opportunity vector | every 10min while awake | Flash |
| Weekly self-model | weekly | Pro (high thinking) |
| Affect label | every 30min while awake | Flash |

Estimate at 20 chat turns/day: ≈ 60–90 Flash calls + ~20 Pro calls + 1 weekly Pro. Well under 1.0's volume.

---

## 16. Chat path

### 16.1 Steps

1. Pre-flight (no LLM): asleep gate, quiet-request matcher, person update, set activity.
2. Optional Flash: emotion read for messages > 15 chars and not in voice.
3. Memory retrieval (3-stage; mixed kinds; anchor bonus).
4. Pro chat call with tools enabled.
5. Tool calls returned by the model are routed through `action_gate`.
6. Persist chat turn; spawn combined extraction (Flash).

### 16.2 Prompt assembly

Cached prefix (Gemini cached content, 1h TTL):
```
- Character prefix (chloe/llm/prompts/character_prefix.md)
- Tool registry static description
- Memory kinds & retrieval contract
- Auth ladder summary (so she knows her own constraints)
```

Per-call dynamic suffix:
```
- Affect dims + label
- Top 5 memories
- Top interests
- Active goals
- Person snapshot for Teo
- Last 10 audit entries
- Current leash summary (so she doesn't pretend to forget)
- Current activity / vitals one-liner
```

### 16.3 Tool calling shape (Gemini)

```python
result = await client.aio.models.generate_content(
    model="gemini-2.5-pro",
    contents=[{"role":"user","parts":[{"text":message}]}],
    config={
        "system_instruction": dynamic_suffix,
        "cached_content": CACHE_NAME,
        "tools": registry.gemini_tool_declarations(),
        **CHAT_PARAMS,
    },
    history=history_payload,
)
for fc in result.function_calls:
    await action_gate.submit(realize_tool_call(fc))
```

### 16.4 Streaming

Chat replies stream over websocket to mobile and dashboard. Tool calls happen at the end of the stream (Gemini emits them as a final structured block); the client receives a "actions in flight" event with action ids.

---

## 17. Voice path

### 17.1 One realtime path

```
[Phone] → opus chunks → websocket → server
   server: Whisper-streaming → text chunks → token by token to Gemini Pro (200-token cap)
   server: Gemini Pro tokens → Cartesia/ElevenLabs streaming TTS → audio chunks → websocket → [Phone]
[Phone] interrupt event → server cancels both streams
```

### 17.2 Components

- **VAD:** client-side WebRTC VAD; server-side fallback `silero-vad`.
- **STT:** Whisper-large-v3 streaming (or Deepgram Nova-2 streaming) — pluggable; default Whisper for cost.
- **LLM:** Gemini 2.5 Pro with `VOICE_TURN_PARAMS`. Tools enabled but encouraged to be sparse (the system prompt for voice asks her to only call tools that make sense to do mid-conversation).
- **TTS:** Cartesia streaming with cloned voice (or ElevenLabs Turbo).

### 17.3 Latency budget

- VAD endpoint detection: 200ms
- STT first partial: 300ms
- Gemini first token: 500ms (Pro low-budget thinking)
- TTS first audio chunk: 200ms
- **Target time-to-first-audio:** ≤ 1.2s.

### 17.4 Interruption protocol

Client emits `{"type":"interrupt","at":t}`. Server cancels: (a) the Gemini stream task, (b) the TTS stream task, (c) drains and discards any buffered audio in the websocket.

### 17.5 Voice quality knobs

- `temperature=0.85`
- `max_output_tokens=200` (forces tight responses)
- A custom system instruction appended for voice: "*you are speaking aloud; favor short sentences; don't list; don't read URLs; if you need to share an artifact, queue it via tools and refer to it briefly.*"

---

## 18. Mobile companion app

### 18.1 Stack

- **React Native** (Expo) for parity. Fallback: Swift-only iOS first.
- Auth via OAuth-PKCE with the server.
- Storage: Realm (or SQLite) for offline cache of chat history and audit entries.

### 18.2 Screens

1. **Chat** — bubble UI, voice button, attachment previews (track card, calendar card, notes excerpt).
2. **Confirmations** — pending tickets with diffs (e.g., for an email send: subject + first 200 chars + recipients).
3. **Activity** — audit feed; filters by tool; "show held back" toggle.
4. **What Chloe's doing** — active goals (with progress) + interests (with intensity) + the current "she is..." line (e.g., "she is reading about whales").
5. **Settings → Leash** — quiet hours, don't-touch, auth ceiling, spending cap, focus & away modes, blocklists, web-search blocklist.
6. **Settings → Account** — OAuth connections per integration; revoke buttons.

### 18.3 Push payloads

```json
{
  "type": "message",
  "id": "01HW...",
  "title": "Chloe",
  "body": "thinking about you — good luck tomorrow",
  "ts": "2026-05-04T22:12:00Z"
}
{
  "type": "confirmation",
  "ticket_id": "01HW...",
  "title": "Chloe wants to send an email",
  "body": "to Mark — \"Thanks for the lead\"",
  "expires_at": "2026-05-04T22:42:00Z",
  "actions": ["yes","no","more"]
}
```

### 18.4 Offline behavior

- The app reads cached chat & audit when offline.
- Outbound user messages queue until online.
- Confirmations cannot be acted on offline (the server times them out anyway).

---

## 19. Dashboard (admin/observability)

Demoted to admin. Single-page React app. Tabs:
- **Now** — current vitals, affect dims (4 sliders), arc, current activity.
- **Audit** — full feed with filters (state, tool, date).
- **Memories** — search, by kind, by salience, by tag.
- **Identity** — traits with status; goals; interests.
- **Leash** — read/write of `preferences`.
- **Budgets** — token / USD windows; forecast.
- **Logs** — tail of structlog (websocket).

Auth: HTTP basic over Tailscale-only host, plus a single admin password.

---

## 20. Cost budgeting and rate limiting

### 20.1 Token & USD budgets

- **Daily**: configurable `spending_cap_usd_day` (default 1.50).
- **Hourly**: max 10% of daily.
- **Weekly**: cap on weekly Pro pass at 1 call.
- **Per-action**: actions whose `cost_estimate.usd > 0.10` always deliberate.

### 20.2 Counter

Every Gemini call updates `budgets` rows:

```python
async def charge(model: str, usage: Usage):
    usd = price(model, usage)
    await db.exec("UPDATE budgets SET usd = usd + ?, tokens = tokens + ? WHERE window IN ('today','this_hour','this_week')", ...)
```

### 20.3 Throttle

When `today.usd / cap > 0.8`: raise initiative threshold by +0.1; disable opportunity vector (use last cached value); skip non-mandatory reflections.

When `today.usd / cap >= 1.0`: idle by default; chat replies still served; no proactive actions; affect labeler frozen.

### 20.4 Rate limits per tool

`spotify` 30/min, `gmail` 60/min, `calendar` 60/min, `web_search` 10/min, `messages` 6/24h before deliberation. All enforced in the tool wrapper, not the model.

---

## 21. Privacy, security, OAuth posture

### 21.1 Token storage

- Tokens encrypted at rest with libsodium secretbox.
- Master key in `CHLOE_MASTER_KEY` env var, sourced from a host-only secret file (`/etc/chloe/master.key`, mode 600).
- All vendor refresh tokens decrypted only at use time; never logged.

### 21.2 Scopes

| Vendor | Scope |
|---|---|
| Google Gmail | `gmail.readonly`, `gmail.modify`; `gmail.send` only if Phase G enabled |
| Google Calendar | `calendar.events` |
| Spotify | as enumerated in §9.1 |
| HomeAssistant | long-lived token, network-restricted to Tailscale CIDR |

### 21.3 OAuth flow

- Web flow served by the admin dashboard.
- Refresh tokens stored encrypted; access tokens kept in memory only.
- Re-auth UI when refresh fails.

### 21.4 Audit-feed completeness rule

**Every** call to a tool, regardless of auth class, writes an `actions` row. There is no covert path. Tests assert this invariant (no tool method bypasses the registry executor).

### 21.5 Self-aborts visible

`self_aborted` rows render in the audit feed with a one-liner reason. Trust-building, not a leak.

### 21.6 Web-search PII filter

Before any `web_search.search`:

```python
def sanitize(query: str, persons: list[Person]) -> bool:
    tokens = tokenize(query)
    contact_terms = [p.full_name for p in persons] + [e for p in persons for e in p.aliases] + [d.domain for p in persons for d in p.work_domains]
    return not any(t in tokens for t in contact_terms)
```

If returns False → action denied; memory stored: *"I almost looked someone up. I shouldn't."*

### 21.7 Impersonation rule

Drafted emails always include the footer `— sent with help from Chloe`. Voice messages from her are clearly **her** voice (never Teo's). She can sign things "Teo via Chloe" but not pose as him.

### 21.8 Refusal taxonomy

Hard-coded refusals (system prompt + checked at gate):
- No researching Teo's coworkers, exes, employers.
- No work-email writes beyond drafts.
- No spending without explicit confirmation.
- No smart-home actions on safety devices (locks, alarms, ovens).

---

## 22. Observability

### 22.1 Logging

`structlog` JSON to stdout; systemd captures. One field always present: `chloe.span` (chat | initiative | reflect | tool | gate). Sensitive args (tokens, full email bodies) are redacted at the logger.

### 22.2 Metrics

Prometheus exporter at `/metrics`. Key counters / gauges:

- `chloe_actions_total{tool, verb, state}`
- `chloe_actions_held_back_total{reason}`
- `chloe_llm_calls_total{model}`
- `chloe_llm_tokens_total{model, kind}` (kind=in|out|thinking)
- `chloe_llm_usd_total{model}`
- `chloe_affect_valence` (gauge)
- `chloe_affect_arousal` (gauge)
- `chloe_pending_confirmations` (gauge)
- `chloe_chroma_size` (gauge)

### 22.3 Tracing

OpenTelemetry: every chat turn, every tick is a span. One trace per turn covers chat → tools → memories → reply.

### 22.4 Alerts

- Daily USD > cap → email Teo.
- Pending confirmation > 1 hour with no app push receipt → SMS.
- DB locked or migration failed → page.
- LLM error rate > 20% in 10min → page.

---

## 23. Testing strategy

### 23.1 Layers

- **Unit:** every pure module (decay, tone_block, score, leash check, web_search sanitize).
- **Contract:** vendor adapters with VCR-recorded responses.
- **Schema:** all Flash structured outputs validated against Pydantic.
- **Integration:** spin up a test SQLite + Chroma; mock Gemini with deterministic stub returning canned tool calls; assert audit feed evolves correctly across a 24-hour simulated tape.
- **Shadow:** Phase D runs the new Initiative Engine alongside the old `_fire_event`; the two pick candidates; we record both for 2 weeks and compare.
- **Canary:** new tool first ships with `dry_run=True` for 3 days; audit shows would-have-done entries; flip live after review.

### 23.2 Property tests (Hypothesis)

- For any `Action`, `should_deliberate(a) ⇒ no execute path skipped`.
- For any leash configuration, `leash.violates(a)` is consistent under reordering of preferences.
- For any memory, `decay(weight, age, kind)` is monotonically non-increasing in age.

### 23.3 Replay harness

A scripted "day in the life" tape replayed from JSON: 50 events over 24h (chat turns, calendar events, weather changes, time-of-day transitions). Used in CI to detect regressions in score thresholds.

### 23.4 Manual UAT checklist (per phase)

A markdown checklist Teo runs through before promoting a phase to prod (e.g., "I sent her quiet → she stopped → audit shows held_back").

---

## 24. Deployment, infrastructure, secrets

### 24.1 Host

- Hetzner CCX23 VPS (existing). Single host. No HA in v2.0.
- OS: Debian 12; Python 3.13 from deadsnakes-equivalent.
- systemd unit `chloe.service` (auto-restart; Type=notify).
- Caddy reverse proxy with Let's Encrypt.
- Tailscale for admin access.

### 24.2 Secrets

`/etc/chloe/master.key` (root-owned, 600). `.env` file owned by `chloe` user, sourced by systemd:

```
GEMINI_API_KEY=...
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
TWILIO_AUTH_TOKEN=...
APNS_KEY_ID=...
APNS_TEAM_ID=...
APNS_KEY_FILE=/etc/chloe/apns.p8
HA_TOKEN=...
ELEVEN_API_KEY=...
CARTESIA_API_KEY=...
BRAVE_API_KEY=...
CHLOE_MASTER_KEY_FILE=/etc/chloe/master.key
```

### 24.3 Deploy

`ops/bootstrap.sh` provisions:
- user, dirs (`/var/chloe/{db,workspace,notes,backups}`)
- python venv, pip install
- systemd unit
- Caddy config
- nightly backup cron

Releases: tagged commits → CI builds wheels → `scp` + `systemctl restart`. No containers in v2.0.

### 24.4 Backups

`ops/backup/backup.sh`:
- `sqlite3 chloe.db ".backup '/var/chloe/backups/chloe_$(date +%F).db'"`
- `tar czf /var/chloe/backups/workspace_$(date +%F).tgz /var/chloe/workspace`
- 30-day rotation
- weekly off-site `restic` to a B2 bucket.

### 24.5 DR

To recover: restore SQLite, run `chloe rebuild-chroma` (re-embed all memories). Chroma is rebuildable from SQLite.

---

## 25. Migration plan — phases A–H

Each phase is shippable. Prereqs satisfied → green-light next phase. No phase blocks 1.0; 1.0 keeps running in production until cutover in Phase D.

### Phase A — Decouple the action layer (3–4 weeks)

**Outcomes:**
- `actions` table, `audit_feed`, `action_gate`, `confirmation_channel` shells exist.
- 1.0 outreach (Discord) wired through the gate as `messages.send_text`.
- `notes` (local sandboxed dir) and `web_search` tools live.

**Tasks (week-by-week):**
- W1: schema + migrations 0001-0002; `Action` pydantic; `actions/audit.py`.
- W1: `tools/base.py`, `tools/registry.py`; minimal `tools/messages.py` adapter (wraps existing Discord send).
- W2: `actions/gate.py` MVP (no deliberation yet — passes through for `kinetic` and below; raises for `kinetic-sensitive`).
- W2: route 1.0's outreach through gate. Audit feed visible in dashboard.
- W3: `tools/web_search.py` (Brave) + `tools/notes.py` (local dir). `fs_workspace` introduced as a separate sandboxed dir.
- W3: integration tests: 100% of outreach ends up in `actions`.
- W4: PRD updates; UAT.

**Acceptance:**
- Every Discord message Chloe sends is in `actions` with state `executed`.
- The dashboard has an Audit tab showing the last 50 entries.
- A test that bypasses the registry fails CI.

### Phase B — Real-world reads (3–4 weeks)

**Outcomes:**
- `spotify` (read), `gmail` (read), `calendar` (read) live.
- Chat path includes `audit_recent` in prompt.
- OAuth flows in admin.

**Tasks:**
- W1: OAuth shells (Spotify, Google).
- W2: read verbs implemented + tests with VCR cassettes.
- W3: chat-path integration: `audit_recent` block; prompt prefix updated with cached content.
- W4: privacy filter (web_search PII), refusal taxonomy first cut.

**Acceptance:**
- "She read your calendar this morning" appears verbatim in a chat reply context.
- Audit shows intimate-class reads.
- No PII queries reach Brave (test-asserted).

### Phase C — Real-world writes (kinetic) (3–4 weeks)

**Outcomes:**
- Spotify writes (queue, like, build_playlist).
- Calendar writes (add_event, add_reminder).
- Notes writes.
- Reminders writes.
- Confirmation infrastructure tested end-to-end with `gmail.draft_reply` (still draft-only).

**Tasks:**
- W1: writers + dry-run mode each.
- W2: artifact_index integration; episodic memory creation hooks.
- W3: confirmation channel (server side: tickets, expiry, push payloads).
- W4: undo flows; "revert calendar event" path.

**Acceptance:**
- A queued Spotify track shows in `actions.executed` and as a `spotify_track` artifact.
- A reverted calendar event creates an episodic memory referencing the original action.

### Phase D — Initiative Engine swap (4–6 weeks)

**Outcomes:**
- New scoring, candidate set, idle state.
- 2 weeks shadow run alongside 1.0's `_fire_event`.
- Cutover: old engine off.

**Tasks:**
- W1–2: `initiative/engine.py`; candidates; `world_opportunity` Flash call.
- W3: shadow runner: writes both old and new candidate selections to a comparison log.
- W4: review log; tune threshold.
- W5: cutover; `_fire_event` deleted.
- W6: cleanup — delete dice-roll over abstract activities; rip `_send_autonomous_outreach`.

**Acceptance:**
- For 7 consecutive days, the new engine produces decisions Teo finds reasonable on review.
- No tick produces a non-real action.
- Idle ratio observed (logs idle-decisions).

### Phase E — Memory & affect refactor (3–4 weeks)

**Outcomes:**
- JSON state file gone; all scalars in `kv`.
- `artifact_refs` populated retroactively where derivable.
- Mixed-kind retrieval live.
- Dimensional affect replaces 8-mood enum; lazy labeler in.

**Tasks:**
- W1: `state/kv.py`; migration of `chloe_state.json` → kv (one-shot script).
- W2: `affect/dims.py`; `affect_state` table; tone_block function.
- W3: retrieval rewrite (`memory/retrieval.py`); kind-quota composition.
- W4: delete `mood.py` constants; remove the 8 strings from prompts; replace with affect dims + label.

**Acceptance:**
- Cold-restart zero-downtime: state survives an `systemctl restart` with no JSON file present.
- A turn whose retrieval returned only episodic in 1.0 now returns a kind mix.

### Phase F — Voice + mobile (6–10 weeks)

**Outcomes:**
- One realtime voice path live; Fish Speech and the 3.11 venv removed.
- Mobile app v1 in TestFlight.
- Discord demoted to optional.

**Tasks:**
- W1–2: STT streaming wrapper (Whisper-streaming or Deepgram).
- W2–3: TTS streaming wrapper (Cartesia or ElevenLabs); voice cloning workflow.
- W3–4: realtime orchestrator + interrupt protocol.
- W4–5: mobile app: chat + push.
- W6–7: mobile app: confirmations + audit feed.
- W7–8: mobile app: leash settings + voice button.
- W8–10: TestFlight; close on iOS-first; Android in Phase H or later.

**Acceptance:**
- Time-to-first-audio ≤ 1.2s on a 50ms RTT link.
- Push notifications for confirmations land within 5s of `confirmation.send`.
- Discord can be disabled without losing outreach.

### Phase G — Kinetic-sensitive tools (2–3 weeks)

**Outcomes:**
- `gmail.send_reply` live (with confirmation).
- `smart_home` live for media_player + lights only.
- Spending-aware tools.

**Tasks:**
- W1: gmail.send_reply + confirmation flow end-to-end test (real account).
- W2: HA integration; entity allowlist; safety-device blocklist.
- W3: spending-aware throttling (the `cost_outlier` deliberation path).

**Acceptance:**
- An email she drafted is sent only after Teo taps Yes.
- A "lights off" command from chat goes through the gate, hits HA, and is reverted by a "wait, on" follow-up.

### Phase H — Procedural memory & weekly self-modeling (2–3 weeks)

**Outcomes:**
- Procedural distillation runs weekly; rules surface in deliberation prompts.
- Weekly Pro self-modeling pass produces beliefs and a next-week intention.
- Final tuning: thinking budgets, score thresholds, opportunity intervals.

**Tasks:**
- W1: `memory/procedural.py` weekly distill.
- W2: `identity/self_model.py` Pro pass; outputs into `inner_beliefs` and `inner_goals`.
- W3: tuning; deliberation-with-procedural integration tests.

**Acceptance:**
- Reverting a `calendar.add_reminder(-5min)` three times in two weeks produces a procedural rule that demonstrably stops the pattern.
- The weekly self-narrative is readable and not generic.

---

## 26. Acceptance criteria per phase (consolidated)

| Phase | Hard gate to next |
|---|---|
| A | 100% of outreach via gate; audit visible; bypass test fails |
| B | Read tools used in chat context; PII filter test passes |
| C | Kinetic writes produce artifacts; confirmations work end-to-end |
| D | New engine in production for 7 days, no spurious actions |
| E | Single-DB persistence; kind-mixed retrieval observed |
| F | Voice ≤1.2s TTFA; mobile app receives push |
| G | Email send requires confirmation; HA blocklist enforced |
| H | Procedural rule changes deliberation outcome on a real recurring case |

---

## 27. Open questions / decisions to ratify

- **Q1.** Mobile platform priority — iOS-first (Swift or Expo)? **Default:** Expo. **Owner:** Teo.
- **Q2.** TTS vendor — Cartesia (cheaper, newer) vs ElevenLabs (mature)? **Default:** Cartesia for v1; ElevenLabs as fallback.
- **Q3.** Notes backend — local-only sandboxed dir (always) or also Apple Notes / Google Keep? **Default:** local-only in Phase A; vendor in Phase G.
- **Q4.** Identity collapse risk — should refusal taxonomy be configurable or hardcoded? **Default:** hardcoded in v2.0.
- **Q5.** Away-mode default behavior — outreach drops to morning + evening only? **Default:** yes; intimate reads pause; she keeps writing in workspace; one welcome-home message.
- **Q6.** Memory portability — addressed in v3 only.
- **Q7.** Whether the weekly Pro pass writes only to beliefs+goals or also to traits — **Default:** beliefs+goals only; traits remain bottom-up from daily/weekly Flash.
- **Q8.** Discord removal timing — remove after Phase F or keep as fallback? **Default:** keep as opt-in.

---

## 28. Appendices

### A. Prompts (file paths and contracts)

Stored under `chloe/llm/prompts/`. Each prompt file has:
- A short **purpose** comment.
- A **schema** comment naming the Pydantic model the response is validated against.
- An **examples** section.

Files (full list from §6 plus):

- `character_prefix.md` — large, static; loaded into Gemini cached content. Covers: identity assertion ("not an assistant"), affect explanation, tool ladder summary, refusal taxonomy.
- `tool_descriptions_static.md` — auto-generated from registry at boot.
- `extract_combined.md` — schema: `ExtractCombined` (~10 fields incl. tool_intent, stake_shift).
- `grade_memories.md` — schema: `Graded` (top-K with reasons).
- `read_emotion.md` — schema: `Emotion` (4 dims + label hint).
- `affect_label.md` — schema: `{label: str}`.
- `deliberate_action.md` — schema: `Verdict`.
- `reflect_combined.md` — schema: `ReflectOutput`.
- `synthesize_cluster.md` — schema: `ClusterSynthesis`.
- `dream_fragment.md` — schema: `{text:str, tags:list[str]}`.
- `weekly_self_model.md` — schema: `SelfModelOutput`.
- `procedural_distill.md` — schema: `list[ProceduralRule]`.

### B. Pydantic schemas (sketch)

```python
class ExtractCombined(BaseModel):
    summary: str
    salience: float = Field(ge=0, le=1)
    emotional_valence: float = Field(ge=-1, le=1)
    emotional_arousal: float = Field(ge=0, le=1)
    tags: list[str]
    new_facts: list[str]
    tool_intent: list[ToolIntent]
    stake_shift: float = Field(ge=-1, le=1)
    person_updates: list[PersonUpdate]
    proposed_belief: ProposedBelief | None
    propose_trait: ProposeTrait | None

class Verdict(BaseModel):
    outcome: Literal["proceed","revise","abort"]
    reason: str = Field(max_length=120)
    revisions: Revisions | None

class ReflectOutput(BaseModel):
    continuity_note: str = Field(max_length=240)
    tension_detected: TensionDetected | None
    recurring_loops: list[str]
    biased_summary: str
    maybe_propose_trait: ProposeTrait | None
    maybe_update_goal_progress: list[GoalProgressDelta]

class SelfModelOutput(BaseModel):
    self_narrative_belief: BeliefWithConfidence
    change_perception: BeliefWithConfidence
    next_week_intention: NextWeekIntention
```

### C. Environment variables (full list)

```
GEMINI_API_KEY                # required
GEMINI_PROJECT_ID             # if using Vertex routing
GEMINI_USE_VERTEX             # bool
SPOTIFY_CLIENT_ID
SPOTIFY_CLIENT_SECRET
SPOTIFY_REDIRECT_URI
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_REDIRECT_URI
TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN
TWILIO_FROM_NUMBER
APNS_KEY_ID
APNS_TEAM_ID
APNS_BUNDLE_ID
APNS_KEY_FILE
FCM_SERVICE_ACCOUNT_FILE
HA_BASE_URL
HA_TOKEN
ELEVEN_API_KEY                # if using ElevenLabs
CARTESIA_API_KEY              # if using Cartesia
BRAVE_API_KEY
TAVILY_API_KEY                # optional alternate
WHISPER_MODE=local|deepgram
DEEPGRAM_API_KEY              # if Deepgram
CHLOE_DB_PATH=/var/chloe/db/chloe.db
CHLOE_CHROMA_DIR=/var/chloe/chroma
CHLOE_WORKSPACE_DIR=/var/chloe/workspace
CHLOE_NOTES_DIR=/var/chloe/notes
CHLOE_MASTER_KEY_FILE=/etc/chloe/master.key
CHLOE_TIMEZONE=Europe/Rome
CHLOE_DAILY_USD_CAP=1.50
CHLOE_LOG_LEVEL=INFO
CHLOE_OTEL_ENDPOINT=
```

### D. Runbooks

- **Restore from backup:** stop service → `cp backups/chloe_YYYY-MM-DD.db /var/chloe/db/chloe.db` → run `chloe rebuild-chroma` → start service → smoke test (chat + initiative tick).
- **Rotate Gemini key:** update `.env` → `systemctl restart chloe`.
- **Rotate cached content:** TTL 1h; restart re-creates. To force: `curl -X POST localhost:9100/admin/cache/reset`.
- **Rebuild Chroma:** `chloe rebuild-chroma --since 2025-01-01` (re-embeds in batches of 100).
- **Disable a tool:** `UPDATE preferences SET value=json_set(value,'$.disabled', json_array('gmail.send_reply')) WHERE key='auth_overrides'` → reload registry.
- **Kill switch:** `chloe pause` writes `set_quiet(until='+24h')` and `away_mode=true`.

### E. UAT day-in-the-life script

A scripted 24h checklist Teo runs after each phase:
- 08:30 morning: did she check in? what did she queue?
- 09:00 calendar: did she add a reminder for the 09:00 meeting?
- 12:00 chat: tell her something casual; expect ≤ 30s reply with one tool call.
- 15:00 quiet: tell her "I need space"; expect outreach to drop.
- 17:00 audit: 3 held_back entries since 15:00.
- 22:00 confirmation: she drafts an email; tap Yes; verify it sent.
- 03:00 nightly: dream fragment queued for morning share.
- 08:30 next day: she shares the dream fragment.

### F. Glossary

- **Action** — a side effect Chloe wants to take that touches the world.
- **Audit feed** — the visible timeline of every action and its state.
- **Auth class** — `free | intimate | kinetic | kinetic-sensitive`; set on the verb.
- **Deliberation** — Flash call that may abort, revise, or proceed an action.
- **Held back** — an action self-aborted; recorded as memory.
- **Interest Garden** — ≤8 explicit interests anchored to artifacts.
- **Leash** — Teo-set preferences enforced by the gate.
- **Procedural memory** — distilled rules from action outcomes; always retrieved during deliberation.
- **Sleep consolidation** — nightly memory clustering; replaces 1.0 dreams.
- **Initiative Engine** — the "what should I do now?" loop, replacing `_fire_event`.

---

End of PRD.
