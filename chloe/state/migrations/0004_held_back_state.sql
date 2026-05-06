-- 0004_held_back_state.sql
-- Add 'held_back' to actions state check constraint (D-01 deliberation abort)

PRAGMA foreign_keys=OFF;

DROP TABLE IF EXISTS actions_v4;

CREATE TABLE actions_v4 (
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
                    'awaiting_confirmation','confirmed','denied','executed','failed','reverted',
                    'held_back'
                  )),
  result        JSON,
  error         TEXT,
  deliberation  JSON,
  user_response JSON,
  becomes_memory_id INTEGER REFERENCES memories(id),
  ticket_id     TEXT
);

INSERT OR IGNORE INTO actions_v4
  SELECT id, tool, verb, args, intent, cost_tokens, cost_usd, authorization,
         preview, proposed_at, state, result, error, deliberation, user_response,
         becomes_memory_id, ticket_id
  FROM actions;

DROP TABLE actions;
ALTER TABLE actions_v4 RENAME TO actions;

CREATE INDEX IF NOT EXISTS idx_actions_state
  ON actions(state);
CREATE INDEX IF NOT EXISTS idx_actions_tool_proposed
  ON actions(tool, proposed_at DESC);
CREATE INDEX IF NOT EXISTS idx_actions_proposed_at
  ON actions(proposed_at DESC);

PRAGMA foreign_keys=ON;
