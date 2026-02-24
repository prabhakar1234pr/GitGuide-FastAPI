"""
LangGraph agent for generating project roadmaps using Gemini (Vertex AI).
This agent creates a structured learning curriculum based on a GitHub repository.

Optimized workflow (v2) - Powered by Gemini:
1. Fetch project context from database
2. Analyze repository using RAG + Gemini (with token budgeting)
3. Plan curriculum with Gemini (generate ALL days + concepts upfront)
4. Insert all days to database
5. Save all concepts to database (with status='empty')
6. Loop: Generate concept content with Gemini (lazy loading)
   a. Build memory context from state ledger
   b. Generate content + tasks with Gemini (retry logic)
   c. Generate inline summary
   d. Mark concept complete
7. End when all concepts generated
"""

import logging
import time
from typing import Literal

from langgraph.graph import END, StateGraph

from app.agents.nodes.analyze_repo import analyze_repository
from app.agents.nodes.fetch_context import fetch_project_context
from app.agents.nodes.generate_content import generate_concept_content
from app.agents.nodes.generate_tasks import generate_tasks
from app.agents.nodes.memory_context import build_memory_context
from app.agents.nodes.plan_curriculum import plan_and_save_curriculum
from app.agents.nodes.save_to_db import (
    insert_all_days_to_db,
    mark_concept_complete,
    save_all_concepts_to_db,
)
from app.agents.state import MemoryLedger, RoadmapAgentState
from app.agents.utils import calculate_recursion_limit, validate_inputs
from app.agents.utils.concept_order import SLIDING_WINDOW_AHEAD
from app.core.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)


def should_continue_concept_generation(
    state: RoadmapAgentState,
) -> Literal["build_memory_context", "end"]:
    """
    Conditional edge function: Check if we should continue generating concepts.

    Uses concept-level tracking instead of day-level.

    Returns:
        "build_memory_context" if there are more concepts to generate
        "end" if all concepts are complete or error occurred
    """
    if state.get("is_complete", False):
        logger.info("✅ All concepts generated. Roadmap complete!")
        return "end"

    if state.get("error"):
        logger.error(f"❌ Agent error: {state['error']}")
        return "end"

    # Check concept-level completion (derive from curriculum, not stored queue)
    from app.agents.utils.concept_order import (
        are_all_concepts_complete,
        get_ordered_concept_ids,
    )

    curriculum = state.get("curriculum", {})
    concept_status_map = state.get("concept_status_map", {})

    # Derive ordered concept list from curriculum
    ordered_concept_ids = get_ordered_concept_ids(curriculum)

    if not ordered_concept_ids:
        logger.info("✅ No concepts in curriculum. Roadmap complete!")
        return "end"

    # Check if all concepts are done
    all_done = are_all_concepts_complete(ordered_concept_ids, concept_status_map)

    if all_done:
        logger.info(f"✅ All {len(ordered_concept_ids)} concepts generated. Roadmap complete!")
        return "end"

    return "build_memory_context"


def should_continue_after_concept(
    state: RoadmapAgentState,
) -> Literal["build_memory_context", "end"]:
    """
    Conditional edge function: Check if we should continue after marking a concept complete.

    For lazy loading with sliding window:
    - Stops after generating n+2 concepts ahead of user position
    - Only continues if there are empty concepts within the window

    Returns:
        "build_memory_context" if there are more concepts to generate within window
        "end" if window is full or all concepts are complete
    """
    if state.get("is_complete", False):
        logger.info("✅ All concepts generated. Roadmap complete!")
        return "end"

    if state.get("error"):
        logger.error(f"❌ Agent error: {state['error']}")
        return "end"

    # Check concept-level completion (derive from curriculum, not stored queue)
    from app.agents.utils.concept_order import (
        are_all_concepts_complete,
        get_ordered_concept_ids,
        get_user_current_index,
        has_generated_up_to_window,
    )

    curriculum = state.get("curriculum", {})
    concept_status_map = state.get("concept_status_map", {})
    user_current_concept_id = state.get("user_current_concept_id")

    # Derive ordered concept list from curriculum
    ordered_concept_ids = get_ordered_concept_ids(curriculum)

    if not ordered_concept_ids:
        logger.info("✅ No concepts in curriculum. Roadmap complete!")
        return "end"

    # Check if all concepts are done
    all_done = are_all_concepts_complete(ordered_concept_ids, concept_status_map)

    if all_done:
        logger.info(f"✅ All {len(ordered_concept_ids)} concepts generated. Roadmap complete!")
        return "end"

    # For lazy loading: Check if we've generated up to n+2 concepts
    user_current_index = get_user_current_index(ordered_concept_ids, user_current_concept_id)

    # Debug logging
    logger.debug(
        f"🔍 Window check: user_current_index={user_current_index}, "
        f"user_current_concept_id={user_current_concept_id}, "
        f"window_size={SLIDING_WINDOW_AHEAD}"
    )

    window_full = has_generated_up_to_window(
        ordered_concept_ids, concept_status_map, user_current_index
    )

    if window_full:
        # Note: is_paused flag is set in mark_concept_complete node, not here
        # Conditional edge functions can't modify state
        logger.info(
            f"⏸️  Sliding window full (n+{SLIDING_WINDOW_AHEAD}). "
            f"User at index {user_current_index}, generated up to index {user_current_index + SLIDING_WINDOW_AHEAD}. "
            f"Pausing generation. Waiting for user progress."
        )
        return "end"

    return "build_memory_context"


def build_roadmap_graph() -> StateGraph:
    """
    Build the LangGraph DAG for roadmap generation (v2 - optimized).

    Flow:
    1. Fetch project context from database
    2. Analyze repository using RAG (with token budgeting)
    3. Plan curriculum (generate ALL days + concepts upfront)
    4. Insert all days to database (Days 1-N, Day 0 is handled separately via API)
    5. Save all concepts to database (with status='empty')
    6. Loop: Generate concept content with lazy loading
       a. Build memory context from state ledger
       b. Generate content (no tasks) with retry
       c. Generate tasks with test files (using notebook repo context)
       d. Extract verification patterns from test files
       e. Mark concept complete (updates day if all concepts done)
    7. End when all concepts generated

    Returns:
        Compiled StateGraph ready to run
    """
    logger.info("🔨 Building roadmap generation graph (v2 - optimized)...")

    # Create the graph
    workflow = StateGraph(RoadmapAgentState)

    # ===== INITIALIZATION PHASE =====
    workflow.add_node("fetch_context", fetch_project_context)
    workflow.add_node("analyze_repo", analyze_repository)

    # ===== PLANNING PHASE =====
    workflow.add_node("plan_curriculum", plan_and_save_curriculum)
    workflow.add_node("insert_all_days", insert_all_days_to_db)
    workflow.add_node("save_all_concepts", save_all_concepts_to_db)

    # ===== CONTENT GENERATION LOOP =====
    # Note: Day 0 is handled separately via API endpoint (initialize-day0)
    workflow.add_node("build_memory_context", build_memory_context)
    workflow.add_node("generate_concept_content", generate_concept_content)
    workflow.add_node("generate_tasks", generate_tasks)
    workflow.add_node("mark_concept_complete", mark_concept_complete)

    # ===== EDGES =====

    # Start: fetch context
    workflow.set_entry_point("fetch_context")

    # Initialization flow
    workflow.add_edge("fetch_context", "analyze_repo")
    workflow.add_edge("analyze_repo", "plan_curriculum")
    workflow.add_edge("plan_curriculum", "insert_all_days")
    workflow.add_edge("insert_all_days", "save_all_concepts")

    # After saving all concepts, check if we need to generate content
    workflow.add_conditional_edges(
        "save_all_concepts",
        should_continue_concept_generation,
        {
            "build_memory_context": "build_memory_context",
            "end": END,
        },
    )

    # Content generation loop (concept-level)
    workflow.add_edge("build_memory_context", "generate_concept_content")
    workflow.add_edge("generate_concept_content", "generate_tasks")
    workflow.add_edge("generate_tasks", "mark_concept_complete")

    # After marking concept complete, check if more concepts needed
    workflow.add_conditional_edges(
        "mark_concept_complete",
        should_continue_after_concept,
        {
            "build_memory_context": "build_memory_context",
            "end": END,
        },
    )

    # Compile the graph
    graph = workflow.compile()
    logger.info("✅ Gemini-powered roadmap generation graph built successfully (v2)")
    logger.info("   📊 Graph ready with Gemini integration")

    return graph


# Create singleton graph instance
_roadmap_graph = None


def get_roadmap_graph() -> StateGraph:
    """
    Get or create the roadmap generation graph (singleton).

    Returns:
        Compiled StateGraph instance
    """
    global _roadmap_graph

    if _roadmap_graph is None:
        logger.info("🔨 Creating roadmap generation graph (first use)...")
        _roadmap_graph = build_roadmap_graph()
        logger.info("✅ Roadmap graph ready")

    return _roadmap_graph


async def run_roadmap_agent(
    project_id: str,
    github_url: str,
    skill_level: str,
    target_days: int,
    rag_chunks: list[dict] | None = None,
) -> dict:
    """
    Run the roadmap generation agent for a project.

    Args:
        project_id: UUID of the project
        github_url: GitHub repository URL
        skill_level: beginner/intermediate/advanced
        target_days: Number of days for the roadmap

    Returns:
        dict with keys:
            - success: bool
            - project_id: str
            - error: Optional[str]
    """
    logger.info("🤖 Starting Gemini-Powered Roadmap Generation Agent")
    logger.info(f"   📦 Project ID: {project_id}")
    logger.info(f"   🔗 GitHub URL: {github_url}")
    logger.info(f"   📊 Skill Level: {skill_level}")
    logger.info(f"   📅 Target Days: {target_days}")
    logger.info("   ✨ All LLM operations powered by Gemini (Vertex AI)")
    start_time = time.time()

    try:
        # Validate inputs early to avoid unnecessary work
        try:
            validate_inputs(project_id, github_url, skill_level, target_days)
        except ValueError as e:
            return {
                "success": False,
                "project_id": project_id,
                "error": f"Invalid input: {str(e)}",
                "duration_seconds": max(0.0, time.time() - start_time),
            }

        # Ensure project exists before running workflow
        try:
            supabase = get_supabase_client()
            project_response = (
                supabase.table("projects")
                .select("project_id")
                .eq("project_id", project_id)
                .execute()
            )
            if not project_response.data:
                return {
                    "success": False,
                    "project_id": project_id,
                    "error": "Project not found",
                    "duration_seconds": max(0.0, time.time() - start_time),
                }
        except Exception as e:
            logger.error(f"Project check failed: {e}")
            return {
                "success": False,
                "project_id": project_id,
                "error": f"Project check failed: {str(e)}",
                "duration_seconds": max(0.0, time.time() - start_time),
            }

        # Initialize state with new optimized structure
        initial_memory_ledger: MemoryLedger = {
            "completed_concepts": [],
            "files_touched": [],
            "skills_unlocked": [],
        }

        initial_state: RoadmapAgentState = {
            # Pre-retrieved RAG chunks (from API; avoids embedding init in roadmap)
            "rag_chunks": rag_chunks,
            # Input (immutable)
            "project_id": project_id,
            "github_url": github_url,
            "skill_level": skill_level,
            "target_days": target_days,
            # Analysis results
            "repo_analysis": None,
            # Curriculum (expanded structure)
            "curriculum": {},  # Will be: {days: [], concepts: {}, dependency_graph: {}}
            # Concept status tracking
            "concept_status_map": {},  # concept_id -> ConceptStatus
            # State-based memory
            "concept_summaries": {},  # concept_id -> summary text
            "memory_ledger": initial_memory_ledger,
            # Lazy loading tracking
            "user_current_concept_id": None,
            # Note: generation_queue is DERIVED from curriculum, not stored
            # Current generation context (deprecated but kept for compatibility)
            "current_day_number": 0,
            "current_day_id": None,
            "current_concepts": [],  # DEPRECATED
            "current_concept_index": 0,
            # Memory context
            "memory_context": None,
            # Internal state (database IDs)
            "day_ids_map": None,
            "concept_ids_map": None,  # curriculum_id -> database_id
            # Status tracking
            "is_complete": False,
            "is_paused": False,
            "error": None,
        }

        # Get graph and run
        graph = get_roadmap_graph()

        # Calculate recursion limit (v2):
        # - Base: 5 (fetch, analyze, plan, insert_days, save_concepts)
        # - Per concept: 5 (build_memory, generate_content, generate_tasks, extract_patterns, mark_complete)
        # - Estimated ~4 concepts per day
        config = {"recursion_limit": calculate_recursion_limit(target_days)}

        # Run the graph (LangGraph handles async execution)
        final_state = await graph.ainvoke(initial_state, config=config)

        if final_state.get("error"):
            logger.error(f"❌ Roadmap generation failed: {final_state['error']}")
            return {
                "success": False,
                "project_id": project_id,
                "error": final_state["error"],
                "duration_seconds": max(0.0, time.time() - start_time),
            }

        # Check if generation was paused (window full) vs truly complete
        is_complete = final_state.get("is_complete", False)
        is_paused = final_state.get("is_paused", False)

        if is_complete:
            logger.info(f"✅ Roadmap generation completed successfully for project_id={project_id}")
        elif is_paused:
            # Window is full - generation paused, waiting for user progress
            logger.info(
                f"⏸️  Roadmap generation paused (window full) for project_id={project_id}. "
                f"Waiting for user progress to continue."
            )
        else:
            # Neither complete nor paused - this shouldn't happen normally
            logger.warning(
                f"⚠️  Roadmap generation ended without completion or pause flag for project_id={project_id}"
            )

        return {
            "success": True,
            "project_id": project_id,
            "error": None,
            "is_complete": is_complete,
            "is_paused": is_paused,
            "duration_seconds": max(0.0, time.time() - start_time),
        }

    except Exception as e:
        logger.error(f"❌ Error running roadmap agent: {e}", exc_info=True)
        return {
            "success": False,
            "project_id": project_id,
            "error": str(e),
            "duration_seconds": max(0.0, time.time() - start_time),
        }
