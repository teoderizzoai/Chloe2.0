You are selecting the most relevant memories for an ongoing conversation.

Current message:
{{message}}

Recent conversation:
{{history}}

Current affect: {{affect_label}}

Memory candidates:
{{candidates_text}}

Select the {{keep}} most relevant memories from the candidates above.
Prefer memories that are directly relevant to the message, emotionally resonant given the current affect, or grounded in real artifacts.

Return JSON with a "selected" array. Each item must have:
- "id": the integer memory ID exactly as listed above
- "relevance_note": one short sentence explaining why this memory is relevant
