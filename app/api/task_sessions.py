"""
Task Sessions API
Endpoints for base commit tracking per task session.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from supabase import Client

from app.core.supabase_client import get_supabase_client
from app.services.task_session_service import TaskSessionService
from app.services.workspace_manager import get_workspace_manager
from app.utils.clerk_auth import verify_clerk_token
from app.utils.db_helpers import get_user_id_from_clerk, user_has_project_access

router = APIRouter()
logger = logging.getLogger(__name__)


class StartSessionRequest(BaseModel):
    task_id: str
    workspace_id: str


class CompleteSessionRequest(BaseModel):
    current_commit: str | None = None


@router.post("/start")
def start_task_session(
    request: StartSessionRequest,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])

    workspace_manager = get_workspace_manager()
    workspace = workspace_manager.get_workspace(request.workspace_id)

    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    if workspace.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Task sessions are for employees only - managers (project owners) cannot start them
    has_access, is_owner = user_has_project_access(supabase, workspace.project_id, user_id)
    if not has_access:
        raise HTTPException(status_code=403, detail="Project access required")
    if is_owner:
        raise HTTPException(status_code=403, detail="Managers cannot start task sessions")

    if not workspace.container_id:
        raise HTTPException(status_code=400, detail="Workspace has no container")

    # Check if container is running, start it if not
    if workspace.container_status != "running":
        logger.info(
            f"Container {workspace.container_id[:12]} is not running (status: {workspace.container_status}), starting..."
        )
        success = workspace_manager.start_workspace(request.workspace_id)
        if not success:
            # Refresh to get current status for error message
            workspace = workspace_manager.get_workspace(request.workspace_id)
            status = workspace.container_status if workspace else "unknown"
            raise HTTPException(
                status_code=503,
                detail=f"Workspace container failed to start (status: {status}). Try refreshing or recreating the workspace.",
            )
        workspace = workspace_manager.get_workspace(request.workspace_id)
        if not workspace or workspace.container_status != "running":
            raise HTTPException(
                status_code=503,
                detail="Container failed to start. Try refreshing the page or recreating the workspace.",
            )

    # Ensure the user's repo is cloned before starting the task session.
    # This prevents git operations from failing with "not a git repository".
    try:
        init_result = workspace_manager.initialize_git_repo(
            request.workspace_id,
            user_id,
            author_name=user_info.get("name") or "GitGuide",
            author_email=user_info.get("email") or "noreply@gitguide.local",
        )
        if not init_result.get("success"):
            raise HTTPException(
                status_code=500,
                detail=init_result.get("error", "Failed to clone repository into workspace"),
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error initializing git repo for task session: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to initialize git repo: {str(e)}"
        ) from e

    service = TaskSessionService(supabase=supabase)

    result = service.start_task_session(request.task_id, user_id, request.workspace_id)
    if not result.get("success"):
        raise HTTPException(
            status_code=500, detail=result.get("error", "Failed to start task session")
        )

    return result


@router.get("/{session_id}")
def get_task_session(
    session_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    service = TaskSessionService(supabase=supabase)

    session_result = service.get_session_by_id(session_id)
    if not session_result.get("success"):
        raise HTTPException(status_code=404, detail="Task session not found")
    session = session_result["session"]
    if session.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    return {"success": True, "session": session}


@router.post("/{session_id}/complete")
def complete_task_session(
    session_id: str,
    request: CompleteSessionRequest,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    service = TaskSessionService(supabase=supabase)

    session_result = service.get_session_by_id(session_id)
    if not session_result.get("success"):
        raise HTTPException(status_code=404, detail="Task session not found")
    session = session_result["session"]
    if session.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    result = service.complete_task_session(session_id, current_commit=request.current_commit)
    if not result.get("success"):
        raise HTTPException(
            status_code=500, detail=result.get("error", "Failed to complete task session")
        )

    return result


@router.get("/{session_id}/diff")
def get_task_session_diff(
    session_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
    service = TaskSessionService(supabase=supabase)

    session_result = service.get_session_by_id(session_id)
    if not session_result.get("success"):
        raise HTTPException(status_code=404, detail="Task session not found")
    session = session_result["session"]
    if session.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    result = service.get_diff_for_verification(session_id)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to get diff"))

    return result
