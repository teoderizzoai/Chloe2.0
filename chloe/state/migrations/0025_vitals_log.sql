-- 0025_vitals_log.sql
-- Periodic snapshots of affect + vitals for the history charts tab.
-- One row per pressure_loop tick (every 10 minutes).
CREATE TABLE IF NOT EXISTS vitals_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    energy      REAL NOT NULL DEFAULT 0.8,
    valence     REAL NOT NULL DEFAULT 0.0,
    arousal     REAL NOT NULL DEFAULT 0.4,
    social_pull REAL NOT NULL DEFAULT 0.5,
    openness    REAL NOT NULL DEFAULT 0.6,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_vitals_log_created ON vitals_log (created_at);
