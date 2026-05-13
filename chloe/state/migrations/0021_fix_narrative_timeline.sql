-- 0021_fix_narrative_timeline.sql
-- narrative_timeline was created with an old generic schema (kind/title/body) in some
-- recovered DBs. Drop and recreate with the correct weaver schema. Always empty at this
-- point because the weaver has never run; no data is lost.

DROP TABLE IF EXISTS narrative_timeline;

CREATE TABLE narrative_timeline (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    period_label        TEXT NOT NULL,
    what_happened       TEXT NOT NULL,
    what_shifted        TEXT NOT NULL DEFAULT '',
    still_sitting_with  TEXT NOT NULL DEFAULT '',
    felt_texture        TEXT NOT NULL DEFAULT '',
    chapter_transition  BOOLEAN NOT NULL DEFAULT 0,
    week_start          TEXT NOT NULL,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_narrative_created ON narrative_timeline(created_at DESC);
