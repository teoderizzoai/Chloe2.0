-- 0005_attachment_depth.sql
-- E-11: Add attachment_depth to persons table and a contact_log for silence tracking.

PRAGMA foreign_keys=OFF;

-- Add attachment_depth to persons (SQLite supports ADD COLUMN)
ALTER TABLE persons ADD COLUMN attachment_depth REAL NOT NULL DEFAULT 0.0;

PRAGMA foreign_keys=ON;
