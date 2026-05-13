-- 0022_narrative_events.sql
-- narrative.py (event logger) and narrative_weaver.py (autobiographical timeline)
-- share narrative_timeline with incompatible schemas. Separate them:
--   narrative_events  — old generic schema for belief/trait/goal events (narrative.py)
--   narrative_timeline — weaver schema for weekly autobiographical entries (narrative_weaver.py)

CREATE TABLE IF NOT EXISTS narrative_events (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,    -- chapter | event | revision | trait_shift | affect_shift
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    valence     REAL,
    source      TEXT NOT NULL DEFAULT 'system',
    source_ref  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_narrative_events_created ON narrative_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_narrative_events_kind ON narrative_events(kind, created_at DESC);
