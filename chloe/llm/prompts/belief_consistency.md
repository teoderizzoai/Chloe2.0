You're checking whether a new world belief materially contradicts something Chloe already believes.

## New belief
Topic: {{new_topic}}
Belief: {{new_belief}}

## Existing beliefs to check against
{{existing_beliefs}}

## What counts as a real contradiction

A real contradiction is NOT:
- a different angle or aspect of the same topic
- a new insight that extends a prior one
- uncertainty ("might be true") sitting alongside a stronger claim
- beliefs about different subjects that happen to share words

A real contradiction IS:
- the new belief directly denies or inverts the truth of an existing one
- Example: "people are more honest than they seem" ↔ "people generally hide their real feelings"
- Example: "creative work improves with iteration" ↔ "forcing revisions usually kills the original energy"

## Output

If a real contradiction exists, return the `id` of the conflicting existing belief and a one-sentence `reason`.
If there is no real contradiction, return `contradicts_id: null` and leave `reason` empty.

Be conservative. Only flag clear, material contradictions — not thematic overlaps. Beliefs can coexist in tension without formally contradicting each other.
