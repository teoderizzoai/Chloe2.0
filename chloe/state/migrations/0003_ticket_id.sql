-- 0003_ticket_id.sql
-- Add ticket_id column to actions for confirmation flow (C-07)
ALTER TABLE actions ADD COLUMN ticket_id TEXT;
