"""
API routes for task verification.
AI agent-based verification system using Groq (Llama 3.1 70B) with GitHub API tools.
Uses GROQ_API_KEY2 for higher token limits.
"""

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from supabase import Client

from app.config import settings
from app.core.supabase_client import get_supabase_client
from app.services.git_service import GitService
from app.services.github_service import extract_repo_info
from app.services.verification_agent import VerificationAgent
from app.services.workspace_manager import WorkspaceManager
from app.utils.clerk_auth import verify_clerk_token
from app.utils.db_helpers import user_has_project_access

router = APIRouter()
logger = logging.getLogger(__name__)


# ============================================
# Request/Response Models
# ============================================


class VerifyTaskRequest(BaseModel):
    """Request body for task verification."""

    workspace_id: str  # Workspace ID (changed from user_code)


class RequirementCheck(BaseModel):
    """Result of checking a single requirement."""

    met: bool
    feedback: str


class TaskVerificationResponse(BaseModel):
    """Response for task verification."""

    success: bool
    task_id: str
    passed: bool
    overall_feedback: str
    requirements_check: dict[str, RequirementCheck]
    hints: list[str]  # LeetCode-style hints (only if passed=False)
    issues_found: list[str]  # List of problems detected
    suggestions: list[str]  # Improvement suggestions
    code_quality: str
    test_status: str | None = None  # "passed"/"failed"/"not_run"
    pattern_match_status: str | None = None  # "all_matched"/"partial"/"none"


# ============================================
# API Endpoints
# ============================================


@router.post("/{task_id}/verify", response_model=TaskVerificationResponse)
async def verify_task(
    task_id: str,
    request: VerifyTaskRequest,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Verify task using AI agent with GitHub API tools.

    The agent autonomously decides when and how to use GitHub API tools to:
    - Compare commits to see what changed
    - Get file contents at specific commits
    - Analyze code changes against task requirements
    - Provide detailed verification feedback

    Uses Groq (Llama 3.1 70B) with function calling for intelligent tool use.
    """
    try:
        clerk_user_id = user_info["clerk_user_id"]

        # Get user_id
        user_response = (
            supabase.table("User").select("id").eq("clerk_user_id", clerk_user_id).execute()
        )
        if not user_response.data:
            raise HTTPException(status_code=404, detail="User not found")

        user_id = user_response.data[0]["id"]

        # Get workspace
        workspace_manager = WorkspaceManager()
        workspace = workspace_manager.get_workspace(request.workspace_id)
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        # Verify workspace belongs to user
        if workspace.user_id != user_id:
            raise HTTPException(status_code=403, detail="Not authorized to access this workspace")

        # Get task with description
        task_response = (
            supabase.table("tasks")
            .select("task_id, title, description, task_type, concept_id")
            .eq("task_id", task_id)
            .execute()
        )

        if not task_response.data:
            raise HTTPException(status_code=404, detail="Task not found")

        task = task_response.data[0]

        # Verify task belongs to user's project
        concept_response = (
            supabase.table("concepts")
            .select("concept_id, day_id, order_index, title, description")
            .eq("concept_id", task["concept_id"])
            .execute()
        )

        if not concept_response.data:
            raise HTTPException(status_code=404, detail="Concept not found")

        concept = concept_response.data[0]
        current_concept_id = concept["concept_id"]
        current_day_id = concept["day_id"]
        current_concept_order = concept.get("order_index", 0)

        day_response = (
            supabase.table("roadmap_days")
            .select("project_id, day_number, theme, description")
            .eq("day_id", current_day_id)
            .execute()
        )

        if not day_response.data:
            raise HTTPException(status_code=404, detail="Day not found")

        day = day_response.data[0]
        project_id = day["project_id"]
        day_number = day.get("day_number")
        day_theme = day.get("theme")
        day_description = day.get("description")

        has_access, is_owner = user_has_project_access(supabase, project_id, user_id)
        if not has_access:
            raise HTTPException(status_code=403, detail="Not authorized to verify this task")
        if is_owner:
            raise HTTPException(
                status_code=403,
                detail="Managers cannot verify tasks. Only employees can verify and complete tasks in the IDE.",
            )

        proj = (
            supabase.table("projects")
            .select("project_id, user_repo_url, github_url")
            .eq("project_id", project_id)
            .execute()
        )
        if not proj.data:
            raise HTTPException(status_code=404, detail="Project not found")
        project = proj.data[0]
        repo_url = project.get("user_repo_url") or project.get("github_url")
        pa = (
            supabase.table("project_access")
            .select("user_repo_url")
            .eq("project_id", project_id)
            .eq("user_id", user_id)
            .execute()
        )
        if pa.data and pa.data[0].get("user_repo_url"):
            repo_url = pa.data[0]["user_repo_url"]

        if not repo_url:
            raise HTTPException(
                status_code=400,
                detail="Project does not have a user repository URL (notebook repo)",
            )

        # Use app's GitHub token from .env (not user's PAT)
        github_token = settings.git_access_token

        # Get base commit from task session (if exists)
        base_commit = None
        try:
            from app.services.task_session_service import TaskSessionService

            session_service = TaskSessionService()
            task_session_result = session_service.get_task_session(
                task_id, user_id, request.workspace_id
            )
            if task_session_result.get("success"):
                session = task_session_result.get("session", {})
                base_commit = session.get("base_commit")
        except Exception as e:
            logger.warning(f"Could not get task session base_commit: {e}")

        async def _get_remote_head_commit_sha(repo_url: str) -> str | None:
            """
            Get the latest commit SHA for the repo's default branch via GitHub API.
            This avoids requiring Docker access from the API container (Cloud Run-safe).
            """
            try:
                owner, repo = extract_repo_info(repo_url)
                headers = {"Accept": "application/vnd.github.v3+json"}
                if github_token:
                    headers["Authorization"] = f"token {github_token}"
                async with httpx.AsyncClient(timeout=20.0) as client:
                    repo_resp = await client.get(
                        f"https://api.github.com/repos/{owner}/{repo}", headers=headers
                    )
                    repo_resp.raise_for_status()
                    default_branch = repo_resp.json().get("default_branch")
                    if not default_branch:
                        return None
                    commit_resp = await client.get(
                        f"https://api.github.com/repos/{owner}/{repo}/commits/{default_branch}",
                        headers=headers,
                    )
                    commit_resp.raise_for_status()
                    return commit_resp.json().get("sha")
            except Exception as e:
                logger.warning(f"Could not fetch remote HEAD from GitHub: {e}")
                return None

        # Get head commit (prefer local workspace HEAD; fall back to GitHub default branch HEAD)
        head_commit = None
        try:
            git_service = GitService()
            rev_result = git_service.git_rev_parse(workspace.container_id, "HEAD")
            if rev_result.get("success"):
                head_commit = rev_result.get("sha")
            else:
                logger.warning(f"Could not get HEAD commit: {rev_result.get('error')}")
        except Exception as e:
            logger.warning(f"Error getting HEAD commit: {e}")

        if not head_commit:
            head_commit = await _get_remote_head_commit_sha(repo_url)

        if not head_commit:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Could not determine current commit (HEAD). "
                    "Workspace git is unavailable and GitHub HEAD could not be fetched."
                ),
            )

        # Use base_commit if available, otherwise use HEAD~1 as fallback
        if not base_commit:
            # If we don't have a task-session base commit, we cannot reliably compute HEAD~1
            # without local git access; fall back to head_commit (diff may be empty).
            base_commit = head_commit
            logger.warning("No base_commit found; using head_commit as base_commit fallback")

        # Extract task description and requirements
        task_description = task.get("description", "")
        current_task_order = task.get("order_index", 0)

        # Fetch previous concept summaries (max 5, skip for 1st concept)
        previous_concept_summaries = []
        if current_concept_order > 0:  # Skip for 1st concept (order_index 0)
            try:
                # Get all days for this project with their order
                all_days_response = (
                    supabase.table("roadmap_days")
                    .select("day_id, day_number")
                    .eq("project_id", project_id)
                    .order("day_number", desc=False)
                    .execute()
                )

                if all_days_response.data:
                    # Build list of all concepts with their ordering
                    all_concepts_with_order = []
                    for day_data in all_days_response.data:
                        day_id = day_data["day_id"]
                        day_order = day_data.get("day_number", 0)

                        concepts_for_day = (
                            supabase.table("concepts")
                            .select("concept_id, order_index")
                            .eq("day_id", day_id)
                            .order("order_index", desc=False)
                            .execute()
                        )

                        if concepts_for_day.data:
                            for concept_data in concepts_for_day.data:
                                concept_order = concept_data.get("order_index", 0)
                                # Create composite key: (day_order, concept_order) for sorting
                                all_concepts_with_order.append(
                                    {
                                        "concept_id": concept_data["concept_id"],
                                        "sort_key": (day_order, concept_order),
                                    }
                                )

                    # Sort by (day_order, concept_order)
                    all_concepts_with_order.sort(key=lambda x: x["sort_key"])

                    # Find current concept position
                    current_position = None
                    for idx, concept_item in enumerate(all_concepts_with_order):
                        if concept_item["concept_id"] == current_concept_id:
                            current_position = idx
                            break

                    # Get previous 5 concepts
                    if current_position is not None and current_position > 0:
                        start_idx = max(0, current_position - 5)
                        previous_concept_items = all_concepts_with_order[start_idx:current_position]
                        previous_concept_ids = [
                            item["concept_id"] for item in previous_concept_items
                        ]

                        # Get summaries and titles
                        if previous_concept_ids:
                            summaries_response = (
                                supabase.table("concept_summaries")
                                .select("concept_id, summary_text")
                                .in_("concept_id", previous_concept_ids)
                                .execute()
                            )

                            concepts_info_response = (
                                supabase.table("concepts")
                                .select("concept_id, title")
                                .in_("concept_id", previous_concept_ids)
                                .execute()
                            )

                            concepts_info = {
                                c["concept_id"]: c.get("title", "")
                                for c in (concepts_info_response.data or [])
                            }

                            summaries_map = {
                                s["concept_id"]: s.get("summary_text", "")
                                for s in (summaries_response.data or [])
                            }

                            # Build list maintaining order
                            for concept_id in previous_concept_ids:
                                summary_text = summaries_map.get(concept_id, "")
                                if summary_text:
                                    previous_concept_summaries.append(
                                        {
                                            "concept_title": concepts_info.get(
                                                concept_id, "Unknown"
                                            ),
                                            "summary": summary_text,
                                        }
                                    )

            except Exception as e:
                logger.warning(f"Failed to fetch previous concept summaries: {e}")

        # Fetch previous task descriptions (max 5, exclude GitHub tasks, skip for 1st task)
        previous_task_descriptions = []
        if current_task_order > 0:  # Skip for 1st task (order_index 0)
            try:
                # Exclude GitHub-related tasks
                github_task_types = ["github_profile", "github_connect", "create_repo"]

                # Get all days for ordering
                all_days_response = (
                    supabase.table("roadmap_days")
                    .select("day_id, day_number")
                    .eq("project_id", project_id)
                    .order("day_number", desc=False)
                    .execute()
                )

                if all_days_response.data:
                    # Build list of all tasks with their ordering
                    all_tasks_with_order = []
                    for day_data in all_days_response.data:
                        day_id = day_data["day_id"]
                        day_order = day_data.get("day_number", 0)

                        # Get concepts for this day
                        concepts_for_day = (
                            supabase.table("concepts")
                            .select("concept_id, order_index")
                            .eq("day_id", day_id)
                            .order("order_index", desc=False)
                            .execute()
                        )

                        if concepts_for_day.data:
                            for concept_data in concepts_for_day.data:
                                concept_id = concept_data["concept_id"]
                                concept_order = concept_data.get("order_index", 0)

                                # Get tasks for this concept
                                tasks_for_concept = (
                                    supabase.table("tasks")
                                    .select("task_id, title, description, order_index, task_type")
                                    .eq("concept_id", concept_id)
                                    .order("order_index", desc=False)
                                    .execute()
                                )

                                if tasks_for_concept.data:
                                    for task_data in tasks_for_concept.data:
                                        # Skip GitHub tasks
                                        if task_data.get("task_type") not in github_task_types:
                                            task_order = task_data.get("order_index", 0)
                                            # Create composite key: (day_order, concept_order, task_order)
                                            all_tasks_with_order.append(
                                                {
                                                    "task_id": task_data["task_id"],
                                                    "title": task_data.get("title", ""),
                                                    "description": task_data.get("description", ""),
                                                    "sort_key": (
                                                        day_order,
                                                        concept_order,
                                                        task_order,
                                                    ),
                                                }
                                            )

                    # Sort by (day_order, concept_order, task_order)
                    all_tasks_with_order.sort(key=lambda x: x["sort_key"])

                    # Find current task position
                    current_task_position = None
                    for idx, task_item in enumerate(all_tasks_with_order):
                        if task_item["task_id"] == task_id:
                            current_task_position = idx
                            break

                    # Get previous 5 tasks
                    if current_task_position is not None and current_task_position > 0:
                        start_idx = max(0, current_task_position - 5)
                        previous_task_items = all_tasks_with_order[start_idx:current_task_position]

                        # Format task descriptions
                        for task_item in previous_task_items:
                            task_desc = task_item.get("description", "")
                            if task_desc:
                                previous_task_descriptions.append(
                                    {
                                        "task_title": task_item.get("title", "Unknown"),
                                        "description": task_desc,
                                    }
                                )

            except Exception as e:
                logger.warning(f"Failed to fetch previous task descriptions: {e}")

        # Run agent-based verification
        logger.info(
            f"🤖 Running verification agent for task {task_id}: "
            f"base={base_commit[:8]}, head={head_commit[:8]}"
        )

        agent = VerificationAgent()
        verification_result = await agent.verify_task(
            task_description=task_description,
            base_commit=base_commit,
            head_commit=head_commit,
            repo_url=repo_url,
            github_token=github_token,  # App's token from .env (not user's PAT)
            additional_context={
                "task_title": task.get("title", ""),
                "task_type": task.get("task_type", ""),
                "day_number": day_number,
                "day_theme": day_theme,
                "day_description": day_description,
                "concept_title": concept.get("title", ""),
                "concept_description": concept.get("description", ""),
                "previous_concept_summaries": previous_concept_summaries,
                "previous_task_descriptions": previous_task_descriptions,
            },
        )

        # Extract results (already normalized by agent)
        passed = verification_result.get("passed", False)
        overall_feedback = verification_result.get("overall_feedback", "")
        requirements_check_raw = verification_result.get("requirements_check", {})
        hints = verification_result.get("hints", [])
        issues_found = verification_result.get("issues_found", [])
        suggestions = verification_result.get("suggestions", [])
        code_quality = verification_result.get("code_quality", "needs_improvement")
        test_status = verification_result.get("test_status")
        pattern_match_status = verification_result.get("pattern_match_status")

        # Convert requirements_check to proper format
        requirements_check: dict[str, RequirementCheck] = {}
        for req_name, req_data in requirements_check_raw.items():
            if isinstance(req_data, dict):
                requirements_check[req_name] = RequirementCheck(
                    met=req_data.get("met", False),
                    feedback=req_data.get("feedback", ""),
                )
            else:
                requirements_check[req_name] = RequirementCheck(
                    met=bool(req_data),
                    feedback="",
                )

        # If no requirements were checked, create a default one
        if not requirements_check:
            requirements_check["main_requirement"] = RequirementCheck(
                met=passed,
                feedback=overall_feedback,
            )

        logger.info(
            f"✅ Task verification complete: {'PASSED' if passed else 'FAILED'} "
            f"(quality: {code_quality}, test: {test_status}, patterns: {pattern_match_status})"
        )

        # Save verification results to database
        verification_status = "passed" if passed else "failed"
        await _save_verification_results(
            supabase=supabase,
            task_id=task_id,
            user_id=user_id,
            workspace_id=request.workspace_id,
            verification_status=verification_status,
            evidence={"agent_verification": True, "repo_url": repo_url},
            verification_result=verification_result,
            hints=verification_result.get("hints", []),
        )

        # If verification passed, update memory ledger
        if passed:
            await _update_memory_ledger_on_task_pass(
                supabase=supabase,
                task_id=task_id,
                concept_id=task["concept_id"],
                evidence={"changed_files": [], "file_contents": {}},  # Simplified for agent system
            )

        return TaskVerificationResponse(
            success=True,
            task_id=task_id,
            passed=passed,
            overall_feedback=overall_feedback,
            requirements_check=requirements_check,
            hints=hints,
            issues_found=issues_found,
            suggestions=suggestions,
            code_quality=code_quality,
            test_status=test_status,
            pattern_match_status=pattern_match_status,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error verifying task: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to verify task: {str(e)}") from e


async def _save_verification_results(
    supabase: Client,
    task_id: str,
    user_id: str,
    workspace_id: str,
    verification_status: str,
    evidence: dict,
    verification_result: dict,
    hints: list[str],
) -> None:
    """
    Save verification results to task_verification_results table.

    Stores all evidence and analysis for audit trail and debugging.
    """
    try:
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()

        verification_record = {
            "task_id": task_id,
            "user_id": user_id,
            "workspace_id": workspace_id,
            "verification_status": verification_status,
            "ast_analysis": {},
            "github_evidence": (
                {"repo_url": evidence.get("repo_url")} if evidence.get("repo_url") else {}
            ),
            "test_results": {},
            "git_diff": "",
            "pattern_match_results": {},
            "llm_analysis": verification_result,
            "hints": hints,
            "error_message": None,
            "verified_at": now,
            "created_at": now,
        }

        supabase.table("task_verification_results").insert(verification_record).execute()

        logger.debug(f"💾 Saved verification results for task {task_id}")

    except Exception as e:
        logger.error(f"Failed to save verification results: {e}", exc_info=True)
        # Don't fail verification if persistence fails


async def _update_memory_ledger_on_task_pass(
    supabase: Client,
    task_id: str,
    concept_id: str,
    evidence: dict,
) -> None:
    """
    Update memory ledger in database when task passes verification.

    For agent-based system, this is simplified since we don't have detailed AST analysis.
    """
    try:
        # Your current `concepts` schema (per provided DDL) does NOT include
        # `files_touched` or `skills_unlocked`. Skip this step to avoid noisy DB errors.
        # (Verification already succeeded; this is only a best-effort enrichment.)
        logger.debug(
            "Skipping concept memory ledger update (concepts.files_touched/skills_unlocked not in schema)"
        )
        return

        # For agent system, we don't have detailed file/skill extraction
        # This can be enhanced later if needed
        files_touched = evidence.get("changed_files", [])
        skills_unlocked = []

        # Get current concept data
        concept_response = (
            supabase.table("concepts")
            .select("files_touched, skills_unlocked")
            .eq("concept_id", concept_id)
            .execute()
        )

        if not concept_response.data:
            logger.warning(f"Concept {concept_id} not found, skipping memory ledger update")
            return

        concept = concept_response.data[0]
        existing_files = set(concept.get("files_touched") or [])
        existing_skills = set(concept.get("skills_unlocked") or [])

        # Merge new files and skills (avoid duplicates)
        updated_files = list(existing_files | set(files_touched))
        updated_skills = list(existing_skills | set(skills_unlocked))

        # Update concepts table
        supabase.table("concepts").update(
            {
                "files_touched": updated_files,
                "skills_unlocked": updated_skills,
            }
        ).eq("concept_id", concept_id).execute()

        logger.info(
            f"✅ Updated memory ledger for concept {concept_id}: "
            f"{len(updated_files)} files, {len(updated_skills)} skills"
        )

    except Exception as e:
        logger.error(f"Failed to update memory ledger: {e}", exc_info=True)
        # Don't fail verification if memory ledger update fails
