-- Add employee-specific Day 0 task data and GitHub token to project_access
-- Each employee provides their own GitHub profile, repo URL, first commit, and PAT

ALTER TABLE project_access
ADD COLUMN IF NOT EXISTS user_repo_url TEXT;

ALTER TABLE project_access
ADD COLUMN IF NOT EXISTS github_username TEXT;

ALTER TABLE project_access
ADD COLUMN IF NOT EXISTS user_repo_first_commit TEXT;

ALTER TABLE project_access
ADD COLUMN IF NOT EXISTS github_access_token TEXT;

ALTER TABLE project_access
ADD COLUMN IF NOT EXISTS github_consent_accepted BOOLEAN DEFAULT FALSE;

ALTER TABLE project_access
ADD COLUMN IF NOT EXISTS github_consent_timestamp TIMESTAMPTZ;
