-- 0020: inner_state_extensions
-- Implements: open questions, ambivalent beliefs, depletion, confidential memories,
-- engagement quality tracking.

CREATE TABLE IF NOT EXISTS inner_questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    domain      TEXT,          -- 'teo', 'world', 'self'
    intensity   REAL DEFAULT 0.5,
    resolved    INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE world_beliefs ADD COLUMN ambivalent INTEGER NOT NULL DEFAULT 0;
ALTER TABLE world_beliefs ADD COLUMN ambivalent_with INTEGER;
ALTER TABLE affect_state ADD COLUMN depletion REAL NOT NULL DEFAULT 0.0;
ALTER TABLE memories ADD COLUMN confidential_to INTEGER;
ALTER TABLE person_affect_log ADD COLUMN engagement_quality REAL NOT NULL DEFAULT 0.5;
