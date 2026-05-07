You are analyzing a week of action-feedback pairs from an AI personal assistant.

Your job is to extract concise procedural rules that the assistant should follow in the future.

## Feedback pairs:
{{feedback_pairs}}

## Instructions:
{{instructions}}

Return a JSON array of ProceduralRule objects. Each rule should be:
- Specific: reference the tool and situation
- Actionable: starts with "When..." or "Always..." or "Never..."
- Evidence-based: grounded in the pairs provided

Return [] if no clear patterns emerge.
