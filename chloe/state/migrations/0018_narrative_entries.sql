CREATE TABLE IF NOT EXISTS narrative_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL DEFAULT 'witness',  -- 'witness', 'weave', 'consolidation'
    text        TEXT NOT NULL,
    salience    REAL NOT NULL DEFAULT 0.5,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_narrative_entries_source_archived
    ON narrative_entries (source, archived);
