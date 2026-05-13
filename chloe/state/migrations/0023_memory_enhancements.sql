-- 0023_memory_enhancements.sql
-- Four memory-fragment improvements:
--   1. Reference tracking: last_referenced_at + reference_count — enables
--      a small scoring bonus for memories Chloe keeps coming back to.
--   2. Superseded memories: when "Marco moved to Munich" corrects "Marco moved
--      to Berlin", the old row is marked superseded_by=new_id and filtered
--      from query_fast results.
--   3. Subject person FK: subject_person_id mirrors the person:<name> tag as a
--      proper INTEGER FK, enabling O(1) "all memories about Marco" queries.
--   4. Preflight capture batch grouping: batch_ref ties all captures from a
--      single preflight call together for audit and dedup review.
--
-- Also adds the reply_reactions table for active learning (4a):
--   👍/👎 on individual replies → weekly procedural distillation.

ALTER TABLE memories ADD COLUMN last_referenced_at TEXT;
ALTER TABLE memories ADD COLUMN reference_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memories ADD COLUMN superseded_by INTEGER;
ALTER TABLE memories ADD COLUMN subject_person_id INTEGER;
ALTER TABLE memories ADD COLUMN batch_ref TEXT;

CREATE INDEX IF NOT EXISTS idx_memories_superseded
    ON memories(superseded_by)
    WHERE superseded_by IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_memories_subject_person
    ON memories(subject_person_id)
    WHERE subject_person_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_memories_batch_ref
    ON memories(batch_ref)
    WHERE batch_ref IS NOT NULL;

CREATE TABLE IF NOT EXISTS reply_reactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    reply_memory_id INTEGER,
    person_id       INTEGER NOT NULL,
    reaction        TEXT NOT NULL CHECK(reaction IN ('thumbs_up', 'thumbs_down')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_reply_reactions_person_created
    ON reply_reactions(person_id, created_at DESC);

-- Aesthetic reaction confidentiality (3a)
ALTER TABLE aesthetic_reactions ADD COLUMN confidentiality TEXT NOT NULL DEFAULT 'public';
