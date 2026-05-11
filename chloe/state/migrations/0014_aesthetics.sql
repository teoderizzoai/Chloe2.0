-- 0014_aesthetics.sql
-- Aesthetic reaction log (Chloe 3.0, Block 5).
-- Raw reactions to specific stimuli — not preferences, not structure.
-- Pattern recognition runs monthly after 90 days of data.

CREATE TABLE IF NOT EXISTS aesthetic_reactions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    stimulus  TEXT NOT NULL,         -- the specific thing (song name, sentence, image desc, etc.)
    domain    TEXT NOT NULL DEFAULT 'unknown',  -- music | language | image | idea | space
    valence   REAL NOT NULL DEFAULT 0.0,        -- -1..1
    intensity REAL NOT NULL DEFAULT 0.5,        -- 0..1
    notes     TEXT NOT NULL DEFAULT '',         -- what she noticed, plain language
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_aesthetic_domain ON aesthetic_reactions(domain, created_at DESC);
