# Remaining Work — From Opus's Full Analysis

Everything in this file is a concrete, unimplemented suggestion from the pre-shipping analysis.
Items are grouped by area and ordered within each group by leverage. The 12 shipping-priority
steps are already done; these are what's left.

---

## 1. Preflight Depth

### 1a. Inbox/calendar timeout fallback note  
**File:** `chloe/channels/preflight.py` — slot resolution section  
When an `inbox` or `calendar` slot times out (600ms hard limit), the slot is silently dropped.
The LLM doesn't know it asked for something it didn't get and may hallucinate email content.

**Fix:** After resolving each network slot, if it timed out inject a short note instead of nothing:
```
*(inbox slot timed out — email content unavailable this turn)*
```
Low effort, high user-visible impact.

---

### 1b. Per-session `_last_slots` cache  
**File:** `chloe/channels/preflight.py`  
Every turn re-resolves slots from scratch. If Teo says "and what does she think about that?"
right after a `person:Zuza` slot resolved, the preflight re-resolves identically.

**Fix:** Keep a per-WebSocket-session dict `_last_slots: dict[str, str]` (slot_key → resolved_text).
On the next turn, if the preflight returns the same slot type+key, reuse the cached result.
Invalidate on session close.

---

### 1c. Capture deduplication  
**File:** `chloe/channels/preflight.py` — `_write_captures()`  
"Marco moved to Berlin" said three times across a week = three rows. No dedup check before write.

**Fix:** Before inserting a new capture, run a quick `query_fast()` over recent semantic memories
with the same tags and a high cosine threshold (>0.92). If a near-duplicate exists and was written
within 7 days, skip the write and log `preflight_capture_deduped`. Cost: ~5ms, one Chroma call.

---

### 1d. Person disambiguation  
**File:** `chloe/channels/preflight.py` — person slot resolution (~line 181)  
Resolution uses `name LIKE %x%`. "Marc" matches Marco AND Marcus. No disambiguation when both exist.

**Fix:** If the LIKE query returns more than one person, pick the one with the highest
`attachment_depth` (most relevant person). Log `preflight_person_ambiguous` with both names
so the ambiguity is visible in logs.

---

### 1e. History summarization for long sessions  
**File:** `chloe/channels/preflight.py` — `_format_history()`  
History is truncated to 6 turns × 300 chars. A 40-turn session has no episodic anchor in the
preflight — the preflight has no sense of what the last 34 turns covered.

**Fix:** If session turn count > 12, prepend a one-sentence kv-cached session summary
(`kv:session:{session_id}:summary`) that is regenerated every 10 turns via a cheap Flash call.
The preflight then sees `[Session so far: ...] + last 6 turns` rather than just 6 turns cold.

---

### 1f. Felt orientation output  
**File:** `chloe/channels/preflight.py`, `chloe/llm/prompts/preflight.md`, `chloe/llm/schemas.py`  
The preflight is purely cognitive — what to look up. There is no slot for Chloe's immediate
felt response to the message, which the witness pass has to reconstruct from scratch afterward.

**Fix:** Add an optional `felt_orientation: str | None` field to `PreflightResult`. The preflight
prompt asks for one short line: *"What is your first felt response to this message — not what you
think, what you feel?"* If non-null, prepend it above `## Specifically relevant context` as
`## First orientation`. This gives the main Flash call an emotional anchor before it reads
the 22-block context stack.

---

### 1g. Tool catalog caching  
**File:** `chloe/channels/preflight.py` — `_get_tool_catalog()` (or equivalent)  
The tool catalog (30 tools × verb descriptions) is rebuilt on every preflight call. These tokens
are wasted on every single turn.

**Fix:** Cache the tool catalog string at process startup in a module-level variable. Rebuild only
when `load_dynamic_verbs()` fires (i.e. when a new dynamic verb is defined). ~30 tokens saved
per turn, zero logic change.

---

## 2. Memory Fragment Metadata

### 2a. `last_referenced_at` + `reference_count`  
**Migration:** new `0023_memory_reference_tracking.sql`  
**File:** `chloe/memory/store.py`, `chloe/memory/retrieval.py`  
A memory surfaced 12 times and woven into 3 narrative entries is structurally different from
one untouched for 6 months. Currently indistinguishable at retrieval time.

**Fix:**
```sql
ALTER TABLE memories ADD COLUMN last_referenced_at TEXT;
ALTER TABLE memories ADD COLUMN reference_count INTEGER NOT NULL DEFAULT 0;
```
In `_batch_build_memories()` (retrieval path), bump both columns for every returned memory.
Add a small `reference_count` bonus (+0.03) to compound score in `query_fast()` when
`reference_count > 3`.

---

### 2b. `superseded_by INTEGER`  
**Migration:** add to `0023` or separate  
**File:** `chloe/memory/store.py`, `chloe/channels/preflight.py`  
When a fact gets corrected ("actually Marco moved to Munich"), both the old and new rows
rank equally in retrieval. Contradiction surfaces silently.

**Fix:**
```sql
ALTER TABLE memories ADD COLUMN superseded_by INTEGER REFERENCES memories(rowid);
```
In `_write_captures()`: if a new capture has overlapping tags and high semantic similarity
to a recent memory, write `superseded_by = old_id` on the old row. In `query_fast()`,
filter `WHERE superseded_by IS NULL` so superseded memories don't surface.

---

### 2c. `subject_person_id INTEGER`  
**Migration:** add to `0023` or separate  
**File:** `chloe/memory/store.py`  
`person:marco` in tags is searchable but not joinable. Getting all memories about Marco
requires JSON-extract gymnastics or a full-text scan.

**Fix:**
```sql
ALTER TABLE memories ADD COLUMN subject_person_id INTEGER REFERENCES persons(id);
```
In `_write_captures()`: if the capture's tags contain a `person:<name>` tag, resolve the
name to `persons.id` and write `subject_person_id`. Enables clean queries like:
`SELECT * FROM memories WHERE subject_person_id=? ORDER BY salience DESC`.

---

### 2d. Canonical tag taxonomy  
**File:** `chloe/llm/prompts/preflight.md`, `chloe/llm/prompts/extract_mentions.md`  
Tags are free-form lowercase strings. Without a controlled vocabulary, `person:marco`,
`marco`, and `re:marco` will all appear in the wild within a month.

**Fix:** Add a `## Tag taxonomy` section to both prompts listing the canonical forms:
- Person references: always `person:<lowercase_name>`
- Topic clusters: `topic:<word>` (e.g. `topic:music`, `topic:work`)
- Relationship types: `relation:<type>` (e.g. `relation:family`)
- Emotional register: `affect:<valence>` (e.g. `affect:heavy`, `affect:warm`)
- Self-referential: `self:<aspect>` (e.g. `self:goal`, `self:belief`)

No code change required — prompt-only fix.

---

## 3. LLM Prompt Quality

### 3a. `extract_mentions.md` — confidentiality on aesthetic reactions  
**File:** `chloe/llm/prompts/extract_mentions.md`, `chloe/llm/schemas.py`  
`social_mentions` has a `confidentiality` field but `aesthetic_reactions` doesn't. If Teo
shares a private emotional response to something ("that song wrecked me — don't tell anyone"),
the aesthetic reaction is logged publicly.

**Fix:** Add `confidentiality: Literal["public", "relational", "private"] = "public"` to
the `AestheticReaction` schema in `schemas.py` and a matching instruction in the prompt.
In `aesthetics.py` log path, pass the field through to the DB (or tag accordingly).

---

### 3b. Token budgets in `flash()` calls  
**File:** `chloe/llm/gemini.py` — `flash()` signature  
No prompt currently specifies `max_output_tokens`. Unbounded reflect outputs can run long
and increase cost unpredictably.

**Fix:** Add an optional `max_output_tokens: int | None = None` parameter to `flash()` and
pass it to `generate_content`. Set per-prompt budgets:
- `reflect_inner_state.md`: 600
- `reflect_signals.md`: 600
- `witness.md`: 300
- `preflight.md`: 800
- `extract_mentions.md`: 400

Document the budget in each prompt file's header.

---

### 3c. Bad/Good example pairs in key prompts  
**Files:** `chloe/llm/prompts/reflect_inner_state.md`, `reflect_signals.md`, `extract_mentions.md`, `preflight.md`  
`character_prefix.md` uses Bad/Good pairs to high effect. Most other prompts tell, don't show.

**Fix:** Add a `## What good output looks like` section to at least the four highest-volume prompts.
For `reflect_inner_state.md` the most important example is `continuity_note`:

```
Bad: "There was a meaningful conversation about connection and distance."
Good: "He said 'I don't know if you actually remember me' — and then changed the subject."
```

---

### 3d. Template variable consistency  
**Files:** all `.md` prompts  
`witness.md` was fixed (`{{ exchange }}` → `{{exchange}}`). Other prompts may still have
inconsistent spacing.

**Fix:** `grep -r '{{ ' chloe/llm/prompts/` and normalize all `{{ var }}` → `{{var}}`.

---

### 3e. Concrete voice examples in `character_prefix.md`  
**File:** `chloe/llm/prompts/character_prefix.md`  
The prefix states voice rules abstractly. Two or three concrete examples drawn from actual
voice drift notes would be far more effective.

**Fix:** After the weekly `voice_drift_note` is written, a short monthly Flash pass should
extract one "you sounded right when…" and one "you slipped into assistant-mode when…"
example from the drift log and update a `## Voice in practice` block in the prefix.
This keeps the examples grounded in real behavior rather than invented.

---

## 4. Pipeline Gaps

### 4a. Active learning from 👍/👎 reactions  
**File:** `chloe/reflect/weekly.py` — procedural distillation pass  
The frontend presumably supports thumbs reactions. Nothing flows from them into procedural
distillation. The procedural pass only sees deny/revert/praise actions.

**Fix:** Write a `reply_reactions` table (reply_id, person_id, reaction, created_at).
In the weekly procedural distillation prompt, include the last 7 days of reactions alongside
action outcomes. Instruct the model to extract rules from positive reactions ("when you said
X, Teo liked it") as well as corrections.

---

### 4b. `archive_verb` self-tool  
**File:** `chloe/tools/self_tools.py`, `chloe/tools/registry.py`  
No way to revoke a dynamic verb without a manual DB edit. `define_verb` has no inverse.

**Fix:** Add `archive_verb` verb (`auth_class="intimate"`, `reversibility=0.5`). Sets
`archived=1` on the `dynamic_verbs` row. `load_dynamic_verbs()` in `registry.py` already
filters (or should filter) `WHERE archived=0`. Add the `archived` column if missing.

---

### 4c. Dynamic verb AST safety check  
**File:** `chloe/tools/self_tools.py` — `_define_verb()` execution path  
`exec()` runs submitted verb code with access to httpx, the DB connection, and oauth tokens.
Gate deliberation mitigates abuse of new verbs but not previously-defined ones.

**Fix:** Before `exec()`, run an AST walk that raises if the code contains:
`__import__`, `eval`, `exec`, `open` (outside `fs_workspace`), `os.system`, `subprocess`.
~20 lines of stdlib `ast` walking. Block the verb definition if any are found.
```python
import ast
tree = ast.parse(code)
for node in ast.walk(tree):
    if isinstance(node, ast.Call):
        # check func name against blocklist
```

---

### 4d. Preflight capture: `parent_memory_id` grouping  
**File:** `chloe/channels/preflight.py` — `_write_captures()`  
Each preflight capture from a single turn is written as an isolated row. Multi-part
information from one message (e.g. "Marco moved to Berlin and started a new job") becomes
two unrelated memories with no structural link.

**Fix:** Generate a single `turn_batch_id` (ULID) per preflight call. Write it to a
`batch_ref TEXT` column on all captures from that turn. Not a parent/child hierarchy —
just a grouping key for retrieval auditing and dedup.

---

## 5. Live Verification Needed (not code — needs running server)

These are wired but unverified. Check these after first real test session:

- **Aesthetic reaction extraction** — watch for `aesthetic_reaction_logged` in logs. If absent after Teo shares music/writing/an idea, the threshold in `extract_mentions.md` is too conservative.
- **Unprocessed memory flagging** — `SELECT COUNT(*) FROM memories WHERE unprocessed=1` after 10+ turns. If zero, `ambiguity > 0.6 AND salience > 0.4` threshold is too tight.
- **Curiosity question trigger** — manually set an interest `intensity > 0.7` in the DB and run a tick. Confirm `interest_driven_candidates()` uses the cached question as search query.
- **Kinetic-sensitive confirm flow** — chat → gate → confirm ticket → user types "yes" → action executes. Full end-to-end, never tested with a real user.
- **Belief consistency Flash path** — `grep belief_consistency_fallback` in logs after belief writes. If it's falling back to lexical every time, the 500ms timeout is too tight.
- **TTFT under tool-hop load** — run a 2-hop tool turn and check `chat_ttft` in logs vs the ~800ms baseline claim in EXPLANATION.md.
