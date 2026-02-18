-- =============================================================================
-- GITGUIDE / AI TUTOR - Minimal Supabase Schema
-- Run this in your new Supabase project (SQL Editor) to create only required tables.
-- =============================================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================================
-- 1. User (Clerk sync)
-- =============================================================================
CREATE TABLE "User" (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    clerk_user_id TEXT NOT NULL UNIQUE,
    email TEXT,
    name TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 2. projects
-- github_url = template/source repo, user_repo_url = user's fork (after Day 0 Task 2)
-- =============================================================================
CREATE TABLE projects (
    project_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
    project_name TEXT NOT NULL,
    github_url TEXT NOT NULL,
    skill_level TEXT NOT NULL CHECK (skill_level IN ('beginner', 'intermediate', 'advanced')),
    target_days INT NOT NULL CHECK (target_days >= 7 AND target_days <= 30),
    status TEXT NOT NULL DEFAULT 'created',
    user_repo_url TEXT,
    github_access_token TEXT,
    github_consent_accepted BOOLEAN DEFAULT FALSE,
    github_consent_timestamp TIMESTAMPTZ,
    user_current_concept_id UUID,
    curriculum_structure JSONB,
    generation_progress TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 3. roadmap_days
-- =============================================================================
CREATE TABLE roadmap_days (
    day_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    day_number INT NOT NULL,
    theme TEXT NOT NULL,
    description TEXT,
    estimated_minutes INT DEFAULT 60,
    generated_status TEXT NOT NULL DEFAULT 'pending',
    concept_ids UUID[] DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(project_id, day_number)
);

-- =============================================================================
-- 4. concepts
-- =============================================================================
CREATE TABLE concepts (
    concept_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    day_id UUID REFERENCES roadmap_days(day_id) ON DELETE CASCADE,
    order_index INT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    content TEXT,
    estimated_minutes INT DEFAULT 15,
    generated_status TEXT NOT NULL DEFAULT 'pending',
    curriculum_id TEXT,
    repo_anchors JSONB,
    depends_on UUID[] DEFAULT '{}',
    difficulty TEXT,
    objective TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 5. concept_summaries (for verification agent context; upserted by generate_content)
-- =============================================================================
CREATE TABLE concept_summaries (
    concept_id UUID PRIMARY KEY REFERENCES concepts(concept_id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    summary_text TEXT,
    skills_unlocked TEXT[] DEFAULT '{}',
    files_touched TEXT[] DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 6. tasks
-- =============================================================================
CREATE TABLE tasks (
    task_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    concept_id UUID NOT NULL REFERENCES concepts(concept_id) ON DELETE CASCADE,
    order_index INT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    task_type TEXT NOT NULL DEFAULT 'coding',
    estimated_minutes INT DEFAULT 15,
    difficulty TEXT DEFAULT 'medium',
    hints JSONB DEFAULT '[]',
    solution TEXT,
    generated_status TEXT NOT NULL DEFAULT 'pending',
    verification_patterns JSONB DEFAULT '{}',
    test_file_path TEXT,
    test_command TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 7. user_day_progress
-- =============================================================================
CREATE TABLE user_day_progress (
    user_id UUID NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
    day_id UUID NOT NULL REFERENCES roadmap_days(day_id) ON DELETE CASCADE,
    progress_status TEXT NOT NULL DEFAULT 'todo',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, day_id)
);

-- =============================================================================
-- 8. user_concept_progress
-- =============================================================================
CREATE TABLE user_concept_progress (
    user_id UUID NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
    concept_id UUID NOT NULL REFERENCES concepts(concept_id) ON DELETE CASCADE,
    progress_status TEXT NOT NULL DEFAULT 'todo',
    content_read BOOLEAN DEFAULT FALSE,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, concept_id)
);

-- =============================================================================
-- 9. user_task_progress
-- =============================================================================
CREATE TABLE user_task_progress (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
    task_id UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    progress_status TEXT NOT NULL DEFAULT 'not_started',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, task_id)
);

-- =============================================================================
-- 10. project_chunks (for RAG embeddings)
-- =============================================================================
CREATE TABLE project_chunks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    chunk_index INT NOT NULL,
    language TEXT,
    content TEXT NOT NULL,
    token_count INT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 11. workspaces (Docker containers per user-project)
-- =============================================================================
CREATE TABLE workspaces (
    workspace_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    container_id TEXT,
    container_status TEXT DEFAULT 'unknown',
    git_remote_url TEXT,
    current_branch TEXT,
    last_platform_commit TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_active_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 12. task_sessions (base/head commit for verification)
-- =============================================================================
CREATE TABLE task_sessions (
    session_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    base_commit TEXT,
    current_commit TEXT,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- =============================================================================
-- 13. task_verification_results
-- =============================================================================
CREATE TABLE task_verification_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
    workspace_id UUID REFERENCES workspaces(workspace_id) ON DELETE SET NULL,
    verification_status TEXT NOT NULL,
    ast_analysis JSONB DEFAULT '{}',
    github_evidence JSONB DEFAULT '{}',
    test_results JSONB DEFAULT '{}',
    git_diff TEXT,
    pattern_match_results JSONB DEFAULT '{}',
    llm_analysis JSONB,
    hints TEXT[] DEFAULT '{}',
    error_message TEXT,
    verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 14. chat_conversations
-- =============================================================================
CREATE TABLE chat_conversations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    task_id UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    title TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 15. chat_messages
-- =============================================================================
CREATE TABLE chat_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- INDEXES (for common queries)
-- =============================================================================
CREATE INDEX idx_projects_user_id ON projects(user_id);
CREATE INDEX idx_roadmap_days_project_id ON roadmap_days(project_id);
CREATE INDEX idx_concepts_day_id ON concepts(day_id);
CREATE INDEX idx_tasks_concept_id ON tasks(concept_id);
CREATE INDEX idx_project_chunks_project_id ON project_chunks(project_id);
CREATE INDEX idx_workspaces_user_project ON workspaces(user_id, project_id);
CREATE INDEX idx_task_sessions_task_user_workspace ON task_sessions(task_id, user_id, workspace_id);
CREATE INDEX idx_chat_conversations_task_user ON chat_conversations(user_id, project_id, task_id);
CREATE INDEX idx_chat_messages_conversation_id ON chat_messages(conversation_id);

-- =============================================================================
-- ROW LEVEL SECURITY (Optional - enable if using Supabase Auth with RLS)
-- =============================================================================
-- Uncomment and adapt if you use Supabase Auth. With Clerk, your backend uses
-- service_role which bypasses RLS. For anon key + RLS, you'd need policies.
-- ALTER TABLE "User" ENABLE ROW LEVEL SECURITY;
-- etc.
