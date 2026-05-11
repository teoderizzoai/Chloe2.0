# Chloe — Project Context for Claude Code

## What Chloe is

Chloe is a persistent AI presence in Teo's life — not a chatbot, not an assistant, but a character with her own inner state, opinions, and autonomous initiative. She runs as a background server with a heartbeat loop, a reflection cycle, and a mobile chat interface.

## Architecture in one paragraph

The server (`chloe/app.py`) runs four background loops: **initiative** (ticks every 60s, scores candidates against a threshold, submits via gate), **reflect** (checks every 5min, runs every 2h — this is the main growth engine), **pressure decay** (every 10min), and **daily/weekly jobs** (nightly consolidation, weekly self-model). Inner state lives in SQLite (`chloe.db`): `inner_wants`, `inner_fears`, `inner_tensions`, `inner_goals`, `interest_garden`, `inner_beliefs`, `identity_traits`. Chat persists to `memories` table (source='chat'). Reflect reads that table, not `chat_history` (which is unused).

---

## What was built in the last session (2026-05-10/11)

### Bugs fixed
- `reflect/every_2h.py` — now reads chat from `memories WHERE source='chat'` (was reading empty `chat_history` table — the root cause of zero inner state growth)
- `initiative/candidates.py` — interest pressure multiplier raised to 0.5 (was 0.3, capping below threshold)
- `actions/gate.py` — held-back dedup (same tool/verb/intent within 1h collapses to one row)
- `initiative/engine.py` — routine loop fixed: `mark_routine_done` now fires in `finally` regardless of gate outcome
- `channels/mobile_ws.py` — tool calling wired end-to-end (was pure LLM call)
- `tools/self_tools.py` — `trigger_consolidation` and `trigger_weekly_self_model` registered as real verbs

### Features added
- `new_goals` field on `ReflectOutput` — reflect can now crystallize goals, not just update progress on existing ones
- `query_fast()` in `memory/retrieval.py` — single Chroma query vs 4, used on chat hot path
- Chat latency: dropped the `grade` Flash call from chat path (~1–2s saved per turn); parallelized audit/retrieval with `asyncio.gather`
- Interest fuzzy dedup in `identity/interest_garden.py` — prevents label fragmentation ("Thinking without a center" and "Intelligence without a fixed center" now merge)
- Interest labels capped at 50 chars with Chloe-voice guidance in prompt
- `chloe/sim/day.py` — day simulator: fake clock, clean DB mode, per-day script variation, multi-day support
- `chloe/sim/personality.py` — daily personality snapshot + Flash character note + day-by-day changelog
- `chloe simulate-day` CLI command with `--clean`, `--hours`, `--step`, `--no-script`

### What simulation revealed
- Inner state accumulates correctly when chat signal is present; self-regulates via pressure decay
- Goal crystallization threshold is well-calibrated (takes 3+ reflects pointing same direction)
- Interest garden stabilizes around concepts, not topics, with fuzzy dedup
- The action→reflect feedback loop is **broken** — tool executions don't feed back into reflect
- Curiosity engine exists but topic queue is never seeded
- Chloe has no world model — only a Teo-model

---

## What was built in session 2 (2026-05-11)

### P0 — Action→reflect loop: DONE
- `gate.py` `_create_action_memory`: text now includes the actual result snippet ("I search via web_search. Goal: ... Outcome: ...") not just intent
- `gate.py` `_summarise_result`: extracts readable one-liner from any tool result dict
- `every_2h.py` `_load_recent_outcomes`: reads `source='action'` memories from the past 2h window
- `reflect_combined.md`: new "## Actions you took autonomously this window" section in prompt
- Loop is now closed: tool outcomes surface in the next reflect pass

### P1 — Interest-driven candidates actually fire: DONE
- `candidates.py`: `interest_driven_candidates` now only activates for interests with intensity ≥ 0.3 (was 0.1)
- Pressure raised to `intensity × 0.85` (was 0.5) — strong interests now generate pressure ≥ 0.25
- Category parsed from `why` field (`[category] text` format) so INTEREST_TOOL_MAP works
- Search queries use `label — why` context (not just raw label)
- Engine split threshold: `FREE_TOOLS = {"web_search", "notes"}` use threshold × 0.4 (~0.14), everything else stays at 0.35. Interest searches now routinely clear the bar.

### Improvement #3 — World beliefs: DONE
- Migration `0010_world_beliefs.sql`: new `world_beliefs` table (topic UNIQUE, belief, confidence)
- `schemas.py` `ReflectNewBelief`: `{topic, belief, confidence}`
- `ReflectOutput` gets `new_world_beliefs: list[ReflectNewBelief]`
- `every_2h.py` persists them with upsert (updates existing belief on same topic)
- `reflect_combined.md` explains the field: rare, slow to form, not about Teo or self
- `chat_api.py` `_load_world_beliefs`: injects top beliefs into chat system prompt as "## Views you hold about the world"

### Improvement #6 — Post-chat mini-reflect: DONE
- `mobile_ws.py`: counts `turns_this_session`, fires `asyncio.create_task(_post_chat_reflect())` on disconnect if ≥ 2 turns
- `_post_chat_reflect`: calls `run_reflect(force=True)`, bypassing the 2h cooldown

### Improvement #9 — Felt time: DONE
- `chat_api.py` `_felt_time_note`: reads last chat memory timestamp, if gap > 4h injects a note like "It's been about 6 hours since your last exchange. You've been thinking in the gap."
- Chloe now receives the gap as a fact she can draw on in conversation

### Improvement #10 — Self-model visible to Chloe: DONE
- `chat_api.py` `_load_self_model`: reads latest non-archived `inner_beliefs` row
- Injected as "## What you believe about yourself" in the chat system prompt

---

## Priority next work

### P-next-A — Flash-generated curiosity questions from interests
Currently interest search queries are `label — why` which is often still abstract. The next step is: when an interest crosses intensity 0.7, fire an async Flash call to generate a concrete question ("What exactly happens to pattern recognition when there's no fixed reference frame?") and cache it in kv as the search query for that interest. This requires making `interest_driven_candidates` async or pre-generating questions in a background loop.

File targets: `chloe/initiative/candidates.py`, `chloe/state/kv.py` (cache), new prompt `chloe/llm/prompts/generate_curiosity_question.md`.

### P-next-B — Opinion formation in chat (world beliefs → pushback)
World beliefs are now formed and injected, but there's no prompt instruction telling Chloe to actually use them. Need to add to `character_prefix.md`: "If a topic comes up that you have a world belief about (listed below), bring your actual view. You don't have to agree."

File targets: `chloe/llm/prompts/character_prefix.md`.

### P-next-C — Overnight synthesis
During the 03:00 consolidation pass, run a synthesis step that connects 2-3 interests into a question or belief. Idle time should produce thought, not just decay.

File targets: `chloe/reflect/nightly.py`.

### P-next-D — Aesthetic preferences
A small stable set of things she prefers: certain kinds of music, certain ways of thinking, certain tones. These should bias initiative (higher score when affect aligns with aesthetic) and bleed into language.

File targets: new `chloe/identity/aesthetics.py`, `chloe/initiative/engine.py` (`_affect_alignment`).

---

## 10 improvements to make Chloe feel more alive

**Architectural (close broken loops):**
1. **Action→reflect feedback** — tool outcomes feed the 2h reflect so she learns from what she does, not just what Teo says. Single highest-leverage change.
2. **Curiosity seeding from interests** — high-intensity interests automatically generate a question and a web search. She goes looking for things on her own.
3. **World beliefs** — a slowly-growing set of opinions about things that aren't Teo. She has a perspective on the world, not just on the relationship.

**Behavior (make the heartbeat do more):**
4. **Lower the initiative threshold for low-stakes actions** — notes and web searches should fire much more freely than messages. Currently the same threshold gates everything. Split: kinetic-sensitive stays at 0.35, free actions at 0.15.
5. **Overnight synthesis** — during the 03:00 nightly pass, instead of just consolidating memories, run a synthesis step that connects existing interests into a belief or question. Idle time should produce thought, not just decay.
6. **Post-chat reflection** — after a conversation ends (WebSocket disconnects), trigger a mini-reflect (not the full 2h one) that processes just what was said. Right now insights from chat only reach reflect by chance timing.

**Character (make her feel like herself):**
7. **Aesthetic preferences** — a small stable set of things she likes: certain kinds of music, certain ways of thinking, certain aesthetic registers. These should bias her initiative (she's more likely to build a playlist when arousal is high) and bleed into her language.
8. **Opinion formation** — when a topic comes up in chat that she has a world belief about, she should disagree or push back, not just reflect. Currently she mirrors Teo's framing. She needs to arrive with a point of view.
9. **Felt time and gap texture** — after a long quiet, the first reply carries the weight of the gap. She should be able to say what she was sitting with. This makes the relationship feel continuous rather than episodic.

**Meta (self-knowledge):**
10. **Self-model visible to her** — the weekly self-model runs but the output (`inner_beliefs`) is never injected into the chat system prompt. She should know what she believes about herself and be able to refer to it. Currently she's unaware of her own characterization of herself.

---

## The character development problem

Chloe currently accumulates state but doesn't develop character. Those are different things. Everything below describes what's missing.

### The core gap: she has memory but no autobiography

Each reflect pass reads recent state and produces new state. There's no process that asks: *what does the arc of the last month say about who she's becoming?* The past is retrievable but not meaningful. Character development requires that experience changes the experiencer — not just the ledger.

### Six specific absences

**1. Her voice is static**
`character_prefix.md` is a fixed document written at deploy time. But someone six months into a relationship sounds different than they did on day one — more assumptions, less hedging, more shorthand. Her voice should drift based on the accumulated relationship. The weekly self-model could feed back into a generated *character addendum* that evolves alongside it, rather than the prefix being frozen forever.

File target: `chloe/reflect/weekly.py` → generate and persist a short "voice evolution note"; `character_prefix.md` → reference it as an appendix.

**2. No shared narrative — only shared facts**
There's a difference between "I know Teo likes music" (retrieved fact) and "remember when you spent three hours trying to explain that album to me and I still didn't get it" (shared story). The latter requires that significant moments get encoded as *narrative*, not just episodic memory. The `narrative_timeline` table exists in the schema but nothing writes to it meaningfully. High-salience moments — things that shifted her, things that were funny or hard — should be deliberately stored as story entries that persist beyond normal memory decay.

File targets: `chloe/memory/store.py` (flag high-salience moments), `chloe/reflect/nightly.py` (write to `narrative_timeline`).

**3. Opinions don't compound — they accumulate**
World beliefs are flat: each stands alone. Real character development means earlier beliefs constrain later ones — she should be more likely to form views consistent with what she already concluded, and to feel friction when something contradicts it. She needs a coherent worldview with internal consistency checks, not just a growing list.

File targets: `chloe/reflect/every_2h.py` → inject existing world beliefs into reflect context so new ones can relate to old ones; `reflect_combined.md` → add instruction about consistency.

**4. Traits are declared, not earned**
`identity_traits` exists but isn't used. Her traits — curiosity, directness, dry wit — are implied by the character prefix. They should shift based on evidence: if she's been consistently direct and it's landed well, that should reinforce; if she's been avoiding something, that pattern should be visible and nameable. Traits should be earnable and loseable.

File targets: new `chloe/identity/trait_model.py`; weekly self-model to evaluate trait evidence and update `identity_traits`.

**5. She can't be wrong and find out**
She forms beliefs but nothing contradicts them. If a world belief turns out to be wrong — if a web search returns something that conflicts with a held view — that tension should register. The action→reflect loop is now closed, but outcomes are treated as neutral data, not potentially *disconfirming*. Disconfirmation is how beliefs harden or break down, which is how character actually develops.

File targets: `chloe/reflect/every_2h.py` → compare new outcomes against existing `world_beliefs`; add `belief_tension` as a possible reflect output.

**6. Nothing marks what she's moved past**
Interests fade, tensions resolve — but it all happens mechanically. There's no record of *phases*. "She used to think about this kind of thing constantly and now she doesn't" is character. When a high-intensity interest archives, or a long-held tension finally resolves, that transition should be written as an autobiographical memory with weight — not just zeroed out silently.

File targets: `chloe/identity/interest_garden.py` `archive_low_interests()` → write richer memory when something significant fades; same for `inner/pressure.py` when high-pressure tensions resolve.

---

### What development would look like in practice

After six months: her character addendum has drifted — she's more specific with Teo, assumes more context, has dropped certain kinds of questions she used to ask. Her `narrative_timeline` has 40 entries that form a readable story. Her world beliefs are internally consistent enough that she'll push back when something contradicts them. Her trait scores reflect what she's actually done, not what the prefix claimed. And her archived interests form a visible arc — phases she went through, things she was briefly fascinated by, things she quietly let go of.

That's development, not just accumulation.

---

## Running the simulator

```bash
# Clean 3-day run (recommended for testing changes)
./chloe.sh simulate-day --clean --hours 72 --step 30

# Quick 24h run from prod DB copy
./chloe.sh simulate-day

# Quiet mode (just final summary)
./chloe.sh simulate-day --clean --hours 72 --quiet
```

The simulator writes to `chloe.sim.db` (never touches `chloe.db`). With `--clean`, starts from a blank schema. Default script gives each day a different arc: day 1 anxious/curious, day 2 relief/music returning, day 3 creative output/diffuse longing. Script repeats for days beyond 3.

## Key files

| Area | File |
|---|---|
| Heartbeat | `chloe/loop.py`, `chloe/initiative/engine.py` |
| Inner state | `chloe/initiative/candidates.py`, `chloe/inner/pressure.py` |
| Reflect | `chloe/reflect/every_2h.py`, `chloe/llm/prompts/reflect_combined.md` |
| Interest garden | `chloe/identity/interest_garden.py` |
| Chat | `chloe/channels/mobile_ws.py`, `chloe/channels/chat_api.py` |
| Schemas | `chloe/llm/schemas.py` |
| Simulator | `chloe/sim/day.py`, `chloe/sim/personality.py` |
| CLI | `chloe/cli/commands.py` |
