CREATE TABLE IF NOT EXISTS person_affect_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id   INTEGER NOT NULL,
    valence     REAL NOT NULL DEFAULT 0.0,
    arousal     REAL NOT NULL DEFAULT 0.4,
    trigger     TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_person_affect_log_person_created
    ON person_affect_log (person_id, created_at);

CREATE TABLE IF NOT EXISTS inner_anticipations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    valence     REAL NOT NULL DEFAULT 0.0,
    intensity   REAL NOT NULL DEFAULT 0.5,
    target_date TEXT,
    resolved    INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
