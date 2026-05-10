CREATE TABLE IF NOT EXISTS dynamic_verbs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tool          TEXT NOT NULL,
    verb          TEXT NOT NULL,
    description   TEXT NOT NULL,
    schema        TEXT NOT NULL DEFAULT '{"type":"object","properties":{}}',
    code          TEXT NOT NULL,
    auth_class    TEXT NOT NULL DEFAULT 'free',
    reversibility REAL NOT NULL DEFAULT 1.0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(tool, verb)
);
