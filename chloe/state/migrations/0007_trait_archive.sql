-- 0007_trait_archive.sql
-- Add archived flag and reason to identity_traits for self-archiving via self_tools.
ALTER TABLE identity_traits ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;
ALTER TABLE identity_traits ADD COLUMN archive_reason TEXT NOT NULL DEFAULT '';
