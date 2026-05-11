-- 0011_gen_level_and_unprocessed.sql
-- Developmental constraints (Chloe 3.0, Block 1):
--   - gen_level on interest_garden (0=instance, 1=loose pattern, 2=interest area, 3=concept)
--   - unprocessed flag on memories (ambiguous high-salience experiences sit until weekly review)
--   - held_loosely + noticing flags on world_beliefs (developmental phrasing)
--   - contradicts on world_beliefs (belief tension tracking)

ALTER TABLE interest_garden ADD COLUMN gen_level INTEGER NOT NULL DEFAULT 0;

ALTER TABLE memories ADD COLUMN unprocessed BOOLEAN NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_memories_unprocessed
  ON memories(unprocessed, created_at) WHERE unprocessed = 1;

ALTER TABLE world_beliefs ADD COLUMN held_loosely BOOLEAN NOT NULL DEFAULT 1;
ALTER TABLE world_beliefs ADD COLUMN noticing BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE world_beliefs ADD COLUMN contradicts INTEGER REFERENCES world_beliefs(id);
ALTER TABLE world_beliefs ADD COLUMN confirmation_count INTEGER NOT NULL DEFAULT 1;
