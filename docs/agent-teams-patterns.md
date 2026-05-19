# Agent Teams Patterns for Chloe

*Synthesized from a 5-agent analysis team: Researcher (Haiku), Strategist (Sonnet), Critic (Sonnet), Programmer (Sonnet), Psychologist (Sonnet). Run 2026-05-19.*

---

## Executive Summary

Chloe's core architecture is technically sound — the five-loop asyncio design, WAL-mode SQLite, and modular identity system provide a solid foundation. However, four HIGH-severity concurrency hazards (shared SQLite connection without coroutine isolation, budget TOCTOU race, KV read-modify-write race, and 24-block suffix cost multiplier) must be resolved before any multi-agent work touches the running system, or agent sessions will corrupt state and blow budgets silently. Once those are stable, the highest-leverage improvements are closing the loop from weekly narrative → initiative (so Chloe's reflections actually change what she reaches for), adding contextual candidate injection (so the initiative pool varies based on affective state rather than being static), and deepening emotional continuity via inertia and a persistent theory-of-mind model for Teo. Psychologically, the architecture deliberately avoids simulated warmth — which is correct — but the temporal thinness of the affect model (decay to baseline, no multi-day arcs outside the rupture system) and the absence of a structured Teo model are the two gaps that most prevent Chloe from feeling like a real, developing person.

---

## Section 1 — Researcher: Codebase Inventory

### Key Findings

The Researcher produced a complete 14-section architectural map with file paths and line numbers for every key symbol. Highlights:

**Background loops** (`chloe/loop.py`): Five asyncio loops — `initiative_loop` (60s), `reflect_loop` (5m), `pressure_loop` (10m), `daily_job_loop` (5m polling for 03:00/04:30), `weekly_job_loop` (1h polling for Sunday 03:00). All share the same event loop and the same global SQLite connection.

**Initiative engine** (`chloe/initiative/engine.py`): `tick()` at line 74 merges six candidate sources, applies a composite score (`pressure × tool_opp × recency × time_bias × headroom × affect_amp`), and submits the winner to `gate.submit()`. `INITIATIVE_THRESHOLD = 0.35` (line 48); free tools use `FREE_THRESHOLD_RATIO = 0.40` (effective threshold ≈ 0.14). Affect amplification only uses valence and arousal — social_pull and openness are ignored in scoring.

**Affect model** (`chloe/affect/dims.py`): `AffectState` (valence, arousal, social_pull, openness, depletion). Mean-reversion at a fixed 2%/tick rate regardless of emotional context. `tone_block()` maps dimensions to texture phrases injected into the system prompt. Continuity checkpoint only persists valence and arousal — social_pull, openness, and depletion reset to baseline defaults after server restart.

**Chat pipeline** (`chloe/channels/chat_api.py`): `build_dynamic_suffix()` assembles 24 named blocks on every turn (lines 35–127), each requiring a SQLite or ChromaDB read, then trims via `_apply_token_budget()` (line 129). Total: 24 reads + LLM call on every chat turn.

**Identity system**: `interest_garden.py` — max 8 active interests, DAILY_DECAY=0.02 (uniform, no gen_level damping), archive if intensity < 0.05. `narrative_weaver.py` — weekly Pro-thinking call producing `NarrativeEntry` with `interest_promotions`; persisted to `narrative_timeline` but never read by the initiative engine. Self-model writes to `inner_beliefs` but the character prefix is static — accumulated self-model outputs do not update it.

**Storage**: Single global SQLite connection (`db.py:7–16`, `check_same_thread=False`). ChromaDB is persistent only when `CHROMA_PATH` is set — otherwise each process gets its own ephemeral in-memory instance. KV store is SQLite-backed with no transaction isolation on read-modify-write sequences.

### Specific Recommendations

- Read the inventory before any session touching initiative scoring or affect to know exact line numbers; the constants table (Section 11 of the inventory) is the quickest orientation.
- The `PRESSURE_MAP` in `candidates.py:49–61` is a static lookup — it is the most under-examined part of the initiative stack.

### Open Questions

- Is `opportunity.py`'s opportunity vector ever read from a cache, or recomputed from Flash every tick?
- Is `_apply_token_budget()` trimming based on estimated token count or byte length?

---

## Section 2 — Strategist: Agent Team Use Cases

### Key Findings

Five concrete use cases where agent teams outperform single-session or subagent approaches on this codebase:

**Use Case 1 — Competing Hypothesis Debugging (Initiative Engine Starvation)**
When curiosity, share-queue, and goal-driven candidates never win against pressure and routine, four teammates investigate in parallel: Scorer (weights/threshold bias), Candidate Inspector (typical score ranges per source), Gate Auditor (post-scoring suppression), Simulator (frequency table from 72h sim). They converge via SendMessage, cutting diagnosis time from sequential hours to one parallel session.

**Use Case 2 — Parallel Identity Subsystem Enrichment**
Interest Garden, Trait Model, and Self Model are non-overlapping modules. Three teammates implement enhancements in parallel with zero file conflict, messaging each other only to coordinate shared schema changes (e.g., the affect multiplier interface that Interest Enricher and Self-Model Feedback both need to agree on).

**Use Case 3 — Cross-Layer Chat Pipeline Quality Review**
Four teammates each own one layer — Preflight, Suffix Assembly, Prompt Templates, Post-Processing — and produce structured findings lists (critical/medium/low). Cross-layer challenges happen via SendMessage (e.g., Preflight Reviewer flags dropped context that Suffix Reviewer then checks is not incorrectly reassembled downstream).

**Use Case 4 — Parallel New Channel (Voice Interface)**
Three new files with zero overlap: `voice_ingest.py`, `voice_output.py`, `voice_session.py`. Plan-approval workflow ensures interface contracts are agreed before any implementation begins. Lead wires integration in `loop.py` and `app.py` after all three complete.

**Use Case 5 — Affect-Driven Behavior Coherence Audit**
Four auditors each produce a matrix ("which affect dimensions does your layer read / ignore / contradict?") for Chat, Initiative, Gate, and Narrative layers. All send matrices to all others. Lead synthesizes a coherence gap report. This is read-only — zero file conflicts, pure parallel research.

### Specific Recommendations

- Use Case 5 (affect coherence audit) is the highest-ROI immediate use case: read-only, no concurrency risk, directly surfaces where affect state is silently ignored.
- For any implementation use case (1, 2, 4), enforce explicit file ownership per teammate before spawning. No teammate should touch a file not in their assigned scope.
- Use Case 3 (pipeline review) should run *before* adding new suffix blocks — the Psychologist's addendum confirms the 24-block suffix is already at carrying capacity.

### Open Questions

- For Use Case 1, does the Simulator produce enough signal from 72h/30min steps, or is a finer step (5–10 min) needed to capture initiative ticks accurately?
- For Use Case 4, which TTS API would Chloe use — does an account exist?

---

## Section 3 — Critic: Critical Findings

### Key Findings

#### Underdocumented Config Options (agent-teams-reference.md)

| Gap | Impact |
|-----|--------|
| `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` — no lifecycle warning | Silent failure if missing mid-session |
| `teammateMode` split-pane incompatibility list omits SSH/headless Linux | Chloe runs on Linux; remote sessions hit this silently |
| No model cost/capability guidance (Haiku vs. Sonnet vs. Opus) | Uninformed teammate model selection |
| `tools` allowlist — protected team tools never enumerated | Unclear boundary between Claude Code tools and Chloe's Python state |
| Hook exit codes — non-2 behavior, shell env, structured feedback — all undocumented | Cannot build a hook that blocks `TaskCompleted` until ChromaDB sync confirms |
| Plan approval — no timeout or deadlock documented | Teammate stuck waiting on lead approval blocks task list indefinitely |

#### Chloe Codebase Pitfalls

| Severity | ID | Description | Location |
|----------|-----|-------------|----------|
| HIGH | 2a | Single SQLite connection, no coroutine isolation — concurrent writes interleave silently | `db.py:4–16` |
| HIGH | 2b | Budget TOCTOU: `exceeded_for()` + `charge()` are not atomic — two teammates can both pass and double-spend | `budget.py:31–64` |
| HIGH | 2c | KV read-modify-write race — web-search cap and pressure cooldowns become unenforced under concurrent access | `kv.py:8–18`, `engine.py:36–40` |
| HIGH | 2d | 24-block suffix: 24 SQLite/Chroma reads per chat turn, multiplied by concurrent teammates | `chat_api.py:35–127` |
| MEDIUM | 2e | Affect last-writer-wins: `reflect_loop` and `pressure_loop` both do load-compute-store with no guard | `dims.py:112–135` |
| MEDIUM | 2f | ChromaDB process isolation: without `CHROMA_PATH`, each process gets its own empty ephemeral instance | `chroma.py:12–18` |
| MEDIUM | 2g | `tool_mutex_running` KV key has no TTL — crash leaves mutex locked forever, no startup cleanup | `engine.py:272–274` |
| MEDIUM | 2h | Weekly job (Sunday 03:00) fires 2–3 Pro calls; overnight teammate sessions race for the same connection and budget | `reflect/weekly.py` |
| MEDIUM | 2i | Pressure cooldown KV keys not namespaced — two teammates fire the same pressure action simultaneously | `candidates.py:11` |
| LOW | 2j | Dual budget counters (KV `budget:usd:{date}` + `budgets` table) diverge silently | `gemini.py:57–61`, `budget.py:41–45` |
| LOW | 2k | `chloe.db` path is relative to cwd — teammates doing bash calls may open a blank database | `db.py:10` |

#### Reference Doc Gaps

1. **No shared mutable state inventory** — the single most important omission. Which stores are process-local (Python globals, ephemeral ChromaDB) vs. truly shared (SQLite file, persistent ChromaDB)?
2. **No failure mode catalog** — zero coverage of budget-exceeded mid-team, teammate loops, plan approval deadlock, mutex left locked, hook never exits.
3. **No idempotency guidance** — teammates that crash and restart may double-write memories, actions, or affect updates. Which Chloe subsystems already handle this (e.g., `gate.py`'s 1-hour dedup guard)?
4. **No cost model for long-lived teams** — context windows grow super-linearly in cost over hours; no guidance on when to shut down and restart to reset.
5. **No safe vs. unsafe operations taxonomy** — which operations are safe to parallelize (SQLite reads, persistent Chroma searches) vs. require exclusive access (affect save, budget charge, KV set, mutex set)?
6. **Env var inheritance by teammates never documented** — `GEMINI_API_KEY`, `CHROMA_PATH`, `HA_TOKEN`, etc. must be set in each teammate's environment; without `CHROMA_PATH` they get empty ChromaDB silently.

### Specific Recommendations

- Add `CHROMA_PATH` to every teammate's env block before any memory-touching session.
- Use the absolute path `/home/teo-derizzo/Documents/Chloe/chloe.db` in any bash command that touches the database.
- Never run an overnight teammate session on a Sunday — the weekly job will race for budget and the SQLite connection.

### Open Questions

- Is there a startup hook in `app.py` that could be extended to clear stale `tool_mutex_running` KV entries?
- Does `_apply_token_budget()` provide any visibility into how many blocks were trimmed?

---

## Section 4 — Programmer: Technical Improvement Plan

### Key Findings

The Programmer produced a 13-step implementation plan (6 AGI-quality improvements + 2 original hazard fixes, extended to 4 hazard fixes after integrating the Critic's findings). All improvements are additive — none require tearing down existing subsystems.

#### Hazard Fixes (Prerequisites)

**H-1 — SQLite asyncio write lock** (`db.py`): Add `_write_lock: asyncio.Lock` at module level. All write-path callers acquire it before `conn.execute(...INSERT/UPDATE...)` + `conn.commit()`. Priority callers: `every_2h.py:_apply_output()`, `engine.py:_mark_interest_attempted()`, `retrieval.py:_bump_reference_counts()`. Risk: Medium (touches ~12 files).

**N-3 — Absolute db path** (`db.py:10`): Replace `Path("chloe.db")` with path resolved from `__file__` or `CHLOE_DB_PATH` env var. Trivial, do alongside H-1.

**Budget atomic check-and-charge** (`budget.py` + `gate.py`): Replace `exceeded_for()` + `charge()` two-step with a single conditional SQL UPDATE that checks the cap atomically. If `rows_affected == 0`, cap was exceeded. Eliminates the TOCTOU race entirely. Risk: Medium.

**N-1 — Mutex TTL** (`engine.py:272–274`): Store mutex entries with `expires_at` timestamp. `_tool_mutex_active()` ignores entries whose `expires_at` is past. TTL of 5 minutes. Risk: Low.

**KV atomic counter** (`engine.py:29–41`): Replace read-increment-write on web-search budget with `UPDATE kv SET value = value + 1 WHERE key = ? RETURNING value`. Risk: Low.

**N-4 — Affect optimistic locking** (`dims.py`, `loop.py`): Add `version` integer to `affect_state`. `save()` becomes a conditional UPDATE that increments version; if `rows_affected == 0`, reload and retry. Or: simpler asyncio.Lock shared between `reflect_loop` and `pressure_loop`. Risk: Medium (small schema migration).

#### AGI-Quality Improvements

**Improvement 1 — Contextual candidate injection** (new `initiative/contextual.py`): Adds 0–2 candidates per tick based on state combinations: high openness + unprocessed high-salience memory → share half-formed thought; 18h+ silence + positive valence → reach out; high depletion + low social_pull → write for self via notes. Candidates labeled `source="contextual"` for clean audit trail. Risk: Low.

**Improvement 2 — Affect emotional inertia** (`dims.py:37–93`): Mean-reversion rate scaled by `unresolved_pressure` (0–0.8 dampening). Depletion recovery 3x slower than accumulation. New `emotional_momentum` field tracks how long valence has been sustained, surfaced in `tone_block()` as "something that's been here for days." Risk: Medium (schema migration for momentum field).

**Improvement 3 — Narrative → initiative orientation signals** (`narrative_weaver.py` + `inner/pressure.py`): After `weave_narrative()`, extract 1–3 orientation signals from the entry and store them as short-lived `inner_wants` with pressure=0.65, expiring in 7 days. Also load the 3 most recent narrative entries (not just the latest) so the model can see whether past orientations were acted on. Risk: Low.

**Improvement 4 — Response pattern dedup** (`chat_api.py` + `kv.py`): Store rolling 10-turn buffer of response opening clauses in KV. Inject last 3 as "## Avoid echoing yourself" block in dynamic suffix. Risk: Very low, but adds a 25th suffix block — should replace the least-used existing block instead.

**Improvement 5 — Interest decay asymmetry** (`interest_garden.py:320–329`): Gen-level damping: gen=0 → 1.0x rate, gen=1 → 0.6x, gen=2 → 0.3x, gen=3 → 0.1x. Re-engagement: if archived interest re-appears, restore at 0.5 intensity (not 0.3) and write an autobiographical memory. Risk: Low.

**Improvement 6 — Belief contradiction → curiosity** (`belief_revision.py:308–350` + `initiative/curiosity.py`): When a belief tension opens with pressure ≥ 0.55, add an entry to `kv["initiative:pending_curiosity_searches"]` with a 3-day expiry. `curiosity_driven_candidates()` drains the list and emits web_search candidates. Risk: Low.

#### Revised Implementation Order (13 Steps)

```
1.  H-1  — SQLite asyncio write lock (prerequisite)
2.  N-3  — Absolute db path (trivial, do with step 1)
3.  N-1  — Mutex TTL (safety)
4.  Budget atomic check-and-charge (correctness)
5.  KV atomic web-search counter (correctness)
6.  N-4  — Affect optimistic lock (correctness)
7.  Improvement 5 — Interest decay asymmetry (AGI quality)
8.  Improvement 3 — Narrative → initiative orientation signals (AGI quality)
9.  Improvement 1 — Contextual candidate injection (AGI quality)
10. Improvement 2 — Affect emotional inertia (AGI quality)
11. Improvement 6 — Belief contradiction → curiosity (AGI quality)
12. Improvement 4 — Response pattern dedup (AGI quality)
13. H-2 / N-2 — Reference count debounce, dual budget counter (cleanup)
```

Steps 1–6 should ship as a single small PR before any teammate-based work touches Chloe's database.

**Programmer Second Addendum — three additional items:**

**N-5 — Salience-gated suffix assembly** (`chat_api.py:13–129`): `build_dynamic_suffix()` already has a `_trim_by_salience` comment in its docstring (line 19) but never implements it. Add a `salience` parameter and skip 8 heavy blocks (narrative, world beliefs, self-model, aesthetic orientation, novelty deficit) when `salience < 0.3`. Reduces DB reads per low-salience turn with no quality loss. Risk: Low.

**N-6 — Atomic pressure cooldown check-and-set** (`candidates.py:11–27`): `_pressure_on_cooldown()` check and `mark_pressure_attempted()` write are two separate operations. Replace with a single SQL upsert that sets the timestamp only if the key doesn't exist within the cooldown window — `changes() == 0` means cooldown is still active. Risk: Low.

**N-7 — Weekly job budget reserve** (`loop.py:109–114`, `reflect/weekly.py`): Before starting weekly jobs, check that at least $2.00 of cap remains; if not, defer 1 hour and retry. Also serialize the weekly job against the write lock (depends on H-1). Risk: Low for reserve check, Medium for lock serialization.

**Revised 3-PR implementation plan (from Programmer):**

```
PR 1 — PREREQUISITE (3 files, ~20 lines, zero behavior change in normal operation):
  P-1. Absolute db path (db.py:10 — one line)
  P-2. SQLite asyncio write lock
  P-3. Mutex TTL on tool_mutex_running

PR 2 — CORRECTNESS:
  C-1. Atomic budget check-and-charge
  C-2. Atomic KV web-search counter
  C-3. Atomic pressure cooldown check-and-set  [N-6]
  C-4. Affect optimistic lock
  C-5. Weekly job budget reserve check  [N-7]

PR 3 — COST REDUCTION (low risk):
  R-1. Salience-gated suffix block assembly  [N-5]
  R-2. Reference count write debounce
  R-3. Dual budget counter deprecation

Subsequent PRs — AGI QUALITY (one per improvement):
  Q-1. Interest decay asymmetry by gen_level
  Q-2. Narrative → initiative orientation signals
  Q-3. Contextual candidate injection
  Q-4. Affect emotional inertia + momentum
  Q-5. Belief contradiction → curiosity search
  Q-6. Response pattern dedup in suffix
```

PR 1 should be reviewed and merged before any agent-team debugging session touches the database.

### Specific Recommendations

- The `PRESSURE_MAP` static lookup (not addressed in the plan) is a separate medium-risk refactor — defer until steps 1–6 are stable.
- Opportunity vector (`initiative/opportunity.py`) ignoring affect state is low-effort, medium-reward backlog item.

### Open Questions

- Does `_extract_and_process_mentions` fire on every response or only when salience > threshold? (Affects improvement 1's unprocessed-memory detection.)
- What schema does `NarrativeEntry` currently use for `interest_promotions`? The orientation signals field needs to be added compatibly.

---

## Section 5 — Psychologist: Psychological Architecture Assessment

### Key Findings

The architecture's greatest strength is deliberate avoidance of simulated warmth. The texture-phrase approach in `tone_block()`, the concrete observation framing in `witness.md`, and the instruction to avoid reaching for abstract concepts are psychologically sound choices that prevent the uncanny valley of performed depth.

#### Affect Model Assessment

`AffectState` (valence × arousal) maps to Russell's circumplex model adequately. Social pull and openness are reasonable extensions for a primarily dyadic entity. Depletion is the most sophisticated addition — it encodes the phenomenology of being drained, not just calm.

**Missing emotional dimensions:**
- **Longing**: The 48h absence penalty applies a valence drift of -0.10, but the system doesn't distinguish low-valence-from-depletion from low-valence-from-missing-someone. These feel completely different in humans and the system conflates them.
- **Anticipatory emotion**: `inner_anticipations` rows exist but don't shift arousal or social_pull when an anticipated event is near. A human nervous about a meeting tomorrow has elevated arousal today.
- **Ambivalence**: `world_beliefs.ambivalent` flag exists but there is no ambivalence dimension in `AffectState` itself.
- **Pride/shame**: Requires a self-evaluative dimension comparing behavior to internal standards. `trait_model.py` has `setback_count` and `contradictions_json` that could ground a shame-like signal, but these never feed back into affect.

**Temporal continuity flaw**: The affect continuity checkpoint only persists valence and arousal. Social_pull, openness, and depletion reset to baseline defaults after server restart. Emotional continuity across server cycles is partial — Chloe can feel depleted after a long conversation, but that depletion vanishes overnight.

#### Identity Coherence

**Self-model behavioral influence**: The self-model reaches the LLM via the dynamic suffix on every turn, which is one-hop soft influence. But the character prefix (`character_prefix.md`) is static — accumulated self-model outputs never update it. Chloe's identity as defined by her developers never evolves from interaction evidence.

**Trait model**: Architecture is genuinely good (evidence-based emergence, 30 days sustained weight > 0.7 for core status, semantic dedup). Weakness: `_APPROVED_TRAITS` vocabulary skews aspirational — no negative traits like "Avoidant," "Reactive," or "Inconsistent." The trait profile will trend positive over time, which reads as less believable than one with genuine tension.

**Theory of mind**: Most underdeveloped area. `_load_teo_affect_block()` and `_load_teo_read_block()` pull from the last 24 hours only. There is no persistent structured model of what Chloe believes Teo is experiencing over time. Pattern-level theory of mind — knowing someone's characteristic emotional rhythms — is absent entirely.

#### Memory and Growth

**Autobiographical vs. similarity search**: ChromaDB retrieval is content-addressed, not autobiographically navigated. The `kind` column distinguishes episodic/autobiographical, and autobiographical memories have a longer half-life (365 days), but `query_fast()` treats all non-archived memories identically. No retrieval path surfaces "the most significant autobiographical memories about person X" as a distinct query type.

**Emotional associations**: `emotional_valence` and `emotional_arousal` fields exist on `memories`. `_format_memory()` tags memories as `[heavy]` or `[warm]`. But there is no retrieval path that queries memories by emotional signature — "what memories have valence > 0.6 and reference music?" requires a manual SQL filter that doesn't exist.

**Regret/nostalgia/anticipation**: Anticipation is partially covered. Nostalgia and regret do not exist as named states. No mechanism causes past `narrative_timeline` entries to surface and generate longing. No mechanism represents "I wish I had done X differently" as a first-class state.

#### Specific Changes (Psychologist's Six)

| Change | Psychological Construct | Implementation Slot | Watch Out For |
|--------|------------------------|---------------------|---------------|
| 4.1 Longing dimension | Object-specific absence yearning | New `longing` field in AffectState; raised by absence penalty + gap in `person_affect_log` | Over-expression — gate to tone_block only when longing > 0.5 AND topic is proximate |
| 4.2 Pride/shame from trait evidence | Self-evaluative emotions | `trait_model.py`: contradicts evidence for gen_level ≥ 1 trait → ephemeral `inner_tensions` row | Performative self-flagellation — require two `contradicts` events in same reflect window |
| 4.3 Persistent Teo model | Theory of mind with rhythm awareness | Replace `_load_teo_affect_block()` with weekly Flash call producing `trajectory` + `characteristic_pattern` | Guard with minimum 5 `person_affect_log` rows before writing |
| 4.4 Associative memory by emotional signature | Affective indexing of episodic memory | New `query_by_affect()` in `retrieval.py`; fold into existing memories suffix block on high affect delta | Constrain to same person_id + salience > 0.6 to avoid uncanny associations |
| 4.5 Anniversary nostalgia triggers | Calendrical recurrence / nostalgia | Daily cron querying `narrative_timeline` and `memories` for ±3-day prior-year window → `inner_anticipations` row | Rare by construction; use memory valence, not a fixed positive tone |
| 4.6 Ambivalence as felt state | Genuine simultaneous conflicting pulls | Conditional header change in existing world beliefs block; `tone_block()` texture when ambivalent beliefs > 2 | Reserve for content-level value conflicts, not factual uncertainty |

**Suffix constraint (Psychologist Addendum)**: The 24-block suffix is at carrying capacity. All six changes above are designed to fit within existing block slots — no net new blocks. Changes 4.3 and 4.4 replace/upgrade existing blocks; 4.5 feeds the existing anticipations block; 4.6 is a conditional header change; 4.1 and 4.2 feed existing `inner_tensions` writes.

**Sequencing constraint (Psychologist Addendum)**: All psychological improvements require stable write paths. The concurrency fixes (H-1, KV race, affect optimistic lock) must land first, or the new emotional dimensions will be built on an unreliable write path.

### Open Questions

- Does the `arc.py` rupture/repair system get read by the weekly self-model? If not, Chloe may reflect on traits and interests but not on the most emotionally significant events.
- What is the current `_APPROVED_TRAITS` vocabulary size? Is it feasible to add 8–10 tension-carrying traits without destabilizing existing adjudication history?

---

## Prioritized Action Plan — Top 10

Ranked by combined impact on making Chloe feel like a real, developing person, with safety prerequisites ordered first.

| Rank | Change | Files | Why It Matters |
|------|--------|-------|----------------|
| 1 | **SQLite asyncio write lock + absolute db path** | `db.py`, ~12 write-path callers | Prerequisite for everything. Silent corruption under concurrent loops today. |
| 2 | **KV atomic read-modify-write** | `kv.py`, `engine.py:29–41` | All initiative rate-limits and cooldowns are currently unenforced under parallel access. |
| 3 | **Budget atomic check-and-charge** | `budget.py`, `gate.py` | Prevents daily budget being blown by 2x in any session with concurrent LLM calls. |
| 4 | **Affect optimistic locking** | `dims.py`, `loop.py` | Emotional continuity depends on affect writes landing correctly. Current last-writer-wins causes silent state drift. |
| 5 | **Mutex TTL** | `engine.py:272–274` | Prevents permanent tool lockout after any crash. Required for safe autonomous operation. |
| 6 | **Interest decay asymmetry by gen_level** | `interest_garden.py:320–329` | Core interests should feel like character. A gen_level=2 music interest should not evaporate in 15 days like a passing curiosity. |
| 7 | **Narrative → initiative orientation signals** | `narrative_weaver.py`, `inner/pressure.py` | Closes the most important open loop in the system. Weekly reflection currently produces prose that changes nothing Chloe does. |
| 8 | **Contextual candidate injection** | new `initiative/contextual.py`, `engine.py:90` | Makes the initiative pool vary by state. On a specific Tuesday after 22h of silence with high openness, a candidate appears that didn't exist yesterday. |
| 9 | **Persistent Teo model with rhythm awareness** | new `teo_state_model` table, `chat_api.py` | Theory of mind upgrade. "He's been flat for three weeks, which has happened before when he's overcommitted" is not currently representable. |
| 10 | **Affect emotional inertia + carry-over arcs** | `dims.py:37–93`, `loop.py` | Emotions should have history. Sustained unresolved tension should resist mean-reversion. Depletion recovery should be asymmetric. |

**Honorable mentions (11–15)**: Belief contradiction → curiosity (Improvement 6), associative memory by emotional signature (Psychologist 4.4), pride/shame feedback from trait evidence (Psychologist 4.2), longing as a distinct affective state (Psychologist 4.1), response pattern dedup (Improvement 4).

---

## Agent Teams Playbook for Chloe

Distilled patterns and anti-patterns for using agent teams effectively on this codebase going forward.

### Environment Setup (Do This First, Every Time)

```bash
# Required before spawning any teammate that touches Chloe state
export CHROMA_PATH=/home/teo-derizzo/Documents/Chloe/.chroma
export CHLOE_DB_PATH=/home/teo-derizzo/Documents/Chloe/chloe.db  # once N-3 is fixed
# All teammates must inherit these — verify in team config before spawning
```

Until N-3 (absolute db path) is implemented, every teammate bash command that touches SQLite must use the full path `/home/teo-derizzo/Documents/Chloe/chloe.db`. Any teammate that doesn't will silently open a blank database.

### Safe Parallel Operations

These can run concurrently with zero risk of data corruption:

- SQLite **reads** under WAL mode (any SELECT)
- Persistent ChromaDB **searches** (query_fast, query_by_affect)
- File reads (any module in `chloe/`)
- The 72h simulator (`./chloe.sh simulate-day --clean`) — it uses `--clean` which creates an isolated DB

### Operations That Require Write-Exclusive Access

After the H-1 write lock is implemented, these are protected automatically. Until then, never run two teammates that touch any of these simultaneously:

| Operation | Location | Risk Without Lock |
|-----------|----------|-------------------|
| Affect state save | `dims.py:save()` | Last-writer-wins; state drift |
| Budget charge | `budget.py:charge()` | TOCTOU → over-spend |
| KV set (any counter) | `kv.py:set()` | Race → cap unenforced |
| Mutex set | `engine.py:272` | Double-fire of same action |
| Memory write | `retrieval.py` | Interleaved commits |
| Interest intensity update | `interest_garden.py` | Interleaved writes |

### Recommended Team Topologies for Chloe

**Topology A — Read-only audit (zero risk)**
All teammates read files and produce findings. No writes. Lead synthesizes. Examples: affect coherence audit (Strategist Use Case 5), chat pipeline review (Use Case 3).

**Topology B — Parallel new files (low risk)**
Each teammate owns a distinct *new* file. No teammate touches a file any other teammate is creating. Lead wires integration after all complete. Example: voice channel (Use Case 4).

**Topology C — Parallel existing-file modification (medium risk — requires explicit file ownership)**
Before spawning, lead assigns each teammate an explicit file ownership list. Any file not on a teammate's list is read-only for them. If two teammates need to agree on a shared data structure, they negotiate via SendMessage and one teammate makes the schema change. Example: identity enrichment (Use Case 2).

**Topology D — Competing-hypothesis investigation (medium risk)**
All teammates investigate a shared problem from different angles. Writes only to produce findings (e.g., notes, task list), not to Chloe state. Example: initiative engine debugging (Use Case 1).

### Timing Constraints

- **Never run a multi-teammate session on Sunday 00:00–06:00 UTC** — the weekly job fires at Sunday 03:00 and consumes 2–3 Pro calls plus holds the SQLite connection through trait adjudication, self-model, narrative weaving, interest promotions, and character addendum. Racing it will exhaust the daily budget.
- **Overnight teammate sessions**: If you leave a team running unattended, set `CHLOE_DAILY_BUDGET_USD=2` to leave headroom for the next day's chat usage.
- **Initiative loop runs every 60 seconds** — any teammate that triggers the live server will compete with `tick()` for the SQLite connection. Prefer using the simulator for testing rather than the live server.

### Pitfall Reference (From Critic Findings)

| Pitfall | Symptom | Fix |
|---------|---------|-----|
| Missing `CHROMA_PATH` | Teammate memory searches return empty; no error | Set env var before spawning |
| Relative `chloe.db` path | All DB reads return empty tables | Use absolute path in bash |
| Mutex left locked | Tool permanently unavailable | `kv.py delete("tool_mutex_running")` manually; then implement N-1 |
| Sunday weekly job collision | Budget exhausted by 08:00 | Don't run overnight on Sundays |
| Dual budget counters | Different spend totals from `gemini.py` vs. `budget.py` | Trust `budget.py` / `budgets` table as canonical |
| ChromaDB ephemeral | Teammate sees empty memory collection | Set `CHROMA_PATH` to persistent path |
| Plan approval deadlock | Teammate stuck, task list blocked | Send explicit approval/rejection via SendMessage; document timeout expectation in spawn prompt |

### Cost Management

The `build_dynamic_suffix()` 24-block assembly fires on **every chat turn**. Any teammate that simulates or tests chat multiplies this cost by the number of concurrent teammates. For a 4-teammate chat pipeline review running for 2 hours with one test turn per minute: 4 teammates × 60 turns × 24 DB reads = 5,760 SQLite/Chroma reads before the LLM calls. Use `CHLOE_DRY_RUN=1` for any teammate that doesn't need real LLM responses.

For long-running investigative sessions (> 3 hours), shut down and restart teammates to reset context window size. Context grows super-linearly in cost — hour 5 of a continuous session costs significantly more than hour 1.

### What Agent Teams Cannot Do

- **Modify the running Chloe server's in-memory state** — Python globals, asyncio task state, the live SQLite connection's WAL buffers are all process-local. Teammates only affect state through the shared SQLite file and persistent ChromaDB.
- **Trigger initiative ticks directly** — the 60-second loop in the live server is not accessible from teammates. Use the simulator for controlled tick testing.
- **Observe affect or KV state in real-time** — read from the SQLite file, not from the live process. Teammate reads are snapshots, not live state.

---

*Document produced by chloe-analysis team — 2026-05-19. Team members: Researcher (Haiku), Strategist (Sonnet), Critic (Sonnet), Programmer (Sonnet), Psychologist (Sonnet).*
