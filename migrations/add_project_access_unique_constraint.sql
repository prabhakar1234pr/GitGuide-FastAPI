-- Add unique constraint on project_access(project_id, user_id) to prevent duplicate grants
-- Run in Supabase SQL Editor. If duplicates exist, remove them first:
--   DELETE FROM project_access a USING project_access b
--   WHERE a.ctid < b.ctid AND a.project_id = b.project_id AND a.user_id = b.user_id;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'project_access_project_user_unique'
  ) THEN
    ALTER TABLE project_access
    ADD CONSTRAINT project_access_project_user_unique UNIQUE(project_id, user_id);
  END IF;
END $$;
