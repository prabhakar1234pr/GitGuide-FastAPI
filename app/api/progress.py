"""
API routes for user progress tracking.
Handles day, concept, and task progress with analytics timestamps.

Includes support for:
- Lazy loading: update user_current_concept_id for sliding window generation
- Concept-level tracking: track individual concept completion
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from supabase import Client

from app.core.supabase_client import execute_with_retry, get_supabase_client
from app.services.roadmap_client import call_roadmap_service_incremental_sync
from app.services.task_validation import validate_task_completion
from app.utils.clerk_auth import verify_clerk_token
from app.utils.db_helpers import (
    get_project_if_accessible,
    get_user_id_from_clerk,
    user_has_project_access,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class UpdateProgressRequest(BaseModel):
    progress_status: str


class UpdateCurrentConceptRequest(BaseModel):
    """Request to update user's current concept position for lazy loading."""

    concept_id: str


@router.get("/{project_id}")
async def get_progress(
    project_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Get user's progress across all days, concepts, and tasks.
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
        has_access, _ = user_has_project_access(supabase, project_id, user_id)
        if not has_access:
            raise HTTPException(status_code=404, detail="Project not found")

        # Get all day progress
        day_progress_response = (
            supabase.table("user_day_progress").select("*").eq("user_id", user_id).execute()
        )
        day_progress = {p["day_id"]: p for p in (day_progress_response.data or [])}

        # Initialize Day 0 if needed
        day0_response = (
            supabase.table("roadmap_days")
            .select("day_id, day_number")
            .eq("project_id", project_id)
            .eq("day_number", 0)
            .execute()
        )
        if day0_response.data:
            day0_id = day0_response.data[0]["day_id"]
            day0_progress = day_progress.get(day0_id)

            if not day0_progress:
                # Initialize Day 0 as "todo"
                supabase.table("user_day_progress").upsert(
                    {
                        "user_id": user_id,
                        "day_id": day0_id,
                        "progress_status": "todo",
                        "updated_at": datetime.now(UTC).isoformat(),
                    },
                    on_conflict="user_id,day_id",
                ).execute()

                # Refresh day_progress
                day_progress_response = (
                    supabase.table("user_day_progress").select("*").eq("user_id", user_id).execute()
                )
                day_progress = {p["day_id"]: p for p in (day_progress_response.data or [])}

        # Get all concept progress
        concept_progress_response = (
            supabase.table("user_concept_progress").select("*").eq("user_id", user_id).execute()
        )
        concept_progress = {p["concept_id"]: p for p in (concept_progress_response.data or [])}

        # Get all task progress
        task_progress_response = (
            supabase.table("user_task_progress").select("*").eq("user_id", user_id).execute()
        )
        task_progress = {p["task_id"]: p for p in (task_progress_response.data or [])}

        return {
            "success": True,
            "day_progress": day_progress,
            "concept_progress": concept_progress,
            "task_progress": task_progress,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching progress: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch progress: {str(e)}") from e


@router.put("/{project_id}/current-concept")
async def update_current_concept(
    project_id: str,
    request: UpdateCurrentConceptRequest,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Update the user's current concept position.

    This enables lazy loading with sliding window generation:
    - When user moves to a new concept, update their position
    - Generation can then pre-generate concepts ahead of user position

    Stores user_current_concept_id in the projects table.
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
        has_access, _ = user_has_project_access(supabase, project_id, user_id)
        if not has_access:
            raise HTTPException(status_code=404, detail="Project not found")

        # Verify concept exists and belongs to project
        concept_response = (
            supabase.table("concepts")
            .select("concept_id, day_id")
            .eq("concept_id", request.concept_id)
            .execute()
        )
        if not concept_response.data:
            raise HTTPException(status_code=404, detail="Concept not found")

        concept = concept_response.data[0]

        # Verify concept belongs to project
        day_response = (
            supabase.table("roadmap_days")
            .select("project_id")
            .eq("day_id", concept["day_id"])
            .execute()
        )
        if not day_response.data or day_response.data[0]["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Concept not found in project")

        # Update user_current_concept_id in projects table
        supabase.table("projects").update(
            {
                "user_current_concept_id": request.concept_id,
            }
        ).eq("project_id", project_id).execute()

        logger.info(f"✅ Updated current concept for project {project_id}: {request.concept_id}")

        return {
            "success": True,
            "project_id": project_id,
            "current_concept_id": request.concept_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating current concept: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to update current concept: {str(e)}"
        ) from e


@router.get("/{project_id}/current-concept")
async def get_current_concept_position(
    project_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Get the user's current concept position for lazy loading.

    Returns the concept_id stored in projects.user_current_concept_id,
    along with concept details and position in the curriculum.
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
        project, _ = get_project_if_accessible(
            supabase, project_id, user_id, "project_id, user_current_concept_id"
        )
        current_concept_id = project.get("user_current_concept_id")

        if not current_concept_id:
            return {
                "success": True,
                "project_id": project_id,
                "current_concept_id": None,
                "current_concept": None,
                "position": None,
            }

        # Get concept details
        concept_response = (
            supabase.table("concepts")
            .select("concept_id, title, order_index, day_id, generated_status")
            .eq("concept_id", current_concept_id)
            .execute()
        )

        current_concept = concept_response.data[0] if concept_response.data else None

        # Get position (count of concepts before this one)
        position = None
        if current_concept:
            # Get all concepts for project ordered by day and order_index
            days_response = (
                supabase.table("roadmap_days")
                .select("day_id, day_number")
                .eq("project_id", project_id)
                .order("day_number", desc=False)
                .execute()
            )

            if days_response.data:
                day_ids = [d["day_id"] for d in days_response.data]

                concepts_response = (
                    supabase.table("concepts")
                    .select("concept_id, order_index, day_id")
                    .in_("day_id", day_ids)
                    .order("order_index", desc=False)
                    .execute()
                )

                if concepts_response.data:
                    # Sort by day_number then order_index
                    day_order = {d["day_id"]: d["day_number"] for d in days_response.data}
                    sorted_concepts = sorted(
                        concepts_response.data,
                        key=lambda c: (day_order.get(c["day_id"], 0), c["order_index"]),
                    )

                    for i, c in enumerate(sorted_concepts):
                        if c["concept_id"] == current_concept_id:
                            position = i
                            break

        return {
            "success": True,
            "project_id": project_id,
            "current_concept_id": current_concept_id,
            "current_concept": current_concept,
            "position": position,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting current concept: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get current concept: {str(e)}"
        ) from e


@router.get("/{project_id}/current")
async def get_current_progress(
    project_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Derive current day + concept from progress data.
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
        has_access, _ = user_has_project_access(supabase, project_id, user_id)
        if not has_access:
            raise HTTPException(status_code=404, detail="Project not found")

        # Get all days for project
        days_response = (
            supabase.table("roadmap_days")
            .select("day_id, day_number, estimated_minutes")
            .eq("project_id", project_id)
            .order("day_number", desc=False)
            .execute()
        )
        days = days_response.data if days_response.data else []

        # Get day progress (with retry)
        def get_day_progress():
            return supabase.table("user_day_progress").select("*").eq("user_id", user_id).execute()

        day_progress_response = execute_with_retry(get_day_progress)
        day_progress_map = {p["day_id"]: p for p in (day_progress_response.data or [])}

        # Find current day (highest "doing" or lowest "todo")
        current_day = None
        for day in days:
            progress = day_progress_map.get(day["day_id"])
            if progress and progress["progress_status"] == "doing":
                current_day = day
                break

        if not current_day:
            for day in days:
                progress = day_progress_map.get(day["day_id"])
                if not progress or progress["progress_status"] == "todo":
                    current_day = day
                    break

        if not current_day and days:
            current_day = days[0]

        current_concept = None
        if current_day:
            # Get concepts for current day
            concepts_response = (
                supabase.table("concepts")
                .select("concept_id, order_index, title, estimated_minutes")
                .eq("day_id", current_day["day_id"])
                .order("order_index", desc=False)
                .execute()
            )
            concepts = concepts_response.data if concepts_response.data else []

            # Get concept progress
            concept_progress_response = (
                supabase.table("user_concept_progress").select("*").eq("user_id", user_id).execute()
            )
            concept_progress_map = {
                p["concept_id"]: p for p in (concept_progress_response.data or [])
            }

            # Find current concept
            for concept in concepts:
                progress = concept_progress_map.get(concept["concept_id"])
                if progress and progress["progress_status"] == "doing":
                    current_concept = concept
                    break

            if not current_concept:
                for concept in concepts:
                    progress = concept_progress_map.get(concept["concept_id"])
                    if not progress or progress["progress_status"] == "todo":
                        current_concept = concept
                        break

        return {
            "success": True,
            "current_day": current_day,
            "current_concept": current_concept,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching current progress: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch current progress: {str(e)}"
        ) from e


@router.post("/{project_id}/concept/{concept_id}/start")
async def start_concept(
    project_id: str,
    concept_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Move concept to "doing" status and record start time.
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
        has_access, _ = user_has_project_access(supabase, project_id, user_id)
        if not has_access:
            raise HTTPException(status_code=404, detail="Project not found")

        # Verify concept exists and belongs to project
        concept_response = (
            supabase.table("concepts")
            .select("concept_id, day_id")
            .eq("concept_id", concept_id)
            .execute()
        )
        if not concept_response.data:
            raise HTTPException(status_code=404, detail="Concept not found")

        concept = concept_response.data[0]

        # Verify day belongs to project
        day_response = (
            supabase.table("roadmap_days")
            .select("project_id")
            .eq("day_id", concept["day_id"])
            .execute()
        )
        if not day_response.data or day_response.data[0]["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Concept not found in project")

        now = datetime.now(UTC).isoformat()

        # Upsert progress with started_at timestamp
        supabase.table("user_concept_progress").upsert(
            {
                "user_id": user_id,
                "concept_id": concept_id,
                "progress_status": "doing",
                "started_at": now,
                "updated_at": now,
            },
            on_conflict="user_id,concept_id",
        ).execute()

        return {"success": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting concept: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to start concept: {str(e)}") from e


@router.post("/{project_id}/concept/{concept_id}/complete")
async def complete_concept(
    project_id: str,
    concept_id: str,
    background_tasks: BackgroundTasks,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Move concept to "done" status and record completion time. Employees only.
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
        has_access, is_owner = user_has_project_access(supabase, project_id, user_id)
        if not has_access:
            raise HTTPException(status_code=404, detail="Project not found")
        if is_owner:
            raise HTTPException(
                status_code=403,
                detail="Managers cannot mark concepts complete. Only employees can mark progress.",
            )

        now = datetime.now(UTC).isoformat()

        # Upsert progress with completed_at timestamp
        supabase.table("user_concept_progress").upsert(
            {
                "user_id": user_id,
                "concept_id": concept_id,
                "progress_status": "done",
                "completed_at": now,
                "updated_at": now,
            },
            on_conflict="user_id,concept_id",
        ).execute()

        # Update user_current_concept_id to the completed concept for lazy loading
        supabase.table("projects").update({"user_current_concept_id": concept_id}).eq(
            "project_id", project_id
        ).execute()

        # Trigger incremental concept generation via roadmap service (event-based)
        logger.info(f"🔄 Triggering incremental generation after concept {concept_id} completion")
        background_tasks.add_task(call_roadmap_service_incremental_sync, project_id)

        # Check if all concepts for the day are done
        concept_response = (
            supabase.table("concepts").select("day_id").eq("concept_id", concept_id).execute()
        )
        if concept_response.data:
            day_id = concept_response.data[0]["day_id"]
            day_response = (
                supabase.table("roadmap_days").select("project_id").eq("day_id", day_id).execute()
            )
            if day_response.data and day_response.data[0]["project_id"] == project_id:
                await _check_and_complete_day_if_ready(supabase, user_id, day_id, project_id)

        return {"success": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error completing concept: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to complete concept: {str(e)}") from e


@router.post("/{project_id}/concept/{concept_id}/content-read")
async def mark_content_read(
    project_id: str,
    concept_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Mark concept content as read by the user. Employees only (managers cannot mark complete).
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
        has_access, is_owner = user_has_project_access(supabase, project_id, user_id)
        if not has_access:
            raise HTTPException(status_code=404, detail="Project not found")
        if is_owner:
            raise HTTPException(
                status_code=403,
                detail="Managers can view content but cannot mark it as complete. Only employees can mark progress.",
            )

        # Verify concept exists and belongs to project
        concept_response = (
            supabase.table("concepts")
            .select("concept_id, day_id")
            .eq("concept_id", concept_id)
            .execute()
        )
        if not concept_response.data:
            raise HTTPException(status_code=404, detail="Concept not found")

        concept = concept_response.data[0]

        # Verify day belongs to project
        day_response = (
            supabase.table("roadmap_days")
            .select("project_id")
            .eq("day_id", concept["day_id"])
            .execute()
        )
        if not day_response.data or day_response.data[0]["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Concept not found in project")

        now = datetime.now(UTC).isoformat()

        # Check if progress record already exists
        existing = (
            supabase.table("user_concept_progress")
            .select("progress_status")
            .eq("user_id", user_id)
            .eq("concept_id", concept_id)
            .execute()
        )

        if existing.data:
            # Update only content_read flag
            supabase.table("user_concept_progress").update(
                {
                    "content_read": True,
                    "updated_at": now,
                }
            ).eq("user_id", user_id).eq("concept_id", concept_id).execute()
        else:
            # Insert new record with required fields
            supabase.table("user_concept_progress").insert(
                {
                    "user_id": user_id,
                    "concept_id": concept_id,
                    "content_read": True,
                    "progress_status": "doing",
                    "updated_at": now,
                }
            ).execute()

        return {"success": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error marking content as read: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to mark content as read: {str(e)}"
        ) from e


@router.post("/{project_id}/day/{day_id}/start")
async def start_day(
    project_id: str,
    day_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Start a day (move to "doing") and record start time.
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
        has_access, _ = user_has_project_access(supabase, project_id, user_id)
        if not has_access:
            raise HTTPException(status_code=404, detail="Project not found")

        # Verify day belongs to project
        day_check = (
            supabase.table("roadmap_days")
            .select("day_id")
            .eq("day_id", day_id)
            .eq("project_id", project_id)
            .execute()
        )
        if not day_check.data:
            raise HTTPException(status_code=404, detail="Day not found")

        now = datetime.now(UTC).isoformat()

        # Upsert progress with started_at timestamp
        supabase.table("user_day_progress").upsert(
            {
                "user_id": user_id,
                "day_id": day_id,
                "progress_status": "doing",
                "started_at": now,
                "updated_at": now,
            },
            on_conflict="user_id,day_id",
        ).execute()

        return {"success": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting day: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to start day: {str(e)}") from e


@router.post("/{project_id}/day/{day_id}/complete")
async def complete_day(
    project_id: str,
    day_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Complete a day and unlock next day. Employees only.
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
        has_access, is_owner = user_has_project_access(supabase, project_id, user_id)
        if not has_access:
            raise HTTPException(status_code=404, detail="Project not found")
        if is_owner:
            raise HTTPException(
                status_code=403,
                detail="Managers cannot mark days complete. Only employees can mark progress.",
            )

        now = datetime.now(UTC).isoformat()

        # Mark day as done with completed_at timestamp
        supabase.table("user_day_progress").upsert(
            {
                "user_id": user_id,
                "day_id": day_id,
                "progress_status": "done",
                "completed_at": now,
                "updated_at": now,
            },
            on_conflict="user_id,day_id",
        ).execute()

        # Get current day number
        day_response = (
            supabase.table("roadmap_days").select("day_number").eq("day_id", day_id).execute()
        )
        if day_response.data:
            current_day_number = day_response.data[0]["day_number"]
            next_day_number = current_day_number + 1

            # Unlock next day
            next_day_response = (
                supabase.table("roadmap_days")
                .select("day_id")
                .eq("project_id", project_id)
                .eq("day_number", next_day_number)
                .execute()
            )
            if next_day_response.data:
                next_day_id = next_day_response.data[0]["day_id"]
                supabase.table("user_day_progress").upsert(
                    {
                        "user_id": user_id,
                        "day_id": next_day_id,
                        "progress_status": "todo",
                        "updated_at": now,
                    },
                    on_conflict="user_id,day_id",
                ).execute()

        return {"success": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error completing day: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to complete day: {str(e)}") from e


class CompleteTaskRequest(BaseModel):
    github_username: str | None = None  # For github_profile task
    user_repo_url: str | None = None  # For create_repo task
    commit_sha: str | None = None  # For verify_commit task


@router.post("/{project_id}/task/{task_id}/complete")
async def complete_task(
    project_id: str,
    task_id: str,
    request: CompleteTaskRequest | None = None,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Mark task as completed and record completion time. Employees only.
    Optionally stores project-specific data (repo_url, commit_sha) for Day 0 tasks.
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
        has_access, is_owner = user_has_project_access(supabase, project_id, user_id)
        if not has_access:
            raise HTTPException(status_code=404, detail="Project not found")
        if is_owner:
            raise HTTPException(
                status_code=403,
                detail="Managers cannot mark tasks complete. Only employees can complete tasks.",
            )

        # Verify task exists and belongs to project
        task_response = (
            supabase.table("tasks")
            .select("task_id, concept_id, task_type")
            .eq("task_id", task_id)
            .execute()
        )
        if not task_response.data:
            raise HTTPException(status_code=404, detail="Task not found")

        task = task_response.data[0]
        task_type = task.get("task_type")

        # Verify concept belongs to project
        concept_response = (
            supabase.table("concepts")
            .select("concept_id, day_id")
            .eq("concept_id", task["concept_id"])
            .execute()
        )
        if not concept_response.data:
            raise HTTPException(status_code=404, detail="Concept not found")

        concept = concept_response.data[0]
        day_response = (
            supabase.table("roadmap_days")
            .select("project_id")
            .eq("day_id", concept["day_id"])
            .execute()
        )
        if not day_response.data or day_response.data[0]["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Task not found in project")

        # For Day 0 Task 3 (verify_commit), validate that the provided commit
        # is actually the latest commit on the user's repository. This prevents
        # users from submitting an old commit that will later be rejected in the
        # GitHub consent step.
        if task_type == "verify_commit":
            if not request or not request.commit_sha:
                raise HTTPException(
                    status_code=400,
                    detail="Commit SHA is required to complete this task.",
                )

            project_data, _ = get_project_if_accessible(
                supabase, project_id, user_id, "project_id, user_id, user_repo_url"
            )

            is_valid, error_message = await validate_task_completion(
                task_type,
                {"sha": request.commit_sha},
                project_data=project_data,
            )
            if not is_valid:
                raise HTTPException(status_code=400, detail=error_message or "Invalid commit")

        now = datetime.now(UTC).isoformat()

        # Check if progress record exists
        existing_response = (
            supabase.table("user_task_progress")
            .select("id, started_at")
            .eq("user_id", user_id)
            .eq("task_id", task_id)
            .execute()
        )

        if existing_response.data and len(existing_response.data) > 0:
            # Update existing record
            supabase.table("user_task_progress").update(
                {
                    "progress_status": "done",
                    "completed_at": now,
                    "updated_at": now,
                }
            ).eq("user_id", user_id).eq("task_id", task_id).execute()
        else:
            # Insert new record
            supabase.table("user_task_progress").insert(
                {
                    "user_id": user_id,
                    "task_id": task_id,
                    "progress_status": "done",
                    "started_at": now,
                    "completed_at": now,
                    "updated_at": now,
                }
            ).execute()

        # Store project-specific data for Day 0 tasks
        project_updates = {}
        if request:
            if task_type == "github_profile" and request.github_username:
                project_updates["github_username"] = request.github_username
                logger.info(
                    f"Storing github_username for project {project_id}: {request.github_username}"
                )
            elif task_type == "create_repo" and request.user_repo_url:
                project_updates["user_repo_url"] = request.user_repo_url
                logger.info(
                    f"Storing user_repo_url for project {project_id}: {request.user_repo_url}"
                )
            elif task_type == "verify_commit" and request.commit_sha:
                project_updates["user_repo_first_commit"] = request.commit_sha
                logger.info(
                    f"Storing user_repo_first_commit for project {project_id}: {request.commit_sha}"
                )

        if project_updates:
            supabase.table("projects").update(project_updates).eq("project_id", project_id).eq(
                "user_id", user_id
            ).execute()

        # Check if all tasks for this concept are done, then auto-complete concept
        await _check_and_complete_concept_if_ready(
            supabase, user_id, task["concept_id"], project_id
        )

        return {"success": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error completing task: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to complete task: {str(e)}") from e


@router.post("/{project_id}/task/{task_id}/start")
async def start_task(
    project_id: str,
    task_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Start a task and record start time.
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])

        # Resolve task -> concept -> day -> project_id and verify access
        task_resp = (
            supabase.table("tasks").select("task_id, concept_id").eq("task_id", task_id).execute()
        )
        if not task_resp.data:
            raise HTTPException(status_code=404, detail="Task not found")
        concept_resp = (
            supabase.table("concepts")
            .select("concept_id, day_id")
            .eq("concept_id", task_resp.data[0]["concept_id"])
            .execute()
        )
        if not concept_resp.data:
            raise HTTPException(status_code=404, detail="Concept not found")
        day_resp = (
            supabase.table("roadmap_days")
            .select("day_id, project_id")
            .eq("day_id", concept_resp.data[0]["day_id"])
            .execute()
        )
        if not day_resp.data:
            raise HTTPException(status_code=404, detail="Day not found")
        proj_id = day_resp.data[0]["project_id"]
        has_access, _ = user_has_project_access(supabase, str(proj_id), user_id)
        if not has_access:
            raise HTTPException(status_code=404, detail="Project not found")

        now = datetime.now(UTC).isoformat()

        # Check if progress record exists
        existing_response = (
            supabase.table("user_task_progress")
            .select("id")
            .eq("user_id", user_id)
            .eq("task_id", task_id)
            .execute()
        )

        if existing_response.data and len(existing_response.data) > 0:
            # Update existing record
            supabase.table("user_task_progress").update(
                {
                    "progress_status": "doing",
                    "started_at": now,
                    "updated_at": now,
                }
            ).eq("user_id", user_id).eq("task_id", task_id).execute()
        else:
            # Insert new record
            supabase.table("user_task_progress").insert(
                {
                    "user_id": user_id,
                    "task_id": task_id,
                    "progress_status": "doing",
                    "started_at": now,
                    "updated_at": now,
                }
            ).execute()

        return {"success": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting task: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to start task: {str(e)}") from e


async def _check_and_complete_concept_if_ready(
    supabase: Client, user_id: str, concept_id: str, project_id: str
):
    """
    Check if all tasks for a concept are done and content is read.
    If so, automatically complete the concept and check if day should be completed.
    """
    try:
        # Get all tasks for this concept
        tasks_response = (
            supabase.table("tasks").select("task_id").eq("concept_id", concept_id).execute()
        )
        task_ids = [t["task_id"] for t in (tasks_response.data or [])]

        # Get concept progress to check content_read
        concept_progress_response = (
            supabase.table("user_concept_progress")
            .select("content_read")
            .eq("user_id", user_id)
            .eq("concept_id", concept_id)
            .execute()
        )
        content_read = False
        if concept_progress_response.data:
            content_read = concept_progress_response.data[0].get("content_read", False)

        # Check if all tasks are done
        all_tasks_done = True
        if task_ids:
            task_progress_response = (
                supabase.table("user_task_progress")
                .select("task_id, progress_status")
                .eq("user_id", user_id)
                .in_("task_id", task_ids)
                .execute()
            )
            task_progress = {
                p["task_id"]: p["progress_status"] for p in (task_progress_response.data or [])
            }
            all_tasks_done = all(task_progress.get(task_id) == "done" for task_id in task_ids)

        # Auto-complete concept if content is read AND all tasks are done
        should_complete = False
        if not task_ids and content_read:
            # No tasks, just need content read
            should_complete = True
            logger.info(f"✅ Concept {concept_id} content read with no tasks, auto-completing")
        elif all_tasks_done and content_read:
            # All tasks done and content read
            should_complete = True
            logger.info(
                f"✅ All tasks completed and content read for concept {concept_id}, auto-completing"
            )
        elif all_tasks_done and not task_ids:
            # No tasks and no content requirement (edge case)
            should_complete = True
            logger.info(f"✅ Concept {concept_id} has no tasks, auto-completing")

        if should_complete:
            now = datetime.now(UTC).isoformat()
            supabase.table("user_concept_progress").upsert(
                {
                    "user_id": user_id,
                    "concept_id": concept_id,
                    "progress_status": "done",
                    "completed_at": now,
                    "updated_at": now,
                },
                on_conflict="user_id,concept_id",
            ).execute()

            # Update user_current_concept_id to the completed concept for lazy loading
            supabase.table("projects").update({"user_current_concept_id": concept_id}).eq(
                "project_id", project_id
            ).execute()

            # Trigger incremental concept generation via roadmap service (event-based)
            logger.info(
                f"🔄 Triggering incremental generation after auto-completion of concept {concept_id}"
            )
            # Use asyncio.create_task for fire-and-forget HTTP call
            # This is safe because the parent function is awaited, so the task will complete
            import asyncio

            from app.services.roadmap_client import call_roadmap_service_incremental

            async def trigger_incremental_safely():
                """Wrapper to safely trigger incremental generation with error handling."""
                try:
                    await call_roadmap_service_incremental(project_id)
                    logger.info(
                        f"✅ Incremental generation triggered successfully for project {project_id}"
                    )
                except Exception as e:
                    logger.warning(
                        f"⚠️  Failed to trigger incremental generation: {e}", exc_info=True
                    )
                    # Don't raise - this is fire-and-forget, we don't want to fail concept completion

            try:
                # Create background task (fire and forget) - calls roadmap service via HTTP
                asyncio.create_task(trigger_incremental_safely())
            except Exception as e:
                logger.warning(
                    f"⚠️  Failed to create incremental generation task: {e}", exc_info=True
                )

            # Check if all concepts for the day are done
            concept_response = (
                supabase.table("concepts").select("day_id").eq("concept_id", concept_id).execute()
            )
            if concept_response.data:
                day_id = concept_response.data[0]["day_id"]
                await _check_and_complete_day_if_ready(supabase, user_id, day_id, project_id)

    except Exception as e:
        logger.error(f"Error checking concept completion: {e}", exc_info=True)


async def _check_and_complete_day_if_ready(
    supabase: Client, user_id: str, day_id: str, project_id: str
):
    """
    Check if all concepts for a day are done.
    If so, automatically complete the day and unlock the next day.
    """
    try:
        # Get all concepts for this day
        concepts_response = (
            supabase.table("concepts").select("concept_id").eq("day_id", day_id).execute()
        )
        concept_ids = [c["concept_id"] for c in (concepts_response.data or [])]

        if not concept_ids:
            return

        # Check if all concepts are done
        concept_progress_response = (
            supabase.table("user_concept_progress")
            .select("concept_id, progress_status")
            .eq("user_id", user_id)
            .in_("concept_id", concept_ids)
            .execute()
        )
        concept_progress = {
            p["concept_id"]: p["progress_status"] for p in (concept_progress_response.data or [])
        }

        all_concepts_done = all(
            concept_progress.get(concept_id) == "done" for concept_id in concept_ids
        )

        if all_concepts_done:
            logger.info(f"✅ All concepts completed for day {day_id}, auto-completing day")

            now = datetime.now(UTC).isoformat()

            # Mark day as done
            supabase.table("user_day_progress").upsert(
                {
                    "user_id": user_id,
                    "day_id": day_id,
                    "progress_status": "done",
                    "completed_at": now,
                    "updated_at": now,
                },
                on_conflict="user_id,day_id",
            ).execute()

            # Get current day number and unlock next day
            day_response = (
                supabase.table("roadmap_days").select("day_number").eq("day_id", day_id).execute()
            )
            if day_response.data:
                current_day_number = day_response.data[0]["day_number"]
                next_day_number = current_day_number + 1

                # Unlock next day
                next_day_response = (
                    supabase.table("roadmap_days")
                    .select("day_id")
                    .eq("project_id", project_id)
                    .eq("day_number", next_day_number)
                    .execute()
                )
                if next_day_response.data:
                    next_day_id = next_day_response.data[0]["day_id"]
                    supabase.table("user_day_progress").upsert(
                        {
                            "user_id": user_id,
                            "day_id": next_day_id,
                            "progress_status": "todo",
                            "updated_at": now,
                        },
                        on_conflict="user_id,day_id",
                    ).execute()
                    logger.info(f"✅ Unlocked Day {next_day_number}")

    except Exception as e:
        logger.error(f"Error checking day completion: {e}", exc_info=True)
