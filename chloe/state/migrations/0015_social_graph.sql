-- 0015_social_graph.sql
-- Social graph extensions (Chloe 3.0, Block 6).
-- Adds relationship_class, gen_level, stance, confidentiality to persons.
-- Adds cross_references table for secondary person mentions.

ALTER TABLE persons ADD COLUMN relationship_class TEXT NOT NULL DEFAULT 'peripheral'
    CHECK (relationship_class IN ('primary', 'secondary', 'peripheral'));
ALTER TABLE persons ADD COLUMN gen_level INTEGER NOT NULL DEFAULT 0;
    -- 0=name known, 1=impression forming, 2=model exists, 3=full relational model

ALTER TABLE persons ADD COLUMN warmth_stance REAL NOT NULL DEFAULT 0.5;
ALTER TABLE persons ADD COLUMN trust_stance REAL NOT NULL DEFAULT 0.5;
ALTER TABLE persons ADD COLUMN interest_stance REAL NOT NULL DEFAULT 0.5;

ALTER TABLE persons ADD COLUMN confidentiality_default TEXT NOT NULL DEFAULT 'relational'
    CHECK (confidentiality_default IN ('public', 'relational', 'private'));

-- Cross-references: what one person said about another
CREATE TABLE IF NOT EXISTS person_cross_references (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id    INTEGER NOT NULL REFERENCES persons(id),   -- person being mentioned
    mentioned_by  INTEGER NOT NULL REFERENCES persons(id),   -- who mentioned them
    content       TEXT NOT NULL,
    emotional_valence REAL,
    confidentiality TEXT NOT NULL DEFAULT 'relational',
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_xref_subject ON person_cross_references(subject_id, created_at DESC);

-- Social mentions extracted from chat exchanges
CREATE TABLE IF NOT EXISTS social_mentions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    mentioned_by  INTEGER REFERENCES persons(id),
    content       TEXT NOT NULL DEFAULT '',
    emotional_valence REAL,
    confidentiality TEXT NOT NULL DEFAULT 'relational',
    resolved_to   INTEGER REFERENCES persons(id),  -- set once person is upserted
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
