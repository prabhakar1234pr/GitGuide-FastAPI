-- =============================================================================
-- RLS for user_project_cursor
-- Users can only access their own cursor (user_id = their User.id)
-- =============================================================================
ALTER TABLE user_project_cursor ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view their own project cursor"
ON user_project_cursor
FOR SELECT
USING (
  user_id = (
    SELECT id FROM "User" WHERE clerk_user_id = auth.jwt() ->> 'sub'
  )
);

CREATE POLICY "Users can insert their own project cursor"
ON user_project_cursor
FOR INSERT
WITH CHECK (
  user_id = (
    SELECT id FROM "User" WHERE clerk_user_id = auth.jwt() ->> 'sub'
  )
);

CREATE POLICY "Users can update their own project cursor"
ON user_project_cursor
FOR UPDATE
USING (
  user_id = (
    SELECT id FROM "User" WHERE clerk_user_id = auth.jwt() ->> 'sub'
  )
)
WITH CHECK (
  user_id = (
    SELECT id FROM "User" WHERE clerk_user_id = auth.jwt() ->> 'sub'
  )
);

CREATE POLICY "Users can delete their own project cursor"
ON user_project_cursor
FOR DELETE
USING (
  user_id = (
    SELECT id FROM "User" WHERE clerk_user_id = auth.jwt() ->> 'sub'
  )
);

-- =============================================================================
-- RLS for project_invites
-- Only project owners (managers) can manage invites for their projects
-- =============================================================================
ALTER TABLE project_invites ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Project owners can view invites for their projects"
ON project_invites
FOR SELECT
USING (
  EXISTS (
    SELECT 1
    FROM projects p
    JOIN "User" u ON p.user_id = u.id
    WHERE p.project_id = project_invites.project_id
      AND u.clerk_user_id = auth.jwt() ->> 'sub'
  )
);

CREATE POLICY "Project owners can create invites for their projects"
ON project_invites
FOR INSERT
WITH CHECK (
  EXISTS (
    SELECT 1
    FROM projects p
    JOIN "User" u ON p.user_id = u.id
    WHERE p.project_id = project_invites.project_id
      AND u.clerk_user_id = auth.jwt() ->> 'sub'
  )
);

CREATE POLICY "Project owners can update invites for their projects"
ON project_invites
FOR UPDATE
USING (
  EXISTS (
    SELECT 1
    FROM projects p
    JOIN "User" u ON p.user_id = u.id
    WHERE p.project_id = project_invites.project_id
      AND u.clerk_user_id = auth.jwt() ->> 'sub'
  )
)
WITH CHECK (
  EXISTS (
    SELECT 1
    FROM projects p
    JOIN "User" u ON p.user_id = u.id
    WHERE p.project_id = project_invites.project_id
      AND u.clerk_user_id = auth.jwt() ->> 'sub'
  )
);

CREATE POLICY "Project owners can delete invites for their projects"
ON project_invites
FOR DELETE
USING (
  EXISTS (
    SELECT 1
    FROM projects p
    JOIN "User" u ON p.user_id = u.id
    WHERE p.project_id = project_invites.project_id
      AND u.clerk_user_id = auth.jwt() ->> 'sub'
  )
);
