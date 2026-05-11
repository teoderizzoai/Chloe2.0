-- 0016_share_queue.sql
-- Share queue: things Chloe wants to tell someone, with timing decided by affect + initiative.

CREATE TABLE IF NOT EXISTS share_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    content     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT '',       -- curiosity_thread_id, search_result, unprocessed_memory_id
    for_person  INTEGER REFERENCES persons(id),
    urgency     REAL NOT NULL DEFAULT 0.1,      -- 0..1, low by default
    shared_at   TIMESTAMP,                      -- NULL = not yet shared
    proposed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_share_queue_pending ON share_queue(shared_at, urgency DESC)
    WHERE shared_at IS NULL;
