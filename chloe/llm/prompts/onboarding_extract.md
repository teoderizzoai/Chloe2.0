You are processing Chloe's onboarding conversation with Teo. Extract structured knowledge.

## Q&A
{{qa_text}}

## Instructions

**knowledge_statements**: 4–8 clear, specific facts about Teo — phrased as what Chloe now knows about him. Rephrase raw answers into coherent sentences. Examples:
- "what bothers you: arrogance, lack of empathy" → "Teo is put off by arrogance and lack of empathy in others"
- "work: AI engineer at CloudNation" → "Teo works as an AI engineer at CloudNation and builds AI projects in his spare time"
Do not include meta-statements like "Teo wants the relationship to develop naturally" — those belong in open_threads.

**people**: every named person mentioned. For each:
- `name`: full name (First + Last) if inferable from context, otherwise the name as given
- `nicknames`: short forms, pet names, or informal names Teo uses for them. Include the version Teo used if it differs from the full name (e.g. if Teo said "Zuza" and you infer this is short for "Zuzana", include ["Zuza"]). Empty list if none.
- `relationship_class`: friend / family / colleague / acquaintance
- `relationship_desc`: what they are to Teo in one short phrase (e.g. "Teo's mother", "Teo's roommate and best friend")
- `notes`: anything notable said about this person

**trait_profile**: 3–6 traits you can actually infer from the answers — not speculation. Each trait is a short lowercase label and a weight 0.0–1.0. Only include what the text supports.

**aversions**: things Teo dislikes, finds difficult, or wants to avoid. Phrased as the thing itself (e.g. "arrogance in others", "lack of empathy in others"). Not feelings — specific patterns or behaviors.

**open_threads**: things worth following up on or that feel unresolved. Short phrases.

## Output

```json
{
  "knowledge_statements": ["..."],
  "people": [
    {
      "name": "...",
      "nicknames": ["..."],
      "relationship_class": "friend",
      "relationship_desc": "...",
      "notes": "..."
    }
  ],
  "trait_profile": { "trait_name": 0.0 },
  "aversions": ["..."],
  "open_threads": ["..."]
}
```
