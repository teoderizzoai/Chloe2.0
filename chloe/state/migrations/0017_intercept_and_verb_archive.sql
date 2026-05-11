-- 0017_intercept_and_verb_archive.sql
-- Three small additions wired together:
--   1. verb_proposals: when the post-message intercept detects Teo asking for a
--      capability Chloe doesn't have, we queue a proposal here instead of
--      writing code on the fly. Reflect can later promote a high-confidence
--      proposal to a real define_verb call (still gated).
--   2. dynamic_verbs.archived_at: revoke_verb soft-archives instead of deleting,
--      so the registry can skip archived rows while we still have the source
--      around for audit.
--   3. chat_pending_confirms: in-chat confirm flow. When Chloe issues a
--      kinetic-sensitive verb from chat, the gate opens a confirm ticket and
--      we stash the (action_id, person_id, ticket_id) here so Teo's next
--      consent message in the same conversation can resolve it.

CREATE TABLE IF NOT EXISTS verb_proposals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_text  TEXT NOT NULL,                 -- raw user phrasing
    tool_hint       TEXT,                          -- best-guess existing tool (e.g. 'spotify') or NULL
    verb_hint       TEXT,                          -- best-guess new verb name
    intent          TEXT NOT NULL DEFAULT '',      -- one-line description of capability
    rationale       TEXT NOT NULL DEFAULT '',      -- why the intercept thinks this is a real gap
    person_id       INTEGER REFERENCES persons(id),
    confidence      REAL NOT NULL DEFAULT 0.5,     -- 0..1, how sure the intercept is
    status          TEXT NOT NULL DEFAULT 'pending', -- pending | dismissed | promoted
    promoted_at     TIMESTAMP,                     -- when define_verb was actually called
    resulting_verb  TEXT,                          -- "tool.verb" once promoted
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_verb_proposals_pending
    ON verb_proposals(status, created_at DESC)
    WHERE status = 'pending';

-- Soft archive for dynamic_verbs. NULL = active.
ALTER TABLE dynamic_verbs ADD COLUMN archived_at TIMESTAMP;
ALTER TABLE dynamic_verbs ADD COLUMN archive_reason TEXT;

CREATE TABLE IF NOT EXISTS chat_pending_confirms (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id       INTEGER NOT NULL,
    action_id       TEXT NOT NULL,                 -- ulid in actions.id
    ticket_id       TEXT NOT NULL,                 -- ulid in confirm tickets
    tool            TEXT NOT NULL,
    verb            TEXT NOT NULL,
    preview         TEXT NOT NULL DEFAULT '',
    state           TEXT NOT NULL DEFAULT 'pending', -- pending | resolved | expired
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at     TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chat_pending_confirms_person
    ON chat_pending_confirms(person_id, state, created_at DESC)
    WHERE state = 'pending';

-- Voice drift log: weekly self-model already produces voice_drift_note but
-- nothing consumes it. Persist into a small structured log so chat_api can
-- read the latest entry into the system prompt for ~14 days.
CREATE TABLE IF NOT EXISTS voice_drift_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    note        TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
