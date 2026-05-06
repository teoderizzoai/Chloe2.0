# Dev Log — Chloe 2.0

## 2026-05-06 — Phase E complete: memory & affect refactor (E-01 → E-12)

All 12 Phase E tasks are done. 363 tests pass.

### What was built

**E-01 · `ops/migrate_json_to_kv.py`**
One-shot migration script that reads `chloe_state.json`, copies each scalar key into the `kv` table via `kv.set()` (skipping already-set keys), then deletes the JSON file. Idempotent. Run on a copy of the production DB before restarting the server without the JSON file.

**E-02 · `affect_state` table — already in `0001_init.sql`**
The 4D affect singleton row (`valence=0.0, arousal=0.4, social_pull=0.5, openness=0.6`) was seeded in migration 0001. No new migration needed.

**E-03 · `affect/dims.py` — 4D state machine**
`AffectState` dataclass with `valence` [-1,1], `arousal` [0,1], `social_pull` [0,1], `openness` [0,1]. `tick(vitals, hour, recent_records, last_chat_seen) -> AffectState` applies time-of-day dynamics, residue from affect records, mean-reversion toward baseline, and a social-pull boost from recent chat. Stickiness: 5% chance per tick to skip re-evaluation. `load()` / `save()` to `affect_state` table. Property test: 1000 ticks with stable inputs stay within all bounds.

**E-04 · `affect/label.py` — lazy Flash labeler**
`get_label(affect) -> str` calls Gemini Flash with `affect_label.md` and caches the result in `kv["affect_label_cache"]` for 30 minutes. `AffectLabelResult` Pydantic schema added to `llm/schemas.py`. `affect_label.md` prompt template added. Integration tests (real Gemini API key): first call hits the API; second call within 30 minutes returns cached value; call after 31 minutes hits the API again.

**E-05 · `tone_block(affect) -> str`**
Pure function in `affect/dims.py` mapping 4D dimensions to a 1–3 line tone hint for the system prompt. `chat_api.py` updated to call `tone_block(load())` instead of `kv.get("mood_label")` — the hardcoded mood-label lookup is gone.

**E-06 · `memory/retrieval.py` — kind-quota composition**
`Memory` dataclass. `query_mixed(rich_q, kinds_mix) -> list[Memory]` runs per-kind ChromaDB queries (default: 12 episodic + 4 semantic + 2 autobiographical + 2 procedural), combines results, removes duplicates, applies anchor bonus (+0.05 to score for memories whose first `artifact_refs[0].ref` exists in `artifact_index` with `exists_=1`), and returns sorted by score. `add_to_chroma()` helper syncs a memory to the `memories_v2` ChromaDB collection. `chloe/state/chroma.py` implemented: `get_client()` returns a `PersistentClient` if `CHROMA_PATH` is set, else `EphemeralClient`; `get_collection()` and `reset_client()`.

**E-07 · Memory grader — `grade_memories.md`**
`grade(candidates, message, history, affect, keep=5) -> list[Memory]` in `memory/store.py`. Builds a formatted candidates list and calls Gemini Flash with `grade_memories.md`. Parses the `GradeResult` response (new schema in `llm/schemas.py`) to select top-K and attach `relevance_note` to each returned Memory. Falls back to `candidates[:keep]` on LLM failure. Integration test (real Gemini API key): 20 candidates → ≤5 returned with notes.

**E-08 · `artifact_refs` — already in `0001_init.sql`**
The `artifact_refs JSON NOT NULL DEFAULT '[]'` column and index were already in the `memories` table from migration 0001. Backfill script `ops/backfill_artifact_refs.py` copies artifact refs from `artifact_index` into memories with `source='action'` and empty `artifact_refs`. Idempotent.

**E-09 · Chat path with memory retrieval**
`channels/chat_api.py` updated: `build_dynamic_suffix(person_id, message="")` now (1) uses `tone_block(load())` for affect text, (2) calls `query_mixed()` + `grade()` when a message is present and includes top-5 graded memories as a `## Relevant memories` block, and (3) injects a relationship prose label from `attachment.relationship_label()` as `## Relationship context`. Integration test (real Gemini API key): action memory queued + grader called → memory surfaces in results.

**E-10 · Memory decay daily job**
`memory/store.py`: `decay(weight, age_days, kind) -> float` applies exponential half-life decay (`weight * 0.5^(age/half_life)`). Half-lives: episodic=60d, semantic=180d, autobiographical=365d, procedural=90d. `decay_all()` updates all hot-tier memories in SQLite and returns the count updated. Unit test: 60-day episodic memory weight halved to 0.5.

**E-11 · `persons.attachment_depth` — relational depth**
Migration `0005_attachment_depth.sql` adds `attachment_depth REAL NOT NULL DEFAULT 0.0` to the `persons` table. `persons/attachment.py`: `apply_delta(person_id, delta)` applies a clamped delta ([-0.05, 0.05]) to attachment depth, clamped to [-1, 1]. `apply_silence_decay(person_id, days_since_contact)` reduces depth at 0.02/day after a 3-day silence threshold. `openness_bias(depth) -> float` returns `0.15 * depth` to add to the affect model's openness. `relationship_label(depth) -> str` returns a prose label (deeply close → warmly connected → friendly → neutral → distant → estranged). `persons/store.py` implemented with `get_attachment_depth()` / `set_attachment_depth()`.

**E-12 · Conflict and repair arcs**
Migration `0006_rupture_arcs.sql` recreates the `arcs` table with `rupture` added to the kind constraint, plus new columns `state` (active/resolved/faded), `note TEXT`, and `positive_turns_count INTEGER`. `affect/arc.py`: `open_rupture(intensity, note)` creates a rupture arc. `record_positive_turn(arc_id)` increments the counter; 3 consecutive positive turns call `_resolve_arc()` which sets state=resolved, writes an autobiographical memory, and logs. `fade_stale()` marks arcs unresolved for 7+ days as faded with a different autobiographical memory. `should_deliberate_all_kinetic()` returns True when any rupture arc is active.

### Implementation notes

- ChromaDB's `EphemeralClient` shares in-process state between instances, so tests use explicit `client.delete_collection(name)` teardown rather than `reset_client()` to isolate test collections.
- `memory/store.add()` writes to both SQLite and ChromaDB atomically (SQLite first, then Chroma); the IDs are SQLite `lastrowid` values, used as Chroma document IDs (string-cast).
- `grade_memories.md` prompt uses `{{keep}}` placeholder so the Flash call self-limits even if more IDs come back.
- E-02 and E-08 were already complete in the foundations phase (affect_state table and artifact_refs column both in 0001_init.sql). Only documentation changes needed.

### Tests

61 new test cases across 10 files (8 unit, 2 integration). All integration tests use the real Gemini API key from `.env`. 363 tests total pass.

---

## 2026-05-06 — Phase D complete: deliberation, initiative engine, shadow mode (D-01 → D-11)

All 11 Phase D tasks are done. 291 tests pass.

### What was built

**Deliberation (D-01 + D-02):** `chloe/actions/deliberate.py` — `deliberate(action, context) -> Verdict | None` makes a Flash call with a structured payload (recent audit feed, budget throttle, time, last chat) and returns a Verdict. Gate now calls deliberation before executing kinetic actions and suppresses (`held_back`) if the verdict is `abort`. `should_deliberate(action) -> bool` is a cheap sync heuristic with five conditions: kinetic-sensitive auth class, budget near cap (>75%), recent outreach spike (>2 kinetic in 1 hour), high cost estimate (>$0.10), repeated same verb (≥3 in 24 h). `deliberate_action.md` prompt template added.

**DB migration (D-01):** `0004_held_back_state.sql` — recreates the `actions` table to include `held_back` in the state CHECK constraint (SQLite doesn't support ALTER CONSTRAINT). `held_back` added to the Pydantic `State` literal.

**Pressure-driven candidates (D-03):** `chloe/initiative/candidates.py` — `pressure_driven_candidates(inner_state)` maps high-pressure wants/fears/tensions (pressure > 0.5) to `CandidateAction` objects via `PRESSURE_MAP` lookup table. Tags like `loneliness` → `messages.send_text`; `curiosity` → `web_search.search` + `notes.append`.

**Goal-driven candidates (D-04):** `goal_driven_candidates(goals)` + `chloe/initiative/goal_steps.py` — `GOAL_STEP_REGISTRY` maps goal tags to factory functions (`_playlist_next_step`, `_research_next_step`, `_writing_next_step`). Stale goals (no progress in 14+ days) trigger `fail_stale_goal()` which marks status as `stale` and writes an episodic memory.

**Interest-driven candidates (D-05):** `interest_driven_candidates(garden)` selects top-3 interests by intensity, scales pressure to `intensity * 0.3` (always ≤ 0.3, always below routine/pressure candidates). `INTEREST_TOOL_MAP` routes science/research → `web_search`, music → `spotify.like`, writing/art → `notes`.

**Routine candidates (D-06):** `routine_candidates(now)` emits time-boxed candidates with fixed `pressure=0.8`. Windows: morning check-in (08:15–08:45), evening check-in (20:45–21:15), sleep consolidation (02:45–03:45), weekly self-model (Sundays 03:00–04:00). KV guard flags (`routine:morning_sent:{today}` etc.) prevent duplicates per period. `mark_routine_done()` sets the flags after gate executes the action.

**Opportunity vector (D-07):** `chloe/initiative/opportunity.py` — `get_opportunity_vector() -> OpportunityVector` makes a Flash call and caches the result in KV for 10 minutes. Stale/failed Flash calls fall back to a time-of-day default (high messages opportunity 09:00–22:00). `OpportunityVector` Pydantic model added to `llm/schemas.py`. `opportunity_vector.md` prompt template added.

**Initiative engine (D-08 + D-11):** `chloe/initiative/engine.py` — `tick()` assembles all four candidate pools, fetches the opportunity vector, scores each candidate with the 6-term formula (`pressure × opp × recency_penalty × time_bonus × budget_headroom × affect_alignment`), takes the top-1, checks mutex, calls `gate.submit()`. `realize()` converts a `CandidateAction` to a full `Action`, looking up `auth_class` from the tool registry. `_get_threshold()` is dynamic: above 80% throttle the base threshold (`0.35`) multiplies by `1 + (throttle - 0.8) × 5`; at 100% throttle with `base ≥ 0.6` the effective threshold exceeds 1.0 making all ticks idle.

**Shadow runner (D-09):** `chloe/initiative/shadow.py` — `shadow_tick()` runs the new engine with `gate_submit` patched to a no-op, logging what it *would* have done to `kv["shadow_decisions"]` (capped at 2000 records). `chloe/admin/shadow_routes.py` — `GET /admin/shadow` returns a summary with `total_ticks`, `idle_rate`, `by_tool` breakdown and last 50 decisions. Router wired into `app.py`.

**Cutover + budget throttle (D-10 + D-11):** `config.py` gains `initiative_threshold: float = 0.35`. `loop.py` now exports `initiative_loop()` that calls `initiative_tick()` on a 60-second interval. `LLM schemas.py` gained `Verdict` (for D-01) and `OpportunityVector` (for D-07). `llm/gemini.py` got `get_client()` factory.

### Implementation notes

- `should_deliberate` is a sync function calling a sync `_audit_recent` (raw DB query returning `SimpleNamespace` objects with string `proposed_at`) to avoid async/await complications in the gate pipeline. Tests monkeypatch `_audit_recent` as a sync lambda.
- The `kv_get`/`kv_set` imports in `candidates.py` are module-level so tests can patch them with `monkeypatch.setattr("chloe.initiative.candidates.kv_get", ...)`.
- `CONSOLIDATION_WIN` extended to 02:45–03:45 so it overlaps the weekly window; both Sunday 03:30 tests (consolidation + weekly) pass.
- Hypothesis property test for `should_deliberate` uses `.filter(lambda s: bool(s.strip()))` to prevent Hypothesis from generating whitespace-only intent strings (which would fail the Pydantic `intent_not_empty` validator).
- D-10's `test_cutover.py` skips `test_no_shadow_runner` and `test_no_send_autonomous_outreach` (which require deleting `shadow.py` and the `ChloeCore._send_autonomous_outreach` stub) — those are manual cutover steps to be done after the 2-week shadow observation period completes.

### Tests

63 new test cases across 11 files. All 291 tests pass.

## 2026-05-05 — Phase C complete: write verbs, confirmation flow, push, revert (C-01 → C-13)

All 13 Phase C tasks are done. 228 tests pass.

### What was built

**Write verbs (C-01–C-05):** Added `kinetic` verbs to all tools — `SpotifyTool` (queue_track, start_playlist, like, skip, build_playlist, clear_queue), `CalendarTool` (add_event, add_reminder, decline, delete_event), `NotesTool` (truncate_append), `RemindersTool` (add, complete, list), `GmailTool` (draft_reply). Each write verb has `reversibility`, `auth_class`, and where applicable `reverse_verb`. All write verbs register artifacts in `artifact_index` via module-level `_register_artifact()`.

**Episodic memory hook (C-06):** After every successful `kinetic` action, `gate.py` creates a row in `memories` (source=`action`, tags include `["action", tool, verb]`) and writes the `becomes_memory_id` back onto the action row.

**Confirmation ticket lifecycle (C-07):** `chloe/actions/confirm.py` — `ConfirmationTicket` Pydantic model with `TICKET_TTL_MINUTES=60`, `is_stale` property. `send()` stores ticket in kv with `ticket:` prefix, updates action state to `awaiting_confirmation`, and fires a push notification. `confirm()` / `deny()` finalize the ticket; `deny()` writes a `held_back` memory. `expire_pending()` sweeps stale tickets.

**APNs push (C-08):** `chloe/channels/push_apns.py` — JWT signed with ES256 (cached 55 min), HTTP/2 to `api.push.apple.com`. Handles 410 Gone by removing stale device tokens. Payload shapes: `message` vs `confirmation`.

**FCM v1 push (C-09):** `chloe/channels/push_fcm.py` — uses `google.oauth2.service_account` to get OAuth2 bearer token, posts to FCM v1 endpoint. `chloe/channels/push.py` — `preferred_push()` dispatches to APNs or FCM by platform; `get_teo_device_info()` reads from kv `devices` key.

**Confirmation HTTP routes (C-10):** `chloe/channels/confirm_routes.py` — `POST /v1/confirmations/{id}/confirm`, `POST /v1/confirmations/{id}/deny`, `GET /v1/confirmations/pending`, `POST /v1/devices`, `DELETE /v1/devices/{token}`.

**Revert/undo route (C-11):** `chloe/channels/revert_routes.py` — `POST /v1/actions/{action_id}/revert`. Looks up `reverse_verb` on the tool verb definition, executes it, marks action `reverted`, writes a `held_back` memory.

**DB migration (C-12):** `0003_ticket_id.sql` adds `ticket_id TEXT` column to `actions`.

**Dependencies (C-13):** Added `PyJWT[cryptography]`, `google-auth`, `httpx[http2]` to `pyproject.toml`.

### Implementation notes

- `artifact_index.created_by_action` FK requires `None` (not `""`) — all call sites use `args.get("__action_id") or None`.
- `memories.id` is `INTEGER AUTOINCREMENT`, so `becomes_memory_id` is set via `cursor.lastrowid`, not a ULID.
- `ticket_id` migration has no FK (ticket IDs are ULIDs stored in kv, not in `actions`).
- `kinetic-sensitive` actions pass through the leash (auth_ceiling allows them), then hit `confirm.send()` and return `ActionResult(awaiting=True)` instead of executing immediately.
- Test suite patches `chloe.actions.gate.leash_mod.violates` to bypass quiet-hours in tests that don't test leash behaviour.

### Tests

32 new test files. All 228 tests pass.

---

## 2026-05-05 — Action layer complete (A-01 → A-08)

The full action layer is implemented and all unit tests pass. This is the foundation for every real-world side effect in 2.0: nothing fires until it passes through the gate.

### What was built

**A-01 · `0002_actions.sql`**
Migration adding four tables: `actions`, `artifact_index`, `preferences`, `budgets`. Preferences are seeded with safe defaults (`auth_ceiling = "kinetic"`, quiet hours, dont_touch lists). Budget windows (`today`, `this_hour`, `this_week`) are seeded with reset timestamps.

**A-02 · `actions/schema.py`**
Pydantic models `Action` and `ActionResult`. `Action` carries the full lifecycle — proposed state, authorization class, deliberation record, user response, artifact refs. `ActionResult` is what the gate returns to callers. `ulid()` helper generates sortable unique IDs (falls back to timestamp-prefixed UUID4 if `python-ulid` is absent).

**A-03 · `actions/audit.py`**
`append(action)` serialises an `Action` to the `actions` table. `recent(n)` and `feed_text(n)` let the LLM prompt see what Chloe recently did or tried to do.

**A-04 · `actions/leash.py`**
Pure function `violates(action, prefs, now) -> (bool, reason)`. Enforces: `auth_ceiling`, `away_mode`, `quiet_hours` (with timezone + exempt_verb patterns), `focus_mode`, and `dont_touch` lists for Gmail labels / Spotify playlists / Notes folders.

**A-05 · `actions/budget.py`**
`charge(model, usage)` updates all three budget windows and records Prometheus metrics. `exceeded_for(action)` gates against the daily USD cap. `throttle_level()` returns 0..1 for soft-throttle use. `reset_windows()` is called by a daily cron.

**A-06 · `tools/base.py` + `tools/registry.py`**
`Tool` ABC with `ToolVerb` descriptors. `ToolRegistry` singleton: registers tools, exposes `gemini_tool_declarations()` for LLM function-calling, and `async execute(tool, verb, args)`.

**A-07 · `tools/messages.py`**
First registered tool. Wraps the existing Discord DM bridge. Verbs: `send_text(body)`, `send_voice(audio_file)`. Auth class: `kinetic`.

**A-08 · `actions/gate.py`**
The single entry point for all side effects. Pipeline per action:
1. **Leash check** — suppresses and writes a held-back episodic memory if violated.
2. **Budget check** — self-aborts if the daily USD cap is exceeded.
3. **Deliberation stub** — always proceeds (D-01).
4. **Auth dispatch** — `free`/`intimate`/`kinetic` execute immediately; `kinetic-sensitive` raises `NotImplementedError` (C-07).

Both suppressed and executed actions are written to the audit table and recorded in Prometheus.

### Implementation note

`_load_prefs()` guards against SQLite returning native Python types from the `JSON`-typed column. `JSON` maps to NUMERIC affinity in SQLite (not TEXT), so numeric preference values like `spending_cap_usd_day` come back as floats rather than strings. The fix: `json.loads(v) if isinstance(v, str) else v`.

### Test coverage

All 6 gate unit tests pass, covering: free pass-through, leash suppression, DB state after suppression, held-back memory write, `kinetic-sensitive` raises, and budget exceeded self-abort. Prior A-series tests remain green.

### What's next

**B-series** — LLM integration (tick loop, tool-call parsing, deliberation).
**D-01** — real deliberation replacing the gate stub.
**Remaining tools** — notes, calendar, Gmail, Spotify, etc.

---

## 2026-05-05 — A-09 · Outreach wired through the gate

### What was built

**`chloe/chloe.py`** — `ChloeCore` stub. `_send_autonomous_outreach(person_id, message)` is the 2.0 migration of the 1.0 pattern: instead of calling `self.on_message(msg, target_id)` directly, it constructs an `Action(tool="messages", verb="send_text", authorization="kinetic")` and calls `await gate.submit(action)`. There is now exactly one path by which Chloe can send a message: through the gate.

### 1.0 audit

Grep of the 1.0 codebase (`/run/media/teo-derizzo/HDD/Chloe/`) identified three `self.on_message(...)` call sites:
- `chloe.py:1952` — activity-based autonomous outreach → **replaced by gate pattern**
- `chloe.py:2047` — `_send_autonomous_outreach()` standalone → **replaced by gate pattern**
- `chloe.py:2392` — reply to queued pending message → **kept as-is** (reactive, not initiative)

### Tests

**`tests/unit/test_no_direct_discord_send.py`**
- `test_discord_send_only_called_from_messages_tool` — AST walk of all `chloe/` files; asserts no `send_dm` or `channel.send` outside `tools/messages.py` and `discord_bot.py`.
- `test_no_direct_discord_send_in_chloe_py` — asserts `gate.submit` is present in `chloe.py` and `self.on_message` is absent.

**`tests/integration/test_outreach_via_gate.py`**
- `test_autonomous_outreach_creates_action_row` — calls `_send_autonomous_outreach` end-to-end; verifies `actions` table row has `state="executed"` and the mock send callback received the message.
- `test_gate_suppressed_outreach_not_sent` — lowers `auth_ceiling` to `"intimate"`; verifies outreach is suppressed (`state="suppressed_by_leash"`) and nothing is sent.

All 65 tests pass.

---

## 2026-05-05 — A-10 · `tools/web_search.py` — Brave Search API

### What was built

**`chloe/tools/web_search.py`** — `WebSearchTool` with three verbs, all `auth_class="free"`:

- `search(query)` — Hits the Brave Search API, returns `list[{title, url, snippet}]`. Runs a PII check via `sanitize()` before touching the network; raises `PermissionError` if the query contains a person's name, alias, or work domain from the `persons` table.
- `fetch_page(url)` — Fetches a URL with `httpx`, caps the response body at 8 KB. Rejects non-HTTP/HTTPS schemes.
- `summarize_url(url)` — Fetches via `_fetch_page`, truncates to 4 KB, then calls `GeminiClient.flash("synthesize_cluster.md", …, ClusterSynthesis)`. Wired to `GeminiClient` / `ClusterSynthesis` (stubs at this stage; will activate when B-series implements those classes).

**PII sanitizer (`sanitize`)** — pure function, reads `persons.name`, `persons.aliases`, and `persons.work_domains`. Returns `True` if safe, `False` (→ `PermissionError` at call site) if any token matches.

**`_load_persons()`** — queries `persons` table via `chloe.state.db.get_connection()`, parses JSON columns.

### Implementation notes

- `api_key` constructor arg falls back to `os.environ["BRAVE_API_KEY"]`, so the tool works with zero wiring in production.
- `summarize_url` reads `GEMINI_API_KEY` from env directly (Settings does not carry it yet).
- `respx` was not available; VCR-style tests were written using `unittest.mock` patches on `httpx.AsyncClient` instead.

### Tests — `tests/unit/test_web_search.py`

12 new tests covering: mocked Brave response → typed results, PII blocks (name / alias / domain), `PermissionError` on PII hit, `dry_run` output, 8 KB cap on `fetch_page`, invalid URL rejection, empty query, missing API key, unknown verb.

All 77 tests pass.

---

## 2026-05-05 — A-11 · `tools/notes.py` — local sandboxed notes directory

### What was built

**`chloe/tools/notes.py`** — `NotesTool` with six verbs: `read`, `append`, `create`, `list`, `move`, `revert`. All operations are confined to `CHLOE_NOTES_DIR` via `_safe_path`, which resolves the path and rejects anything that escapes the root. `append` and `revert` maintain a `.versions/` shadow directory alongside each file for rollback.

**`chloe/config.py`** — Added `chloe_notes_dir: Path` to `Settings`, defaulting to `~/chloe_notes`, overridable via `CHLOE_NOTES_DIR` env var.

### Implementation note

Version filenames use microsecond precision (`%Y%m%dT%H%M%S_%fZ`) to avoid collisions when `append` and `revert` execute within the same second — a second-precision timestamp would cause `_save_version` inside `revert` to overwrite the backup it was about to restore from.

### Tests — `tests/unit/test_notes_tool.py`

11 tests covering: create, read, append+revert round-trip, path traversal rejection, list, artifact ref on create, duplicate create fails, read missing file, move, revert with no versions, unknown verb.

All 88 tests pass.

### What's next

**B-series** — LLM integration (tick loop, tool-call parsing, deliberation).
**D-01** — real deliberation replacing the gate stub.
**Remaining tools** — calendar, Gmail, Spotify, etc.

---

## 2026-05-05 — A-12 · `tools/fs_workspace.py` — Chloe's own workspace

### What was built

**`chloe/tools/fs_workspace.py`** — `FsWorkspaceTool` with four verbs, all `auth_class="free"`:

- `read(path)` — reads a file from the workspace root.
- `write(path, text)` — writes/overwrites a file. Enforces a 10 MB per-file cap and a 1 GB total workspace cap at write time; both raise `CapExceededError` which is caught and returned as an error result.
- `list(dir?)` — lists files in a workspace subdirectory (or the root if omitted), skipping dot-files.
- `delete(path)` — removes a file. No versioning (Chloe owns these files).

**`chloe/config.py`** — Added `chloe_workspace_dir: Path` to `Settings`, defaulting to `~/chloe_workspace`, overridable via `CHLOE_WORKSPACE_DIR` env var.

### Key differences from `notes`

`fs_workspace` has no `.versions/` shadow (no revert verb), all verbs are `free` auth (no human confirmation needed), and enforces storage caps that `notes` does not.

### Tests — `tests/unit/test_fs_workspace.py`

10 tests covering: write+read round-trip, per-file cap rejection, delete removes file, path traversal rejected, list returns files, all verbs are `free` auth, read missing file, delete missing file, write returns byte count, unknown verb.

All 96 tests pass.

---

## 2026-05-05 — A-13 · Audit feed tab in admin dashboard

### What was built

**`chloe/admin/api.py`** — Two new routes on `admin_router`:

- `GET /admin/audit` — returns the last N actions (default 200, max 500) as JSON: `{count, actions[]}`. Each action object carries `id`, `tool`, `verb`, `intent`, `preview`, `state`, `authorization`, `proposed_at`, `cost_usd`, and `error`.
- `GET /admin/audit/ui` — serves a minimal single-file HTML page (monospace, dark theme) that calls `/admin/audit?limit=200` on load and every 5 seconds via `setInterval`. State values are colour-coded: green for `executed`, amber for self-aborts and leash suppressions, red for denials/failures, blue for `awaiting_confirmation`.

No backend dependencies beyond `audit.recent(n)` (A-03) which was already implemented.

### Tests — `tests/unit/test_admin_audit.py`

6 tests covering: endpoint returns 200, JSON has `count` and `actions` fields, action appended via `audit.append` appears in response, all required fields present on each item, `/admin/audit/ui` returns HTML, `limit` query param respected.

All 102 tests pass.

---

## 2026-05-05 — A-14 · Phase A integration test: all outreach in `actions`

### What was built

**`tests/integration/test_phase_a_acceptance.py`** — Phase A gate test. Replays 10 scripted events (4 × `messages.send_text`, 6 × `notes` verbs) through the real gate against a temp SQLite DB:
- Asserts every event has a row in `actions`.
- Asserts zero rows with `state="proposed"` (all resolved).
- Asserts `feed_text(10)` is non-empty and mentions at least one tool name.
- Second test: asserts the count of mock sends equals the number of scripted `messages` events (4).

**`tests/unit/test_no_bypass_gate.py`** — Static analysis gate test. Text-searches all `chloe/` Python files for `send_dm(`, `channel.send(`, and other direct Discord send patterns. Allowlist: `tools/messages.py` and `discord_bot.py`. A second function asserts `chloe.py` references `gate.submit` or `gate import`.

### Implementation note

The integration test uses `monkeypatch.setattr("chloe.actions.gate.get_registry", lambda: registry)` to inject a `ToolRegistry` wired with a `mock_send` callback and an in-temp-dir `NotesTool`, keeping the test fully self-contained without touching real filesystem paths or network.

### Tests

4 tests (2 integration, 2 unit). All 106 tests pass. Phase A is complete.

### What's next

**B-series** — LLM integration: tick loop, tool-call parsing, real deliberation (D-01).

---

## 2026-05-05 — B-04 through B-10 · Read tools, chat context, cache, PII gate

Phase B is complete. The three read tools are wired through the gate, the chat path includes the audit feed, and the PII filter is elevated to gate level.

### What was built

**B-04 · `tools/spotify.py`**
`SpotifyTool` with two read verbs (`show_currently_playing`, `show_recent_listens`), both `intimate` auth. Uses `httpx` with the stored Spotify token; handles 401 by calling `oauth_tokens.refresh("spotify")` and retrying once.

**B-05 · `tools/gmail.py`**
`GmailTool` with four verbs (`read_recent`, `read_thread`, `search`, `summarize_inbox`), all `intimate`. `read_recent` fetches metadata for the last N messages. `summarize_inbox` calls `GeminiClient.flash()` (stubbed until F-05). 401 triggers `oauth_tokens.refresh("google")`.

**B-06 · `tools/calendar.py`**
`CalendarTool` with `read_today`, `read_week`, and `find_free_slot`. The free-slot finder scans for gaps between busy intervals, working in the configured timezone (`chloe_timezone` on `Settings`, defaults to `"UTC"`). All verbs `intimate`.

**B-07 · `channels/chat_api.py`**
`build_dynamic_suffix(person_id)` assembles the per-call dynamic system suffix: `## Recent actions` (from `audit.feed_text`) and `## Current affect` (from the KV `mood_label` key). Returns a non-empty string even with an empty audit feed. `chloe/llm/prompts/chat_system.md` documents the block structure for prompt engineers.

**B-08 · `admin/api.py` + `llm/gemini.py`**
`GET /admin/cache/status` returns `{cache_name, active, refresh_interval_seconds, ttl_seconds}`. `POST /admin/cache/reset` triggers a cache refresh. `GeminiClient` and `get_cache_name()` stubbed in `llm/gemini.py` pending F-07. `registry.describe_static()` (already on the registry) will be included in cached content when F-07 lands.

**B-09 · Gate-level PII filter + `character_prefix.md`**
`_check_pii_filter(action)` in `gate.py` intercepts any `web_search.search` action whose query matches a person name, alias, or work domain from the `persons` table. Blocked queries get `state="self_aborted"`, a memory row with tags `["held_back","refusal"]`, and a `record_held_back("pii_filter")` metric. The gate check fires before the tool is called — Brave never sees the query. `chloe/llm/prompts/character_prefix.md` enumerates the five hard limits.

**B-10 · Phase B acceptance test — `tests/integration/test_phase_b_acceptance.py`**
Three tests: (1) Spotify + Gmail + Calendar reads through the real gate → all three `actions` rows have `authorization="intimate"` and `state="executed"`, zero kinetic rows; (2) `build_dynamic_suffix` after a Spotify read contains the tool name; (3) PII-blocked query → zero Brave calls, memory row with `"refusal"` tag.

### Test coverage

29 new tests across 6 unit files and 1 integration file. All 151 tests pass. Phase B complete.

### What's next

**C-series** — Confirmation flow for `kinetic-sensitive` actions.
**D-series** — Real deliberation replacing the gate stub.
**F-series** — GeminiClient, caching, loop tick.

---

## 2026-05-05 — B-03 · Admin OAuth flow for Google (Gmail + Calendar)

### What was built

**`chloe/admin/api.py`** — Two new admin endpoints added to the existing `admin_router`:

- `GET /admin/oauth/google/start` — builds the Google authorization URL with scopes for `openid`, `userinfo.profile`, `gmail.readonly`, `gmail.modify`, and `calendar.events`. Returns a redirect. Returns HTTP 500 if `GOOGLE_CLIENT_ID` is not configured. Uses `urllib.parse.urlencode` with `quote_via=quote` so spaces in the scope list are encoded as `%20`.
- `GET /admin/oauth/google/callback` — receives the authorization code, exchanges it for tokens via `https://oauth2.googleapis.com/token`, stores the encrypted token via `store_token("google", …)` (B-01), then fetches the user's display name from `/oauth2/v3/userinfo` and renders a success page. Errors return appropriate 4xx/5xx HTML responses.

`access_type=offline` and `prompt=consent` are set so a `refresh_token` is always returned.

**`chloe/config.py`** — Added `google_redirect_uri` field to `Settings`, defaulting to `http://localhost:8000/admin/oauth/google/callback`, overridable via `GOOGLE_REDIRECT_URI` env var.

### Implementation note

`gmail.send` scope is deliberately excluded from `GOOGLE_SCOPES` — it is added in Phase G (G-01) when `send_reply` goes live.

### Tests — `tests/unit/test_google_oauth.py`

5 tests covering: start redirects to Google with gmail.readonly in scope, start returns 500 when client_id missing, callback with no code returns 400, callback with error param returns 400 with error text, full success path stores token and shows display name.

All 122 tests pass.

---

## 2026-05-05 — B-01 · OAuth token storage layer

### What was built

**`chloe/state/oauth_tokens.py`** — Encrypted token storage with three public functions:

- `store(service, token_data)` — encrypts the token dict and writes it to KV under `oauth_token:<service>`. Logs only the service name, never the token values.
- `load(service)` — reads and decrypts the token; returns `None` if not stored or if decryption fails (errors are logged).
- `refresh(service)` — loads the stored token, calls the appropriate vendor endpoint (`_refresh_spotify` / `_refresh_google` via `httpx`), stores the new token, and returns it. Preserves the existing `refresh_token` if the vendor response omits it (Spotify pattern).

Encryption uses `PyNaCl` `SecretBox` (XSalsa20-Poly1305) when available, with an AES-GCM fallback via the `cryptography` library. The master key is loaded from `settings.chloe_master_key_file` (raw bytes or base64-encoded) or the `CHLOE_MASTER_KEY_INLINE` env var for development.

### Tests — `tests/unit/test_oauth_tokens.py`

4 tests covering: store→load round-trip, `load` returns `None` for unknown service, raw KV value does not contain the plaintext token, logs contain no token values.

All 112 tests pass.

---

## 2026-05-05 — B-02 · Admin OAuth flow for Spotify

### What was built

**`chloe/admin/api.py`** — Two new admin endpoints added to the existing `admin_router`:

- `GET /admin/oauth/spotify/start` — builds the Spotify authorization URL (with scopes for playback control, library, and playlists) and returns a redirect. Returns HTTP 500 if `SPOTIFY_CLIENT_ID` is not configured.
- `GET /admin/oauth/spotify/callback` — receives the authorization code, exchanges it for tokens via the Spotify API (Basic-auth header using client_id + client_secret), stores the encrypted token via `store_token("spotify", …)` (B-01), then fetches the Spotify user profile to display the display name on a success page. Errors at any stage return appropriate 4xx/5xx HTML responses.

**`chloe/config.py`** — Added `spotify_redirect_uri` field to `Settings`, defaulting to `http://localhost:8000/admin/oauth/spotify/callback`, overridable via `SPOTIFY_REDIRECT_URI` env var.

### Implementation notes

- Used `urllib.parse.urlencode` for the auth URL to properly encode all parameters (the PRD's manual string join does not encode the space-separated scopes correctly).
- Token values are passed directly to `store_token` which encrypts them; nothing token-related is logged.

### Tests — `tests/unit/test_spotify_oauth.py`

5 tests covering: start redirects to Spotify with correct client_id, start returns 500 when client_id missing, callback with no code returns 400, callback with error param returns 400 with error text, full success path stores token and shows display name.

All 117 tests pass.
