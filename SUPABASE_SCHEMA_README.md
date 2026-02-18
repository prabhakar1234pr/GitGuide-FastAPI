# Minimal Supabase Schema

This schema contains **only the tables and columns** used by the current backend. No legacy or unused columns.

## Tables (15 total)

| Table | Purpose |
|-------|---------|
| **User** | Clerk user sync (id, clerk_user_id, email, name) |
| **projects** | User projects (GitHub repo, skill level, target days, GitHub consent) |
| **roadmap_days** | Days in curriculum (theme, description, concept_ids) |
| **concepts** | Learning concepts (content, tasks parent) |
| **concept_summaries** | Concept summaries for verification agent context |
| **tasks** | Coding tasks per concept |
| **user_day_progress** | User progress per day (todo/doing/done) |
| **user_concept_progress** | User progress per concept |
| **user_task_progress** | User progress per task |
| **project_chunks** | Code chunks for RAG embeddings (Qdrant uses these) |
| **workspaces** | Docker workspaces (container_id, git state) |
| **task_sessions** | Base/head commit per task for verification |
| **task_verification_results** | Verification audit trail |
| **chat_conversations** | Task chatbot conversations |
| **chat_messages** | Chat messages per conversation |

## How to Apply

1. Create a new Supabase project (or use a different account).
2. Go to **SQL Editor** in the Supabase dashboard.
3. Paste the contents of `supabase_schema_minimal.sql`.
4. Run the script.

## After Setup

1. Copy the new project's **URL** and **service_role key** from Supabase → Settings → API.
2. Update your `.env` and GitHub secrets:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
   - `DATABASE_URL` (Connection string from Settings → Database)
   - `SUPABASE_ANON_KEY` (if frontend uses it)

## Relationships

```
User ──┬── projects ──┬── roadmap_days ── concepts ──┬── tasks
       │              │                    │          │
       │              │                    │          └── concept_summaries
       │              │                    │
       │              └── project_chunks   └── user_concept_progress
       │
       ├── user_day_progress (→ roadmap_days)
       ├── user_task_progress (→ tasks)
       ├── workspaces (→ projects)
       │       └── task_sessions (→ tasks)
       │       └── task_verification_results
       └── chat_conversations (→ projects, tasks)
               └── chat_messages
```
