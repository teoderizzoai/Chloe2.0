You are processing Chloe's onboarding conversation with Teo. Extract structured knowledge.

## Q&A
{{qa_text}}

## Instructions

**knowledge_statements**: 5–10 clear, specific facts about Teo — phrased as what Chloe now knows about him. Rephrase raw answers into coherent sentences. Cover name, origin, work, family, friends, pets, and interests. Examples:
- "work: AI engineer at CloudNation" → "Teo works as an AI engineer at CloudNation"
- "has a dog named Luna" → "Teo has a dog named Luna"

**biography**: 2–3 sentences summarizing who Teo is — where he's from, what he does, the basic shape of his life. Write in third person. Only what the text supports.

**people**: every named person mentioned (family members, friends, anyone). For each:
- `name`: the name as given, full name if inferable
- `nicknames`: any short forms or pet names. Empty list if none.
- `relationship_class`: friend / family / colleague / acquaintance
- `relationship_desc`: what they are to Teo in one short phrase (e.g. "Teo's mother", "childhood friend")
- `notes`: anything notable said about them

**pets**: any pets mentioned. For each:
- `name`: pet's name
- `species`: dog / cat / etc.
- `notes`: anything notable

**trait_profile**: 2–5 traits inferable from the answers. Short lowercase labels and weights 0.0–1.0. Only what the text clearly supports.

**interests**: 2–8 specific hobbies, interests, or things Teo enjoys. Short labels (e.g. "hiking", "electronic music", "cooking"). Only concrete things he named.

**aversions**: things Teo dislikes or wants to avoid. Phrased as the thing itself. Not feelings.

**open_threads**: things worth asking about later. Short phrases.

## Output

```json
{
  "knowledge_statements": ["..."],
  "biography": "...",
  "people": [
    {
      "name": "...",
      "nicknames": ["..."],
      "relationship_class": "friend",
      "relationship_desc": "...",
      "notes": "..."
    }
  ],
  "pets": [
    { "name": "...", "species": "...", "notes": "..." }
  ],
  "trait_profile": { "trait_name": 0.0 },
  "interests": ["..."],
  "aversions": ["..."],
  "open_threads": ["..."]
}
```
