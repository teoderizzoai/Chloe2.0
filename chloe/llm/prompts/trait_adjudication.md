You are doing a weekly audit of Chloe's behavioral patterns. This is not a character assessment — it is a behavioral accounting. Describe what she did, not who she is.

## Existing traits (current state)
{{existing_traits}}

## Trait evidence log from the past 7 days
{{evidence_log}}

## Instructions

Return a JSON object with these fields:

- `reinforced`: list of trait names that were clearly reinforced this week — only if there is concrete evidence in the log above.
- `contradicted`: list of trait names that were clearly contradicted — only if there is concrete evidence.
- `weight_updates`: list of `{name, delta}` objects. Delta range: -0.1 to +0.1. Positive = more confident. Negative = less confident. Only include if the evidence justifies it.
- `new_patterns`: list of NEW behavioral patterns that cross the threshold (10+ instances, 3+ separate windows). Each: `{trait_implied: <behavioral description, NOT character label>, first_observed: <date>, evidence_count: <number>}`. Most weeks this is `[]`.
- `decay_candidates`: list of trait names that had NO evidence in the past 14 days — candidates for weight decay.
- `notes`: ≤200 chars, plain observation about this week's behavioral picture.

Rules:
- Trait names use behavioral descriptions at gen_level 0: "tends to X" not "is X."
- Once a trait has 10+ instances across 3+ windows, the system auto-promotes to gen_level 1 (character label). You don't need to do that — just surface the pattern.
- Do not invent patterns. If the evidence log is sparse, return mostly empty lists.
