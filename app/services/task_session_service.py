"""
Task Session Service
Tracks base commit per task session for accurate verification.
"""

import logging
from datetime import UTC, datetime

from fastapi import HTTPException
from supabase import Client

from app.core.supabase_client import get_supabase_client
from app.services.git_service import GitService
from app.utils.db_helpers import get_project_if_accessible

logger = logging.getLogger(__name__)


class TaskSessionService:
    """Service for managing task sessions."""

    def __init__(self, supabase: Client | None = None, git_service: GitService | None = None):
        self.supabase = supabase or get_supabase_client()
        self.git_service = git_service or GitService()

    def start_task_session(
        self, task_id: str, user_id: str, workspace_id: str
    ) -> dict[str, object]:
        """
        Create or get an existing task session and capture base commit.
        """
        existing = (
            self.supabase.table("task_sessions")
            .select(
                "session_id, task_id, user_id, workspace_id, base_commit, current_commit, started_at, completed_at"
            )
            .eq("task_id", task_id)
            .eq("user_id", user_id)
            .eq("workspace_id", workspace_id)
            .execute()
        )
        if existing.data:
            return {"success": True, "session": existing.data[0], "created": False}

        workspace = self._get_workspace_row(workspace_id, user_id)
        if not workspace:
            return {"success": False, "error": "Workspace not found"}

        container_id = workspace.get("container_id")
        if not container_id:
            return {"success": False, "error": "Workspace has no container"}

        # Try to get HEAD commit, if it fails with "not a git repository", clone the repo
        rev = self.git_service.git_rev_parse(container_id, "HEAD")
        if not rev.get("success"):
            error_msg = rev.get("error", "")
            error_msg_lower = error_msg.lower()
            logger.debug(f"Git rev-parse failed for container {container_id[:12]}: {error_msg}")

            # Check if error is due to missing git repository
            # Git error messages can vary: "not a git repository", "not a git repo",
            # "fatal: not a git repository", etc.
            is_not_git_repo = (
                "not a git repository" in error_msg_lower or "not a git repo" in error_msg_lower
            )

            if is_not_git_repo:
                logger.info(
                    f"Git repository not found in container {container_id[:12]}, attempting to clone..."
                )
                # Get project info to clone the repository
                workspace_full = (
                    self.supabase.table("workspaces")
                    .select("project_id")
                    .eq("workspace_id", workspace_id)
                    .execute()
                )
                if not workspace_full.data:
                    return {"success": False, "error": "Workspace project not found"}

                project_id = workspace_full.data[0].get("project_id")
                try:
                    project, is_owner = get_project_if_accessible(
                        self.supabase,
                        project_id,
                        user_id,
                        select_fields="user_repo_url, github_url, github_access_token",
                    )
                except HTTPException as e:
                    return {"success": False, "error": e.detail or "Project not found"}
                repo_url = project.get("user_repo_url") or project.get("github_url")
                token = project.get("github_access_token")
                if not is_owner:
                    pa = (
                        self.supabase.table("project_access")
                        .select("user_repo_url, github_access_token")
                        .eq("project_id", project_id)
                        .eq("user_id", user_id)
                        .execute()
                    )
                    if pa.data:
                        repo_url = pa.data[0].get("user_repo_url") or project.get("github_url")
                        token = pa.data[0].get("github_access_token") or token

                if not repo_url:
                    return {
                        "success": False,
                        "error": "Repository URL not found. Please complete Day 0 Task 2 first.",
                    }

                if not token:
                    return {
                        "success": False,
                        "error": "GitHub token not configured. Please connect your GitHub account.",
                    }

                # Clone the repository
                logger.info(
                    f"Attempting to clone repository {repo_url[:50]}... into container {container_id[:12]}"
                )
                clone_result = self.git_service.clone_repository(
                    container_id, repo_url, token=token
                )
                if clone_result.get("status") == "error":
                    error_msg = clone_result.get("message", "Unknown error")
                    logger.error(f"Clone failed for container {container_id[:12]}: {error_msg}")
                    return {"success": False, "error": f"Failed to clone repository: {error_msg}"}

                logger.info("Clone successful, verifying git repository...")
                # Retry getting HEAD commit after cloning
                rev = self.git_service.git_rev_parse(container_id, "HEAD")
                if not rev.get("success"):
                    error_msg = rev.get("error", "Unknown error")
                    logger.error(
                        f"Failed to get HEAD commit after cloning in container {container_id[:12]}: {error_msg}"
                    )
                    return {
                        "success": False,
                        "error": f"Failed to get HEAD commit after cloning: {error_msg}",
                    }

                logger.info(
                    f"Successfully cloned repository and verified HEAD commit: {rev.get('sha', 'unknown')[:7]}"
                )
            else:
                # Other git errors
                return {"success": False, "error": rev.get("error")}

        base_commit = rev.get("sha")
        now = datetime.now(UTC).isoformat()

        row = {
            "task_id": task_id,
            "user_id": user_id,
            "workspace_id": workspace_id,
            "base_commit": base_commit,
            "started_at": now,
        }

        result = self.supabase.table("task_sessions").insert(row).execute()
        if not result.data:
            return {"success": False, "error": "Failed to create task session"}

        return {"success": True, "session": result.data[0], "created": True}

    def get_task_session(self, task_id: str, user_id: str, workspace_id: str) -> dict[str, object]:
        """
        Get a task session for the given task/user/workspace.
        """
        response = (
            self.supabase.table("task_sessions")
            .select(
                "session_id, task_id, user_id, workspace_id, base_commit, current_commit, started_at, completed_at"
            )
            .eq("task_id", task_id)
            .eq("user_id", user_id)
            .eq("workspace_id", workspace_id)
            .execute()
        )
        if not response.data:
            return {"success": False, "error": "Task session not found"}
        return {"success": True, "session": response.data[0]}

    def get_session_by_id(self, session_id: str) -> dict[str, object]:
        """
        Get a task session by session_id.
        """
        session = self._get_session_by_id(session_id)
        if not session:
            return {"success": False, "error": "Task session not found"}
        return {"success": True, "session": session}

    def complete_task_session(
        self, session_id: str, current_commit: str | None = None
    ) -> dict[str, object]:
        """
        Mark a session complete and store the current commit SHA.
        """
        now = datetime.now(UTC).isoformat()
        updates: dict[str, object] = {"completed_at": now}
        if current_commit:
            updates["current_commit"] = current_commit

        result = (
            self.supabase.table("task_sessions")
            .update(updates)
            .eq("session_id", session_id)
            .execute()
        )
        if not result.data:
            return {"success": False, "error": "Failed to update task session"}
        return {"success": True, "session": result.data[0]}

    def get_diff_for_verification(self, session_id: str) -> dict[str, object]:
        """
        Compute diff from base_commit to HEAD for a session.
        """
        session = self._get_session_by_id(session_id)
        if not session:
            return {"success": False, "error": "Task session not found"}

        workspace = self._get_workspace_row(session["workspace_id"], session["user_id"])
        if not workspace:
            return {"success": False, "error": "Workspace not found"}

        container_id = workspace.get("container_id")
        if not container_id:
            return {"success": False, "error": "Workspace has no container"}

        diff = self.git_service.git_diff(
            container_id, base_commit=session["base_commit"], head_commit="HEAD"
        )
        if not diff.get("success"):
            return {"success": False, "error": diff.get("error")}

        return {
            "success": True,
            "diff": diff.get("diff", ""),
            "base_commit": session["base_commit"],
        }

    def _get_session_by_id(self, session_id: str) -> dict[str, object] | None:
        response = (
            self.supabase.table("task_sessions")
            .select(
                "session_id, task_id, user_id, workspace_id, base_commit, current_commit, started_at, completed_at"
            )
            .eq("session_id", session_id)
            .execute()
        )
        if not response.data:
            return None
        return response.data[0]

    def _get_workspace_row(self, workspace_id: str, user_id: str) -> dict[str, object] | None:
        response = (
            self.supabase.table("workspaces")
            .select("workspace_id, user_id, container_id")
            .eq("workspace_id", workspace_id)
            .eq("user_id", user_id)
            .execute()
        )
        if not response.data:
            return None
        return response.data[0]
