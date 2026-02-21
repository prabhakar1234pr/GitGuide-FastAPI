"""
Git API Router
REST endpoints for git operations inside workspace containers.
"""

import logging
import re
import shlex

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from supabase import Client

from app.core.supabase_client import get_supabase_client
from app.services.external_commit_service import ExternalCommitService
from app.services.git_service import GitService
from app.services.workspace_manager import WorkspaceManager, get_workspace_manager
from app.utils.clerk_auth import verify_clerk_token
from app.utils.db_helpers import get_user_id_from_clerk, user_has_project_access

router = APIRouter()
logger = logging.getLogger(__name__)


class CommitRequest(BaseModel):
    message: str = Field(..., description="Commit message")
    author_name: str | None = None
    author_email: str | None = None


class PushRequest(BaseModel):
    branch: str = Field(default="main", description="Branch name")
    force: bool = False
    set_upstream: bool = Field(
        default=False, description="Set upstream tracking (use for new branches)"
    )


class PullRequest(BaseModel):
    branch: str = Field(default="main", description="Branch name")
    handle_uncommitted: str | None = Field(
        default=None, description="How to handle uncommitted changes: commit|stash|discard"
    )


class ResetExternalRequest(BaseModel):
    confirmed: bool = Field(..., description="User confirmed reset")


class StageRequest(BaseModel):
    files: list[str] | None = Field(
        default=None, description="List of file paths to stage. If empty, stages all changes."
    )


class UnstageRequest(BaseModel):
    files: list[str] | None = Field(
        default=None,
        description="List of file paths to unstage. If empty, unstages all staged files.",
    )


class CreateBranchRequest(BaseModel):
    branch_name: str = Field(..., description="Name of the branch to create")
    start_point: str | None = Field(
        default=None, description="Starting point (commit/branch) for the new branch"
    )


class CheckoutBranchRequest(BaseModel):
    branch_name: str = Field(..., description="Name of the branch to checkout")
    create: bool = Field(default=False, description="Create branch if it doesn't exist")


class DeleteBranchRequest(BaseModel):
    branch_name: str = Field(..., description="Name of the branch to delete")
    force: bool = Field(default=False, description="Force delete even if not merged")


class ResolveConflictRequest(BaseModel):
    file_path: str = Field(..., description="Path to the conflicted file")
    side: str = Field(default="ours", description="Resolution side: ours, theirs, or both (manual)")
    content: str | None = Field(
        default=None, description="Manual resolution content (required if side=both)"
    )


class MergeRequest(BaseModel):
    branch: str = Field(..., description="Branch name to merge into current branch")
    no_ff: bool = Field(
        default=False, description="Create merge commit even if fast-forward is possible"
    )
    message: str | None = Field(default=None, description="Merge commit message")


class ResetToCommitRequest(BaseModel):
    commit: str = Field(..., description="Commit SHA to reset to")
    hard: bool = Field(default=True, description="Hard reset (discard all changes) or soft reset")


def _get_container_id(
    workspace_id: str,
    user_id: str,
    workspace_manager: WorkspaceManager,
) -> str:
    """Get container ID for a workspace, verifying ownership and status."""
    workspace = workspace_manager.get_workspace(workspace_id)

    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    if workspace.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this workspace")

    if not workspace.container_id:
        raise HTTPException(status_code=400, detail="Workspace has no container")

    if workspace.container_status != "running":
        raise HTTPException(
            status_code=400,
            detail=f"Workspace container is not running (status: {workspace.container_status})",
        )

    return workspace.container_id


def _maybe_auto_clone_repo(
    *,
    workspace_id: str,
    user_id: str,
    user_info: dict,
    workspace_manager: WorkspaceManager,
    git_service: GitService,
    container_id: str,
    git_error: str | None,
) -> None:
    """
    If a git operation fails because the workspace isn't a git repo yet,
    attempt to initialize (clone) the repo once.
    """
    err = (git_error or "").lower()
    if "not a git repository" not in err:
        return

    author_name = user_info.get("name") or "GitGuide"
    author_email = user_info.get("email") or "noreply@gitguide.local"
    init = workspace_manager.initialize_git_repo(
        workspace_id,
        user_id,
        author_name=author_name,
        author_email=author_email,
    )
    if not init.get("success"):
        raise HTTPException(status_code=400, detail=init.get("error", "Failed to clone repository"))

    # Best-effort warmup: ensures status/branch metadata can be read immediately.
    try:
        git_service.git_status(container_id)
    except Exception:
        pass


def _get_project_token(supabase: Client, project_id: str, user_id: str) -> str | None:
    has_access, is_owner = user_has_project_access(supabase, project_id, user_id)
    if not has_access:
        return None
    if is_owner:
        r = (
            supabase.table("projects")
            .select("github_access_token")
            .eq("project_id", project_id)
            .eq("user_id", user_id)
            .execute()
        )
        return r.data[0].get("github_access_token") if r.data else None
    pa = (
        supabase.table("project_access")
        .select("github_access_token")
        .eq("project_id", project_id)
        .eq("user_id", user_id)
        .execute()
    )
    return pa.data[0].get("github_access_token") if pa.data else None


def _get_project_repo_url(supabase: Client, project_id: str, user_id: str) -> str | None:
    has_access, is_owner = user_has_project_access(supabase, project_id, user_id)
    if not has_access:
        return None
    proj = (
        supabase.table("projects")
        .select("user_repo_url, github_url")
        .eq("project_id", project_id)
        .execute()
    )
    if not proj.data:
        return None
    project = proj.data[0]
    repo_url = project.get("user_repo_url") or project.get("github_url")
    if not is_owner:
        pa = (
            supabase.table("project_access")
            .select("user_repo_url")
            .eq("project_id", project_id)
            .eq("user_id", user_id)
            .execute()
        )
        if pa.data and pa.data[0].get("user_repo_url"):
            repo_url = pa.data[0]["user_repo_url"]
        else:
            repo_url = project.get("github_url")
    return repo_url


def _normalize_repo_url(url: str) -> str:
    """
    Normalize repo URL by stripping all auth and normalizing format.
    Handles URLs with multiple @ signs.
    """
    cleaned = url.strip()
    # Strip all auth patterns (handles multiple @ signs)
    # Extract protocol and domain/path, removing everything between protocol and domain
    match = re.match(r"(https?://)(?:[^@]+@)+([^/]+(?:/.*)?)", cleaned)
    if match:
        cleaned = match.group(1) + match.group(2)
    else:
        # Fallback: simple strip of first auth pattern
        cleaned = re.sub(r"^(https?://)[^@]+@", r"\1", cleaned)
    # Remove .git suffix
    cleaned = cleaned[:-4] if cleaned.endswith(".git") else cleaned
    # Remove trailing slash
    return cleaned.rstrip("/")


@router.get("/{workspace_id}/status")
def get_status(
    workspace_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    container_id = _get_container_id(workspace_id, user_id, workspace_manager)
    git_service = GitService()

    result = git_service.git_status(container_id)
    if not result.get("success"):
        _maybe_auto_clone_repo(
            workspace_id=workspace_id,
            user_id=user_id,
            user_info=user_info,
            workspace_manager=workspace_manager,
            git_service=git_service,
            container_id=container_id,
            git_error=str(result.get("error") or ""),
        )
        result = git_service.git_status(container_id)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to get git status"))

    return result


@router.get("/{workspace_id}/diff")
def get_diff(
    workspace_id: str,
    base_commit: str | None = Query(default=None),
    head_commit: str | None = Query(default=None),
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    container_id = _get_container_id(workspace_id, user_id, workspace_manager)
    git_service = GitService()

    result = git_service.git_diff(container_id, base_commit=base_commit, head_commit=head_commit)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to get git diff"))

    return result


@router.get("/{workspace_id}/diff/{file_path:path}")
def get_file_diff(
    workspace_id: str,
    file_path: str,
    staged: bool = Query(default=False, description="If true, shows diff of staged changes"),
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    """
    Get diff for a specific file.
    """
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    container_id = _get_container_id(workspace_id, user_id, workspace_manager)
    git_service = GitService()

    result = git_service.git_get_file_diff(container_id, file_path, staged=staged)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to get file diff"))

    return result


@router.post("/{workspace_id}/stage")
def stage_files(
    workspace_id: str,
    request: StageRequest,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    """
    Stage files. If files list is empty or None, stages all changes.
    """
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    container_id = _get_container_id(workspace_id, user_id, workspace_manager)
    git_service = GitService()

    result = git_service.git_add(container_id, files=request.files)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to stage files"))

    return result


@router.post("/{workspace_id}/unstage")
def unstage_files(
    workspace_id: str,
    request: UnstageRequest,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    """
    Unstage files. If files list is empty or None, unstages all staged files.
    """
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    container_id = _get_container_id(workspace_id, user_id, workspace_manager)
    git_service = GitService()

    result = git_service.git_reset(container_id, files=request.files, mode="mixed")
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to unstage files"))

    return result


@router.post("/{workspace_id}/commit")
def commit_changes(
    workspace_id: str,
    request: CommitRequest,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    container_id = _get_container_id(workspace_id, user_id, workspace_manager)
    git_service = GitService()

    author_name = request.author_name or user_info.get("name") or "GitGuide"
    author_email = request.author_email or user_info.get("email") or "noreply@gitguide.local"

    result = git_service.git_commit(container_id, request.message, author_name, author_email)
    if not result.get("success"):
        error_msg = result.get("error", "Failed to commit")
        if "nothing to commit" in error_msg.lower():
            logger.info(f"Nothing to commit for workspace {workspace_id}")
            raise HTTPException(status_code=400, detail="Nothing to commit - no changes detected")
        logger.error(f"Commit failed for workspace {workspace_id}: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)

    return result


@router.post("/{workspace_id}/push")
def push_changes(
    workspace_id: str,
    request: PushRequest,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    workspace = workspace_manager.get_workspace(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if workspace.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this workspace")

    container_id = _get_container_id(workspace_id, user_id, workspace_manager)
    token = _get_project_token(supabase, workspace.project_id, user_id)
    if not token:
        raise HTTPException(status_code=400, detail="GitHub token not configured for this project")

    git_service = GitService()
    repo_url = _get_project_repo_url(supabase, workspace.project_id, user_id)
    if repo_url:
        # Always reset to clean repo URL from database (without auth) before pushing
        # This ensures we start from a known good state
        clean_repo_url = _normalize_repo_url(repo_url)
        # Ensure it's a full URL (add https:// if missing)
        if not clean_repo_url.startswith(("http://", "https://")):
            clean_repo_url = f"https://{clean_repo_url}"
        set_result = git_service.git_set_remote_url(container_id, clean_repo_url)
        if not set_result.get("success"):
            logger.warning(
                f"Failed to reset remote URL: {set_result.get('error')}, continuing anyway"
            )

    # Check if this is a new branch that needs upstream tracking
    # If set_upstream is explicitly requested, use it
    # Otherwise, check if branch exists on remote
    needs_upstream = request.set_upstream
    if not needs_upstream:
        # Check if branch exists on remote
        ls_remote_result = git_service.git_ls_remote(container_id, "origin", request.branch)
        needs_upstream = not ls_remote_result.get("success") or not ls_remote_result.get("sha")

    result = git_service.git_push(
        container_id, request.branch, token=token, force=request.force, set_upstream=needs_upstream
    )
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to push"))

    # Update last_platform_commit after successful push
    # This ensures commits made through the platform are tracked correctly
    # Wrap in try-except to ensure push success is returned even if update fails
    try:
        head_result = git_service.git_rev_parse(container_id, "HEAD")
        if head_result.get("success") and head_result.get("sha"):
            supabase.table("workspaces").update(
                {"last_platform_commit": head_result.get("sha")}
            ).eq("workspace_id", workspace_id).eq("user_id", user_id).execute()
            logger.info(
                f"Updated last_platform_commit to {head_result.get('sha')[:7]} for workspace {workspace_id}"
            )
    except Exception as e:
        logger.warning(f"Failed to update last_platform_commit for workspace {workspace_id}: {e}")
        # Don't fail the push if database update fails

    return result


@router.post("/{workspace_id}/pull")
def pull_changes(
    workspace_id: str,
    request: PullRequest,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    workspace = workspace_manager.get_workspace(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if workspace.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this workspace")

    container_id = _get_container_id(workspace_id, user_id, workspace_manager)
    token = _get_project_token(supabase, workspace.project_id, user_id)
    if not token:
        raise HTTPException(status_code=400, detail="GitHub token not configured for this project")

    git_service = GitService()
    repo_url = _get_project_repo_url(supabase, workspace.project_id, user_id)
    if repo_url:
        current_remote = git_service.git_get_remote_url(container_id)
        if current_remote.get("success"):
            current = _normalize_repo_url(current_remote.get("url", ""))
            desired = _normalize_repo_url(repo_url)
            if current and desired and current != desired:
                set_result = git_service.git_set_remote_url(container_id, repo_url)
                if not set_result.get("success"):
                    raise HTTPException(
                        status_code=500, detail=set_result.get("error", "Failed to update remote")
                    )

    uncommitted = git_service.git_check_uncommitted(container_id)
    if not uncommitted.get("success"):
        raise HTTPException(
            status_code=500, detail=uncommitted.get("error", "Failed to check changes")
        )

    if uncommitted.get("has_changes"):
        action = (request.handle_uncommitted or "").lower()
        if action == "commit":
            author_name = user_info.get("name") or "GitGuide"
            author_email = user_info.get("email") or "noreply@gitguide.local"
            commit_message = "WIP: auto-commit before pull"
            commit_result = git_service.git_commit(
                container_id,
                commit_message,
                author_name=author_name,
                author_email=author_email,
            )
            if not commit_result.get("success"):
                raise HTTPException(
                    status_code=500, detail=commit_result.get("error", "Failed to auto-commit")
                )
        elif action == "stash":
            stash_result = git_service.git_stash(container_id, "WIP: auto-stash before pull")
            if not stash_result.get("success"):
                raise HTTPException(
                    status_code=500, detail=stash_result.get("error", "Failed to stash changes")
                )
        elif action == "discard":
            discard_result = git_service.git_discard(container_id)
            if not discard_result.get("success"):
                raise HTTPException(
                    status_code=500, detail=discard_result.get("error", "Failed to discard changes")
                )
        else:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Uncommitted changes detected",
                    "files": uncommitted.get("files", []),
                },
            )

    result = git_service.git_pull(container_id, request.branch, token=token)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to pull"))

    return result


@router.get("/{workspace_id}/external-commits")
def external_commits(
    workspace_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    service = ExternalCommitService(supabase=supabase)
    result = service.check_external_commits(workspace_id, user_id)
    if not result.get("success"):
        raise HTTPException(
            status_code=500, detail=result.get("error", "Failed to check external commits")
        )
    return result


@router.post("/{workspace_id}/reset-external")
def reset_external_commits(
    workspace_id: str,
    request: ResetExternalRequest,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    service = ExternalCommitService(supabase=supabase)
    result = service.reset_to_platform_commit(workspace_id, user_id, request.confirmed)
    if not result.get("success"):
        raise HTTPException(
            status_code=500, detail=result.get("error", "Failed to reset external commits")
        )
    return result


@router.get("/{workspace_id}/commits")
def list_commits(
    workspace_id: str,
    range_spec: str | None = Query(default=None),
    max_count: int = Query(default=50, ge=1, le=200),
    show_all: bool = Query(
        default=False, description="Show all branches including remote, or only HEAD history"
    ),
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    """
    List commits. By default, only shows commits reachable from HEAD.
    Set show_all=True to see all branches including remote branches.
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
        container_id = _get_container_id(workspace_id, user_id, workspace_manager)
        git_service = GitService()

        result = git_service.git_log(
            container_id, range_spec=range_spec, max_count=max_count, show_all=show_all
        )
        if not result.get("success"):
            _maybe_auto_clone_repo(
                workspace_id=workspace_id,
                user_id=user_id,
                user_info=user_info,
                workspace_manager=workspace_manager,
                git_service=git_service,
                container_id=container_id,
                git_error=str(result.get("error") or ""),
            )
            result = git_service.git_log(
                container_id, range_spec=range_spec, max_count=max_count, show_all=show_all
            )
        if not result.get("success"):
            logger.error(
                f"Failed to get commits for workspace {workspace_id}: {result.get('error')}"
            )
            raise HTTPException(
                status_code=500, detail=result.get("error", "Failed to get commits")
            )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error getting commits for workspace {workspace_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Failed to get commits: {str(e)}") from e


@router.get("/{workspace_id}/commits/graph")
def get_commit_graph(
    workspace_id: str,
    max_count: int = Query(default=50, ge=1, le=200),
    show_all: bool = Query(
        default=False, description="Show all branches including remote, or only HEAD history"
    ),
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    """
    Get commit graph with parent relationships for visualization.
    By default, only shows commits reachable from HEAD (current branch history).
    Set show_all=True to see all branches including remote branches.
    """
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    container_id = _get_container_id(workspace_id, user_id, workspace_manager)
    git_service = GitService()

    result = git_service.git_log_graph(container_id, max_count=max_count, show_all=show_all)
    if not result.get("success"):
        raise HTTPException(
            status_code=500, detail=result.get("error", "Failed to get commit graph")
        )
    return result


@router.get("/{workspace_id}/branches")
def list_branches(
    workspace_id: str,
    include_remote: bool = Query(default=False),
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    """List all branches."""
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    container_id = _get_container_id(workspace_id, user_id, workspace_manager)
    git_service = GitService()

    result = git_service.git_list_branches(container_id, include_remote=include_remote)
    if not result.get("success"):
        _maybe_auto_clone_repo(
            workspace_id=workspace_id,
            user_id=user_id,
            user_info=user_info,
            workspace_manager=workspace_manager,
            git_service=git_service,
            container_id=container_id,
            git_error=str(result.get("error") or ""),
        )
        result = git_service.git_list_branches(container_id, include_remote=include_remote)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to list branches"))
    return result


@router.post("/{workspace_id}/branches")
def create_branch(
    workspace_id: str,
    request: CreateBranchRequest,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    """Create a new branch."""
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    container_id = _get_container_id(workspace_id, user_id, workspace_manager)
    git_service = GitService()

    result = git_service.git_create_branch(container_id, request.branch_name, request.start_point)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to create branch"))
    return result


@router.post("/{workspace_id}/branches/checkout")
def checkout_branch(
    workspace_id: str,
    request: CheckoutBranchRequest,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    """Switch to a branch."""
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    container_id = _get_container_id(workspace_id, user_id, workspace_manager)
    git_service = GitService()

    result = git_service.git_checkout_branch(
        container_id, request.branch_name, create=request.create
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=500, detail=result.get("error", "Failed to checkout branch")
        )
    return result


@router.delete("/{workspace_id}/branches/{branch_name:path}")
def delete_branch(
    workspace_id: str,
    branch_name: str,
    force: bool = Query(default=False),
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    """Delete a branch (local and remote). Supports branch names with slashes (e.g., feature/hi)."""
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    workspace = workspace_manager.get_workspace(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if workspace.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this workspace")

    container_id = _get_container_id(workspace_id, user_id, workspace_manager)
    git_service = GitService()

    # URL decode the branch name in case it was double-encoded
    from urllib.parse import unquote

    branch_name = unquote(branch_name)

    # Get GitHub token for remote branch deletion
    token = _get_project_token(supabase, workspace.project_id, user_id)

    result = git_service.git_delete_branch(
        container_id, branch_name, force=force, token=token, delete_remote=True
    )
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to delete branch"))
    return result


@router.get("/{workspace_id}/conflicts")
def check_conflicts(
    workspace_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    """Check for merge conflicts."""
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    container_id = _get_container_id(workspace_id, user_id, workspace_manager)
    git_service = GitService()

    result = git_service.git_check_conflicts(container_id)
    if not result.get("success"):
        raise HTTPException(
            status_code=500, detail=result.get("error", "Failed to check conflicts")
        )
    return result


@router.get("/{workspace_id}/conflicts/{file_path:path}")
def get_conflict_content(
    workspace_id: str,
    file_path: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    """Get conflict content for a file."""
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    container_id = _get_container_id(workspace_id, user_id, workspace_manager)
    git_service = GitService()

    result = git_service.git_get_conflict_content(container_id, file_path)
    if not result.get("success"):
        raise HTTPException(
            status_code=500, detail=result.get("error", "Failed to get conflict content")
        )
    return result


@router.post("/{workspace_id}/conflicts/resolve")
def resolve_conflict(
    workspace_id: str,
    request: ResolveConflictRequest,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    """Resolve a conflict."""
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    container_id = _get_container_id(workspace_id, user_id, workspace_manager)
    git_service = GitService()

    if request.side == "both" and not request.content:
        raise HTTPException(status_code=400, detail="Content is required for manual resolution")

    # For manual resolution, write content first via file write API
    # The content should be written before calling git_resolve_conflict
    # This is handled by the frontend writing the file first

    result = git_service.git_resolve_conflict(
        container_id, request.file_path, request.content or "", request.side
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=500, detail=result.get("error", "Failed to resolve conflict")
        )
    return result


@router.post("/{workspace_id}/merge")
def merge_branch(
    workspace_id: str,
    request: MergeRequest,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    """
    Merge a branch into the current branch.
    """
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    container_id = _get_container_id(workspace_id, user_id, workspace_manager)
    git_service = GitService()

    author_name = user_info.get("name") or "GitGuide"
    author_email = user_info.get("email") or "noreply@gitguide.local"

    result = git_service.git_merge(
        container_id,
        request.branch,
        no_ff=request.no_ff,
        message=request.message,
        author_name=author_name,
        author_email=author_email,
    )

    if not result.get("success"):
        # Check if it's a conflict
        if result.get("has_conflicts"):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": result.get("error", "Merge conflicts detected"),
                    "conflicts": result.get("conflicts", []),
                },
            )
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to merge"))

    # Update last_platform_commit after successful merge
    # This ensures merge commits made through the platform are tracked correctly
    # Wrap in try-except to ensure merge success is returned even if update fails
    try:
        head_result = git_service.git_rev_parse(container_id, "HEAD")
        if head_result.get("success") and head_result.get("sha"):
            supabase.table("workspaces").update(
                {"last_platform_commit": head_result.get("sha")}
            ).eq("workspace_id", workspace_id).eq("user_id", user_id).execute()
            logger.info(
                f"Updated last_platform_commit to {head_result.get('sha')[:7]} for workspace {workspace_id} after merge"
            )
    except Exception as e:
        logger.warning(
            f"Failed to update last_platform_commit for workspace {workspace_id} after merge: {e}"
        )
        # Don't fail the merge if database update fails

    return result


@router.post("/{workspace_id}/merge/abort")
def abort_merge(
    workspace_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    """
    Abort an ongoing merge.
    """
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    container_id = _get_container_id(workspace_id, user_id, workspace_manager)
    git_service = GitService()

    result = git_service.git_abort_merge(container_id)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to abort merge"))

    return result


@router.post("/{workspace_id}/reset")
def reset_to_commit(
    workspace_id: str,
    request: ResetToCommitRequest,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
):
    """
    Reset HEAD to a specific commit.
    """
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    container_id = _get_container_id(workspace_id, user_id, workspace_manager)
    git_service = GitService()

    # Validate commit SHA format (basic check)
    if not re.match(r"^[a-fA-F0-9]{7,40}$", request.commit):
        raise HTTPException(status_code=400, detail="Invalid commit SHA format")

    if request.hard:
        result = git_service.git_reset_hard(container_id, request.commit)
    else:
        # Soft reset - keep changes in staging area
        # Use git reset --soft to move HEAD without touching index or working tree
        cmd = f"git reset --soft {shlex.quote(request.commit)}"
        exit_code, output = git_service._exec(container_id, cmd)
        if exit_code != 0:
            result = {"success": False, "error": output}
        else:
            result = {"success": True, "output": output}

    if not result.get("success"):
        raise HTTPException(
            status_code=500, detail=result.get("error", "Failed to reset to commit")
        )

    # Update last_platform_commit if reset was successful
    try:
        supabase.table("workspaces").update({"last_platform_commit": request.commit}).eq(
            "workspace_id", workspace_id
        ).eq("user_id", user_id).execute()
        logger.info(
            f"Updated last_platform_commit to {request.commit[:7]} for workspace {workspace_id} after reset"
        )
    except Exception as e:
        logger.warning(
            f"Failed to update last_platform_commit for workspace {workspace_id} after reset: {e}"
        )

    return result
