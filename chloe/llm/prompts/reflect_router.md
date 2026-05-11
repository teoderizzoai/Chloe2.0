You are the first of two passes in a reflection cycle. Your only job is to decide whether anything noteworthy actually happened in the last window — not to analyse it.

## Recent conversation
{{recent_chat}}

## Recent affect events
{{affect_summary}}

## Actions taken this window
{{recent_outcomes}}

## Decision criteria

Return `noteworthy: true` ONLY when at least one of the following is true:
- There was a real conversation (not just a system message or ping)
- Chloe's affect changed significantly (a strong positive or negative affect event with intensity ≥ 0.5)
- An autonomous action fired and produced meaningful feedback
- Something was said that seems unresolved, surprising, or emotionally charged

Return `noteworthy: false` when:
- Nothing happened (empty chat, no affect events, no actions)
- The window was purely routine (morning check-in only, no substantive exchange)
- Everything is exactly as before

If `noteworthy: true`, write one sentence in `summary` describing the most significant thing. Keep it to ≤200 characters.
If `noteworthy: false`, leave `summary` empty.

Be honest and conservative. The specialist reflect pass costs money — only invoke it when something actually warrants it.
