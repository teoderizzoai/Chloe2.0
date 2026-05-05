# Dev Log — Chloe 2.0

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
