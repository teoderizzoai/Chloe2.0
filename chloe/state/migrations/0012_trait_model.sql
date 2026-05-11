-- 0012_trait_model.sql
-- Add developmental fields to identity_traits.
--   gen_level    0 = behavioral description ("tends to X")
--                1 = character label ("direct")
--                2 = core trait (sustained weight > 0.7 for 30+ days)
--   evidence_json     list of {behavior_observed, at, context}
--   contradictions_json  list of {behavior_observed, at, context}
--   first_observed_at, last_reinforced
--   windows_observed  count of distinct reflect windows where it was reinforced

ALTER TABLE identity_traits ADD COLUMN gen_level INTEGER NOT NULL DEFAULT 0;
ALTER TABLE identity_traits ADD COLUMN evidence_json JSON NOT NULL DEFAULT '[]';
ALTER TABLE identity_traits ADD COLUMN contradictions_json JSON NOT NULL DEFAULT '[]';
ALTER TABLE identity_traits ADD COLUMN first_observed_at TIMESTAMP;
ALTER TABLE identity_traits ADD COLUMN last_reinforced TIMESTAMP;
ALTER TABLE identity_traits ADD COLUMN windows_observed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE identity_traits ADD COLUMN core_since TIMESTAMP;
