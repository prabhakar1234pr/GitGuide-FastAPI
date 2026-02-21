-- project_invites: pending invites for users who don't exist yet
-- When manager adds an email that has no User, we create an invite and send link
-- After sign-up, sync_user grants project_access from pending invites

CREATE TABLE IF NOT EXISTS project_invites (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    granted_by UUID NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(project_id, email)
);

CREATE INDEX IF NOT EXISTS idx_project_invites_token ON project_invites(token);
CREATE INDEX IF NOT EXISTS idx_project_invites_email ON project_invites(email);
CREATE INDEX IF NOT EXISTS idx_project_invites_project_id ON project_invites(project_id);
