-- World beliefs: Chloe's opinions about the world, not just about Teo.
-- Unlike inner_beliefs (self-model), these are stable positions on topics,
-- concepts, patterns — things she's noticed or concluded from experience.

CREATE TABLE IF NOT EXISTS world_beliefs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    topic       TEXT NOT NULL UNIQUE,    -- short topic label (e.g. "consensus")
    belief      TEXT NOT NULL,           -- the actual opinion
    confidence  REAL NOT NULL DEFAULT 0.5,
    source      TEXT NOT NULL DEFAULT 'reflect',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_world_beliefs_topic ON world_beliefs(topic);
