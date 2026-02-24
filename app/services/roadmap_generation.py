"""
Service to trigger roadmap generation agent as a background task.
This runs after embeddings are complete.

Also includes incremental concept generation for lazy loading.
"""

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from supabase import Client

from app.agents.roadmap_agent import run_roadmap_agent
from app.agents.state import ConceptStatus, MemoryLedger, RoadmapAgentState
from app.core.supabase_client import get_supabase_client
from app.utils.clerk_auth import verify_clerk_token

logger = logging.getLogger(__name__)

# Create router for roadmap generation endpoints
router = APIRouter()


class GenerateRoadmapRequest(BaseModel):
    project_id: str
    github_url: str
    skill_level: str
    target_days: int


@router.post("/generate")
async def generate_roadmap(
    request: GenerateRoadmapRequest,
    background_tasks: BackgroundTasks,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Trigger roadmap generation for a project.
    This is a heavy LLM operation that runs in the background.
    """
    try:
        # Verify project belongs to user
        clerk_user_id = user_info["clerk_user_id"]
        user_response = (
            supabase.table("User").select("id").eq("clerk_user_id", clerk_user_id).execute()
        )
        if not user_response.data:
            raise HTTPException(status_code=404, detail="User not found")

        user_id = user_response.data[0]["id"]

        project_response = (
            supabase.table("projects")
            .select("project_id")
            .eq("project_id", request.project_id)
            .eq("user_id", user_id)
            .execute()
        )
        if not project_response.data:
            raise HTTPException(status_code=404, detail="Project not found")

        # Trigger roadmap generation in background
        # Use sync wrapper for BackgroundTasks (which doesn't support async functions directly)
        background_tasks.add_task(
            trigger_roadmap_generation_sync,
            project_id=request.project_id,
            github_url=request.github_url,
            skill_level=request.skill_level,
            target_days=request.target_days,
        )

        return {
            "success": True,
            "message": "Roadmap generation started",
            "project_id": request.project_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering roadmap generation: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to trigger roadmap generation: {e}"
        ) from e


async def run_roadmap_generation(
    project_id: str,
    github_url: str,
    skill_level: str,
    target_days: int,
):
    """
    Run the roadmap generation agent for a project.

    This function is designed to be called as a background task after
    embeddings are complete.

    Args:
        project_id: UUID of the project
        github_url: GitHub repository URL
        skill_level: beginner/intermediate/advanced
        target_days: Number of days for the roadmap
    """
    logger.info("=" * 70)
    logger.info("🚀 Starting Roadmap Generation Pipeline")
    logger.info("=" * 70)
    logger.info(f"   📦 Project ID: {project_id}")
    logger.info(f"   🔗 GitHub URL: {github_url}")
    logger.info(f"   📊 Skill Level: {skill_level}")
    logger.info(f"   📅 Target Days: {target_days}")
    logger.info("   ✨ Powered by Gemini (Vertex AI) for all LLM operations")
    logger.info("   🔄 Pipeline: Analyze → Plan → Generate Content → Generate Tasks")
    logger.info("=" * 70)

    try:
        logger.info("🔄 Calling run_roadmap_agent (LangGraph workflow)")
        logger.info(f"   📦 Project ID: {project_id}")
        logger.info(f"   🔗 GitHub URL: {github_url}")
        logger.info(f"   📊 Skill Level: {skill_level}")
        logger.info(f"   📅 Target Days: {target_days}")

        # Run the roadmap agent
        result = await run_roadmap_agent(
            project_id=project_id,
            github_url=github_url,
            skill_level=skill_level,
            target_days=target_days,
        )

        logger.info(f"📊 Roadmap agent returned result: success={result.get('success')}")
        logger.debug(f"   Full result: {result}")

        if result["success"]:
            # Check if it was paused vs truly completed
            if result.get("is_complete", False):
                logger.info("=" * 70)
                logger.info("✅ Gemini-Powered Roadmap Generation Completed Successfully")
                logger.info(f"   📦 Project ID: {project_id}")
                logger.info("   ✨ All operations completed using Gemini (Vertex AI)")
                logger.info("=" * 70)
            elif result.get("is_paused", False):
                logger.info(
                    f"⏸️  Roadmap generation paused (window full) for project_id={project_id}. "
                    f"Waiting for user progress to continue."
                )
            else:
                logger.info("=" * 70)
                logger.info("✅ Gemini-Powered Roadmap Generation Completed")
                logger.info(f"   📦 Project ID: {project_id}")
                logger.info("=" * 70)
        else:
            logger.error("=" * 70)
            logger.error("❌ Gemini-Powered Roadmap Generation Failed")
            logger.error(f"   📦 Project ID: {project_id}")
            logger.error(f"   ⚠️  Error: {result.get('error')}")
            logger.error("=" * 70)
            # Optionally update project status to indicate roadmap generation failed
            # But we don't want to mark the whole project as failed since embeddings succeeded

    except Exception as e:
        logger.error("=" * 70)
        logger.error("❌ CRITICAL ERROR IN ROADMAP GENERATION")
        logger.error(f"   📦 Project ID: {project_id}")
        logger.error(f"   ⚠️  Error Type: {type(e).__name__}")
        logger.error(f"   ⚠️  Error Message: {str(e)}")
        logger.error("=" * 70, exc_info=True)
        # Don't raise - this is a background task, we don't want to crash the main process


def trigger_roadmap_generation_sync(
    project_id: str,
    github_url: str,
    skill_level: str,
    target_days: int,
):
    """
    Synchronous wrapper to trigger roadmap generation.

    This is used by FastAPI BackgroundTasks which doesn't support async directly.
    Uses asyncio.run() to create an isolated event loop - avoids "Event loop is closed"
    when BackgroundTasks run after the HTTP response is sent.

    Args:
        project_id: UUID of the project
        github_url: GitHub repository URL
        skill_level: beginner/intermediate/advanced
        target_days: Number of days for the roadmap
    """
    asyncio.run(
        run_roadmap_generation(
            project_id=project_id,
            github_url=github_url,
            skill_level=skill_level,
            target_days=target_days,
        )
    )


async def run_incremental_concept_generation(project_id: str, user_id_override: str | None = None):
    """
    Run incremental concept generation for lazy loading.

    This function:
    1. Loads state from database (curriculum, concept_status_map, user_current_concept_id)
    2. Runs only the generation loop (skips planning phase)
    3. Generates concepts up to n+2 ahead of user position
    4. Stops when window is full

    This is triggered when user completes a concept.

    Args:
        project_id: UUID of the project
        user_id_override: UUID of the user who completed (per-user cursor); uses project owner if None
    """
    logger.info("=" * 70)
    logger.info("🔄 STARTING INCREMENTAL CONCEPT GENERATION")
    logger.info("=" * 70)
    logger.info(f"   📦 Project ID: {project_id}")
    logger.info(
        f"   🕐 Timestamp: {__import__('datetime').datetime.now(__import__('datetime').UTC).isoformat()}"
    )
    logger.info("=" * 70)

    try:
        logger.info("📊 Loading project data from Supabase...")
        supabase = get_supabase_client()

        # Load project data
        project_response = (
            supabase.table("projects")
            .select(
                "project_id, github_url, skill_level, target_days, user_id, curriculum_structure"
            )
            .eq("project_id", project_id)
            .execute()
        )

        if not project_response.data:
            logger.error("=" * 70)
            logger.error(f"❌ Project {project_id} not found in database")
            logger.error("=" * 70)
            return

        project = project_response.data[0]
        github_url = project["github_url"]
        skill_level = project["skill_level"]
        target_days = project["target_days"]
        user_id = user_id_override or project.get("user_id")
        curriculum_structure = project.get("curriculum_structure")

        logger.info("✅ Project data loaded:")
        logger.info(f"   🔗 GitHub URL: {github_url}")
        logger.info(f"   📊 Skill Level: {skill_level}")
        logger.info(f"   📅 Target Days: {target_days}")
        logger.info(f"   👤 User ID: {user_id}")
        logger.info(f"   📚 Has Curriculum: {curriculum_structure is not None}")

        if not curriculum_structure:
            logger.warning("=" * 70)
            logger.warning(f"⚠️  No curriculum_structure found for project {project_id}")
            logger.warning("   Skipping incremental generation.")
            logger.warning("=" * 70)
            return

        # Load day_ids first (concepts link to project via day_id -> roadmap_days)
        days_response = (
            supabase.table("roadmap_days")
            .select("day_id, day_number")
            .eq("project_id", project_id)
            .execute()
        )
        day_ids = [d["day_id"] for d in (days_response.data or [])]
        if not day_ids:
            logger.warning("No roadmap_days found for project, skipping incremental generation")
            return

        # Load concept_status_map from concepts table (use project_id if available, else day_id)
        concepts_response = (
            supabase.table("concepts")
            .select("concept_id, generated_status, title, curriculum_id")
            .eq("project_id", project_id)
            .execute()
        )

        concept_status_map: dict[str, ConceptStatus] = {}
        concept_ids_map: dict[str, str] = {}  # curriculum_id -> database_id

        # Build mapping from curriculum structure for fallback
        curriculum_concepts = curriculum_structure.get("concepts", {})
        title_to_curriculum_id: dict[str, str] = {}
        for cid, cdata in curriculum_concepts.items():
            title = cdata.get("title", "")
            if title:
                title_to_curriculum_id[title] = cid

        for concept in concepts_response.data or []:
            db_concept_id = concept["concept_id"]
            generated_status = concept.get("generated_status", "pending")

            # Map database status to internal status
            if generated_status == "generated":
                status = "ready"
            elif generated_status == "generated_with_errors":
                status = "generated_with_errors"
            elif generated_status == "failed":
                status = "failed"
            elif generated_status == "generating":
                status = "generating"
            elif generated_status == "pending":
                status = "empty"  # Map "pending" (DB) to "empty" (internal state)
            else:
                status = "empty"  # Default fallback

            # Get curriculum_id: use curriculum_id field if available, otherwise match by title
            curriculum_id = concept.get("curriculum_id")
            if not curriculum_id:
                # Fallback: match by title
                title = concept.get("title", "")
                curriculum_id = title_to_curriculum_id.get(title)
                if not curriculum_id:
                    logger.warning(
                        f"⚠️  Could not find curriculum_id for concept {db_concept_id} (title: {title})"
                    )
                    continue

            concept_status_map[curriculum_id] = {
                "status": status,
                "attempt_count": 0,
                "failure_reason": None,
            }
            concept_ids_map[curriculum_id] = db_concept_id

        # Build day_ids_map from days_response (already loaded above)
        day_ids_map: dict[int, str] = {}
        for day in days_response.data or []:
            day_ids_map[day["day_number"]] = day["day_id"]

        # Load memory_ledger from completed concepts
        # concepts: curriculum_id, title; concept_summaries: summary_text, files_touched, skills_unlocked
        completed_concepts_response = (
            supabase.table("concepts")
            .select("concept_id, curriculum_id, title")
            .eq("project_id", project_id)
            .eq("generated_status", "generated")
            .execute()
        )

        # Fetch concept_summaries for completed concepts (summary_text, files_touched, skills_unlocked)
        completed_concept_ids = [c["concept_id"] for c in (completed_concepts_response.data or [])]
        concept_summaries_data: dict[str, dict] = {}
        if completed_concept_ids:
            summaries_response = (
                supabase.table("concept_summaries")
                .select("concept_id, summary_text, files_touched, skills_unlocked")
                .in_("concept_id", completed_concept_ids)
                .execute()
            )
            for row in summaries_response.data or []:
                concept_summaries_data[str(row["concept_id"])] = row

        memory_ledger: MemoryLedger = {
            "completed_concepts": [],
            "files_touched": [],
            "skills_unlocked": [],
        }

        concept_summaries: dict[str, str] = {}

        for concept in completed_concepts_response.data or []:
            db_concept_id = concept["concept_id"]
            # Use curriculum_id if available, otherwise match by title
            curriculum_id = concept.get("curriculum_id")
            if not curriculum_id:
                title = concept.get("title", "")
                curriculum_id = title_to_curriculum_id.get(title)
                if not curriculum_id:
                    continue

            memory_ledger["completed_concepts"].append(curriculum_id)
            summary_row = concept_summaries_data.get(str(db_concept_id), {})
            if summary_row.get("summary_text"):
                concept_summaries[curriculum_id] = summary_row["summary_text"]
            if summary_row.get("files_touched"):
                memory_ledger["files_touched"].extend(summary_row["files_touched"] or [])
            if summary_row.get("skills_unlocked"):
                memory_ledger["skills_unlocked"].extend(summary_row["skills_unlocked"] or [])

        # Determine user's current concept from user_concept_progress table
        from app.agents.nodes.save_to_db import get_user_current_concept_from_progress

        user_current_concept_id = None
        if user_id and concept_ids_map:
            user_current_concept_id = get_user_current_concept_from_progress(
                project_id=project_id,
                user_id=user_id,
                concept_ids_map=concept_ids_map,
            )

        # Build initial state for incremental generation
        initial_state: RoadmapAgentState = {
            "project_id": project_id,
            "github_url": github_url,
            "skill_level": skill_level,
            "target_days": target_days,
            "repo_analysis": None,  # Not needed for incremental generation
            "curriculum": curriculum_structure,
            "concept_status_map": concept_status_map,
            "concept_summaries": concept_summaries,
            "memory_ledger": memory_ledger,
            "user_current_concept_id": user_current_concept_id,
            "current_day_number": 0,
            "current_day_id": None,
            "current_concepts": [],
            "current_concept_index": 0,
            "memory_context": None,
            "day_ids_map": day_ids_map,
            "concept_ids_map": concept_ids_map,
            "is_complete": False,
            "is_paused": False,
            "error": None,
        }

        # Run only the generation loop manually (not using graph)
        # The graph would start from fetch_context but curriculum is already loaded
        # We manually call the generation loop nodes instead

        logger.info(f"🔄 Running incremental generation loop for project_id={project_id}")
        logger.info(f"   User current concept: {user_current_concept_id}")
        logger.info("   Concepts to generate: up to n+2 ahead")

        # Invoke the graph - it will start from fetch_context but curriculum is already loaded
        # The graph needs to be modified to skip planning if curriculum exists
        # For now, we'll manually call the generation loop nodes
        from app.agents.nodes.generate_content import generate_concept_content
        from app.agents.nodes.memory_context import build_memory_context
        from app.agents.nodes.save_to_db import mark_concept_complete
        from app.agents.roadmap_agent import should_continue_after_concept

        # Run generation loop manually until window is full
        max_iterations = 10  # Safety limit
        iteration = 0

        logger.info("🔄 Starting incremental generation loop...")
        logger.info(f"   Max iterations: {max_iterations}")

        while iteration < max_iterations:
            iteration += 1
            logger.info(f"🔄 Incremental generation iteration {iteration}/{max_iterations}")

            # Check if we should continue
            should_continue = should_continue_after_concept(initial_state)
            logger.debug(f"   Should continue: {should_continue}")

            if should_continue == "end":
                logger.info("⏸️  Generation window full or complete. Stopping.")
                break

            # Build memory context
            logger.info(f"   📚 Building memory context (iteration {iteration})...")
            initial_state = await build_memory_context(initial_state)
            logger.info("   ✅ Memory context built")

            # Generate concept content
            logger.info(f"   ✨ Generating concept content (iteration {iteration})...")
            initial_state = await generate_concept_content(initial_state)
            logger.info("   ✅ Concept content generated")

            # Mark concept complete
            logger.info(f"   💾 Marking concept complete (iteration {iteration})...")
            initial_state = mark_concept_complete(initial_state)
            logger.info("   ✅ Concept marked complete")

            if initial_state.get("error"):
                logger.error("=" * 70)
                logger.error(f"❌ Error in incremental generation iteration {iteration}")
                logger.error(f"   ⚠️  Error: {initial_state['error']}")
                logger.error("=" * 70)
                break

        logger.info("=" * 70)
        logger.info("✅ INCREMENTAL GENERATION COMPLETED")
        logger.info(f"   📦 Project ID: {project_id}")
        logger.info(f"   🔄 Iterations: {iteration}/{max_iterations}")
        logger.info("=" * 70)

        if initial_state.get("error"):
            logger.error("=" * 70)
            logger.error("❌ INCREMENTAL GENERATION FAILED")
            logger.error(f"   📦 Project ID: {project_id}")
            logger.error(f"   ⚠️  Error: {initial_state['error']}")
            logger.error("=" * 70)

    except Exception as e:
        logger.error("=" * 70)
        logger.error("❌ CRITICAL ERROR IN INCREMENTAL CONCEPT GENERATION")
        logger.error(f"   📦 Project ID: {project_id}")
        logger.error(f"   ⚠️  Error Type: {type(e).__name__}")
        logger.error(f"   ⚠️  Error Message: {str(e)}")
        logger.error("=" * 70, exc_info=True)
        # Don't raise - this is a background task


def trigger_incremental_generation_sync(project_id: str, user_id: str | None = None):
    """
    Synchronous wrapper to trigger incremental concept generation.

    This is used by FastAPI BackgroundTasks.
    Uses asyncio.run() for isolated event loop (avoids "Event loop is closed").

    Args:
        project_id: UUID of the project
        user_id: UUID of the user who completed (per-user cursor; uses project owner if None)
    """
    asyncio.run(run_incremental_concept_generation(project_id, user_id))
