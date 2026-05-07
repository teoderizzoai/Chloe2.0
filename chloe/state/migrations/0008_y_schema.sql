-- Phase Y schema additions

-- persons: add is_active flag for filtering
ALTER TABLE persons ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;

-- inner_beliefs: add revision tracking, source, archived flag, updated_at
ALTER TABLE inner_beliefs ADD COLUMN source TEXT;
ALTER TABLE inner_beliefs ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;
ALTER TABLE inner_beliefs ADD COLUMN updated_at TEXT NOT NULL DEFAULT (datetime('now'));
ALTER TABLE inner_beliefs ADD COLUMN superseded_by TEXT;
ALTER TABLE inner_beliefs ADD COLUMN supersedes   TEXT;
ALTER TABLE inner_beliefs ADD COLUMN revision_note TEXT;

-- inner_goals: add missing_context for gap detection
ALTER TABLE inner_goals ADD COLUMN missing_context TEXT;

-- person_fields: structured knowledge about each person
CREATE TABLE IF NOT EXISTS person_fields (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id  INTEGER NOT NULL REFERENCES persons(id),
    field_name TEXT    NOT NULL,
    value      TEXT,
    updated_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(person_id, field_name)
);

-- narrative_timeline: autobiographical story entries
CREATE TABLE IF NOT EXISTS narrative_timeline (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    valence    REAL,
    source     TEXT NOT NULL,
    source_ref TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
