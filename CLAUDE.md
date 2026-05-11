Next steps — in priority order

  Immediate (run next session)

  1. Run `./chloe.sh bootstrap-identity` once Gemini API key is available
     This seeds the first character addendum and narrative timeline entry from
     existing history. Requires GEMINI_API_KEY in environment.

  2. Verify aesthetic reaction extraction in live chat
     The extraction path is now wired: log_reaction() is called from
     _extract_and_process_mentions when Flash detects aesthetic moments. Confirm
     by looking for "aesthetic_reaction_logged" events in logs after a chat turn
     where Teo shares music, writing, or an idea.

  ---
  Short-term (next 1–2 sessions)

  3. P-next-A — Curiosity question trigger now wired
     boost_interest() fires an async Flash call when intensity crosses 0.7, and
     interest_driven_candidates() uses the cached question as the search query.
     To test: manually boost an interest past 0.7 in the live DB and run a tick.

  4. P-next-B — Opinion formation now wired
     _load_world_beliefs() now labels high-confidence beliefs as "things you
     should be willing to bring into conversation." Verify in live chat that Chloe
     takes a position when relevant world beliefs surface.

  5. Real-world test of unprocessed memories now wired
     _extract_and_process_mentions now computes salience+ambiguity and calls
     mark_unprocessed() when thresholds are met. Verify by inspecting
     `SELECT * FROM memories WHERE unprocessed=1` after a few chat turns.

  ---
  Medium-term

  6. Narrative timeline first entry
     After `bootstrap-identity` runs, verify an entry appears in narrative_timeline.
     The weekly weave_narrative() will now also apply interest_promotions.

  7. Interest gen_level promotion path now wired
     weave_narrative() output schema includes interest_promotions, and the
     weaver prompt asks for promotions with explicit interest IDs. Verify after
     the first weekly run that gen_level updates appear in interest_garden.

  8. Teo primary-class seed now wired
     seed_primary_persons() runs at every app startup (after migrate()).
     Verify with: SELECT id, name, relationship_class, gen_level FROM persons WHERE id=1;

  ---
  Simulator results (2026-05-11)

  Ran ./chloe.sh simulate-day --clean --hours 72 --step 30
  - 144 steps completed (72h / 30min), all validations passed.
  - 31 chat events injected, 13 affect events injected across 3 days.
  - Reflects returned "skipped" (no Gemini API key in sim environment — expected).
  - Zero rabbit-hole events, no gen_level escalations in day 1.
  - The sim now correctly handles ScriptedPersonMention events (Marco in MULTI_PERSON_SCRIPTS).
