-- Add Day 0 task columns to projects table for employer/manager GitHub info
-- Allows managers to store github_username, user_repo_url, user_repo_first_commit
-- (user_repo_url and github_access_token already exist)

ALTER TABLE projects
ADD COLUMN IF NOT EXISTS github_username TEXT;

ALTER TABLE projects
ADD COLUMN IF NOT EXISTS user_repo_first_commit TEXT;
