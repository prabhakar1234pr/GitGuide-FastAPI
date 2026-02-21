"""
Context builder for task chatbot.
Builds comprehensive context from concept, task, user code, verification, and progress.
"""

import logging
from typing import Any

from supabase import Client

from app.core.supabase_client import execute_with_retry

logger = logging.getLogger(__name__)


async def build_task_context(
    task_id: str,
    user_id: str,
    user_code: list[dict[str, str]],
    supabase: Client,
    verification: dict[str, Any] | None = None,
    is_manager: bool = False,
) -> str:
    """
    Build comprehensive context string for task chatbot.

    Includes:
    - Concept details (title, description, full markdown content)
    - Task details (title, description, hints, task_type)
    - Previous tasks in the concept (for context)
    - User progress (task and concept status)
    - User's code (files they've written)
    - Verification feedback (if available)

    Args:
        task_id: UUID of the task
        user_id: UUID of the user
        user_code: List of {path: str, content: str} for user's open files
        supabase: Supabase client
        verification: Optional verification feedback dict

    Returns:
        Formatted context string
    """
    try:
        # 1. Get task details
        def get_task():
            return supabase.table("tasks").select("*").eq("task_id", task_id).execute()

        task_response = execute_with_retry(get_task)
        if not task_response.data:
            raise ValueError(f"Task {task_id} not found")

        task = task_response.data[0]
        concept_id = task["concept_id"]
        project_id = task.get("project_id")  # May not be in task table

        # 2. Get concept details (with full markdown content)
        def get_concept():
            return supabase.table("concepts").select("*").eq("concept_id", concept_id).execute()

        concept_response = execute_with_retry(get_concept)
        if not concept_response.data:
            raise ValueError(f"Concept {concept_id} not found")

        concept = concept_response.data[0]

        # 3. Get project_id from concept's day if not in task
        if not project_id:
            day_id = concept.get("day_id")
            if day_id:

                def get_day():
                    return (
                        supabase.table("roadmap_days")
                        .select("project_id")
                        .eq("day_id", day_id)
                        .execute()
                    )

                day_response = execute_with_retry(get_day)
                if day_response.data:
                    project_id = day_response.data[0].get("project_id")

        # 4. Get previous tasks in this concept (for context)
        def get_prev_tasks():
            return (
                supabase.table("tasks")
                .select("*")
                .eq("concept_id", concept_id)
                .lt("order_index", task.get("order_index", 0))
                .order("order_index", desc=False)
                .execute()
            )

        prev_tasks_response = execute_with_retry(get_prev_tasks)
        prev_tasks = prev_tasks_response.data if prev_tasks_response.data else []

        # 5. Get user progress
        task_progress = None
        concept_progress = None

        def get_task_progress():
            return (
                supabase.table("user_task_progress")
                .select("*")
                .eq("task_id", task_id)
                .eq("user_id", user_id)
                .execute()
            )

        def get_concept_progress():
            return (
                supabase.table("user_concept_progress")
                .select("*")
                .eq("concept_id", concept_id)
                .eq("user_id", user_id)
                .execute()
            )

        task_progress_response = execute_with_retry(get_task_progress)
        if task_progress_response.data:
            task_progress = task_progress_response.data[0]

        concept_progress_response = execute_with_retry(get_concept_progress)
        if concept_progress_response.data:
            concept_progress = concept_progress_response.data[0]

        # 6. Build context string
        context_parts = []

        # User role section
        context_parts.append("=== USER ROLE ===")
        context_parts.append(
            "Manager (project owner, view only—cannot perform or verify tasks)"
            if is_manager
            else "Employee (learning and implementing tasks)"
        )

        # Concept section
        context_parts.append(f"=== CONCEPT: {concept.get('title', 'Unknown')} ===")
        if concept.get("description"):
            context_parts.append(f"\n{concept['description']}")

        if concept.get("content"):
            context_parts.append(f"\n{concept['content']}")

        # Current task section
        context_parts.append(f"\n\n=== CURRENT TASK: {task.get('title', 'Unknown')} ===")
        context_parts.append(f"Difficulty: {task.get('difficulty', 'medium')}")
        context_parts.append(f"Estimated Time: {task.get('estimated_minutes', 0)} minutes")
        context_parts.append(f"Task Type: {task.get('task_type', 'coding')}")

        if task.get("description"):
            context_parts.append(f"\nDescription:\n{task['description']}")

        hints = task.get("hints")
        if hints and isinstance(hints, list) and len(hints) > 0:
            context_parts.append("\nHints:")
            for hint in hints:
                context_parts.append(f"- {hint}")

        # Previous tasks section
        if prev_tasks:
            context_parts.append("\n\n=== PREVIOUS TASKS IN THIS CONCEPT ===")
            for i, prev_task in enumerate(prev_tasks, 1):
                title = prev_task.get("title", "Unknown")
                desc = prev_task.get("description", "")
                desc_preview = desc[:150] + "..." if len(desc) > 150 else desc
                context_parts.append(f"{i}. {title}: {desc_preview}")

        # User progress section
        context_parts.append("\n\n=== YOUR PROGRESS ===")
        task_status = (
            task_progress.get("progress_status", "not started") if task_progress else "not started"
        )
        concept_status = (
            concept_progress.get("progress_status", "not started")
            if concept_progress
            else "not started"
        )
        context_parts.append(f"Task Status: {task_status}")
        context_parts.append(f"Concept Status: {concept_status}")

        # User code section
        context_parts.append("\n\n=== YOUR CODE ===")
        if user_code:
            for file_info in user_code:
                path = file_info.get("path", "unknown")
                content = file_info.get("content", "")
                # Limit content to avoid token overflow (keep first 2000 chars per file)
                content_preview = (
                    content[:2000] + "\n... (truncated)" if len(content) > 2000 else content
                )
                context_parts.append(f"\nFile: {path}")
                context_parts.append(f"```\n{content_preview}\n```")
        else:
            context_parts.append("No files currently open.")

        # Verification feedback section
        context_parts.append("\n\n=== VERIFICATION FEEDBACK ===")
        if verification:
            passed = verification.get("passed", False)
            overall_feedback = verification.get("overall_feedback", "")
            issues_found = verification.get("issues_found", [])
            suggestions = verification.get("suggestions", [])
            code_quality = verification.get("code_quality", "")

            context_parts.append(f"Passed: {passed}")
            if overall_feedback:
                context_parts.append(f"\nOverall Feedback:\n{overall_feedback}")

            if issues_found:
                context_parts.append("\nIssues Found:")
                for issue in issues_found:
                    context_parts.append(f"- {issue}")

            if suggestions:
                context_parts.append("\nSuggestions:")
                for suggestion in suggestions:
                    context_parts.append(f"- {suggestion}")

            if code_quality:
                context_parts.append(f"\nCode Quality Assessment:\n{code_quality}")
        else:
            context_parts.append(
                "No verification feedback yet. "
                + (
                    "Managers cannot verify—employees verify after implementing."
                    if is_manager
                    else "Try implementing the task and clicking 'Verify Changes'."
                )
            )

        context = "\n".join(context_parts)
        logger.info(f"✅ Built task context for task_id={task_id} ({len(context)} chars)")

        return context

    except Exception as e:
        logger.error(f"❌ Error building task context: {e}", exc_info=True)
        raise
