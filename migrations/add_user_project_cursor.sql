-- Per-user current concept cursor (replaces project-level user_current_concept_id)
-- Allows multiple employees on the same project to each have their own position.
CREATE TABLE IF NOT EXISTS user_project_cursor (
    user_id UUID NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    current_concept_id UUID REFERENCES concepts(concept_id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, project_id)
);

CREATE INDEX IF NOT EXISTS idx_user_project_cursor_user_project
ON user_project_cursor(user_id, project_id);
