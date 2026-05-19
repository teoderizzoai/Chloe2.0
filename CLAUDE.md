# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Start / stop server
./chloe.sh start             # background server on :8000, logs → chloe-server.log
./chloe.sh start --reload    # dev mode with auto-reload
./chloe.sh stop
./chloe.sh status

# Run all tests
.venv/bin/pytest

# Run a single test file
.venv/bin/pytest tests/unit/test_initiative_engine.py

# Run tests by marker (integration tests require external services)
.venv/bin/pytest -m "not integration and not live"

# Rebuild ChromaDB index from SQLite
./chloe.sh rebuild-chroma

# Bootstrap identity (requires GEMINI_API_KEY)
./chloe.sh bootstrap-identity

# Run day simulator (no real API calls)
./chloe.sh simulate-day --clean --hours 72 --step 30
```

`chloe.sh` auto-creates `.venv` and installs the package on first run. The venv must be activated or commands run via `.venv/bin/` directly.

## Architecture

Chloe is an autonomous AI companion running as a **FastAPI server** (`chloe/app.py`). On startup it runs DB migrations, seeds the primary person (Teo, id=1), syncs SQLite memories to ChromaDB, registers all tools, then launches five background asyncio loops.

### Background loops (`chloe/loop.py`)

| Loop | Interval | Purpose |
|------|----------|---------|
| `initiative_loop` | 60 s | Picks and executes proactive actions |
| `reflect_loop` | 5 min | Runs every-2h emotional/memory reflection when due |
| `pressure_loop` | 10 min | Decays inner tension/pressure rows |
| `daily_job_loop` | 5 min | Nightly memory retention + reflection at 03:00/04:30 |
| `weekly_job_loop` | 1 h | Narrative weaving on Sunday 03:00 |

### Storage

- **SQLite** (`chloe.db`) — primary store. Schema lives in `chloe/state/migrations/` (sequential numbered `.sql` files applied once). `chloe/state/db.py` owns the single global connection with WAL mode.
- **ChromaDB** — vector index for memory semantic search (`chloe/state/chroma.py`). Collection `memories_v2`. Synced from SQLite on startup.
- **KV store** (`chloe/state/kv.py`) — simple SQLite-backed key-value for lightweight state (budgets, timestamps, flags).

### Affect model (`chloe/affect/`)

`AffectState` (valence, arousal, social_pull, openness, depletion) is persisted in SQLite and decays over time. `tone_block()` serializes it into a system-prompt snippet injected on every chat turn.

### Initiative engine (`chloe/initiative/engine.py`)

Each tick produces `CandidateAction` objects from several sources (pressure-driven, goal-driven, interest-driven, routine, curiosity, share-queue), scores them, and submits the winner to `actions/gate.py`. The gate enforces daily budgets, deliberation, and confirmation flows. Threshold constant: `INITIATIVE_THRESHOLD = 0.35`.

### Chat pipeline (`chloe/channels/chat_api.py`)

Each inbound message goes through:
1. **Preflight** — salience scoring + tone-block decision (`chloe/channels/preflight.py`)
2. **Dynamic suffix** — assembles affect, recent actions, memories, relationship context, self-model, narrative, world beliefs, inside jokes into a system-prompt suffix
3. **LLM call** — Gemini Flash or Pro via `chloe/llm/gemini.py`
4. **Post-processing** — `_extract_and_process_mentions` logs aesthetic reactions, marks unprocessed memories

### LLM layer (`chloe/llm/`)

All LLM calls go through `chloe/llm/gemini.py` (`call_flash` / `call_pro`). Prompts are `.md` files in `chloe/llm/prompts/` with `{{placeholder}}` interpolation. Per-prompt output token budgets are enforced. Daily spend is tracked against `CHLOE_DAILY_BUDGET_USD` (default $5).

### Identity system (`chloe/identity/`)

| Module | Purpose |
|--------|---------|
| `interest_garden.py` | Interests with `gen_level` (0–5) and `intensity` |
| `self_model.py` | Chloe's beliefs about herself |
| `trait_model.py` | Character traits with adjudication history |
| `character_addendum.py` | Per-person relationship addendum injected into chat |
| `narrative_weaver.py` | Weekly prose narrative accumulation |

### Channels (`chloe/channels/`)

- `chat_api.py` — core chat logic (used by both mobile and dashboard)
- `mobile_ws.py` / `mobile_routes.py` — mobile WebSocket + REST
- `dashboard_ws.py` / `dashboard_routes.py` — React dashboard WebSocket + REST
- `push.py` / `push_apns.py` / `push_fcm.py` — push notifications (APNs + FCM)

### Tools (`chloe/tools/`)

All tools are registered in `loop.py:register_all_tools()` via `chloe/tools/registry.py`. Tools include: Spotify, Calendar, Gmail, Messages, SmartHome (Home Assistant), Weather, Maps, WebSearch, Notes, CodeRunner, FsWorkspace, SelfTools.

### Key env vars

| Var | Purpose |
|-----|---------|
| `GEMINI_API_KEY` | Required for all LLM calls |
| `CHLOE_DAILY_BUDGET_USD` | LLM spend cap (default 5.0) |
| `CHLOE_DRY_RUN` | Skip real API calls |
| `CHLOE_TIMEZONE` | Default UTC |
| `HA_BASE_URL` / `HA_TOKEN` | Home Assistant |
| `GOOGLE_CLIENT_ID/SECRET` | Google OAuth (Calendar, Gmail) |
| `SPOTIFY_CLIENT_ID/SECRET` | Spotify |

Configuration is in `chloe/config.py` (`Settings` dataclass, all fields populated from env vars).

## Next steps — in priority order

### Immediate (run next session)

1. Run `./chloe.sh bootstrap-identity` once Gemini API key is available
   This seeds the first character addendum and narrative timeline entry from
   existing history. Requires `GEMINI_API_KEY` in environment.

2. Verify aesthetic reaction extraction in live chat
   The extraction path is now wired: `log_reaction()` is called from
   `_extract_and_process_mentions` when Flash detects aesthetic moments. Confirm
   by looking for `"aesthetic_reaction_logged"` events in logs after a chat turn
   where Teo shares music, writing, or an idea.

### Short-term (next 1–2 sessions)

3. **P-next-A — Curiosity question trigger now wired**
   `boost_interest()` fires an async Flash call when intensity crosses 0.7, and
   `interest_driven_candidates()` uses the cached question as the search query.
   To test: manually boost an interest past 0.7 in the live DB and run a tick.

4. **P-next-B — Opinion formation now wired**
   `_load_world_beliefs()` now labels high-confidence beliefs as "things you
   should be willing to bring into conversation." Verify in live chat that Chloe
   takes a position when relevant world beliefs surface.

5. **Real-world test of unprocessed memories now wired**
   `_extract_and_process_mentions` now computes salience+ambiguity and calls
   `mark_unprocessed()` when thresholds are met. Verify by inspecting
   `SELECT * FROM memories WHERE unprocessed=1` after a few chat turns.

### Medium-term

6. Narrative timeline first entry — after `bootstrap-identity` runs, verify an
   entry appears in `narrative_timeline`. The weekly `weave_narrative()` will now
   also apply `interest_promotions`.

7. Interest `gen_level` promotion path — `weave_narrative()` output schema includes
   `interest_promotions`. Verify after the first weekly run that `gen_level` updates
   appear in `interest_garden`.

8. Teo primary-class seed — `seed_primary_persons()` runs at every startup (after
   `migrate()`). Verify with:
   `SELECT id, name, relationship_class, gen_level FROM persons WHERE id=1;`

### Simulator results (2026-05-11)

Ran `./chloe.sh simulate-day --clean --hours 72 --step 30`
- 144 steps completed (72h / 30min), all validations passed.
- 31 chat events injected, 13 affect events injected across 3 days.
- Reflects returned "skipped" (no Gemini API key in sim environment — expected).
- Zero rabbit-hole events, no gen_level escalations in day 1.
- The sim now correctly handles `ScriptedPersonMention` events.
