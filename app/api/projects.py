import logging
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from supabase import Client

from app.agents.day0 import get_day_0_content
from app.config import settings
from app.core.supabase_client import get_supabase_client
from app.services.embedding_pipeline import run_embedding_pipeline
from app.services.qdrant_service import get_qdrant_service
from app.services.smtp_service import SMTPError, send_access_invite_email
from app.services.terminal_service import get_terminal_service
from app.services.workspace_manager import get_workspace_manager
from app.utils.clerk_auth import verify_clerk_token
from app.utils.db_helpers import get_project_if_accessible, get_user_id_from_clerk
from app.utils.github_utils import extract_project_name, validate_github_url
from app.utils.markdown_sanitizer import sanitize_markdown_content

router = APIRouter()
logger = logging.getLogger(__name__)


class CreateProjectRequest(BaseModel):
    github_url: str = Field(..., description="GitHub repository URL")
    skill_level: Literal["beginner", "intermediate", "advanced"] = Field(
        ..., description="User's skill level"
    )
    target_days: int = Field(..., ge=7, le=30, description="Target duration in days (7-30)")

    @field_validator("github_url")
    @classmethod
    def validate_github_url(cls, v: str) -> str:
        if not validate_github_url(v):
            raise ValueError("Invalid GitHub repository URL format")
        return v


@router.post("/create")
async def create_project(
    project_data: CreateProjectRequest,
    background_tasks: BackgroundTasks,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Create a new project in Supabase projects table and automatically start the embedding pipeline

    Flow:
    1. Verify Clerk token (get clerk_user_id)
    2. Get Supabase user_id from User table using clerk_user_id
    3. Extract project name from GitHub URL
    4. Validate input data
    5. Insert project into projects table
    6. Trigger embedding pipeline in background
    7. Return created project data
    """
    api_start_time = time.time()

    try:
        clerk_user_id = user_info["clerk_user_id"]

        logger.info(
            f"⏱️  [TIMING] User clicked 'Let's start building' - API request received at {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        logger.info(f"Creating project for user: {clerk_user_id}")

        # Get Supabase user_id and role from User table
        user_response = (
            supabase.table("User").select("id, role").eq("clerk_user_id", clerk_user_id).execute()
        )

        user_data = user_response.data if user_response.data is not None else []
        if not user_data or len(user_data) == 0:
            raise HTTPException(
                status_code=404,
                detail="User not found in database. Please ensure you're logged in.",
            )

        user_id = user_data[0]["id"]
        user_role = user_data[0].get("role") or "employee"

        if user_role != "manager":
            raise HTTPException(
                status_code=403,
                detail="Only managers can create projects. Sign up as a manager to create guides.",
            )

        # Extract project name from GitHub URL
        try:
            project_name = extract_project_name(project_data.github_url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        # Prepare project data
        # Set user_repo_url = github_url so cloning works before Day 0 Task 2
        project_insert = {
            "user_id": user_id,
            "project_name": project_name,
            "github_url": project_data.github_url,
            "user_repo_url": project_data.github_url,
            "skill_level": project_data.skill_level,
            "target_days": project_data.target_days,
            "status": "created",
        }

        logger.info(f"Inserting project: {project_name}")

        # Insert project into projects table
        project_response = supabase.table("projects").insert(project_insert).execute()

        proj_data = project_response.data if project_response.data is not None else []
        if not proj_data or len(proj_data) == 0:
            raise HTTPException(status_code=500, detail="Failed to create project")

        created_project = proj_data[0]
        project_id = created_project["project_id"]
        github_url = created_project["github_url"]

        api_duration = time.time() - api_start_time
        logger.info(f"Project created successfully: {project_id}")
        logger.info(
            f"⏱️  [TIMING] API endpoint completed in {api_duration:.3f}s - Project inserted into database"
        )

        # Initialize Day 0 content immediately
        try:
            # Call the Day 0 initialization logic directly
            await _initialize_day0_internal(str(project_id), user_id, supabase)
            logger.info("✅ Day 0 initialized")
        except Exception as e:
            logger.error(f"❌ Error initializing Day 0: {e}", exc_info=True)
            # Don't fail project creation if Day 0 fails - it can be retried

        # Trigger embedding pipeline in background
        # Pass the API start time to track total time from user click to completion
        background_tasks.add_task(
            run_embedding_pipeline,
            str(project_id),
            github_url,
            api_start_time,  # Pass API start time to pipeline
        )
        logger.info("⏱️  [TIMING] Background task scheduled - Pipeline will start processing")
        logger.info(f"Embedding pipeline scheduled for project: {project_id}")

        return {
            "success": True,
            "project": {
                "project_id": created_project["project_id"],
                "project_name": created_project["project_name"],
                "github_url": created_project["github_url"],
                "skill_level": created_project["skill_level"],
                "target_days": created_project["target_days"],
                "status": created_project["status"],
                "created_at": created_project["created_at"],
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating project: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create project: {str(e)}") from e


async def _initialize_day0_internal(project_id: str, user_id: str, supabase: Client):
    """
    Internal function to initialize Day 0 content.
    Can be called from project creation or as a standalone endpoint.
    """
    logger.info(f"📝 Initializing Day 0 content for project_id={project_id}")

    # Verify project exists and belongs to the user
    project_response = (
        supabase.table("projects")
        .select("*")
        .eq("project_id", project_id)
        .eq("user_id", user_id)
        .execute()
    )

    if not project_response.data or len(project_response.data) == 0:
        raise ValueError("Project not found or you don't have permission")

    project = project_response.data[0]
    project_status = project.get("status")

    # Check if project is in valid status
    if project_status not in ["created", "processing"]:
        raise ValueError(
            f"Project must be in 'created' or 'processing' status. Current status: {project_status}"
        )

    # Check if Day 0 already exists
    day0_response = (
        supabase.table("roadmap_days")
        .select("day_id, day_number, generated_status")
        .eq("project_id", project_id)
        .eq("day_number", 0)
        .execute()
    )

    day0_id = None
    if day0_response.data and len(day0_response.data) > 0:
        day0_id = day0_response.data[0]["day_id"]
        generated_status = day0_response.data[0].get("generated_status")

        if generated_status == "generated":
            logger.info(f"✅ Day 0 already generated for project {project_id}")
            return {
                "success": True,
                "message": "Day 0 already initialized",
                "day0_id": day0_id,
                "already_exists": True,
            }

    # Insert Day 0 into roadmap_days if not exists
    if not day0_id:
        day0_theme, _ = get_day_0_content()
        day0_insert = {
            "project_id": project_id,
            "day_number": 0,
            "theme": day0_theme["theme"],
            "description": day0_theme["description"],
            "estimated_minutes": 30,
            "generated_status": "pending",
        }

        day0_insert_response = supabase.table("roadmap_days").insert(day0_insert).execute()

        if not day0_insert_response.data:
            raise ValueError("Failed to insert Day 0 into roadmap_days")

        day0_id = day0_insert_response.data[0]["day_id"]
        logger.info(f"✅ Inserted Day 0 into roadmap_days: {day0_id}")

    # Get Day 0 content
    _, day0_concepts = get_day_0_content()

    # Insert concepts with content field
    concepts_to_insert = []
    for concept in day0_concepts:
        raw_content = concept.get("content", "")
        sanitized_content = sanitize_markdown_content(raw_content)
        concepts_to_insert.append(
            {
                "day_id": day0_id,
                "project_id": project_id,
                "order_index": concept["order_index"],
                "title": concept["title"],
                "description": concept["description"],
                "content": sanitized_content,
                "estimated_minutes": concept.get("estimated_minutes", 10),
                "generated_status": "generated",
            }
        )

    concepts_response = supabase.table("concepts").insert(concepts_to_insert).execute()

    if not concepts_response.data:
        raise ValueError("Failed to insert Day 0 concepts")

    logger.info(f"✅ Inserted {len(concepts_response.data)} concepts for Day 0")

    # Create mapping: order_index -> concept_id
    concept_ids_map: dict[int, str] = {}
    for concept_data in concepts_response.data:
        order_idx = concept_data["order_index"]
        concept_id = concept_data["concept_id"]
        concept_ids_map[order_idx] = concept_id

    # Insert tasks for each concept
    total_tasks = 0
    for concept in day0_concepts:
        concept_id = concept_ids_map[concept["order_index"]]

        if concept.get("tasks"):
            tasks_to_insert = []
            for task in concept["tasks"]:
                tasks_to_insert.append(
                    {
                        "concept_id": concept_id,
                        "order_index": task["order_index"],
                        "title": task["title"],
                        "description": task["description"],
                        "task_type": task["task_type"],
                        "estimated_minutes": task.get("estimated_minutes", 15),
                        "difficulty": task.get("difficulty", "medium"),
                        "hints": task.get("hints", []),
                        "solution": task.get("solution"),
                        "generated_status": "generated",
                    }
                )

            supabase.table("tasks").insert(tasks_to_insert).execute()
            total_tasks += len(tasks_to_insert)
            logger.debug(f"   Inserted {len(tasks_to_insert)} tasks for concept {concept['title']}")

    # Mark Day 0 as generated
    supabase.table("roadmap_days").update({"generated_status": "generated"}).eq(
        "day_id", day0_id
    ).execute()

    logger.info(
        f"✅ Day 0 content initialized successfully: {len(concepts_response.data)} concepts, {total_tasks} tasks"
    )

    return {
        "success": True,
        "message": "Day 0 initialized successfully",
        "day0_id": day0_id,
        "concepts_count": len(concepts_response.data),
        "tasks_count": total_tasks,
        "already_exists": False,
    }


@router.post("/{project_id}/initialize-day0")
async def initialize_day0_content(
    project_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Initialize Day 0 content for a project.
    This endpoint should be called when a project is created or in processing phase.

    Flow:
    1. Verify project exists and belongs to user
    2. Check project status (must be "created" or "processing")
    3. Check if Day 0 already exists
    4. Insert Day 0 into roadmap_days if not exists
    5. Generate and save Day 0 content (concepts and tasks)
    6. Mark Day 0 as generated

    Returns:
        dict with success status and details
    """
    try:
        clerk_user_id = user_info["clerk_user_id"]

        # Get user_id
        user_response = (
            supabase.table("User").select("id").eq("clerk_user_id", clerk_user_id).execute()
        )

        if not user_response.data or len(user_response.data) == 0:
            raise HTTPException(status_code=404, detail="User not found")

        user_id = user_response.data[0]["id"]

        # Call internal function
        result = await _initialize_day0_internal(project_id, user_id, supabase)

        return result

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Error initializing Day 0: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to initialize Day 0: {str(e)}") from e


@router.get("/user/list")
async def list_user_projects(
    user_info: dict = Depends(verify_clerk_token), supabase: Client = Depends(get_supabase_client)
):
    """
    List all projects for the authenticated user (owned + granted access).
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])

        # Owned projects
        owned_response = (
            supabase.table("projects")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        owned = owned_response.data or []

        # Granted projects (via project_access)
        access_response = (
            supabase.table("project_access").select("project_id").eq("user_id", user_id).execute()
        )
        granted_ids = [r["project_id"] for r in (access_response.data or [])]
        granted = []
        if granted_ids:
            granted_response = (
                supabase.table("projects").select("*").in_("project_id", granted_ids).execute()
            )
            granted = granted_response.data or []

        # Merge and dedupe by project_id, add is_owner flag, sort by created_at desc
        seen = {p["project_id"]: {**p, "is_owner": True} for p in owned}
        for p in granted:
            if p["project_id"] not in seen:
                seen[p["project_id"]] = {**p, "is_owner": False}
        projects = list(seen.values())
        projects.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        return {"success": True, "projects": projects}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing projects: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list projects: {str(e)}") from e


class GrantAccessRequest(BaseModel):
    email: str = Field(..., description="Employee email to grant access")
    frontend_base_url: str | None = Field(
        None,
        description="Base URL for invite link (e.g. https://crysivo.com). Uses FRONTEND_BASE_URL if not provided.",
    )


INVITE_EXPIRY_HOURS = 168  # 7 days


@router.post("/{project_id}/access")
async def grant_project_access(
    project_id: str,
    body: GrantAccessRequest,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Grant project access to an employee by email. Manager (project owner) only.
    Sends access link via email. Existing users go to sign-in; new users go to get started.
    """
    try:
        manager_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])

        project, is_owner = get_project_if_accessible(supabase, project_id, manager_id)
        if not is_owner:
            raise HTTPException(status_code=403, detail="Only the project owner can grant access")

        email = body.email.strip().lower()
        project_name = project.get("project_name", "Project")

        # Check if inviting self (owner)
        manager_res = supabase.table("User").select("email").eq("id", manager_id).execute()
        manager_email = (manager_res.data[0].get("email") or "").lower() if manager_res.data else ""
        if email == manager_email:
            raise HTTPException(status_code=400, detail="You already own this project")

        # Resolve email -> User.id (optional)
        user_response = supabase.table("User").select("id").eq("email", email).execute()
        employee_id = user_response.data[0]["id"] if user_response.data else None

        # Create invite token for access link
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(UTC) + timedelta(hours=INVITE_EXPIRY_HOURS)).isoformat()

        # Upsert project_invites (one per project+email)
        invite_data = {
            "project_id": project_id,
            "email": email,
            "token": token,
            "granted_by": manager_id,
            "expires_at": expires_at,
        }
        existing = (
            supabase.table("project_invites")
            .select("id")
            .eq("project_id", project_id)
            .eq("email", email)
            .execute()
        )
        if existing.data:
            supabase.table("project_invites").update(invite_data).eq(
                "id", existing.data[0]["id"]
            ).execute()
        else:
            supabase.table("project_invites").insert(invite_data).execute()

        # If user exists, add project_access now
        if employee_id:
            try:
                supabase.table("project_access").insert(
                    {
                        "project_id": project_id,
                        "user_id": employee_id,
                        "granted_by": manager_id,
                    }
                ).execute()
            except Exception as e:
                if "unique" not in str(e).lower() and "duplicate" not in str(e).lower():
                    raise
                # Already granted - still send email

        # Send access link via SMTP (use request origin if provided, else settings)
        base = (body.frontend_base_url or settings.frontend_base_url or "").rstrip("/")
        if not base:
            base = "https://gitguide.dev"
        access_link = f"{base}/invite?token={token}"
        if not send_access_invite_email(email, access_link, project_name):
            return {
                "success": True,
                "message": "Access invite created (email not sent - SMTP not configured)",
                "action": "invite_sent",
            }

        return {
            "success": True,
            "message": "Access invite sent",
            "action": "invite_sent",
        }

    except HTTPException:
        raise
    except SMTPError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Error granting access: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to grant access: {str(e)}") from e


@router.get("/{project_id}/access")
async def list_project_access(
    project_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    List emails with access to the project. Manager (project owner) only.
    """
    try:
        manager_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
        _, is_owner = get_project_if_accessible(supabase, project_id, manager_id)
        if not is_owner:
            raise HTTPException(status_code=403, detail="Only the project owner can view access")

        access_response = (
            supabase.table("project_access")
            .select("user_id")
            .eq("project_id", project_id)
            .execute()
        )
        if not access_response.data:
            return {"success": True, "access_list": []}

        user_ids = [r["user_id"] for r in access_response.data]
        users_response = supabase.table("User").select("id, email").in_("id", user_ids).execute()
        email_map = {u["id"]: u.get("email") or "—" for u in (users_response.data or [])}
        access_list = [{"user_id": uid, "email": email_map.get(uid, "—")} for uid in user_ids]

        return {"success": True, "access_list": access_list}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing access: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list access: {str(e)}") from e


@router.get("/{project_id}/employees-progress")
async def get_employees_progress(
    project_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Get progress of all employees who have access to this project.
    Manager (project owner) only.
    """
    try:
        manager_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
        _, is_owner = get_project_if_accessible(supabase, project_id, manager_id)
        if not is_owner:
            raise HTTPException(
                status_code=403, detail="Only the project owner can view employee progress"
            )

        access_response = (
            supabase.table("project_access")
            .select("user_id, github_username, user_repo_url, github_consent_accepted")
            .eq("project_id", project_id)
            .execute()
        )
        if not access_response.data:
            return {"success": True, "employees": []}

        user_ids = [r["user_id"] for r in access_response.data]
        access_map = {r["user_id"]: r for r in access_response.data}

        users_response = (
            supabase.table("User").select("id, email, name").in_("id", user_ids).execute()
        )
        user_map = {u["id"]: u for u in (users_response.data or [])}

        days_response = (
            supabase.table("roadmap_days")
            .select("day_id, day_number")
            .eq("project_id", project_id)
            .order("day_number", desc=False)
            .execute()
        )
        days = days_response.data or []
        day_ids = [d["day_id"] for d in days]
        total_days = len(days)

        concept_ids = []
        task_ids = []
        if day_ids:
            concepts_response = (
                supabase.table("concepts")
                .select("concept_id, day_id")
                .in_("day_id", day_ids)
                .execute()
            )
            concept_ids = [c["concept_id"] for c in (concepts_response.data or [])]

            if concept_ids:
                tasks_response = (
                    supabase.table("tasks")
                    .select("task_id, concept_id")
                    .in_("concept_id", concept_ids)
                    .execute()
                )
                task_ids = [t["task_id"] for t in (tasks_response.data or [])]

        total_concepts = len(concept_ids)
        total_tasks = len(task_ids)

        employees = []
        for uid in user_ids:
            user_info_row = user_map.get(uid, {})
            pa = access_map.get(uid, {})

            day_progress = (
                (
                    supabase.table("user_day_progress")
                    .select("day_id, progress_status")
                    .eq("user_id", uid)
                    .in_("day_id", day_ids)
                    .execute()
                )
                if day_ids
                else type("R", (), {"data": []})()
            )
            days_done = sum(1 for d in (day_progress.data or []) if d["progress_status"] == "done")

            concept_progress = (
                (
                    supabase.table("user_concept_progress")
                    .select("concept_id, progress_status")
                    .eq("user_id", uid)
                    .in_("concept_id", concept_ids)
                    .execute()
                )
                if concept_ids
                else type("R", (), {"data": []})()
            )
            concepts_done = sum(
                1 for c in (concept_progress.data or []) if c["progress_status"] == "done"
            )

            task_progress = (
                (
                    supabase.table("user_task_progress")
                    .select("task_id, progress_status")
                    .eq("user_id", uid)
                    .in_("task_id", task_ids)
                    .execute()
                )
                if task_ids
                else type("R", (), {"data": []})()
            )
            tasks_done = sum(
                1 for t in (task_progress.data or []) if t["progress_status"] == "done"
            )

            employees.append(
                {
                    "user_id": uid,
                    "email": user_info_row.get("email") or "—",
                    "name": user_info_row.get("name") or "",
                    "github_username": pa.get("github_username"),
                    "user_repo_url": pa.get("user_repo_url"),
                    "github_connected": bool(pa.get("github_consent_accepted")),
                    "days_completed": days_done,
                    "total_days": total_days,
                    "concepts_completed": concepts_done,
                    "total_concepts": total_concepts,
                    "tasks_completed": tasks_done,
                    "total_tasks": total_tasks,
                }
            )

        return {"success": True, "employees": employees}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching employees progress: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch employees progress: {str(e)}"
        ) from e


@router.delete("/{project_id}/access/{access_user_id}")
async def revoke_project_access(
    project_id: str,
    access_user_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Revoke project access from an employee. Manager (project owner) only.
    """
    try:
        manager_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
        _, is_owner = get_project_if_accessible(supabase, project_id, manager_id)
        if not is_owner:
            raise HTTPException(status_code=403, detail="Only the project owner can revoke access")

        supabase.table("project_access").delete().eq("project_id", project_id).eq(
            "user_id", access_user_id
        ).execute()

        # Clean up revoked employee's workspace and terminal sessions
        workspace_manager = get_workspace_manager()
        employee_workspaces = workspace_manager.get_workspaces_by_project(project_id)
        terminal_service = get_terminal_service()
        for ws in employee_workspaces:
            if ws.user_id == access_user_id:
                try:
                    terminal_service.delete_sessions_for_workspace(ws.workspace_id)
                    workspace_manager.destroy_workspace(ws.workspace_id, delete_volume=True)
                    logger.info(f"Cleaned up workspace {ws.workspace_id[:8]} for revoked user")
                except Exception as e:
                    logger.warning(f"Failed to clean workspace on revoke: {e}")

        return {"success": True, "message": "Access revoked"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error revoking access: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to revoke access: {str(e)}") from e


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Get project details by project_id. Allowed if owner or has granted access.
    For employees, merges GitHub fields from project_access so the frontend
    receives the employee's own repo URL, username, etc.
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
        project, is_owner = get_project_if_accessible(supabase, project_id, user_id)

        if not is_owner:
            pa = (
                supabase.table("project_access")
                .select(
                    "user_repo_url, github_username, user_repo_first_commit, github_consent_accepted, github_consent_timestamp"
                )
                .eq("project_id", project_id)
                .eq("user_id", user_id)
                .execute()
            )
            if pa.data:
                employee_fields = {k: v for k, v in pa.data[0].items() if v is not None}
                project = {**project, **employee_fields}

        project.pop("github_access_token", None)
        return {"success": True, "project": {**project, "is_owner": is_owner}}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching project: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch project: {str(e)}") from e


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Delete a project and all associated data (chunks in Supabase, embeddings in Qdrant).

    Flow:
    1. Verify Clerk token (get clerk_user_id)
    2. Get Supabase user_id from User table using clerk_user_id
    3. Verify project exists and belongs to the user
    4. Delete embeddings from Qdrant
    5. Delete project from Supabase (chunks will cascade delete)
    6. Return deletion summary
    """
    try:
        clerk_user_id = user_info["clerk_user_id"]

        logger.info(f"🗑️  Deleting project project_id={project_id} for user: {clerk_user_id}")

        # Get user_id
        user_response = (
            supabase.table("User").select("id").eq("clerk_user_id", clerk_user_id).execute()
        )

        if not user_response.data or len(user_response.data) == 0:
            raise HTTPException(status_code=404, detail="User not found")

        user_id = user_response.data[0]["id"]

        # Verify project exists and belongs to the user
        project_response = (
            supabase.table("projects")
            .select("*")
            .eq("project_id", project_id)
            .eq("user_id", user_id)
            .execute()
        )

        if not project_response.data or len(project_response.data) == 0:
            raise HTTPException(
                status_code=404,
                detail="Project not found or you don't have permission to delete it",
            )

        project = project_response.data[0]
        project_name = project.get("project_name", "Unknown")

        logger.info(f"   Project found: {project_name} (project_id={project_id})")

        # Step 0: Delete all workspaces and terminal sessions for this project
        workspace_manager = get_workspace_manager()
        workspaces = workspace_manager.get_workspaces_by_project(project_id)
        logger.info(f"   Found {len(workspaces)} workspace(s) to clean up")

        terminal_service = get_terminal_service()
        total_sessions_deleted = 0

        for workspace in workspaces:
            logger.info(f"   Cleaning up workspace {workspace.workspace_id[:8]}...")

            # Delete terminal sessions for this workspace
            sessions_deleted = terminal_service.delete_sessions_for_workspace(
                workspace.workspace_id
            )
            total_sessions_deleted += sessions_deleted
            logger.info(
                f"   Deleted {sessions_deleted} terminal session(s) for workspace {workspace.workspace_id[:8]}"
            )

            # Destroy workspace (stops container, removes container, deletes volume)
            try:
                workspace_manager.destroy_workspace(workspace.workspace_id, delete_volume=True)
                logger.info(
                    f"   ✅ Destroyed workspace {workspace.workspace_id[:8]} (container and volume)"
                )
            except Exception as e:
                logger.warning(
                    f"   ⚠️  Failed to destroy workspace {workspace.workspace_id[:8]}: {e}"
                )
                # Continue with project deletion even if workspace cleanup fails

        logger.info(
            f"✅ Cleaned up {len(workspaces)} workspace(s) and {total_sessions_deleted} terminal session(s)"
        )

        # Step 1: Delete embeddings from Qdrant
        qdrant_deleted_count = 0
        try:
            qdrant_service = get_qdrant_service()  # Use singleton for better performance
            qdrant_deleted_count = qdrant_service.delete_points_by_project_id(project_id)
            logger.info(f"✅ Deleted {qdrant_deleted_count} embeddings from Qdrant")
        except Exception as e:
            logger.warning(
                f"⚠️  Failed to delete embeddings from Qdrant (continuing with project deletion): {e}"
            )
            # Continue with project deletion even if Qdrant deletion fails

        # Step 2: Delete project from Supabase (chunks will cascade delete)
        try:
            (
                supabase.table("projects")
                .delete()
                .eq("project_id", project_id)
                .eq("user_id", user_id)
                .execute()
            )
            logger.info("✅ Deleted project from Supabase (chunks cascaded)")
        except Exception as e:
            logger.error(f"❌ Failed to delete project from Supabase: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail=f"Failed to delete project: {str(e)}"
            ) from e

        logger.info(f"🎉 Successfully deleted project project_id={project_id}")

        return {
            "success": True,
            "message": "Project deleted successfully",
            "project_id": project_id,
            "project_name": project_name,
            "deleted_workspaces": len(workspaces),
            "deleted_terminal_sessions": total_sessions_deleted,
            "deleted_embeddings": qdrant_deleted_count,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting project: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete project: {str(e)}") from e
