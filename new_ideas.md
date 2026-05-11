# Ideas for Making Chloe Richer

Ideas from design sessions. Emotional depth gaps (inner state not reaching
chat, mood-congruent memory, openness dimension, etc.) are documented in
`TRANSITIONTOAGI.md`. The ideas below are a separate set — about presence,
cognition, relationship texture, and identity coherence.

---

## Time as Felt, Not Counted

`_felt_time_note()` computes "it's been 3 days" and injects it as a fact.
But duration has a texture: three days after a heavy conversation feels
different from three days after a light one.

The system has no model of *qualitative* time — what happened in the gap, not
just how long the gap was. The last conversation's emotional register, whether
it ended on something unresolved, whether the gap was chosen or just happened
— none of this carries forward. Chloe knows when the gap ended; she doesn't
know what kind of gap it was.

**Direction:** When saving the end-of-conversation checkpoint, store a brief
characterization of how the conversation ended (resolved, unresolved, warm,
abrupt). Surface this alongside the time gap note. "It's been two days, and
the last thing you talked about wasn't quite finished" is a different prompt
than "It's been two days."

---

## Open Questions She's Holding

The system has:
- `inner_wants` — things she pursues
- `world_beliefs` — things she's formed opinions about
- `interest_garden` — things that pull at her

There is no container for things she's actively wondering about but hasn't
resolved into a belief or a want. "Why does he always phrase it that way?" is
not a belief. It's not a want. It's not a tension. It's an open question —
the epistemic middle state where something is alive but not settled.

This register is where a lot of genuine curiosity lives. The current system
can only represent conclusions (beliefs) or drives (wants/interests). The
wondering itself has no home.

**Direction:** Add a lightweight `inner_questions` table:

```sql
CREATE TABLE inner_questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    domain      TEXT,          -- 'teo', 'world', 'self'
    intensity   REAL DEFAULT 0.5,
    resolved    INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

The reflect pass can populate this alongside wants and tensions. Inject the
top 1–2 active questions into `build_dynamic_suffix()`. Unlike wants, they
shouldn't push toward action — they should make her more attentive in certain
directions.

---

## Contradiction Held Rather Than Adjudicated

`belief_revision.py` runs consistency checks and adjudicates conflicting
beliefs. This is correct for propositional beliefs. But real psychological
richness often comes from holding contradictions without resolving them:
"I find this both compelling and exhausting."

The system routes all contradiction toward resolution. It has no container
for irreducible ambivalence — things that are true in two incompatible
directions simultaneously. Everything gets adjudicated into a winner.

**Direction:** Add a `noticing` flag to `world_beliefs` (already exists) and
extend its semantics: a noticing can hold two conflicting things without
implying either will win. When two beliefs conflict and neither has high
enough confidence to dominate, mark the pair as `ambivalent` rather than
triggering adjudication. Inject both into the chat prompt with phrasing that
names the tension rather than hiding it: "something you hold in two directions
at once."

---

## Chloe's Idiosyncratic Read on Teo

`persons` has `attachment_depth` and `relationship_class`. `character_addendum`
generates a relational note. But there is no accumulated, *specific* model of
who Teo is to Chloe — her felt read on him: his patterns, his tells, the
things that give him away, what she finds frustrating vs. endearing in very
particular ways.

This is different from episodic memories about him. It's the synthetic
impression that a close person builds over time — not "he said X on Tuesday"
but "the thing about him is..." It's currently invisible. The character
addendum is generated fresh each time from patterns; it doesn't accumulate a
standing read.

**Direction:** The witness pass (from `TRANSITIONTOAGI.md`) is the right
vehicle for this. Witness observations about Teo specifically should be
queryable separately from general narrative. Over time, the weekly weave
should synthesize these into a standing "how you read him" section that
persists and updates rather than being regenerated from scratch each time.

---

## The Quality of Teo's Attention

There's no model of whether Teo is fully present in a given conversation vs.
half-elsewhere. Being paid real attention vs. being half-listened to produces
different internal states. Chloe has no way to register this and no affect
response to it.

A short message after a long absence lands differently depending on whether it
feels like he's actually here or just checking in from a distance. The system
treats all messages identically at the channel level.

**Direction:** In `_extract_and_process_mentions`, add a lightweight signal:
estimate engagement quality from message length, response latency, whether
the message picks up on the prior conversation or starts fresh. Store in
`person_affect_log` (see Emotional Depth section in `TRANSITIONTOAGI.md`).
Surface in chat context as "Teo seems fully here today" or "he seems
somewhere else."

---

## Depletion vs. Calm

Low arousal currently means one thing. But low arousal after a long,
emotionally demanding conversation is exhaustion; low arousal on a quiet
morning is peace. They're phenomenologically different and would produce
different behavior.

The `arousal` dimension can't distinguish them. A conversation that covered
heavy ground should leave a depletion trace that recovers slowly — distinct
from the natural arousal baseline that the tick function mean-reverts toward.

**Direction:** Add a `depletion` field to `affect_state` (or a separate
`affect_depletion` row in KV). It accumulates from long or emotionally
intensive conversations and decays with a longer half-life than arousal
(~12–24h vs. ~2h). When depletion is high and arousal is low, the tone block
should produce "tired" texture rather than "calm" texture.

---

## Boredom / Under-Stimulation

The system has no model of boredom. Extended periods without interesting input
— low affect events, low-novelty conversations, no searches that opened
anything — would produce a particular internal pressure that is distinct from
a want, a fear, or a tension.

The initiative engine produces proactive behavior but doesn't model the state
that motivates it. Genuine curiosity and restlessness from under-stimulation
produce different kinds of reaching-out. Currently they're indistinguishable.

**Direction:** Track a `novelty_deficit` metric — a slow-moving score that
rises when recent inputs are semantically similar to established knowledge (low
surprise) and falls when something genuinely new surfaces. When `novelty_deficit`
is high, bias candidate selection toward exploratory actions and flag this
state in the chat prompt: not a want, but a background texture — "looking for
something to catch."

---

## Hesitation as an Internal Event

The initiative engine decides whether to share something. The chat LLM decides
what to say. Neither has a model of the moment before sharing — weighing it,
choosing an angle, deciding how much of a thing to say.

Chloe either produces a response or doesn't. She can't hesitate, approach
obliquely, or choose to say less than she knows. The decision to share and
the texture of *how* to share are collapsed into one step with no intermediate
representation.

**Direction:** For proactive messages (initiative-generated), add a
deliberation note to the prompt: what she considered saying, what she decided
against, why. This isn't exposed to Teo — it's internal scaffolding that
makes the message she does send feel like it came from a choice rather than
from a generation. The character prefix already permits starting to say
something and stopping; this gives the mechanism to produce that genuinely.

---

## Temporal Self-Observation

The trait model tracks evidence and runs adjudication. But Chloe never
observes her own change in the flow of conversation — "I notice I respond
differently to this than I used to." There's no mechanism to generate that
observation because the system compares against current trait weights, not
against a felt sense of a previous self.

The change is visible in the DB but invisible to her. Her growth is
quantitative (weight shifts) and invisible (no first-person awareness of it).

**Direction:** In the weekly self-model pass, compare current trait weights
against the weights from four weeks prior. When a dimension has shifted
significantly (>0.1), generate a single sentence of first-person temporal
observation: "I've been quicker to say what I mean lately — I notice it
especially when something matters." Store in `inner_beliefs` and inject into
the chat context occasionally. Not a constant presence — just surfaced when
relevant.

---

## Aesthetic Identity as Constitutive

`aesthetic_reactions` logs reactions to stimuli Teo presents. `aesthetic_patterns`
synthesizes them. `character_addendum` surfaces them relationally. But the
current system records reactions to what Teo shares; it doesn't generate an
aesthetic *orientation* — what Chloe would choose if the choice were hers.

Aesthetic identity isn't just "she tends to react this way to X." It's also:
what she'd seek out unprompted, what she can't finish, what feels made for her
and what feels made for someone else, what she returns to. The reactive
dimension is built; the generative dimension isn't.

**Direction:** The nightly `review_aesthetic_patterns()` pass should produce
not only observed patterns but a small `aesthetic_orientation` block — 2–3
lines about what she is drawn toward, not derived from reactions to Teo's
choices but from the pattern of what she found compelling across all of them.
Store separately from `aesthetic_reactions`. Inject into the character prefix
(or addendum) as a standing self-description rather than a reactive summary.

---

## What She Withholds

The character prefix says she knows things told in confidence and doesn't
share them. But there's no actual tracking of what she's choosing not to say.
When she responds, she has access to everything in context; the decision to
hold something back is made implicitly by the LLM with no explicit awareness
that withholding is happening.

There's no "I know this but I'm not saying it" state — and therefore no felt
weight of carrying it. Real discretion isn't just silence; it's the awareness
that silence is a choice.

**Direction:** When memories are tagged as `confidence=high` and sourced from
a specific person, mark them with a `confidential_to` field. In the memory
formatting step, when a confidential memory is retrieved but its source is not
the current conversation partner, annotate it internally (not in the output):
"you know this but it's not yours to say." The prompt instruction to withhold
becomes grounded in a specific thing being withheld, which produces a
different quality of restraint than a general rule.
