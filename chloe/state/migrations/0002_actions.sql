-- 0002_actions.sql
-- Action layer tables for Chloe 2.0 Phase A

-- ──────────────────────────────────────────────────────────────
-- Actions (audit log + gate state machine)
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS actions (
  id            TEXT PRIMARY KEY,
  tool          TEXT NOT NULL,
  verb          TEXT NOT NULL,
  args          JSON NOT NULL DEFAULT '{}',
  intent        TEXT NOT NULL,
  cost_tokens   INTEGER NOT NULL DEFAULT 0,
  cost_usd      REAL NOT NULL DEFAULT 0.0,
  authorization TEXT NOT NULL
                  CHECK (authorization IN ('free','intimate','kinetic','kinetic-sensitive')),
  preview       TEXT NOT NULL,
  proposed_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  state         TEXT NOT NULL DEFAULT 'proposed'
                  CHECK (state IN (
                    'proposed','deliberating','self_aborted','suppressed_by_leash',
                    'awaiting_confirmation','confirmed','denied','executed','failed','reverted'
                  )),
  result        JSON,
  error         TEXT,
  deliberation  JSON,
  user_response JSON,
  becomes_memory_id INTEGER REFERENCES memories(id)
);

CREATE INDEX IF NOT EXISTS idx_actions_state
  ON actions(state);
CREATE INDEX IF NOT EXISTS idx_actions_tool_proposed
  ON actions(tool, proposed_at DESC);
CREATE INDEX IF NOT EXISTS idx_actions_proposed_at
  ON actions(proposed_at DESC);

-- ──────────────────────────────────────────────────────────────
-- Artifact index
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS artifact_index (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  kind              TEXT NOT NULL
                      CHECK (kind IN (
                        'spotify_track','spotify_playlist','gmail_thread',
                        'calendar_event','notes_doc','url','bookmark','reminder'
                      )),
  ref               TEXT NOT NULL UNIQUE,
  title             TEXT,
  snapshot          TEXT,
  created_by_action TEXT REFERENCES actions(id),
  last_verified_at  TIMESTAMP,
  exists_           BOOLEAN NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_artifacts_kind
  ON artifact_index(kind);
CREATE INDEX IF NOT EXISTS idx_artifacts_exists
  ON artifact_index(exists_);

-- ──────────────────────────────────────────────────────────────
-- Preferences (leash configuration)
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS preferences (
  key        TEXT PRIMARY KEY,
  value      JSON NOT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Seed default preferences
INSERT OR IGNORE INTO preferences (key, value) VALUES
  ('quiet_hours',        '{"start":"23:00","end":"08:00","timezone":"Europe/Rome","exempt_verbs":["self.*"]}'),
  ('dont_touch',         '{"gmail_labels":[],"spotify_playlists":[],"notes_folders":[]}'),
  ('auth_ceiling',       '"kinetic"'),
  ('spending_cap_usd_day', '1.50'),
  ('focus_mode',         'false'),
  ('away_mode',          'false'),
  ('web_search_blocklist','[]'),
  ('gmail_footer',       'true'),
  ('ha_allowlist',       '[]'),
  ('ha_blocklist',       '["lock.*","alarm.*","switch.oven*"]');

-- ──────────────────────────────────────────────────────────────
-- Budgets
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS budgets (
  window   TEXT PRIMARY KEY CHECK (window IN ('today','this_hour','this_week')),
  tokens   INTEGER NOT NULL DEFAULT 0,
  usd      REAL    NOT NULL DEFAULT 0.0,
  reset_at TIMESTAMP NOT NULL
);

INSERT OR IGNORE INTO budgets (window, tokens, usd, reset_at) VALUES
  ('today',     0, 0.0, date('now', '+1 day')),
  ('this_hour', 0, 0.0, datetime('now', '+1 hour')),
  ('this_week', 0, 0.0, date('now', 'weekday 1'));
