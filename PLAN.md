# Chloe 2.0 — Implementation Plan

Each step is a self-contained unit of work: one PR, one commit, one reviewable change.
Prereqs are listed where non-obvious. Steps within a phase are sequential unless marked **(parallel-safe)**.

---

## Foundations — before any phase ships

These create the skeleton every phase builds on. Do them in order.

---

### F-01 · Bootstrap the new repo layout

Create the directory tree from PRD §6 with empty `__init__.py` files, a `pyproject.toml`, and a `.env.example`. No logic yet — just the shape.

**Deliverable:** `chloe/` tree exists; `python -c "import chloe"` succeeds; `pytest tests/` collects 0 tests and exits 0.

---

### F-02 · `config.py` — env vars and feature flags

Read all env vars listed in PRD Appendix C into a single `Settings` dataclass (using `pydantic-settings`). Provide a `get_settings()` singleton. Include a `FEATURE_FLAGS` dict for toggling tools on/off without code changes.

**Deliverable:** unit test asserts every required key raises `ValidationError` when missing.

---

### F-03 · `state/db.py` — SQLite WAL connection and migration runner

Open `chloe.db` with `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`. Implement a `migrate()` function that reads `state/migrations/*.sql` files ordered by name, tracks applied migrations in a `_migrations` table, and applies pending ones idempotently at boot. No migrations yet — just the runner.

**Deliverable:** calling `migrate()` twice in a row with no `.sql` files raises no errors and writes one row to `_migrations`.

---

### F-04 · Migration `0001_init.sql` — core tables

Write the full SQL for: `memories`, `chat_history`, `persons`, `person_notes`, `person_events`, `person_moments`, `person_third_parties`, `person_attachment`, `identity_traits`, `identity_contradictions`, `inner_goals`, `inner_wants`, `inner_fears`, `inner_aversions`, `inner_beliefs`, `inner_tensions`, `arcs`, `affect_records`, `ideas`. All from PRD §7.1, §7.5, §7.9. Include all indexes.

**Deliverable:** `migrate()` applies the file; `sqlite3 chloe.db ".tables"` lists every table.

---

### F-05 · `llm/gemini.py` — Gemini client wrapper

One async client class wrapping `google-generativeai` (or `google-genai`). Methods:
- `async chat(contents, system, cached_content, tools, **kwargs) -> ChatResult`
- `async flash(prompt_name, payload, schema) -> dict` — structured-output call using `BACKGROUND_MODEL`
- `async pro_thinking(prompt_name, payload, schema, thinking_budget) -> dict`

Implements retry (2 attempts, exponential backoff, max 8 s). On final failure returns `None` and logs; callers handle `None`. Hard-codes model IDs and parameter blocks from PRD §5.7. No cached content support yet (added in F-07).

**Deliverable:** unit test with `httpx`-mocked Gemini endpoint verifies retry behavior and `None` on exhaustion.

---

### F-06 · `llm/schemas.py` — Pydantic output schemas for all Flash calls

Define every structured-output model referenced in the PRD: `ExtractCombined`, `Graded`, `Emotion`, `AffectLabel`, `Verdict`, `ReflectOutput`, `ClusterSynthesis`, `DreamFragment`, `SelfModelOutput`, `ProceduralRule`, `OpportunityVector`. Include field validators (ranges, max lengths).

**Deliverable:** `from chloe.llm.schemas import *` imports all 11 models without error. Each model has at least one passing `pytest` validation test.

---

### F-07 · Gemini context caching for the static prefix

Add `cache_static_prefix()` to `llm/gemini.py`. It uploads the concatenation of `character_prefix.md` + `tool_descriptions_static.md` as a Gemini cached content blob (1h TTL). Stores the cache name in `kv` (once `kv` exists, stub with module-level var for now). Refreshes at boot and every 50 minutes via a background task. Chat calls pass `cached_content=cache_name`.

**Prereq:** F-05.
**Deliverable:** integration test (real Gemini key, marked `@pytest.mark.integration`) hits the API and confirms the cache name is a non-empty string.

---

### F-08 · `state/kv.py` — key-value scalar store

Typed `get(key, default)` / `set(key, value)` / `delete(key)` over the `kv` table. Serializes to JSON. Used by everything that 1.0 wrote to `chloe_state.json`.

**Prereq:** F-03, F-04 (needs `kv` table — add it to 0001 or create `0001b_kv.sql`).
**Deliverable:** unit test round-trips str, int, float, list, dict, None.

---

### F-09 · `observability/logging.py` — structlog JSON

Configure `structlog` with JSON renderer, always-present fields `chloe.span` and `ts`. Provide `get_logger(span)` helper. Redact a configurable list of key names (tokens, email bodies) before emission.

**Deliverable:** `get_logger("test").info("hello", secret="REDACTED")` emits JSON where `secret` is `"[REDACTED]"`.

---

### F-10 · `observability/metrics.py` — Prometheus exporter

Register all counters and gauges listed in PRD §22.2. Expose `/metrics` endpoint via FastAPI. No data yet — just the registrations.

**Deliverable:** `GET /metrics` returns `200` with `# HELP chloe_actions_total` present.

---

### F-11 · `app.py` — FastAPI factory and `loop.py` — asyncio bootstrap

`app.py` creates the FastAPI app, mounts `/metrics`, `/admin`, `/v1`. `loop.py` starts the event loop, runs `migrate()` at boot, starts background tasks as stubs (they just `await asyncio.sleep(∞)` for now), launches uvicorn.

**Deliverable:** `python -m chloe` starts, serves `GET /` → `200`, shuts down cleanly on `SIGTERM`.

---

## Phase A — Decouple the action layer

Goal: every outreach Chloe sends passes through the gate; the audit feed is real.
No new tools beyond `notes` (local) and `web_search`.

---

### A-01 · Migration `0002_actions.sql` — actions + artifact_index + preferences + budgets

Tables: `actions`, `artifact_index`, `preferences`, `budgets` (with `INSERT OR IGNORE` seed rows for the three budget windows). All from PRD §7.3, §7.4, §7.7.

**Deliverable:** migration applies cleanly; `chloe_actions_total` counter can be incremented.

---

### A-02 · `actions/schema.py` — Action pydantic model

The full `Action` dataclass from PRD §8.1 plus `ActionResult` (`executed`, `suppressed`, `awaiting`, `reason`, `ticket_id`). Include a `ulid()` helper for `id` generation.

**Deliverable:** `Action(**valid_data).model_dump()` round-trips through JSON without loss.

---

### A-03 · `actions/audit.py` — audit feed

`append(action: Action)` persists to `actions` table.
`recent(n=200)` returns last N rows as `list[Action]` ordered by `proposed_at DESC`.
`feed_text(n=10)` returns a formatted string suitable for injection into the chat prompt (matches the display format in PRD §5.6).

**Deliverable:** unit test writes 3 actions, calls `feed_text(3)`, asserts the lines contain tool + verb + state.

---

### A-04 · `actions/leash.py` — leash checker

Pure function `violates(action: Action, prefs: dict, now: datetime) -> bool`. Checks quiet hours, `dont_touch` lists, `auth_ceiling`, `away_mode`. No LLM. Reads preferences from the dict (not DB — the gate fetches prefs before calling this).

**Deliverable:** property tests (Hypothesis): for any `auth_ceiling="kinetic"`, any kinetic-sensitive action returns `True`.

---

### A-05 · `actions/budget.py` — cost tracker

`charge(model, usage)` → updates `budgets` rows.
`exceeded_for(action) -> bool` checks `today.usd / cap` against the threshold.
`throttle_level() -> float` returns 0..1 used fraction for the initiative engine.
`reset_windows()` — called by a daily cron job to zero out expired windows.

**Deliverable:** unit test: charge $1.49 against a $1.50 cap → `exceeded_for` returns `False`; charge $0.02 more → returns `True`.

---

### A-06 · `tools/base.py` + `tools/registry.py` — tool scaffold

`Tool` ABC with abstract `execute(verb, args) -> ToolResult` and concrete `dry_run(verb, args) -> str`. `ToolVerb` dataclass (name, schema, auth_class, reversibility, cost_per_call_usd, description_for_model, description_for_human). `ToolRegistry` singleton: registers tools, exposes `gemini_tool_declarations()`, `describe_static()`, and `async execute(tool_name, verb, args)`.

**Deliverable:** empty registry returns `[]` from `gemini_tool_declarations()`. Registering a stub tool and calling its dry-run returns a non-empty string.

---

### A-07 · `tools/messages.py` — messages tool (wrapping existing Discord send)

Verbs: `send_text(body)`, `send_voice(audio_file)`. Auth: `kinetic`. For now, delegates to the existing Discord DM bridge from 1.0. Push (APNs/FCM) wired in Phase F.

**Deliverable:** calling `send_text` in dry-run mode returns `"Would send: <body>"` without hitting Discord.

---

### A-08 · `actions/gate.py` MVP — pass-through gate

Implements the full gate logic from PRD §8.2 **except** deliberation (stub: always `proceed`). Handles: leash check → budget check → auth dispatch (free/intimate execute immediately; kinetic execute + audit; kinetic-sensitive raise `NotImplementedError` for now).

**Prereq:** A-02 through A-07.
**Deliverable:** unit test: a `free` action passes through; a leash-blocked action returns `suppressed=True`; the action row in DB has the correct `state`.

---

### A-09 · Wire 1.0 outreach through the gate

In the 1.0 codebase, find every call site of the Discord outreach function. Replace each with `await action_gate.submit(Action(tool="messages", verb="send_text", ...))`. The gate then calls `tools/messages.py`.

**Deliverable:** integration test: simulate a 1.0 outreach trigger → confirm `actions` table has one row with `state="executed"`.

---

### A-10 · `tools/web_search.py` — Brave Search API

Verbs: `search(query)` (free), `fetch_page(url)` (free), `summarize_url(url)` (free — Flash call internally). Include the PII sanitizer from PRD §21.6. Return results as `list[{title, url, snippet}]`.

**Deliverable:** VCR cassette test: canned Brave response → typed result. Unit test: a query containing a name from `persons` raises `PermissionError` before hitting the API.

---

### A-11 · `tools/notes.py` — local sandboxed directory

Verbs: `read(path)`, `append(path, text)`, `create(path, text)`, `list(dir?)`, `move(src, dst)`, `revert(path, version)`. All operations confined to `CHLOE_NOTES_DIR` (path traversal rejected). `revert` keeps a `.versions/` shadow.

**Deliverable:** unit test: `create` → `append` → `revert` leaves the file at the pre-append content.

---

### A-12 · `tools/fs_workspace.py` — Chloe's own workspace

Same verbs as `notes` but rooted at `CHLOE_WORKSPACE_DIR`. Auth: `free` (her own files). 10 MB per-file cap, 1 GB total cap enforced at write time.

**Deliverable:** unit test: write 10 MB + 1 byte → `CapExceeded` error.

---

### A-13 · Audit feed tab in admin dashboard

Add a `/admin/audit` route returning the last 200 `actions` rows as JSON. Add a minimal HTML page (no framework needed — `<table>` is fine) that auto-refreshes every 5 seconds.

**Deliverable:** `GET /admin/audit` returns `200` with correct JSON schema.

---

### A-14 · Phase A integration test: all outreach in `actions`

Write a test that replays 10 scripted outreach events through the full stack and asserts: (a) every event has a row in `actions`; (b) no row has `state="proposed"` (all resolve); (c) `feed_text(10)` is non-empty.

Also write a test that calls the Discord send function directly (bypassing the gate) and asserts it is now unreachable from any path except through the gate (import-graph analysis or a runtime spy).

**Deliverable:** both tests green in CI.

---

## Phase B — Real-world reads (intimate)

Goal: Chloe can read Teo's Spotify, Gmail, and Calendar; the chat prompt includes recent audit context.

---

### B-01 · OAuth token storage layer

`state/oauth_tokens.py`: `store(service, token_data)` encrypts with `libsodium.secretbox` using the master key, persists to `kv`. `load(service)` decrypts. `refresh(service)` calls the vendor's token endpoint and stores the new token. Never logs decrypted tokens.

**Deliverable:** unit test: store → load round-trip returns identical dict. Logs inspected: no token values present.

---

### B-02 · Admin OAuth flow for Spotify

`/admin/oauth/spotify/start` → redirect to Spotify authorization URL. `/admin/oauth/spotify/callback` → exchange code, store token. Displays "Connected as {display_name}" on success.

**Deliverable:** manual UAT: visit start URL in browser, complete Spotify auth, see success page, `kv` has encrypted token.

---

### B-03 · Admin OAuth flow for Google (Gmail + Calendar)

Same pattern as B-02. Single OAuth app covers both Gmail and Calendar scopes (`gmail.readonly`, `calendar.events`). Separate `load("google")` call for both tools.

**Deliverable:** manual UAT: Google auth completes; token usable for both Gmail and Calendar reads.

---

### B-04 · `tools/spotify.py` — read verbs

`show_currently_playing()` → `{track, artist, progress_ms, is_playing}`.
`show_recent_listens(limit=10)` → `list[{track, artist, played_at}]`.
Auth: `intimate`. Uses `httpx.AsyncClient` with the stored Spotify token. Handles 401 by triggering `oauth_tokens.refresh("spotify")`.

**Deliverable:** VCR cassette test. Integration test (marked `@pytest.mark.live`) against real Spotify account.

---

### B-05 · `tools/gmail.py` — read verbs

`read_recent(limit, label?)`, `read_thread(threadId)`, `search(query)`.
`summarize_inbox(window="24h")` — reads recent messages then makes one Flash call returning `{unread_count, senders, top_subjects, action_items}`.
Auth: `intimate`. Token auto-refresh on 401.

**Deliverable:** VCR cassette tests for all verbs. `summarize_inbox` asserts the Flash call schema.

---

### B-06 · `tools/calendar.py` — read verbs

`read_today()`, `read_week()`, `find_free_slot(duration_minutes, between)`.
Returns structured dicts. Auth: `intimate`.

**Deliverable:** VCR cassette tests. `read_today` returns a list of `{title, start, end, location?}`.

---

### B-07 · Update chat path to include `audit_recent` in the dynamic suffix

In `channels/chat_api.py`, assemble `audit_recent = audit.feed_text(n=10)` and include it in the per-call dynamic suffix passed to Gemini Pro. Write the corresponding `chat_system.md` dynamic stub.

**Deliverable:** integration test: after executing one `notes.append` action, the next chat call's prompt contains that action's intent string.

---

### B-08 · Register static tool descriptions in Gemini cached content

At boot, call `registry.describe_static()` and include it in the cached-content prefix alongside `character_prefix.md`. Update F-07's `cache_static_prefix()` accordingly.

**Deliverable:** `GET /admin/cache/status` returns the cache name and TTL.

---

### B-09 · PII filter for `web_search` + refusal taxonomy in character prefix

PII filter (`tools/web_search.py`): before any `search` call, run `sanitize(query, persons)`. If it returns `False`, gate raises `PermissionError`; an episodic memory is stored: *"I almost searched for someone. I shouldn't."*

Refusal taxonomy: add a section to `character_prefix.md` enumerating hard refusals (research coworkers, work email beyond drafts, spending without confirmation, safety devices).

**Deliverable:** unit test: query with a person's name from `persons` → `PermissionError` + memory row with tag `refusal`.

---

### B-10 · Phase B acceptance test

Write a scripted replay: (1) Spotify read → audit entry; (2) Gmail read → audit entry; (3) Calendar read → audit entry; (4) chat turn → reply text contains words from the audit context.

Assert: all reads have `authorization="intimate"` in `actions`; no kinetic actions fired; no PII query reached Brave.

---

## Phase C — Real-world writes (kinetic)

Goal: Chloe can queue tracks, add calendar events, append notes, add reminders, and create Gmail drafts. Confirmation infrastructure end-to-end.

---

### C-01 · `tools/spotify.py` — write verbs

`queue_track(uri)`, `start_playlist(uri)`, `like(uri)`, `skip()`, `build_playlist(name, description, track_uris)`.
Auth: `kinetic`. Each write calls `artifact_index.register(...)` after success.

**Deliverable:** dry-run returns correct preview string. VCR cassette test for `queue_track`. Artifact row appears after successful execute.

---

### C-02 · `tools/calendar.py` — write verbs

`add_event(title, start, end, description?)`, `add_reminder(time, body)`, `decline(eventId, reason?)`.
Auth: `add_event`/`add_reminder` → `kinetic`; `decline` → `kinetic-sensitive`.

**Deliverable:** `add_event` in dry-run shows title + formatted time. Artifact row written on execute.

---

### C-03 · `tools/notes.py` — write verbs already exist (from A-11); wire to artifact_index

After every `create` or `append`, call `artifact_index.register(kind="notes_doc", ref=path, snapshot=first_200_chars)`.

**Deliverable:** unit test: `create` → `artifact_index` has one row with `kind="notes_doc"`.

---

### C-04 · `tools/reminders.py` — reminders tool

Backed by Apple Reminders (EventKit via a small local helper script) or Google Tasks (via Google API). Verbs: `add(title, due?)`, `complete(taskId)`, `list()`. Auth: `kinetic`.

**Deliverable:** dry-run works without an actual backend. Integration test (marked `@pytest.mark.live`) against real account for the chosen backend.

---

### C-05 · `tools/gmail.py` — `draft_reply` verb

`draft_reply(threadId, body)` → creates a Gmail draft (not sent). Auto-appends the `"— sent with help from Chloe"` footer unless `preferences["gmail_footer"] == false`. Auth: `kinetic`. Returns `{draftId, preview}`.

**Deliverable:** VCR cassette test. The footer is present in the draft body. Artifact row written with `kind="gmail_thread"`.

---

### C-06 · Episodic memory creation hook on action execute

In `gate.py`'s `execute_and_record()`, after a successful execute: create an episodic memory whose `text` is `action.intent`, `source="action"`, `source_ref=action.id`, `artifact_refs=action_artifact_refs`. Write its `id` back to `actions.becomes_memory_id`.

**Prereq:** C-01 (artifact refs pattern established).
**Deliverable:** unit test: after `queue_track` executes, `memories` has one row with `source="action"` and non-empty `artifact_refs`.

---

### C-07 · `actions/confirm.py` — confirmation ticket lifecycle

`ConfirmationTicket` pydantic model. `send(action) -> ConfirmationTicket` persists ticket to `kv`, updates `actions.state = "awaiting_confirmation"`. `confirm(ticket_id)` → `state="confirmed"`, re-evaluates staleness (if >TTL, run deliberation again; may decline). `deny(ticket_id)` → `state="denied"`, stores `held_back` memory. `expire_pending()` → runs as a cron every 5 minutes, marks stale tickets denied.

**Deliverable:** unit test: create ticket → wait (mock) TTL → `expire_pending()` → action state is `"denied"` → memory row with tag `held_back`.

---

### C-08 · APNs push client — `channels/push_apns.py`

`send_push(device_token, payload)` sends a push notification via APNs HTTP/2. Supports `type="message"` and `type="confirmation"` payloads from PRD §18.3. Uses `httpx` with the p8 key. Handles 410 (token invalidated) by removing the stored device token.

**Deliverable:** unit test with mocked APNs endpoint verifies the JSON payload shape.

---

### C-09 · FCM push client — `channels/push_fcm.py`

Same interface as push_apns but for FCM v1 API. Provides a `preferred_push(device_info, payload)` dispatcher that picks APNs or FCM based on stored device platform.

**Deliverable:** unit test with mocked FCM endpoint.

---

### C-10 · Wire confirmation push to `confirm.py`

In `confirm.send()`, after persisting the ticket, call `channels.preferred_push(teo_device_info, confirmation_payload)`. Add `/v1/confirmations/{ticket_id}/confirm` and `/v1/confirmations/{ticket_id}/deny` HTTP endpoints (these are the "tap Yes / tap No" deep-link targets from the push notification buttons).

**Deliverable:** integration test: `confirm.send(action)` → mock APNs receives a push with `"type":"confirmation"` and the action preview.

---

### C-11 · Undo flow for kinetic actions

For `calendar.add_event`, `notes.append`, `spotify.queue_track`: implement a `reverse_verb` on each tool that undoes the action (delete event, truncate appended lines, clear queue entry). Expose `/v1/actions/{action_id}/revert` endpoint. On revert, update `actions.user_response`, set `state="reverted"`, store a `held_back`-tagged episodic memory.

**Deliverable:** integration test: add_event → revert → calendar event deleted → memory row with `state="reverted"`.

---

### C-12 · Dry-run canary for all kinetic verbs

Set `DRY_RUN=true` in `.env`. Assert that no kinetic verb makes an outbound HTTP call. Add a CI job that runs the full test suite with `DRY_RUN=true` and asserts zero calls to any vendor URL.

**Deliverable:** CI job green.

---

### C-13 · Phase C acceptance test (end-to-end confirmation flow)

Scripted test:
1. Initiative produces a `gmail.draft_reply` action (kinetic → executes immediately, draft saved).
2. Initiative produces a `gmail.send_reply` action (kinetic-sensitive → awaiting).
3. Mock push received.
4. `/v1/confirmations/{id}/confirm` called.
5. Email "sent" (mocked). Action `state="executed"`. Memory created. Artifact row created.

All assertions in one test function.

---

## Phase D — Initiative Engine

Goal: replace `_fire_event` with scored, world-aware candidate selection. Idle is real.

---

### D-01 · `actions/deliberate.py` — deliberation Flash call

`deliberate(action, context) -> Verdict`. Builds the input pack from PRD §8.4, calls `llm.flash("deliberate_action.md", ...)`. Validates response against `Verdict` schema. Handles `None` from Gemini (treat as `proceed`). Wires into `gate.py` replacing the stub.

**Deliverable:** unit test with mocked Flash: an action where recent_audit shows 2 prior outreaches → verdict is `abort` (canned response); memory stored with tag `held_back`.

---

### D-02 · `should_deliberate()` heuristic

Implement the five conditions from PRD §8.3 as a pure function. Each condition is a separate predicate with its own unit test. The compound function is property-tested: for any `kinetic-sensitive` action, always returns `True`.

**Deliverable:** 6 unit tests (one per condition + compound).

---

### D-03 · `initiative/candidates.py` — pressure-driven candidates

`pressure_driven_candidates(inner_state) -> list[CandidateAction]`. Reads `inner_wants`, `inner_fears`, `inner_goals`, `inner_tensions` where pressure > 0.5. Maps each to 1–2 candidate actions via a lookup table (e.g., high `loneliness` want → `messages.send_text` candidate). Returns candidates with `pressure` attached.

**Deliverable:** unit test: inject a want with `pressure=0.8, tags=["loneliness"]` → at least one candidate with `tool="messages"`.

---

### D-04 · `initiative/candidates.py` — goal-driven candidates

`goal_driven_candidates(goals) -> list[CandidateAction]`. Each active goal exposes a `next_step()` function (registered at goal creation) that returns the most logical next action (e.g., a "build playlist" goal → `spotify.build_playlist` candidate). Stale goals (no progress in 14 days) get `fail_stale_goal()` called.

**Deliverable:** unit test: a "build playlist" goal with no recent actions → candidate `tool="spotify", verb="build_playlist"`.

---

### D-05 · `initiative/candidates.py` — interest-driven candidates

`interest_driven_candidates(garden) -> list[CandidateAction]`. Top-3 interests each contribute one low-pressure candidate: `web_search.search` (for research interests) or `notes.append` (for writing interests) or `spotify.like` (for music interests). Candidate `pressure` is `interest.intensity * 0.3` (always lower than pressure-driven candidates).

**Deliverable:** unit test: interest `label="marine biology", intensity=0.7` → candidate `tool="web_search"` with `pressure≈0.21`.

---

### D-06 · `initiative/candidates.py` — routine candidates

`routine_candidates(now) -> list[CandidateAction]`. Emits candidates based on time-of-day and elapsed time:
- Morning check-in (`messages.send_text`) if `08:15 ≤ now ≤ 08:45` and not yet sent today.
- Evening check-in (`messages.send_text`) if `20:45 ≤ now ≤ 21:15` and not yet sent today.
- Sleep consolidation (`self.trigger_consolidation`) if `02:45 ≤ now ≤ 03:15` and not yet run tonight.
- Weekly self-model (`self.trigger_weekly`) if Sunday and not yet run this week.

All candidates get a fixed high pressure (0.8) so they reliably win the scoring race.

**Deliverable:** unit test: time = 08:30 Sunday, none run today/this week → 3 candidates returned.

---

### D-07 · `initiative/opportunity.py` — world-opportunity Flash call

`get_opportunity_vector() -> OpportunityVector`. Makes a Flash call every 10 minutes (cached in `kv`). Inputs: time of day, `kv["last_chat_seen"]`, today's calendar events, whether Spotify is playing, user location signals (from calendar). Returns `{messages: float, spotify: float, ...}` matching `OpportunityVector` schema.

**Deliverable:** unit test with mocked Flash: verifies the result is cached and the second call within 10 minutes does not make a new LLM call.

---

### D-08 · `initiative/engine.py` — tick, score, idle

`tick()`: assembles candidate set (D-03 through D-06), fetches opportunity vector (D-07), scores each candidate using the 6-term formula from PRD §14.4, selects top-1 above threshold, runs mutex check, calls `realize(chosen)` to build an `Action`, submits to `action_gate`. If no candidate exceeds threshold: returns `idle`.

`realize(candidate) -> Action`: turns a `CandidateAction` into a full `Action` with intent, preview, auth class (from tool registry).

**Deliverable:** unit test: 10 candidates, all scores below threshold → `tick()` returns without calling the gate. Second test: one candidate above threshold → gate receives exactly one `Action`.

---

### D-09 · Shadow runner — compare old vs new engine

Wire the new engine to run alongside 1.0's `_fire_event` for 2 weeks. Both engines evaluate each tick; new engine's selected candidate is logged to `kv["shadow_decisions"]` without executing. Old engine still drives actual actions.

**Deliverable:** After 3 days of shadow runs, a `/admin/shadow` endpoint shows a side-by-side comparison of old vs new selections.

---

### D-10 · Threshold tuning + cutover

Review 2 weeks of shadow logs. Adjust `INITIATIVE_THRESHOLD` in `config.py`. Document the chosen value and the reasoning in a comment. Delete `_fire_event`, `_send_autonomous_outreach`, the shadow runner, and all dice-roll abstract activity code.

**Deliverable:** `grep -r "_fire_event" chloe/` returns no results. 7 consecutive days of production with new engine and no spurious actions in the audit feed.

---

### D-11 · `actions/budget.py` — throttle integration in scoring

Add `budget.throttle_level()` to the scoring formula: when `>0.8` daily cap used, multiply `INITIATIVE_THRESHOLD` by `1 + (throttle_level - 0.8) * 5`. At 100% cap, threshold exceeds 1.0 (effectively idle).

**Deliverable:** unit test: throttle_level=0.95 → effective threshold > 1.0 → `tick()` always returns idle.

---

## Phase E — Memory & Affect refactor ✓ Done

Goal: JSON state file gone; 4D dimensional affect; kind-mixed retrieval; anchor bonus.

---

### E-01 · One-shot migration: `chloe_state.json` → `kv` table ✓ Done

Write a script `ops/migrate_json_to_kv.py` that reads `chloe_state.json`, maps each scalar key to the correct `kv` key, and inserts via `kv.set()`. Idempotent (skips already-set keys). Deletes the JSON file on success.

**Deliverable:** run on a copy of production DB. Restart server with no JSON file present. All scalars accessible via `kv.get()`.

---

### E-02 · Migration `0004_dimensional_affect.sql` — affect_state table ✓ Done

Add `affect_state` singleton row and `affect_records` (if not already in 0001). Seed with `(valence=0.0, arousal=0.4, social_pull=0.5, openness=0.6)`.

**Deliverable:** migration applies; singleton row exists.

---

### E-03 · `affect/dims.py` — 4D state machine ✓ Done

`AfftectState` dataclass. `tick(vitals, hour, recent_records, last_chat_seen) -> AffectState`: applies the dynamics from PRD §12.2 with the stickiness probability (0.05/tick re-evaluation). `load()` / `save()` to `affect_state` table.

**Deliverable:** property test: `tick()` called 1000 times with stable inputs → final state stays within [−1,1] / [0,1] bounds.

---

### E-04 · `affect/label.py` — lazy Flash labeler ✓ Done

`get_label(affect: AffectState) -> str`. Makes a Flash call with `affect_label.md`. Caches result for 30 minutes in `kv["affect_label_cache"]`. Returns cached value on miss.

**Deliverable:** unit test: two calls within 30 minutes → one Flash call. Call 31 minutes later → second Flash call.

---

### E-05 · `affect/dims.py` — `tone_block(affect) -> str` ✓ Done

A pure function mapping the 4 dimensions to a 1–3 line tone hint appended to the chat system prompt. Replace 1.0's per-mood string lookups with calls to this function. Remove all `mood.py` imports and the 8-mood enum from the codebase.

**Deliverable:** `grep -r "mood_label" chloe/` returns no results (except in `kv` migration and audit feed for display purposes).

---

### E-06 · `memory/retrieval.py` — kind-quota composition ✓ Done

`query_mixed(rich_q, kinds_mix) -> list[Memory]`. Builds the candidate set by running separate ChromaDB queries per kind (with per-kind quotas as defaults). Combines into one list. Applies anchor bonus (+0.05 to retrieval score) for memories whose `artifact_refs[0].ref` exists in `artifact_index` with `exists_=1`.

**Prereq:** Chroma collection `memories_v2` already set up (from state/chroma.py F-03 equivalent).
**Deliverable:** integration test: 30 memories inserted (mix of kinds) → `query_mixed` returns exactly the quota-mix requested.

---

### E-07 · Memory grader Flash call — `grade_memories.md` ✓ Done

`grade(candidates, message, history, affect, keep=5) -> list[Memory]`. Flash call using `grade_memories.md`. Returns the top-K from the candidates with a per-memory relevance note. Updates the chat path to use this grader.

**Deliverable:** unit test with mocked Flash: 20 candidates → exactly 5 returned.

---

### E-08 · `memories` table: add `artifact_refs` column (if not in 0001) ✓ Done

Migration `0003_artifact_refs.sql` adding `artifact_refs JSON NOT NULL DEFAULT '[]'` and the index. Backfill: for every memory with `source="action"`, look up the action's artifact and copy the ref.

**Deliverable:** backfill script runs; `SELECT COUNT(*) FROM memories WHERE artifact_refs != '[]'` is non-zero.

---

### E-09 · Update chat path to use new memory retrieval ✓ Done

In `channels/chat_api.py`, replace the old single-kind ChromaDB query with `memory_store.query_mixed()` + `memory_store.grade()`. Update the prompt assembly to use the graded memories.

**Deliverable:** integration test: a chat turn that should recall an action memory (with `source="action"`) does so.

---

### E-10 · Memory decay daily job ✓ Done

`memory/store.py`: `decay_all()` applies `decay(weight, age_days, kind)` from PRD §11.7 to all memories. Run at 04:00 local time via the background task loop.

**Deliverable:** unit test: a 60-day-old episodic memory (half-life 60) has its weight halved by `decay_all()`.

---

## Phase F — Voice + Mobile

Goal: one realtime voice path; mobile app v1 in TestFlight; Discord optional.

---

### F-V01 · `voice/stt_whisper.py` — streaming Whisper wrapper

`transcribe_stream(audio_chunks_iter) -> AsyncIterator[str]`: loads Whisper-large-v3 (or calls Deepgram streaming API if `WHISPER_MODE=deepgram`). Emits partial transcripts as they arrive. Include 30s silence timeout.

**Deliverable:** unit test with a WAV file fixture → full transcript emitted; silence timeout fires correctly.

---

### F-V02 · `voice/tts_cartesia.py` — Cartesia streaming TTS

`synthesize_stream(text_iter) -> AsyncIterator[bytes]`: calls Cartesia streaming API with the cloned voice ID from config. Emits audio chunks. Fallback adapter `tts_elevenlabs.py` follows the same interface.

**Deliverable:** unit test with mocked Cartesia → receives audio chunks in PCM format.

---

### F-V03 · `voice/realtime.py` — full realtime pipeline

`handle_voice_session(websocket)`: receives opus audio chunks, feeds to STT stream, feeds transcripts to `chat_2_0()` (with `voice=True` flag — caps reply at 200 tokens), feeds reply tokens to TTS stream, sends audio chunks back. Handles the interrupt event: cancels STT, LLM, and TTS tasks atomically.

**Prereq:** F-V01, F-V02.
**Deliverable:** integration test with a recorded audio fixture achieves time-to-first-audio ≤ 2s on localhost (target is 1.2s on a 50ms RTT link).

---

### F-V04 · Remove Fish Speech and the Python 3.11 venv

Delete `voice/legacy.py`, `voice/pipeline.py`, the Fish Speech model files, and the 3.11 venv. Update `pyproject.toml` and `ops/bootstrap.sh`. Ensure `python -m chloe` still starts.

**Deliverable:** `grep -r "fish_speech" .` returns nothing. CI passes.

---

### F-M01 · Mobile app scaffold — React Native (Expo)

`expo init ChloeApp` in `mobile/ChloeApp/`. Configure TypeScript, ESLint, Prettier. Set up navigation (React Navigation) with 5 tabs: Chat, Confirmations, Activity, Now, Settings. No logic yet — placeholder screens only.

**Deliverable:** `expo start` runs; emulator shows 5 tabs.

---

### F-M02 · Mobile chat screen — WebSocket + history

Connect to `wss://{server}/v1/mobile/ws`. Render incoming messages as bubbles. Render Chloe's messages with an `artifact_preview` card (track title for Spotify, event title for Calendar). Send user messages over the socket. Cache last 100 messages in local SQLite (Expo SQLite).

**Deliverable:** manual UAT: send a message, receive Chloe's reply, see it rendered.

---

### F-M03 · Mobile push notification handler

Register device token with the server (`POST /v1/devices`). Handle `type="message"` push → surface as iOS/Android notification and update chat. Handle `type="confirmation"` push → navigate to Confirmations tab with the ticket pre-selected.

**Deliverable:** manual UAT: trigger a `messages.send_text` action → push notification appears on phone.

---

### F-M04 · Mobile Confirmations tab

List `GET /v1/confirmations/pending`. Each ticket shows: preview text, diff if available, Yes/No/More buttons. Tapping Yes calls `/v1/confirmations/{id}/confirm`. Tapping No calls `/v1/confirmations/{id}/deny`. Tapping More shows the full action detail.

**Deliverable:** manual UAT: `gmail.send_reply` awaiting confirmation → appears in tab → tap Yes → action executes.

---

### F-M05 · Mobile Activity tab (audit feed)

`GET /v1/audit?limit=50&offset=0`. Scrollable list. Each row: timestamp, tool icon, verb, intent, state chip (green/yellow/red/grey). "Show held back" toggle. Revert button for `kinetic` rows with a reverse verb.

**Deliverable:** manual UAT: perform 5 actions → all appear in Activity tab.

---

### F-M06 · Mobile "Now" tab (Chloe's state)

`GET /v1/state/now`. Renders: active goals with progress bars, top 3 interests with intensity bars, "she is currently…" one-liner from the latest `kv["current_activity"]`.

**Deliverable:** manual UAT: check tab → see goals and interests.

---

### F-M07 · Mobile Leash settings screen

Edit quiet hours, don't-touch lists, auth ceiling, spending cap, focus mode, away mode. Each change calls `PATCH /v1/preferences` which writes to `preferences` table.

**Deliverable:** manual UAT: set quiet hours → server `leash.violates()` correctly blocks outreach during that window.

---

### F-M08 · Mobile Account settings

Per-integration OAuth status (connected / disconnected). Revoke button calls `DELETE /v1/oauth/{service}` which clears the stored token.

**Deliverable:** manual UAT: revoke Spotify → tool returns `PermissionError`; re-auth restores.

---

### F-M09 · TestFlight submission + Discord demotion

Build iOS IPA, submit to TestFlight. Flip `DISCORD_ENABLED=false` in `config.py`. Update `channels/discord_optional.py` to check the flag before sending. Discord can still be re-enabled via the flag.

**Deliverable:** app installable via TestFlight. CI passes with `DISCORD_ENABLED=false`.

---

### F-M10 · Voice button in mobile chat

A hold-to-talk button opens a WebSocket to `/v1/voice`. Streams audio from the microphone. Displays a waveform animation while Chloe responds. Plays received audio chunks via `expo-av`. Releases button = send interrupt event.

**Deliverable:** manual UAT: hold button, speak, hear Chloe's voice response.

---

## Phase G — Kinetic-sensitive tools

Goal: email send and smart home write live, always requiring confirmation.

---

### G-01 · `tools/gmail.py` — `send_reply` verb (kinetic-sensitive)

`send_reply(draftId)` → sends the draft via Gmail API. Auth: `kinetic-sensitive`. Hard filter: if draft `to` field contains any address matching `preferences["gmail_dont_send_to"]`, gate denies without sending to confirmation channel.

**Deliverable:** integration test against a real Gmail account (sandboxed). Draft created in B → sent in G only after `/v1/confirmations/{id}/confirm`.

---

### G-02 · End-to-end email send canary

Script: (1) create a Gmail draft via `draft_reply`; (2) confirmation push received; (3) confirm; (4) `send_reply` executes; (5) verify the email appears in Gmail Sent. Run manually before promoting Phase G.

**Deliverable:** manual UAT checklist item marked done.

---

### G-03 · `tools/smart_home.py` — HomeAssistant integration

`lights(entity, state)`, `thermostat(entity, value)`, `media_player(entity, op)`, `scene(name)`. Connect via HA REST API over Tailscale. Auth: `kinetic-sensitive` for lights/thermostat/scene; `kinetic` for media_player. Safety-device blocklist (`preferences["ha_blocklist"]`) checked before any write.

**Deliverable:** dry-run returns the HA REST payload. VCR cassette test. Integration test (marked live) against a real HA instance.

---

### G-04 · HA entity allowlist + safety-device blocklist

`preferences["ha_allowlist"]` — only entities in this list can be controlled. `preferences["ha_blocklist"]` — entities explicitly blocked (locks, alarms, ovens). Gate checks allowlist at submit time; blocklist check in `smart_home.py` raises `PermissionError` before execution.

**Deliverable:** unit test: entity not in allowlist → gate denies. Entity in blocklist → PermissionError even if in allowlist.

---

### G-05 · Cost-outlier deliberation path

In `should_deliberate()`: if `action.cost_estimate.usd > 0.10`, return `True`. In `deliberate.py`: if the action is also `kinetic-sensitive`, escalate the Flash call to `llm.pro_thinking()` (small budget) instead of standard Flash.

**Deliverable:** unit test: high-cost kinetic-sensitive action → `pro_thinking` called; normal kinetic action → standard Flash.

---

### G-06 · Spending-aware tool cost estimates

Populate `cost_per_call_usd` for all tools. Add `cost_estimate` calculation to `realize(candidate) -> Action` in the initiative engine. Log `cost_usd` to `actions` table. Feed into `budget.charge()`.

**Deliverable:** after 24h in production, `budgets.today.usd` is non-zero and tracks against Gemini billing.

---

## Phase H — Procedural memory & weekly self-modeling

Goal: Chloe learns from reverts; she knows who she's been this month.

---

### H-01 · `memory/procedural.py` — weekly procedural distillation

`distill_procedural()`: query all `(action, user_response)` pairs from the last 7 days where `user_response.kind IN ("deny","revert")` or where Teo explicitly praised an action (tagged `user_praised`). Make one Flash call per 10-pair batch (≤ 3 batches). Each call returns `list[ProceduralRule]`. Store rules as `Memory(kind="procedural", ...)`.

**Deliverable:** unit test: 5 reverted calendar actions → at least 1 procedural rule involving `calendar` in its text.

---

### H-02 · Procedural memory injected into deliberation

In `deliberate.py`, before building the input pack, query `memory_store.query_mixed()` for `kind="procedural"` memories relevant to the proposed action's `tool/verb`. Inject top-3 as `procedural_hits` in the Flash prompt.

**Deliverable:** integration test: after H-01 generates a calendar procedural rule, the next deliberation for `calendar.add_reminder` receives that rule in its context.

---

### H-03 · `identity/self_model.py` — weekly Pro pass

`run_weekly_self_model()`: runs on Sundays at 03:00. Assembles the broad input pack (PRD §13.4). Calls `llm.pro_thinking("weekly_self_model.md", payload, thinking_budget=8192)`. Validates against `SelfModelOutput` schema. Writes `self_narrative_belief` → `inner_beliefs`, `next_week_intention` → `inner_goals`.

**Deliverable:** integration test with mocked Pro: `inner_beliefs` grows by 1 row, `inner_goals` grows by 1 row. The new belief has `confidence=0.5`.

---

### H-04 · Thinking-budget calibration

Run the weekly self-model on 3 past snapshots (can be replayed from DB). Compare output quality (read by Teo) at budgets 1024, 4096, 8192. Record chosen value in `WEEKLY_PARAMS` comment. Repeat for deliberation (target: feels considered, not verbose).

**Deliverable:** `WEEKLY_PARAMS["thinking_config"]["thinking_budget"]` has a comment with the calibration rationale.

---

### H-05 · Memory retention tier promoter

Daily job at 04:30: move memories older than 90 days to `archived_tier="warm"` and cluster them in batches of 10 (one Flash call per cluster → one semantic summary). Move memories older than 2 years to `archived_tier="cold"` and remove them from Chroma (SQLite row kept).

**Deliverable:** unit test: 11 memories aged 95 days → one semantic summary written; originals remain in SQLite but absent from Chroma.

---

### H-06 · `chloe rebuild-chroma` CLI command

A CLI command (via `typer`) that re-embeds all `archived_tier="hot"` and `"warm"` memories from SQLite into Chroma, in batches of 100, with a progress bar. Used for DR.

**Deliverable:** run against a test DB with 500 memories; Chroma collection count matches the expected hot+warm count.

---

### H-07 · Final Prometheus metrics + alerts wiring

Ensure all counters from PRD §22.2 are being incremented. Wire Grafana alerts (or simple email alerts): daily USD > cap, pending confirmation > 1h, DB migration failure, LLM error rate > 20% in 10min.

**Deliverable:** `GET /metrics` in production shows non-zero values for all 10 named metrics.

---

### H-08 · Phase H acceptance: procedural rule changes deliberation outcome

Reproduce the scenario from PRD §26: revert a `calendar.add_reminder` three times in two weeks. Run `distill_procedural()`. Verify the resulting procedural rule appears in the next deliberation prompt for `calendar.add_reminder`. Verify the deliberation outcome is `abort` or `revise` (not `proceed`) as a result.

This is a manual scenario run, documented and signed off in the UAT log.

**Deliverable:** the scenario is documented in `docs/RUNBOOKS.md` with actual DB IDs from the production run.

---

### H-09 · Held-back memories as identity input + verbal voice drift

Extend the weekly self-model (H-03) with two additions. First: add `HeldBackSummary` to `SelfModelInput` — count, top tools, sample notes of `held_back`-tagged memories from the last 7 and 30 days. The model produces an optional `restraint_reflection` stored as an autobiographical belief (`confidence=0.45`). Second: the model produces an optional `voice_drift_note` (one sentence) stored in `kv["voice_drift_notes"]` (capped at last 3). The character prefix assembly injects the latest note into the static block, giving Chloe a slow-moving self-observation about her own speaking style.

**Deliverable:** integration test: seed 5 held-back memories, run weekly self-model with mocked Pro → `inner_beliefs` gains a row with tag `restraint`; `kv["voice_drift_notes"]` is non-empty; character prefix logged at next startup includes the note.

---

## Cross-cutting steps (do these alongside whichever phase they first become relevant)

---

### X-01 · `tools/weather.py` — weather tool

`current(lat, lon)`, `forecast(lat, lon, days)`. Backed by Open-Meteo (no API key). Auth: `free`. Used by the initiative engine's opportunity vector.

**When:** Phase B.

---

### X-02 · `tools/maps.py` — maps tool

`find_place`, `directions`, `traffic_to(destination, depart_at)`, `commute_estimate`. Backed by Google Maps Platform. Auth: `free` / `intimate`. Used by chat and initiative.

**When:** Phase C.

---

### X-03 · `tools/code_runner.py` — sandboxed Python

`execute(language="python", code)` → `{stdout, stderr, exit_code, duration_ms}`. Sandboxed with firejail (or a single-shot Docker container). 30s wall, 256 MB RAM, 64 MB output limits.

**When:** Phase C (interest-driven curiosity actions).

---

### X-04 · `tools/self_tools.py` — Chloe's self-modification tools

`set_quiet(until)`, `set_focus(mode, until)`, `add_goal(...)`, `add_want(...)`, `update_preference(k, v)`, `archive_trait(traitId)`. Auth: `free`. Exposed to the chat model so Chloe can change her own configuration mid-conversation ("I'll mute myself for a bit").

**When:** Phase A (leash path), but wire to chat model in Phase C.

---

### X-05 · Structlog + OTel tracing across all spans

Add `@traced("span_name")` decorator to every major async function (chat path, gate, tick, reflect). Wire to the OTel endpoint in config. Add trace IDs to structlog output.

**When:** Phase B (do before the system gets complex).

---

### X-06 · `ops/bootstrap.sh` — full provisioning script

Automates the Hetzner VPS from bare Debian 12: creates the `chloe` user, directories, venv, installs dependencies, writes the systemd unit, enables Caddy, sets up the nightly backup cron, and the Chroma rebuild script.

**When:** Phase D (before cutover, the server needs to be cleanly provisionable).

---

### X-07 · Replay harness for CI

`tests/shadow/replay.py`: plays a JSON tape of 50 events over a simulated 24h (chat turns, calendar events, time-of-day transitions, weather changes). Asserts: correct number of actions executed, correct number of held-back, correct memory counts, no budget exceeded, no leash violations.

**When:** Phase D (add to CI after cutover).

---

### X-08 · Humor as seeded emergent trait + inside-joke memory

Add `humor: HumorDetection` to `ExchangeExtraction` (detected, kind, direction, inside_joke_candidate, topic). After extraction, call `record_humor_detection(kind)` which counts detections per kind in `kv`; at 4 detections within 14 days it seeds a candidate trait (e.g. `"finds dry wit charming"`, `weight=0.3`) if none exists. When `inside_joke_candidate=True`, create or reinforce a `semantic` memory tagged `joke_topic:<topic>` with a retrieval bonus applied when the same topic appears in a future query.

**When:** Phase E (alongside E-07/E-08 trait cycle work).

**Deliverable:** unit test: 4 dry-humor detections → `identity_traits` gains `"finds dry wit charming"` at weight 0.3. Inside-joke memory created on first candidate; weight bumped (not duplicated) on second.

---

### E-11 · `persons.attachment_depth` — relational depth as first-class state ✓ Done

Add `attachment_depth REAL DEFAULT 0.0` to `persons` table (range `[-1, 1]`). Extend per-chat extraction with `attachment_delta` (float, `[-0.05, 0.05]`). After each turn, apply delta via `persons/attachment.py`. Daily decay job applies silence decay after 3-day threshold. Affect model's `openness` receives a `+0.15 * attachment_depth` bias. Initiative engine scales outreach score by an attachment multiplier. Chat prompt injects a prose relationship label. Weekly self-model input includes `attachment_depth`.

**When:** Phase E (alongside affect model refactor E-03/E-04).

**Deliverable:** unit tests: positive delta increases depth; 6-day silence decreases depth; `openness` at `depth=0.8` measurably higher than at `depth=0.0`; chat prompt includes relationship prose label.

---

### E-12 · Conflict and repair arcs — rupture detection and resolution ✓ Done

Extend per-reflect synthesis output with `rupture_signal: bool` and `rupture_note: str | None`. When Haiku fires `rupture_signal`, open a `rupture` arc (`arcs` table, `kind="rupture"`, intensity set from note weight). During an active rupture: initiative threshold rises, `should_deliberate()` returns True for all kinetic actions, chat prompt includes a care note, `attachment_depth` takes the cold penalty. Repair: 3 consecutive positive `attachment_delta` turns resolve the arc and write an autobiographical memory. Fade: arc unresolved for 7 days → state `"faded"` and a different autobiographical memory.

**When:** Phase E (after E-11; depends on attachment_delta from extraction).

**Deliverable:** unit tests: `rupture_signal=True` → arc opened; 3 warm turns → arc resolved + autobiographical memory written; 8-day-old arc → faded + different memory written. Integration: active rupture → `should_deliberate()` always True.

---

## Summary table

| Phase | Steps | Primary deliverable |
|---|---|---|
| Foundations | F-01 – F-11 | Skeleton, Gemini client, schemas, DB runner, observability |
| A | A-01 – A-14 | Every outreach via gate; audit feed live; notes + web_search |
| B | B-01 – B-10 | Spotify/Gmail/Calendar reads; audit in chat prompt |
| C | C-01 – C-13 | Kinetic writes; artifact index; confirmation channel end-to-end |
| D | D-01 – D-11 | New Initiative Engine live; old event loop deleted |
| E ✓ | E-01 – E-12 | Single DB; 4D affect; attachment depth; rupture arcs; humor seeding |
| F | F-V01 – F-M10 | One voice path; mobile app in TestFlight |
| G | G-01 – G-06 | Email send + smart home with confirmation |
| H | H-01 – H-09 | Procedural memory; weekly self-modeling; held-back identity; voice drift |
| Cross-cutting | X-01 – X-08 | Weather, maps, code runner, self tools, tracing, bootstrap, replay, humor |

**Total steps: ~110**

Each step is a pull request. Each phase ends with a named acceptance test (the `-14`, `-10`, `-13` steps etc.) that gates promotion to the next phase.
