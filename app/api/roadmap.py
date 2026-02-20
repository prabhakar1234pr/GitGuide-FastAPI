"""
API routes for roadmap content (read-only for users).
Returns days, concepts (with content), and tasks.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from app.core.supabase_client import get_supabase_client
from app.utils.clerk_auth import verify_clerk_token
from app.utils.db_helpers import (
    get_project_if_accessible,
    get_user_id_from_clerk,
    user_has_project_access,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/{project_id}")
async def get_roadmap(
    project_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Get all days for a project with their status and estimated times.
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])

        has_access, _ = user_has_project_access(supabase, project_id, user_id)
        if not has_access:
            raise HTTPException(status_code=404, detail="Project not found")

        # Get all days with all fields including estimated_minutes
        days_response = (
            supabase.table("roadmap_days")
            .select("*")
            .eq("project_id", project_id)
            .order("day_number", desc=False)
            .execute()
        )

        return {"success": True, "days": days_response.data if days_response.data else []}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching roadmap: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch roadmap: {str(e)}") from e


@router.get("/{project_id}/day/{day_id}")
async def get_day_details(
    project_id: str,
    day_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Get day details with all concepts (including content and estimated_minutes).
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
        has_access, _ = user_has_project_access(supabase, project_id, user_id)
        if not has_access:
            raise HTTPException(status_code=404, detail="Project not found")

        # Get day
        day_response = (
            supabase.table("roadmap_days")
            .select("*")
            .eq("day_id", day_id)
            .eq("project_id", project_id)
            .execute()
        )
        if not day_response.data:
            raise HTTPException(status_code=404, detail="Day not found")

        day = day_response.data[0]

        # Get concepts for this day (including content and estimated_minutes)
        # Return ALL concepts regardless of generated_status to support lazy generation
        from app.core.supabase_client import execute_with_retry

        def get_concepts():
            return (
                supabase.table("concepts")
                .select("*")
                .eq("day_id", day_id)
                .order("order_index", desc=False)
                .execute()
            )

        concepts_response = execute_with_retry(get_concepts)
        concepts = concepts_response.data if concepts_response.data else []

        # Log detailed concept data for debugging
        logger.info(
            f"📊 Concepts for day {day_id} (day_number={day.get('day_number')}): "
            f"total={len(concepts)}, "
            f"concepts_detail={[(c.get('concept_id'), c.get('title'), c.get('generated_status'), bool(c.get('content')), len(c.get('content', '')) if c.get('content') else 0) for c in concepts]}"
        )

        return {"success": True, "day": day, "concepts": concepts}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching day details: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch day details: {str(e)}") from e


@router.get("/{project_id}/concept/{concept_id}")
async def get_concept_details(
    project_id: str,
    concept_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Get concept details with content and tasks.
    Content is rich markdown documentation.
    Tasks include difficulty, hints, and estimated time.
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
        has_access, _ = user_has_project_access(supabase, project_id, user_id)
        if not has_access:
            raise HTTPException(status_code=404, detail="Project not found")

        # Get concept (includes content and estimated_minutes)
        # Use execute_with_retry to handle connection issues
        from app.core.supabase_client import execute_with_retry

        def get_concept():
            return supabase.table("concepts").select("*").eq("concept_id", concept_id).execute()

        concept_response = execute_with_retry(get_concept)

        if not concept_response.data:
            logger.error(f"Concept {concept_id} not found in database")
            raise HTTPException(status_code=404, detail="Concept not found")

        concept = concept_response.data[0]

        # Log raw concept data from database
        logger.info(
            f"📊 Concept from DB (concept_id={concept_id}): "
            f"title={concept.get('title')}, "
            f"generated_status={concept.get('generated_status')}, "
            f"content_is_null={concept.get('content') is None}, "
            f"content_is_empty={concept.get('content') == ''}, "
            f"content_length={len(concept.get('content', '')) if concept.get('content') else 0}, "
            f"content_preview={str(concept.get('content', ''))[:100] if concept.get('content') else 'None'}"
        )

        # Get tasks (includes difficulty, hints, solution, estimated_minutes)
        def get_tasks():
            return (
                supabase.table("tasks")
                .select("*")
                .eq("concept_id", concept_id)
                .order("order_index", desc=False)
                .execute()
            )

        tasks_response = execute_with_retry(get_tasks)
        tasks = tasks_response.data if tasks_response.data else []

        # Log tasks data
        logger.info(
            f"📋 Tasks from DB (concept_id={concept_id}): "
            f"tasks_count={len(tasks)}, "
            f"task_ids={[t.get('task_id') for t in tasks]}, "
            f"task_titles={[t.get('title') for t in tasks]}"
        )

        # Log final response
        logger.info(
            f"✅ Returning concept details for {concept_id}: "
            f"generated_status={concept.get('generated_status')}, "
            f"has_content={bool(concept.get('content'))}, "
            f"content_length={len(concept.get('content', '')) if concept.get('content') else 0}, "
            f"tasks_count={len(tasks)}"
        )

        return {"success": True, "concept": concept, "tasks": tasks}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching concept details: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch concept details: {str(e)}"
        ) from e


@router.get("/{project_id}/generation-status")
async def get_generation_status(
    project_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Get overall roadmap generation status with progress percentage.
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
        project, _ = get_project_if_accessible(
            supabase,
            project_id,
            user_id,
            "project_id, target_days, generation_progress, error_message",
        )
        target_days = project["target_days"]
        generation_progress = project.get("generation_progress", 0)
        error_message = project.get("error_message")

        # Count days by status
        days_response = (
            supabase.table("roadmap_days")
            .select("generated_status")
            .eq("project_id", project_id)
            .execute()
        )
        days = days_response.data if days_response.data else []

        status_counts = {
            "pending": 0,
            "generating": 0,
            "generated": 0,
            "failed": 0,
        }

        for day in days:
            status = day.get("generated_status", "pending")
            status_counts[status] = status_counts.get(status, 0) + 1

        total_days = len(days)
        generated_days = status_counts["generated"]
        is_complete = total_days == target_days and generated_days == target_days

        return {
            "success": True,
            "total_days": total_days,
            "target_days": target_days,
            "generated_days": generated_days,
            "generation_progress": generation_progress,
            "error_message": error_message,
            "status_counts": status_counts,
            "is_complete": is_complete,
            "is_generating": status_counts["generating"] > 0,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching generation status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch generation status: {str(e)}"
        ) from e


@router.get("/{project_id}/concept/{concept_id}/debug")
async def debug_concept_details(
    project_id: str,
    concept_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Debug endpoint to check what's actually in the database for a concept.
    Returns raw database data without any processing.
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])
        has_access, _ = user_has_project_access(supabase, project_id, user_id)
        if not has_access:
            raise HTTPException(status_code=404, detail="Project not found")

        # Get concept with ALL fields explicitly listed
        concept_response = (
            supabase.table("concepts")
            .select(
                "concept_id, day_id, order_index, title, description, content, "
                "generated_status, estimated_minutes, created_at, difficulty, "
                "depends_on, repo_anchors, objective, failure_reason, attempt_count"
            )
            .eq("concept_id", concept_id)
            .execute()
        )

        if not concept_response.data:
            return {
                "success": False,
                "error": "Concept not found",
                "concept_id": concept_id,
                "concept_data": None,
            }

        concept = concept_response.data[0]

        # Get tasks
        tasks_response = (
            supabase.table("tasks")
            .select("*")
            .eq("concept_id", concept_id)
            .order("order_index", desc=False)
            .execute()
        )
        tasks = tasks_response.data if tasks_response.data else []

        return {
            "success": True,
            "concept_id": concept_id,
            "concept": {
                **concept,
                "content_is_null": concept.get("content") is None,
                "content_is_empty_string": concept.get("content") == "",
                "content_length": len(concept.get("content", "")) if concept.get("content") else 0,
                "content_preview": (
                    concept.get("content", "")[:200] if concept.get("content") else None
                ),
            },
            "tasks": tasks,
            "tasks_count": len(tasks),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in debug endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Debug endpoint error: {str(e)}") from e


@router.get("/task/{task_id}")
async def get_task_details(
    task_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Get task details with related concept, day, and project information.
    Includes task difficulty, hints, solution, and estimated time.
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])

        # Get task (includes all new fields)
        task_response = supabase.table("tasks").select("*").eq("task_id", task_id).execute()
        if not task_response.data:
            raise HTTPException(status_code=404, detail="Task not found")

        task = task_response.data[0]
        concept_id = task["concept_id"]

        # Get concept
        concept_response = (
            supabase.table("concepts").select("*").eq("concept_id", concept_id).execute()
        )
        if not concept_response.data:
            raise HTTPException(status_code=404, detail="Concept not found")

        concept = concept_response.data[0]
        day_id = concept["day_id"]

        # Get day
        day_response = supabase.table("roadmap_days").select("*").eq("day_id", day_id).execute()
        if not day_response.data:
            raise HTTPException(status_code=404, detail="Day not found")

        day = day_response.data[0]
        project_id = day["project_id"]

        has_access, is_owner = user_has_project_access(supabase, project_id, user_id)
        if not has_access:
            raise HTTPException(status_code=404, detail="Project not found")

        project, _ = get_project_if_accessible(supabase, project_id, user_id)
        project_with_owner = {**project, "is_owner": is_owner}

        return {
            "success": True,
            "task": task,
            "concept": concept,
            "day": day,
            "project": project_with_owner,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching task details: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch task details: {str(e)}"
        ) from e
