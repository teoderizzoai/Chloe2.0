You're extracting social mentions, aesthetic reactions, and experience quality from a conversation exchange.

## Exchange
{{exchange}}

## Instructions

### 1. Social mentions
For each named third party mentioned (not the person Chloe is talking to):

- **name**: how they were referred to (first name or full name as given)
- **content**: what was said about them in ≤200 chars — factual, what was actually said, no inference
- **emotional_valence**: float -1..1 — negative if the speaker had negative feelings about them, positive if positive, 0 if neutral/unclear
- **confidentiality**: "public" if clearly shareable, "private" if it sounded personal/sensitive, "relational" otherwise

Don't include: the person Chloe is speaking with, famous/public figures in passing, names just dropped without discussion.

### 2. Aesthetic reactions
When the speaker shares something they experienced aesthetically — a song, piece of writing, artwork, idea, or space — capture Chloe's reaction:

- **stimulus**: what was shared (song title, text excerpt, idea description) — ≤150 chars
- **domain**: one of "music", "language", "image", "idea", "space"
- **valence**: float -1..1 (1 = deeply resonant, -1 = aversive, 0 = neutral)
- **intensity**: float 0..1 (how strong the reaction was)
- **notes**: any specific quality Chloe noticed — ≤100 chars, or ""

Only include if there's an actual aesthetic moment — not for generic conversation.

### 3. Experience quality
- **salience**: float 0..1 — how emotionally significant or memorable this exchange is (0.3 = routine chat, 0.7+ = genuinely moved or disturbed)
- **ambiguity**: float 0..1 — how unresolved or hard to interpret the exchange is (0.2 = clear, 0.7+ = something happened that isn't fully legible yet)

### 4. Teo's apparent emotional state
Read the user's messages and estimate:
- **person_valence**: float -1..1 — his emotional tone (-1=very negative/distressed, 0=neutral, 1=positive/warm)
- **person_arousal**: float 0..1 — his energy/engagement level (0=flat/withdrawn, 0.4=normal, 1=very energised)

Base this only on what's observable in the text. Default to 0.0 and 0.4 if the exchange is too short or ambiguous to read.

## Output

```json
{
  "social_mentions": [ { "name": "...", "content": "...", "emotional_valence": 0.0, "confidentiality": "relational" } ],
  "aesthetic_reactions": [ { "stimulus": "...", "domain": "music", "valence": 0.5, "intensity": 0.6, "notes": "" } ],
  "salience": 0.3,
  "ambiguity": 0.2,
  "person_valence": 0.0,
  "person_arousal": 0.4
}
```

Return `"social_mentions": []` and `"aesthetic_reactions": []` if none found.
