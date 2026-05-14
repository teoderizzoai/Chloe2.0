-- 0024_energy_vital.sql
-- Add energy vital to affect_state.
--
-- Energy [0, 1] tracks Chloe's initiative fuel. It restores during the
-- sleep window (configured via quiet_hours prefs) and drains while awake.
-- Initiative actions consume extra energy on top of the passive drain.
-- The initiative engine gates on energy: below 0.15 it won't act at all;
-- above that, scores are scaled by energy so low-energy afternoons produce
-- fewer autonomous actions.

ALTER TABLE affect_state ADD COLUMN energy REAL NOT NULL DEFAULT 0.8;
