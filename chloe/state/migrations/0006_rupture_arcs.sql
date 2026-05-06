-- 0006_rupture_arcs.sql
-- E-12: Add 'rupture' kind and state/tracking columns to arcs table.

PRAGMA foreign_keys=OFF;

CREATE TABLE IF NOT EXISTS arcs_v6 (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  kind                 TEXT NOT NULL
                         CHECK (kind IN (
                           'melancholic_stretch','restless_phase','curious_spell',
                           'withdrawn_period','rupture'
                         )),
  intensity            REAL NOT NULL DEFAULT 0.3,
  duration_h           REAL NOT NULL DEFAULT 0.0,
  active               BOOLEAN NOT NULL DEFAULT 1,
  state                TEXT NOT NULL DEFAULT 'active'
                         CHECK (state IN ('active','resolved','faded')),
  note                 TEXT,
  positive_turns_count INTEGER NOT NULL DEFAULT 0,
  started_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ended_at             TIMESTAMP
);

INSERT OR IGNORE INTO arcs_v6
  SELECT id, kind, intensity, duration_h, active,
         CASE WHEN active = 1 THEN 'active' ELSE 'resolved' END AS state,
         NULL AS note,
         0 AS positive_turns_count,
         started_at, ended_at
  FROM arcs;

DROP TABLE IF EXISTS arcs;
ALTER TABLE arcs_v6 RENAME TO arcs;

CREATE INDEX IF NOT EXISTS idx_arcs_kind_active
  ON arcs(kind, active);

PRAGMA foreign_keys=ON;
