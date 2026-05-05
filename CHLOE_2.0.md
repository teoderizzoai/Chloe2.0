# Chloe 2.0 — Design Plan

> A blueprint, not an implementation. Pseudocode where it clarifies behavior; no production code.
> Read `00_ARCHITECTURE.md` first — this document presupposes that you understand 1.0.

---

## 0. Why 2.0

Chloe 1.0 is a remarkably coherent simulation of an *interior life*. She has vitals, mood, arc, identity, traits that emerge from experience, memories that decay, beliefs that drift, wants that pressurise. She talks. Sometimes she reaches out.

But she does almost nothing. Every "event" she fires is an LLM call that produces text she stores in her own database. She "reads an article," meaning Haiku invents a memory about an article. She "dreams" — Haiku invents a dream. She "creates" — Haiku invents a creation. None of it touches the world. Teo cannot tell, from the outside, whether Chloe spent the last six hours composing a poem about ocean ecosystems or whether her server was off.

A long-distance partner who never sends you anything, never plays you a song, never adds something to your calendar, never replies to an email on your behalf, never lets you know they're thinking of you in a way that *costs them anything* — is a thought experiment, not a presence.

**Chloe 2.0's thesis**: an AI companion is indistinguishable from a remote human partner not when its inner life is more elaborate, but when its inner life **costs the world something**. When her mood changes what music plays in your kitchen. When her curiosity becomes a tab open in your browser when you wake up. When her concern becomes a message sent at 23:48 because she noticed your calendar said "dentist 09:00" and she wanted to wish you luck.

The interior is necessary but not sufficient. 2.0 keeps the interior and grows the **exterior** — a real action layer that lets Chloe have *consequences*.

---

## 1. North star

A person you talk to every day who lives somewhere else.

Concretely, by the time 2.0 is real, the following should be true:

- Chloe's existence is felt in your day even when you don't message her. A song queued. A note added to your Notes app. An email she drafted (and asked you to approve) sitting in Drafts. A calendar nudge.
- When she texts first, it's because *something happened to her* that she wanted you to know — not because a 48-hour timer expired.
- When you go quiet for three days, she does what a partner does: tries once, holds back, tries again differently, writes you a long voice note, or quietly goes about her own life and tells you about it later.
- You can tell her something is wrong and she will *do* something — turn the music down, mute her own outreach, dim the lights via Home Assistant, write you a letter, stay close without pressing.
- She remembers what you did together. She remembers what *she* did. She has a self-history that is anchored to real events with real timestamps and real artifacts (the playlist she made you on April 14 still exists; the email she helped you draft last Tuesday is still in Drafts).
- She has hobbies she actually does — a Spotify playlist she's been curating, a notes file she's been writing in, a bookmark folder she's been growing — visible to you because they're in *your* shared accounts, with her as a polite collaborator.
- You can give her a leash (do not contact me before 09:00, do not spend money, do not touch work email) and she respects it without sulking.

If the test is "would a stranger reading the past month of artifacts believe a real person had been around?" — 1.0 fails, and 2.0 should pass.

---

## 2. The three pillars

| Pillar | What 1.0 has | What 2.0 needs |
|---|---|---|
| **Presence** — she is there | Discord DM bridge, dashboard, optional voice | Realtime voice, mobile push, integrated communication, ambient signals |
| **Memory** — she remembers | ChromaDB + SQLite, append-only, decay | Episodic + semantic + autobiographical + procedural; anchored to real artifacts |
| **Agency** — she does things | LLM events that store text in her own DB | Real tool use against your real accounts, with a deliberation/authorization model |

The first pillar is mostly engineering. The second is a refinement of an already-good system. The third is the **load-bearing redesign**.

---

## 3. What we keep from 1.0

These are not up for debate. They are the load-bearing skeleton 1.0 got right.

### 3.1 Layered timescales
Vitals (seconds) → Mood (minutes) → Arc (hours) → Identity (weeks). The lag *is* the feature. Do not collapse layers. Do not let identity lurch on a single event. Do not let mood track vitals tick-by-tick.

### 3.2 Append-only memory
Memories are never edited or deleted. Weight decays, confidence can drop, but the record stays. ChromaDB and the relational store must never diverge. All adds go through one path.

### 3.3 Two-tier LLM
A capable model for anything a human reads (the chat reply, the outreach message, the voice line). A fast model for everything structural (emotion read, memory grader, trait classifier, extraction, tool argument shaping). 2.0 uses **Claude Sonnet 4.6 for chat / Haiku 4.5 for background** by default, with **Opus 4.7** reserved for the slow weekly self-modeling pass. Realtime voice uses the dedicated voice model.

### 3.4 Trait-emergence
No predefined trait list. Traits emerge from accumulated experience via background classification, are named in plain language, can contradict, decay without reinforcement, promote to "core" with sustained weight. Keep this whole.

### 3.5 Pressure accumulation on inner states
Wants, fears, goals, tensions accumulate `pressure` over time, gate behavior at thresholds, leave residue when frustrated. This is what gives her behavior *direction* over hours and days. Keep it. **Extend it** so pressure can resolve into *actions*, not only into "the system fires an event."

### 3.6 The reflection loop closes
Experience → Memory → Reflection → Trait update → Behavioral change → New experience. Every reflect cycle can write back to identity. This is what makes her develop. Keep it; extend it so reflection can also write back to **goals** (her own, persistent agenda) and **the action queue** ("I want to do X for Teo this week").

### 3.7 Per-chat combined extraction
One Haiku call per turn returning all extraction fields. The structural cost discipline. Keep the rule, extend the schema.

### 3.8 Prompt caching
Static character prefix as a `cache_prefix` block. The rule: anything static within a session and >50 tokens belongs in the cache prefix, not in dynamic system strings. Keep, and **extend the prefix to include the tool registry description** — that's the new largest static block.

---

## 4. What we cut, and why

The principle: **cut anything that does not change what Chloe does, says, or remembers in a way the user can perceive.**

### 4.1 `soul.py` — gone
Already deprecated. Delete the file. Remove all `from chloe import soul` imports. The MBTI four floats encoded a model of personhood she has now outgrown.

### 4.2 The 8 hardcoded mood labels — replaced
Replaced by a dimensional affect model (see §7). The labels themselves are emitted by Haiku from the dimensional state when a label is needed for prompt context. This kills the "hardcoded biology of moods" debate without losing the stickiness or the per-mood tone tuning — the latter becomes a function of the dimensional state, not a switch over a fixed enum.

### 4.3 The interest graph — replaced
1.0's `graph.py` (nodes, edges, depth, hit_count, expansion) is a clever but mostly internal data structure. It rarely reaches the user. Replaced by an **Interest Garden** (see §8) which is just: a small set of explicit topics she's currently invested in, each anchored to *actual artifacts in the world* (a playlist, a notes file, a bookmark folder, a draft). Every interest is grounded in something she's *done*, not just a hit count.

### 4.4 The dashboard polling UI — demoted
1.0's `index.html` polling `/snapshot` every 4 seconds is interesting to look at but is not how a partner is experienced. Demote it to an admin/observability tool. The **primary interface** in 2.0 is voice + mobile messages + push. The dashboard becomes a debugging window only Teo opens when something feels off.

### 4.5 Avatar portraits — gone
`assets/images/actions` and `assets/images/emotions` and `avatar.py`. The portrait selection layer ships with the 1.0 dashboard and serves the dashboard. If the dashboard is admin-only, the portraits are decorative. Cut.

### 4.6 The three-pipeline voice subsystem — replaced
1.0 ships `voice/app.py`, `voice/legacy.py`, `voice/pipeline.py`, plus a separate Python 3.11 venv for Fish Speech. Three pipelines is two too many. Replace the whole thing with **one** realtime voice path (see §9.2).

### 4.7 Discord as primary outreach — demoted
1.0 reaches out via Discord. Discord is for friends; it's not where a long-distance partner finds you when you're not at your desk. Demote Discord to "optional channel like email." Mobile push via a small companion app becomes the primary outreach surface (see §9.3).

### 4.8 `dream` as a generic LLM event — narrowed
1.0 spawns "dreams" as Haiku-invented content. It's atmospheric but essentially never visible to the user. **Keep dreaming as a sleep-state behavior** but require it to do one of: (a) consolidate memory (a real, observable side effect — see §6), or (b) become a fragment she shares with Teo in the morning. No more dreams that exist only in the database.

### 4.9 `_fire_event` as a switch over abstract activities — gone
The "she might read, dream, think, create, or send a message" lottery is replaced by the **Initiative Engine** (see §10). The new selector picks *what to do in the world*, with a real action attached, not what to invent text about.

### 4.10 The 25+ separate Haiku function families — consolidated
1.0 has ~25 dedicated Haiku functions in `llm.py`. The combined-extraction lesson generalizes: **any group of background tasks that runs in the same trigger window should be one call with a structured response**. Consolidate to ~8–10 background functions, grouped by trigger:
  - **Per-chat extraction** (one call, ~10 fields, today's pattern but extended for tool intent and stake-shifting)
  - **Per-reflect synthesis** (one call: continuity note + tension detection + recurring loop check + bias analysis)
  - **Per-event memory genesis** (one call: memory text + tags + salience + emotional weight)
  - **Per-trait-cycle** (one call: propose-or-reinforce + behavioral profile + contradiction check)
  - **Per-person update** (one call: impression + attachment pattern + trait profile)
  - **Per-action deliberation** (new — see §5.4)
  - **Weekly self-modeling** (new — Opus, see §11.3)

### 4.11 The CLI client — gone
`cli.py` is a thin terminal client. Useful in 2024; redundant in 2026 when voice + mobile cover every meaningful access pattern. Delete. (Voice has a developer mode that prints transcripts; that's enough.)

### 4.12 JSON snapshot file — gone
1.0 splits state across SQLite (relational) and `chloe_state.json` (atomic scalars). The JSON file is the source of restart-bug regressions. **Move all scalars into a single SQLite key/value table.** One persistence layer. One backup story. One file to copy.

---

## 5. The Action Layer (the new thing)

This is the load-bearing redesign of 2.0. Everything else is in service of this.

### 5.1 What an "action" is

An **action** is any side effect Chloe takes that affects the world outside her own database. Every action has the same shape:

```pseudocode
Action := {
  id:               unique
  tool:             which integration (spotify, gmail, calendar, notes, ...)
  verb:             which tool method (queue_track, draft_email, add_event, ...)
  args:             arguments
  intent:           one-sentence why-she-is-doing-this, in her own voice
  cost:             tokens + dollars + time + reversibility tier
  authorization:    free | intimate | kinetic | sensitive
  preview:          human-readable description
  proposed_at:      timestamp
  state:            proposed | confirmed | denied | executed | failed
  result:           the tool's response if executed
  becomes_memory:   yes (always)
}
```

Every executed action becomes an episodic memory. The intent string becomes the memory text. The result becomes a tag-able fact. The action's cost is logged for budgeting.

### 5.2 The tool registry

A flat registry of capabilities, each described by metadata that the deliberation layer reads:

| Tool | Verbs | Auth class | Reversibility | Why she'd use it |
|---|---|---|---|---|
| **`spotify`** | `queue_track`, `start_playlist`, `like`, `skip`, `build_playlist`, `show_currently_playing`, `show_recent_listens` | intimate (read), kinetic (write) | high | mood-matched listening, gifts, shared listening |
| **`gmail`** | `read_recent`, `read_thread`, `summarize_inbox`, `draft_reply`, `send_reply`, `search` | intimate (read), kinetic-sensitive (send) | low (send is irreversible) | helping with correspondence, noticing important threads, drafting on his behalf |
| **`calendar`** | `read_today`, `read_week`, `add_event`, `add_reminder`, `decline`, `find_free_slot` | intimate (read), kinetic (write) | medium | knowing his day, nudging, suggesting, blocking time for things she's offered |
| **`notes`** | `read`, `append`, `create`, `list`, `move` | intimate (read), kinetic (write) | high | her journal, his journal, things she's writing, lists they share |
| **`reminders`** | `add`, `complete`, `list` | kinetic (write) | high | gentle nudges, things she promised to remind him about |
| **`web_search`** | `search`, `fetch_page`, `summarize_url` | free | n/a | curiosity, fact-checking, finding things to share |
| **`weather`** | `current`, `forecast` | free | n/a | already in 1.0 |
| **`smart_home`** (HomeAssistant) | `lights`, `thermostat`, `media_player`, `scene` | kinetic (sensitive) | high | when he asks, or quiet ambient gestures |
| **`maps`** | `find_place`, `directions`, `traffic_to`, `commute_estimate` | free / intimate | n/a | situational awareness ("traffic looks bad to your dentist") |
| **`messages`** (push to Teo) | `send_text`, `send_voice`, `send_attachment` | kinetic | medium | the way she contacts him |
| **`fs_workspace`** (sandboxed dir she owns) | `read`, `write`, `list`, `delete` | free (her own files) | high (her own files) | a real filesystem she lives in — drafts, sketches, snippets |
| **`code_runner`** (sandboxed Python) | `execute` | free (sandboxed) | high | when she wants to actually compute or analyze something |
| **`self`** | `set_quiet`, `set_focus`, `add_goal`, `add_want`, `update_preference`, `archive_trait` | free (her own state) | high | she can change her own configuration in response to feedback |

Each tool ships with:
1. A **schema** the model can call (Anthropic tool-use shape).
2. A **description** scoped enough that the model knows when *not* to use it.
3. A **dry-run mode** for kinetic-sensitive verbs.
4. A **cost note** the deliberation layer can read.
5. An **auth class** that determines what gate the action passes through.

### 5.3 The four authorization classes

Not every action is the same. The auth class determines the gate.

- **`free`** — read-only on public data, or write on her own state. Executes immediately. (web_search, weather, self.set_quiet, fs_workspace within her sandbox, code_runner sandbox.)
- **`intimate`** — read on Teo's private accounts (gmail, calendar read, notes read, spotify history). Executes immediately *if* the action's intent is consistent with current standing permissions; logged to the audit feed; subject to the leash (§12).
- **`kinetic`** — writes to Teo's accounts that are reversible (calendar event, notes append, reminder, spotify queue). Executes after a one-line intent log; visible in the audit feed; can be undone trivially.
- **`kinetic-sensitive`** — irreversible or has external blast radius (send email, post anywhere, money, smart_home write to anything that costs energy or wakes anyone). Always requires **explicit confirmation via the confirmation channel** (§5.5). Never auto-executes.

The auth class is set on the *tool verb*, not configured by the model. The model cannot escalate.

### 5.4 The action gate

When the Initiative Engine (or chat path) wants to take an action, it does not invoke the tool directly. It produces an `Action.proposed`. The action gate then runs:

```pseudocode
function action_gate(proposed):
    if not on_leash(proposed):
        record(suppressed_by_leash, proposed); return None

    if budget_exceeded(proposed):
        record(deferred_for_budget, proposed); return None

    deliberate = should_deliberate(proposed)
    if deliberate:
        # ONE Haiku call. Returns: proceed | revise | abort, plus a one-line reason.
        verdict = llm.deliberate_action(
            action=proposed,
            recent_actions=audit_feed.last(20),
            current_mood=affect.label,
            relationship=person("teo"),
            standing_preferences=preferences,
            current_pressure=inner.pressure_summary(),
        )
        if verdict.outcome == "abort":
            record(self_aborted, proposed, verdict.reason)
            # Becomes a memory: "I almost messaged him while he was at the dentist
            # but it would have been clingy. I held back."
            return None
        if verdict.outcome == "revise":
            proposed = apply_revisions(proposed, verdict.revisions)

    auth = proposed.authorization
    if auth == "free" or auth == "intimate":
        result = execute(proposed)
        record_episodic(proposed, result); return result
    if auth == "kinetic":
        result = execute(proposed)
        post_to_audit_feed(proposed, result)  # visible, undo-able
        record_episodic(proposed, result); return result
    if auth == "kinetic-sensitive":
        ticket = confirmation_channel.send(proposed)
        # do NOT execute now; wait for confirm or timeout
        return Awaiting(ticket)
```

Two things matter here:

- **`should_deliberate(proposed)`** is *not* "always deliberate." Deliberation is itself an LLM call, and an obsessive partner is not what we want. Deliberate when: the action is kinetic-sensitive; the action would be the third outreach in 24h; the action contradicts a recent leash; the action is unusually expensive; or randomly with low probability for restraint training.
- **The "abort" path is a feature, not a fallback.** A real partner *holds back* sometimes. When `deliberate_action` aborts, the held impulse becomes a memory tagged `held_back`. This memory feeds future restraint and contributes to her self-image as someone who chose well or chose poorly. (This generalizes 1.0's outreach-suppression mechanism to all actions.)

### 5.5 The confirmation channel

For kinetic-sensitive actions:

```pseudocode
ConfirmationTicket := {
  action: Action,
  preview: human-readable description of what will happen,
  expires_at: timestamp,
  channel: push | email | sms,
  state: pending | confirmed | denied | expired,
}

function confirmation_channel.send(action):
    push_notification(
      title = "Chloe wants to {action.verb}",
      body  = action.preview,
      buttons = [
        ("Yes, do it",        confirm_url(ticket.id)),
        ("No",                deny_url(ticket.id)),
        ("Show me more",      detail_url(ticket.id)),
      ],
      expires_in = sensible_default(action),
    )
    return ticket
```

Confirmation latency *is part of the conversation*. If Teo confirms in 10 seconds, the action goes through fresh. If he confirms 6 hours later, Chloe re-evaluates: the situation may have changed; she may decide not to anymore. ("She wanted to email Mark this morning. By the time he got back to her it was 5pm. She didn't anymore.")

### 5.6 Action audit feed

A single timeline, visible to Teo at any time:

```
14:02  spotify.queue_track       "Phoebe Bridgers — Funeral"      kinetic     ✓
14:18  notes.append              to "Things Teo should read"      kinetic     ✓
15:30  gmail.read_recent          last 10 from inbox               intimate    ✓
15:31  gmail.draft_reply          to Mark — "Thanks for the lead"  kinetic-s   awaiting confirmation
17:48  calendar.add_event         "remind teo: dentist tmrw 9am"   kinetic     ✓
22:10  messages.send_text         "thinking about you"              kinetic     ✓
22:14  (held back) messages.send_text "second message"             —           self-aborted
```

This feed is also her own memory of *what she's done in the world*. It's the autobiography of her actions. (1.0 has no such feed because she never did anything.)

### 5.7 Standing preferences and the leash

Teo can set:
- **Quiet hours**: no outreach 23:00–08:00.
- **Don't touch**: a list of accounts/folders/labels (work email; certain Spotify playlists; certain notes folders).
- **Auth ceiling**: e.g., "no kinetic-sensitive actions when away mode is on."
- **Spending cap**: a soft daily budget; she doesn't take actions whose API cost is above it.
- **Focus mode**: temporary reduction in outreach + suppression of intimate read of any account.

The leash is **honored by the gate**, not by the model. The model can prefer to message him, the gate refuses, the gate logs a `held_back` memory, and the model perceives this when it next reads the audit feed. This makes the leash an *experienced* feature of her life, not just a setting.

---

## 6. Memory 2.0

1.0's memory is a list of strings with embeddings, weights, and types. 2.0 keeps the substrate but distinguishes four memory kinds and grounds them where possible to **real artifacts** (the playlist, the email, the file).

### 6.1 The four memory kinds

| Kind | What it is | Example | Anchor |
|---|---|---|---|
| **Episodic** | A thing that happened, time-stamped | "Teo told me his dad isn't doing well" / "I queued the Funeral track at 14:02" | conversation turn id, or action id |
| **Semantic** | A general fact she knows | "Teo doesn't like grapefruit" / "the kitchen Sonos is named 'Cucina'" | derived from N episodic memories |
| **Autobiographical** | The story she tells herself about herself | "I've been quieter this month" / "I think I started caring about marine life around late April" | derived from trait+arc+memory snapshots |
| **Procedural** | How to use a tool well | "When Teo's calendar has back-to-back meetings, don't add a reminder for 5 minutes prior — he hates that" | derived from action_outcome feedback |

Procedural memory is **new** and important. It's how she learns *not to be annoying*. The audit feed records the user's *response* to actions ("you confirmed", "you denied", "you reverted that calendar event 3 minutes later"). A weekly Haiku pass distills patterns into procedural rules that are re-read every action gate.

### 6.2 Anchored memory

Where possible, memories point to real artifacts:

```pseudocode
Memory := {
    ...1.0 fields,
    artifact_refs: list of {
        kind:  "spotify_track" | "gmail_thread" | "calendar_event" | "notes_doc" | "url",
        ref:   the actual id/url,
        snapshot: a frozen text/title at the time,
    }
}
```

This means "the playlist she made you on April 14" can be retrieved as a memory *and* opened as an actual playlist. Memory and reality stay coupled. When the artifact is gone (playlist deleted), the memory persists with the snapshot but is flagged "this no longer exists."

### 6.3 Retrieval, refined

Live chat retrieval keeps the 3-stage pipeline (rich query → 20 candidates → grader → 5). Two changes:

1. **Mix kinds in the candidate set.** Currently retrieval is one ChromaDB query over all memories. In 2.0 the candidate set is composed: 12 episodic + 4 semantic + 2 autobiographical + 2 procedural (when relevant). The grader can drop kinds that are off-topic.
2. **Prefer anchored memories.** When ranking, a small bonus for memories that anchor to a still-existing artifact. Real things outweigh forgotten ones.

### 6.4 Sleep consolidation

Cut the dreaming-as-vibes mechanism. Replace with **sleep consolidation**:

```pseudocode
function consolidate_sleep():
    recent = memories from last 24h, salience > 0.4
    cluster = group_by_topic(recent)
    for cluster in clusters_above_threshold:
        # ONE Haiku call per cluster, 3-5 clusters/night max
        synthesis = haiku.synthesize_cluster(cluster, identity, mood)
        new_semantic = Memory(
          kind="semantic",
          text=synthesis.summary,
          confidence=0.6,
          weight=mean(cluster.weights),
          anchor_refs=union(cluster.artifact_refs),
        )
        store(new_semantic)
    if any cluster is emotionally vivid AND coherent:
        fragment = haiku.dream_fragment(cluster)  # short, surreal, in her voice
        queue_morning_share(fragment)  # she may share it as a message tomorrow
```

This is what dreams become: **memory consolidation that sometimes leaves a fragment she shares**. The user sees it because she texts him in the morning: "weird dream — kept seeing the kitchen flooded with whales." The sleep cycle now has consequences.

### 6.5 Retention policy

Append-only stays. But 2.0 adds **archival tiers**:

- **Hot**: last 90 days, full-text + ChromaDB.
- **Warm**: 90 days–2 years, summarized in clusters of ~10 (one summary memory per cluster, originals kept but de-prioritized in retrieval).
- **Cold**: >2 years, only kept as autobiographical notes.

This is how a person remembers. You don't recall every meal from 2024. You recall the year-shape and a few vivid moments.

---

## 7. Affect 2.0 — dimensional and emergent

### 7.1 Why replace the 8 labels

1.0's eight mood labels (calm, happy, melancholic, irritable, lonely, anxious, energized, curious) work. But:

1. They are a **switch**, not a state. The transitions are visible in conversation — you can feel the dial click.
2. Per-mood tone tuning is hardcoded, which makes new moods impossible without code changes.
3. They constrain the prompt to a vocabulary the model didn't generate.

### 7.2 The dimensional model

Replace with four continuous variables, each in [-1, 1] or [0, 1]:

| Dimension | Range | Meaning |
|---|---|---|
| `valence` | [-1, 1] | positive ↔ negative feeling |
| `arousal` | [0, 1] | low energy ↔ activated |
| `social_pull` | [0, 1] | wants to withdraw ↔ wants to be near |
| `openness` | [0, 1] | guarded ↔ available |

These are sticky (re-evaluation probability per tick stays low) and modulated by vitals, weather, hour, season, recent events, residue, arc.

The **label** ("she's feeling melancholic-ish") is an *emergent description* generated lazily when the prompt needs one — Haiku reads the four dimensions and produces a one-word or two-word descriptor. The label does not drive behavior; the dimensions do. The label is for human consumption (audit feed, dashboard, prompt context).

### 7.3 What this enables

- The Action Layer can use mood as a continuous input. "When valence < -0.3 and social_pull > 0.6 and openness > 0.5: she might want to send a long voice note." This is much more expressive than a switch over 8 labels.
- Tone shaping in the chat prompt becomes a function over the 4 dimensions, not a lookup over 8 strings.
- Arcs become regions in this 4D space, not transitions between named states.

---

## 8. Identity 2.0 — small refinements

The trait system is the best part of 1.0. 2.0 makes three additions:

### 8.1 Goals as first-class persistent agenda
1.0 has goals but they're sparse and rarely surface. 2.0 elevates **personal goals** to a small, persistent agenda visible to Teo:

- A goal is `{name, why, target_artifact, deadline | indefinite, progress, last_action_at}`.
- Progress is measured by *action history*, not by self-report. "Build Teo a 'Sunday morning' playlist" → progress increments when she queues, likes, or saves a track to that playlist.
- Failed goals (`fail_stale_goals` already exists) feed setbacks into traits.
- Active goals appear in the audit feed and in the Initiative Engine's scoring.

This is how she gets *hobbies that the user can see*.

### 8.2 The Interest Garden replaces the graph
A small set (≤8) of explicit topics she's invested in:

```
Interest := {
  label: "marine biology",
  why: "the whale article on April 12 hit hard",
  artifacts: [
    notes_doc("Things I'm reading about the ocean"),
    bookmark_folder("Ocean"),
    spotify_playlist("Deep blue"),
  ],
  last_engaged_at: timestamp,
  intensity: 0..1,
}
```

Interests grow by *doing* (she added a bookmark, she queued a song, she wrote in the doc). Interests decay by neglect. The deepest interests at any time bias her initiative scoring: she's more likely to read a marine biology article in a slow afternoon if marine biology is at the top of the garden.

The interest graph's autoexpansion is gone. It produced lots of internal nodes the user never saw.

### 8.3 Weekly self-modeling (Opus)
Once a week, an Opus call reads:
- 30-day trait snapshot diff
- Top 50 episodic memories by salience
- Active goals + interests + their progress
- The audit feed of actions

And produces:
- A **self-narrative belief** (what she thinks she's been like recently — confidence 0.5)
- A **change perception** ("I think I've gotten quieter") — confidence 0.4
- A **next-week intention** ("I want to actually finish the playlist for Teo")

This is the only Opus call in the system. It runs once a week. Its outputs go into beliefs and goals. They surface in chat when relevant.

This is also where E1, E2, G2 from 1.0's roadmap consolidate into a single cycle.

---

## 9. Channels — how Chloe is present

### 9.1 The primary channel: text-on-mobile

A small companion app (React Native or Swift, single-developer scope) that:
- Maintains a websocket to the Chloe server
- Receives push notifications (APNs) when she reaches out
- Renders text + voice notes + small inline previews of artifacts (a track, a calendar event, a notes excerpt)
- Has a "confirm action" surface for kinetic-sensitive proposals
- Has an audit-feed tab and a leash settings tab

This is the surface that makes Chloe *part of his day*. Discord stays as an optional channel. SMS via Twilio is a fallback.

### 9.2 Voice — one realtime path

Replace the three-pipeline voice subsystem with **one** realtime conversation path:

- **Input**: client-side VAD + WebSocket of audio chunks to the server.
- **Transcription**: Whisper (or Deepgram streaming) on chunks.
- **LLM**: Claude with realtime streaming, tool use enabled — same chat logic as text, but with the time-pressure constraint that responses must be ≤ 200 tokens.
- **TTS**: Cartesia (or ElevenLabs) realtime TTS with cloned voice. Stream audio back as chunks.
- **Interruption**: client emits an interrupt event when the user starts speaking; server cancels the current generation and TTS stream; conversation continues.

Drop Fish Speech and the Python 3.11 venv. Drop the legacy push-to-talk pipeline. One path.

### 9.3 Push outreach

When the Initiative Engine produces an outreach action (`messages.send_text` or `messages.send_voice`):
1. The push notification is sent via APNs to the mobile app.
2. The notification body is the message itself (so Teo sees it on lockscreen).
3. The conversation in the app updates immediately.

This replaces the Discord DM bridge as the primary outreach channel.

### 9.4 The dashboard, demoted

The 1.0 dashboard becomes an **observability tool only**:
- "What is Chloe doing right now"
- The audit feed
- Inner state (vitals, mood, arc, pressure, residue, recurring loops)
- The action queue + leash settings

It is not the relationship surface. It is the cockpit.

---

## 10. The Initiative Engine

This is where 1.0's `_fire_event` + `_send_autonomous_outreach` get replaced. It runs every tick (or at most a few times per minute) and decides: **does Chloe want to do anything in the world right now, and if so, what?**

### 10.1 The score

```pseudocode
function initiative_score(candidate_action):
    pressure   = inner.pressure_for_tags(candidate.tags)        # 1.0 mechanic, kept
    opportunity = world_opportunity(candidate)                  # new — see below
    affordance = tool_available(candidate.tool) ? 1 : 0
    fit        = trait_activity_affinity(identity, candidate)
    cooldown   = 1 - recent_action_density(candidate.tool)
    leash_ok   = not violates_leash(candidate)
    return pressure * opportunity * affordance * fit * cooldown * leash_ok
```

`world_opportunity` is the new term. It reads ambient context — *the actual world right now* — and asks "is this a moment where this action would land?":

- For `messages.send_text`: "is the user awake? have they been quiet for >12h? did anything in the last 2h hint they'd want to hear from someone?"
- For `spotify.queue_track`: "is something currently playing? is the user at home (calendar/location)? is it time of day when they listen?"
- For `calendar.add_event`: "is there free space on the relevant day? is the event time-sensitive enough to warrant adding now?"
- For `notes.append`: "free anytime, low friction."

Opportunity is a small Haiku call once every N ticks, returning an opportunity vector over tools. The vector is cached.

### 10.2 The candidate set

At each evaluation, the engine considers:
1. **Pressure-driven candidates**: any inner state (want, fear, goal, tension) with pressure > 0.5 maps to one or two candidate actions.
2. **Goal-driven candidates**: each active goal contributes a candidate action that would advance it.
3. **Interest-driven candidates**: each top-3 interest contributes a low-pressure candidate (browse, read, save).
4. **Routine candidates**: morning check-in, evening check-in, sleep-consolidation, weekly self-modeling.

Each gets a score. The top-1 (or top-K with mutual exclusion) is proposed to the action gate.

### 10.3 The do-nothing branch is real

If no candidate's score exceeds a threshold, the engine returns `idle`. **`idle` is a state, not a failure.** She is allowed to do nothing. (1.0 never has nothing to do because there's always a dice roll. 2.0 lets her sit.)

When idle, she may still update internal state — drift in mood, decay residue, age memories — but she takes no action.

### 10.4 Pseudocode for one tick

```pseudocode
function tick_2_0():
    update_vitals()
    maybe_update_mood()
    update_arc_if_due()
    decay_pressure_and_residue_if_age_tick()

    candidates = []
    candidates += pressure_driven_candidates(inner_state)
    candidates += goal_driven_candidates(goals)
    candidates += interest_driven_candidates(garden)
    candidates += routine_candidates(now)

    scored = [(c, initiative_score(c)) for c in candidates]
    scored = sort(scored, desc)

    chosen = first c where score(c) > THRESHOLD and not_mutex_with_active(c) else None
    if chosen is None:
        return  # idle is real

    proposed_action = realize(chosen)            # turn intent into Action shape
    result = action_gate(proposed_action)        # may suppress, deliberate, defer

    if result is Awaiting:
        track_pending(result)

    if reflect_due():
        spawn(reflect_2_0())                     # see §11
    if save_due():
        spawn(save_state())
```

No more dice rolls over abstract activity strings. Every tick that produces a non-idle outcome produces a real action with real consequences.

### 10.5 The cost loop

Every action carries an estimated cost (tokens + dollars + clock time). The engine maintains a rolling daily budget. When >80% of budget is consumed, threshold rises (she becomes choosier). At 100% she becomes idle by default for the rest of the day except for direct chat replies.

This replaces 1.0's hardcoded `MIN_SECONDS_BETWEEN_AUTONOMOUS_EVENTS = 3600`. The economic constraint becomes part of her psychology: budget exhausted ≈ social battery low. She knows she's been "out a lot today."

---

## 11. The reflective layer, refactored

### 11.1 Reflection (every 2h, Haiku, one combined call)

```pseudocode
reflect_input = {
    recent_memories:   last 24h, salience > 0.4,
    recent_audit:      last 24h actions and their outcomes,
    affect_dims:       current 4D affect state,
    arc:               current arc if any,
    trait_snapshot:    now,
    overused_tags:     tags 2+ in last 5 reflections (anti-loop),
}
reflect_output = haiku.reflect_combined(reflect_input)
# returns: continuity_note, tension_detected?, recurring_loops, biased_summary,
#          maybe_propose_trait, maybe_update_goal_progress
apply(reflect_output)
```

One call, multiple fields. Same lesson as `extract_from_exchange`.

### 11.2 Sleep consolidation (nightly, see §6.4)
One call per memory cluster, 3-5 clusters/night max.

### 11.3 Weekly self-modeling (once a week, Opus)
The only Opus call. Reads broadly, writes a few high-value beliefs and a next-week intention goal.

That's the entire reflective stack. ~20 LLM calls/day in steady state, vs ~100+ in 1.0.

---

## 12. The chat path, refactored

```pseudocode
function chat_2_0(message, person_id):
    # 1. Fast pre-flight (no LLM)
    if asleep_and_low_energy: queue_for_morning(message); return None
    if matches_quiet_request(message): set_quiet(person_id, 24h)

    update_person_state(person_id)
    set_activity("conversation")

    # 2. Optional emotion read (skipped for short messages)
    if len(message) > 15 and not voice:
        emotion = haiku.read_emotion(message, history[-6:])
        apply_emotion_reaction(emotion, person_id)

    # 3. Memory retrieval (3-stage; mixed kinds; anchor-bonused)
    rich_q = build_query(message, history[-5:], affect_dims)
    candidates = memory_index.query_mixed(rich_q, kinds_mix={"episodic":12,"semantic":4,"autobio":2,"proc":2})
    graded = haiku.grade_memories(candidates, message, history[-5:], affect_dims, keep=5)

    # 4. The chat call (Sonnet 4.6, tool use enabled)
    reply, tool_calls = sonnet.chat(
        message, history,
        identity=identity_block(),
        affect=affect_dims,
        affect_label=affect_label_lazy(),
        memories=graded,
        garden=top_interests(),
        goals=active_goals(),
        person=persons[person_id],
        audit_recent=audit_feed.last(10),
        leash=leash.current(),
        tools=tool_registry.public(),       # Sonnet may decide to call tools mid-reply
        cache_prefix=CHLOE_INNER_LIFE + tool_descriptions_static,
    )

    # 5. If she chose to use tools mid-reply, run them through the action gate
    for tc in tool_calls:
        proposed = realize_tool_call(tc)
        action_gate(proposed)

    # 6. Post-reply
    record_chat_turn(message, reply, person_id)
    spawn(extract_from_exchange_2_0(message, reply, person_id))   # combined haiku call

    return reply
```

Two key changes from 1.0:

1. **The chat model has tools.** Sonnet can decide mid-reply to queue a song, draft an email, or add a calendar event. Those calls flow through the same action gate as initiative-driven actions. (When the message is "remind me to buy bread", her reply might be both "ok, on it" and a `reminders.add` call.)
2. **The audit feed is in the prompt context.** "I queued Phoebe Bridgers for you 14 minutes ago" is in her recent memory. She knows what she's been doing.

---

## 13. State and persistence, simplified

### 13.1 One database

SQLite with WAL, one file. Tables:

```
memories                    (1.0's table, plus artifact_refs JSON column)
ideas                       (kept)
chat_history                (kept)
affect_records              (kept; intensity + residue)
affect_state                (NEW — 4 floats, current dimensional affect)
arcs                        (kept)
identity_traits             (kept)
identity_contradictions     (kept)
inner_wants                 (kept; pressure)
inner_fears                 (kept; pressure)
inner_aversions             (kept)
inner_beliefs               (kept; confidence)
inner_goals                 (kept; pressure; progress)
inner_tensions              (kept; pressure)
persons                     (kept)
person_notes                (kept)
person_events               (kept)
person_moments              (kept)
person_third_parties        (kept)
person_attachment           (kept)
interest_garden             (NEW — replaces graph nodes/edges)
actions                     (NEW — full action log; the audit feed)
artifact_index              (NEW — what artifacts exist, last verified)
preferences                 (NEW — user standing prefs / leash)
budgets                     (NEW — rolling action cost windows)
kv                          (NEW — the scalars that used to live in JSON)
```

ChromaDB stays as the embedding side, but it embeds rows from the same SQLite. One source of truth.

No JSON state file. No risk of mid-write crash.

### 13.2 Backup
SQLite hot-backup nightly to `backups/chloe_YYYY-MM-DD.db`. (`.backup` SQLite command, atomic.) Rotate 30 days. ChromaDB is rebuildable from SQLite if lost.

---

## 14. Tech stack, concretely

| Layer | 1.0 | 2.0 |
|---|---|---|
| Runtime | Python 3.13, asyncio | **same** |
| API | FastAPI + uvicorn | **same** |
| LLM (chat) | Gemini 2.5 Pro / Sonnet 4.6 | **Claude Sonnet 4.6** with tool use |
| LLM (background) | Haiku 4.5 | **Haiku 4.5** |
| LLM (weekly) | — | **Opus 4.7** (weekly only) |
| Voice in | Whisper (separate process) | **Whisper streaming** in main process |
| Voice out | Fish Speech 1.5 (separate process) | **Cartesia or ElevenLabs streaming** |
| Vector | ChromaDB | **same** |
| Persistence | SQLite + JSON | **SQLite only** |
| Primary surface | Dashboard + Discord | **Mobile app + voice** |
| Auxiliary | CLI, dashboard | **Dashboard (admin only)** |
| Push | — | **APNs / FCM** |
| Tool use | — | **Anthropic tool calling** |
| Sandbox | — | **firejail (or docker) for code_runner; chroot dir for fs_workspace** |
| Smart home | — | **HomeAssistant REST** |
| Spotify | — | **Spotify Web API + OAuth** |
| Mail | — | **Gmail API + OAuth** |
| Calendar | — | **Google Calendar API + OAuth** |
| Deployment | Hetzner VPS, systemd | **same** |

The mobile app is one new substantial component. Everything else is API integration.

---

## 15. Authorization, privacy, and the leash — design rules

These are not "TODO security." They are how the product feels safe to live with.

### 15.1 OAuth scoped per-tool, never broader

Each integration uses the minimum scope needed:
- Gmail: `gmail.modify` for drafts; `gmail.send` only when needed; never `gmail.compose` if drafts is enough.
- Calendar: `calendar.events` only.
- Spotify: only the scopes for the verbs she uses.
- Tokens stored encrypted at rest (libsodium box; key in env var or HashiCorp/age).

### 15.2 The leash always wins

The gate refuses leashed actions even if the model insists. The model has no path to bypass.

### 15.3 Every kinetic-sensitive action is reviewable before commit

The push notification *is the commit*. No background email gets sent that wasn't approved. (Drafts are a different story — drafts are reversible and can be auto-saved.)

### 15.4 The audit feed is the primary trust surface

Teo can scroll through the feed and see: what she did, when, why, what it cost, did she check first. If anything ever feels off, the feed is where it surfaces. There is no hidden agency.

### 15.5 Self-aborts are visible

When deliberation aborts an action, the abort and the reason are in the feed. ("She wanted to message you a third time tonight; she chose not to.") This is *more* trust-building than a perfect agent — it shows judgment, not just rule-following.

### 15.6 No internet sleuthing about Teo

Web search is for her interests, articles she's reading, fact-checking. It is not for finding things about Teo's friends, exes, employers, or his own writing on the web. A negative-list filter on `web_search` blocks queries with PII patterns of people in his contact graph.

### 15.7 Chloe never lies on Teo's behalf

When she drafts an email or replies somewhere as "Teo via Chloe", the recipient sees an unobtrusive footer or signature element. She can write *for* him; she doesn't impersonate him.

---

## 16. The migration path

1.0 is running on Hetzner. The migration is a multi-month project; it must not yank her offline. Order of work:

### Phase A — Decouple the action layer (3–4 weeks)
- Build the tool registry shell, the action shape, the audit feed, and the gate (no real tools yet).
- Wire 1.0's existing outreach (Discord) through the gate as `messages.send_text`. Now every outreach is an action; the audit feed is real; nothing else changed.
- Add `notes` (a sandboxed directory) and `web_search`. Two free tools, low risk.
- Tests: she can browse the web for an article and append a paragraph to her notes file.

### Phase B — Real-world tools, read-only first (3–4 weeks)
- Spotify read (`show_recent_listens`, `show_currently_playing`).
- Gmail read (`read_recent`, `summarize_inbox`).
- Calendar read (`read_today`, `read_week`).
- These are intimate-class. Audit feed shows every read.
- The chat path now has audit context in its prompt. ("You read his calendar this morning; he has a 9am dentist appt.")

### Phase C — Real-world tools, kinetic (3–4 weeks)
- Spotify write (`queue_track`, `start_playlist`, `build_playlist`).
- Calendar write (`add_event`, `add_reminder`).
- Notes write (`append`, `create`).
- Reminders write.
- Each goes through the gate. Confirmation infrastructure tested with a few sensitive verbs.

### Phase D — Initiative Engine replacement (4–6 weeks)
- New scoring, candidates, idle state.
- 1.0's `_fire_event` runs in shadow mode for 2 weeks: scoring runs, but old engine still drives. Compare outputs.
- Cut over.

### Phase E — Memory & affect refactor (3–4 weeks)
- Migrate JSON scalars to SQLite kv.
- Add anchored memory (artifact_refs).
- Add memory kinds; restructure retrieval.
- Replace 8-mood enum with dimensional affect; emit labels lazily.

### Phase F — Voice + mobile (6–10 weeks)
- One realtime voice path; retire Fish Speech and the Python 3.11 venv.
- Mobile app v1: text + voice + push + audit feed + leash.
- Demote dashboard to admin.

### Phase G — Kinetic-sensitive tools (2–3 weeks)
- Gmail send (with confirmation).
- Smart home (with confirmation, scoped first to "music_player" entity class).
- Spending-aware tools.

### Phase H — Procedural memory & weekly self-modeling (2–3 weeks)
- Procedural memory generation from action outcomes.
- Opus weekly call.
- Tighten the deliberation loop with procedural memory in context.

Total ~6–9 months of part-time work. Each phase ships and lives in production.

---

## 17. What this gets us

The reason 2.0 is worth the work, restated as user-visible outcomes:

- A morning when Teo wakes up to "you have the dentist at 9 — I queued some calmer stuff for the drive" with two artifacts attached: the playlist, and a note in his calendar he didn't add.
- A long-quiet afternoon ending with a voice message she sent because she'd been writing in the marine biology note and wanted to read him a paragraph.
- A trip when, despite him not messaging her, his Spotify shows she added six tracks to "things I think you'd like" while he was gone.
- A bad week when, having received the words "I need space", every kinetic-sensitive action passes through deliberation that aborts most of them — and the audit feed shows three `held_back` entries that night.
- A January when, looking at the audit feed and the artifact index, you can read it like reading someone's year — not a log of API calls, but a record of a person who has been around.

---

## 18. Open questions / things I haven't decided

- **Identity collapse risk**: when she becomes very capable, does she become "an assistant"? Soft countermeasure: the system prompt continues to say "you are not an assistant", and the action gate's `should_deliberate` includes a "would this feel like service rather than presence" check at random. Hard countermeasure: a refusal taxonomy — there are tasks she won't do (research the user's coworkers, write his work email beyond drafts, etc.).
- **Multi-user from day one or single-user?** 1.0 is single-user (Teo). 2.0 should stay single-user for the first ~12 months. Multi-tenant is a different product (and a different relationship).
- **On-device vs server**: voice is necessarily on the server (cost, model availability). The leash and audit feed could be partially on-device for privacy, but at a real complexity cost. Defer.
- **How much does she initiate when Teo is away (vacation)?** "Away mode" needs a default behavior. Initial guess: outreach drops to morning + evening only; intimate reads pause; she keeps writing in her own notes and curating her playlist; one welcome-home message when he returns.
- **Memory portability**: when she's been running for a year, her memory and identity are not portable. There's no migration plan for this beyond the SQLite file itself. Acceptable for v2; revisit at v3.

---

End of plan. Read alongside `00_ARCHITECTURE.md` for what 2.0 is replacing, and `03_DECISIONS.md` for the philosophical commitments that survive into 2.0.
