"""
Database operations for saving roadmap content.
All nodes that write to Supabase database.
Saves concepts with content field and tasks with new fields (difficulty, hints, estimated_minutes).

Optimized for the new curriculum structure:
- save_all_concepts_to_db: Save ALL concepts from plan_curriculum upfront
- mark_concept_complete: Mark individual concepts as complete
- Supports concept-level tracking instead of day-level
"""

import logging
from typing import Any

import httpx

from app.agents.state import ConceptStatus, RoadmapAgentState
from app.core.supabase_client import execute_with_retry, get_supabase_client

# Day 0 is handled separately via API endpoint, not imported here
from app.utils.markdown_sanitizer import sanitize_markdown_content

logger = logging.getLogger(__name__)


def get_user_current_concept_from_progress(
    project_id: str, user_id: str, concept_ids_map: dict[str, str]
) -> str | None:
    """
    Determine user's current concept from user_concept_progress table.

    Logic:
    1. Find concept with status 'doing' (user is currently working on it)
    2. If none, find most recent concept with status 'done' (last completed)
    3. Map database concept_id to curriculum concept_id using concept_ids_map

    Args:
        project_id: Project UUID
        user_id: User UUID
        concept_ids_map: Map of curriculum_id -> database_id

    Returns:
        Curriculum concept_id (e.g., "c1", "c2") or None if user hasn't started
    """
    if not user_id or not concept_ids_map:
        return None

    supabase = get_supabase_client()

    # Get all day_ids for this project (with retry)
    def get_days():
        return (
            supabase.table("roadmap_days").select("day_id").eq("project_id", project_id).execute()
        )

    days_response = execute_with_retry(get_days)

    if not days_response.data:
        return None

    day_ids = [d["day_id"] for d in days_response.data]

    # Get all concepts for these days (with retry)
    def get_concepts():
        return supabase.table("concepts").select("concept_id").in_("day_id", day_ids).execute()

    concepts_response = execute_with_retry(get_concepts)

    if not concepts_response.data:
        return None

    project_concept_ids = [c["concept_id"] for c in concepts_response.data]

    # Query user_concept_progress for concepts with 'doing' status (with retry)
    def get_doing_progress():
        return (
            supabase.table("user_concept_progress")
            .select("concept_id, progress_status, completed_at")
            .eq("user_id", user_id)
            .in_("concept_id", project_concept_ids)
            .eq("progress_status", "doing")
            .execute()
        )

    progress_response = execute_with_retry(get_doing_progress)

    if progress_response.data:
        # User is currently working on a concept
        doing_concept_id = progress_response.data[0]["concept_id"]
        # Map database ID to curriculum ID
        for curriculum_id, db_id in concept_ids_map.items():
            if db_id == doing_concept_id:
                logger.info(f"📍 User is currently working on concept: {curriculum_id}")
                return curriculum_id

    # If no 'doing' concept, find most recent 'done' concept (with retry)
    def get_done_progress():
        return (
            supabase.table("user_concept_progress")
            .select("concept_id, completed_at")
            .eq("user_id", user_id)
            .in_("concept_id", project_concept_ids)
            .eq("progress_status", "done")
            .order("completed_at", desc=True)
            .limit(1)
            .execute()
        )

    done_response = execute_with_retry(get_done_progress)

    if done_response.data:
        done_concept_id = done_response.data[0]["concept_id"]
        # Map database ID to curriculum ID
        for curriculum_id, db_id in concept_ids_map.items():
            if db_id == done_concept_id:
                logger.info(f"📍 User's last completed concept: {curriculum_id}")
                return curriculum_id

    # User hasn't started any concepts
    logger.info("📍 User hasn't started any concepts yet")
    return None


# Import postgrest exception if available
try:
    from postgrest.exceptions import APIError as PostgrestAPIError
except ImportError:
    PostgrestAPIError = Exception


def insert_all_days_to_db(state: RoadmapAgentState) -> RoadmapAgentState:
    """
    Insert all day themes into roadmap_days table (Days 1-N only).
    Day 0 is handled separately via API endpoint (initialize-day0).

    Handles expanded curriculum structure:
    - curriculum["days"] contains list of DayTheme objects
    - Each day includes concept_ids linking to concepts in curriculum["concepts"]

    Includes estimated_minutes and concept_ids for each day.
    """
    project_id = state["project_id"]
    curriculum = state.get("curriculum", {})
    target_days = state["target_days"]

    # Extract days from expanded curriculum structure
    # Support both old format (list) and new format (dict with "days" key)
    if isinstance(curriculum, dict):
        days_list = curriculum.get("days", [])
    else:
        # Legacy format: curriculum is a list of DayTheme
        days_list = curriculum

    logger.info(f"💾 Inserting {len(days_list)} days (Days 1-{target_days - 1}) into database...")
    logger.info("   Note: Day 0 is handled separately via API endpoint")

    supabase = get_supabase_client()

    # Prepare all days to insert (Days 1-N only, Day 0 excluded)
    days_to_insert = []

    # Days 1 to target_days-1 (from curriculum)
    for theme in days_list:
        day_data = {
            "project_id": project_id,
            "day_number": theme["day_number"],
            "theme": theme["theme"],
            "description": theme.get("description", ""),
            "estimated_minutes": theme.get("estimated_minutes", 60),
            "generated_status": "pending",
        }

        # Note: concept_ids are curriculum-level IDs (like "c1", "c2"), not database UUIDs
        # We'll update days with database concept UUIDs after concepts are inserted
        # For now, set to empty array - will be populated after concepts are saved
        day_data["concept_ids"] = []

        days_to_insert.append(day_data)

    # Insert all days
    try:
        if not days_to_insert:
            logger.warning("⚠️  No days to insert (curriculum is empty)")
            state["day_ids_map"] = {}
            return state

        # Insert days with retry logic
        def insert_days():
            return supabase.table("roadmap_days").insert(days_to_insert).execute()

        response = execute_with_retry(insert_days)

        if not response.data:
            raise ValueError("Failed to insert days into database")

        # Store day_ids in state
        day_ids_map: dict[int, str] = {}
        for day_data in response.data:
            day_number = day_data["day_number"]
            day_id = day_data["day_id"]
            day_ids_map[day_number] = day_id

        logger.info(
            f"✅ Inserted {len(response.data)} days (Days 1-{target_days - 1}) into database"
        )

        # Log concept distribution
        total_concepts = sum(len(d.get("concept_ids", [])) for d in days_to_insert)
        if total_concepts > 0:
            logger.info(f"   Total concepts across days: {total_concepts}")

        state["day_ids_map"] = day_ids_map
        return state

    except Exception as e:
        logger.error(f"❌ Failed to insert days: {e}", exc_info=True)
        state["error"] = f"Failed to insert days: {str(e)}"
        return state


# Note: save_day0_content has been removed - Day 0 is now handled via API endpoint (initialize-day0)


def save_all_concepts_to_db(state: RoadmapAgentState) -> RoadmapAgentState:
    """
    Save ALL concepts from plan_curriculum to database upfront.

    This replaces the old per-day concept saving approach. All concepts are
    inserted at once with initial status 'pending' (database), which maps to
    'empty' (internal state), then content is generated later via generate_concept_content.

    This node:
    1. Extracts all concepts from curriculum.concepts
    2. Links each concept to its day via day_ids_map
    3. Inserts all concepts with status='pending' (database)
    4. Creates concept_ids_map: curriculum_concept_id -> database_concept_id

    Note: generation_queue is NOT stored in state - it is derived on demand
    from curriculum using get_ordered_concept_ids().

    Args:
        state: Current agent state with curriculum and day_ids_map

    Returns:
        Updated state with concept_ids_map (generation_queue is derived, not stored)
    """
    curriculum = state.get("curriculum", {})
    day_ids_map = state.get("day_ids_map", {})
    project_id = state.get("project_id")

    # Extract concepts from curriculum
    if isinstance(curriculum, dict):
        concepts_dict = curriculum.get("concepts", {})
        days_list = curriculum.get("days", [])
    else:
        logger.warning("⚠️  Curriculum is not in expanded format, cannot save concepts")
        state["concept_ids_map"] = {}
        return state

    if not concepts_dict:
        logger.warning("⚠️  No concepts found in curriculum")
        state["concept_ids_map"] = {}
        return state

    logger.info(f"💾 Saving {len(concepts_dict)} concepts to database...")

    # Build concept_id -> day_id mapping
    concept_to_day: dict[str, int] = {}
    for day in days_list:
        day_number = day["day_number"]
        for concept_id in day.get("concept_ids", []):
            concept_to_day[concept_id] = day_number
            concept_to_day[concept_id] = day_number

    # Build ordered list of concept IDs (by day, then by position in day)
    ordered_concept_ids: list[str] = []
    for day in sorted(days_list, key=lambda d: d["day_number"]):
        for concept_id in day.get("concept_ids", []):
            if concept_id in concepts_dict:
                ordered_concept_ids.append(concept_id)

    # Add any concepts not assigned to days
    for concept_id in concepts_dict:
        if concept_id not in ordered_concept_ids:
            ordered_concept_ids.append(concept_id)

    supabase = get_supabase_client()

    try:
        # Prepare concepts for insertion
        concepts_to_insert = []

        for order_index, concept_id in enumerate(ordered_concept_ids):
            concept_metadata = concepts_dict[concept_id]
            day_number = concept_to_day.get(concept_id)
            day_id = day_ids_map.get(day_number) if day_number else None

            if not day_id and day_number:
                logger.warning(f"⚠️  Day ID not found for day {day_number}, concept {concept_id}")

            concept_data: dict[str, Any] = {
                "day_id": day_id,
                "order_index": order_index,
                "title": concept_metadata.get("title", concept_id),
                "description": concept_metadata.get("objective", ""),
                "estimated_minutes": 15,  # Default, updated during generation
                "generated_status": "pending",
            }
            if project_id:
                concept_data["project_id"] = project_id

            # Add new curriculum metadata fields if database supports them
            # These are optional and may not exist in older schema versions
            if concept_metadata.get("repo_anchors"):
                concept_data["repo_anchors"] = concept_metadata["repo_anchors"]
            # Note: depends_on contains curriculum-level IDs (like "c1", "c2"), not database UUIDs
            # We'll update depends_on with database UUIDs after all concepts are inserted
            concept_data["depends_on"] = []  # Set to empty initially, will be updated later
            if concept_metadata.get("difficulty"):
                concept_data["difficulty"] = concept_metadata["difficulty"]
            if concept_metadata.get("objective"):
                concept_data["objective"] = concept_metadata["objective"]

            concepts_to_insert.append(
                {
                    "curriculum_id": concept_id,  # Track original ID
                    "data": concept_data,
                }
            )

        # Insert concepts in batches (Supabase can handle ~100 at a time)
        BATCH_SIZE = 50
        concept_ids_map: dict[str, str] = {}  # curriculum_id -> database_id

        for i in range(0, len(concepts_to_insert), BATCH_SIZE):
            batch = concepts_to_insert[i : i + BATCH_SIZE]

            # Extract just the data for insertion
            batch_data = [c["data"] for c in batch]
            batch_curriculum_ids = [c["curriculum_id"] for c in batch]

            # Insert with retry logic
            def insert_batch(batch=batch_data):
                return supabase.table("concepts").insert(batch).execute()

            response = execute_with_retry(insert_batch)

            if not response.data:
                raise ValueError(f"Failed to insert concepts batch {i // BATCH_SIZE + 1}")

            # Map curriculum IDs to database IDs
            for j, concept_data in enumerate(response.data):
                curriculum_id = batch_curriculum_ids[j]
                database_id = concept_data["concept_id"]
                concept_ids_map[curriculum_id] = database_id

            logger.debug(f"   Inserted batch {i // BATCH_SIZE + 1} ({len(batch)} concepts)")

        logger.info(f"✅ Inserted {len(concept_ids_map)} concepts to database")
        logger.info(f"   Concept IDs mapped: {list(concept_ids_map.keys())[:5]}...")

        # Update days with database concept UUIDs
        # Map curriculum concept_ids to database UUIDs for each day
        if isinstance(curriculum, dict):
            days_list = curriculum.get("days", [])
            for day in days_list:
                day_number = day.get("day_number")
                day_id = day_ids_map.get(day_number) if day_number else None

                if day_id:
                    # Get curriculum concept_ids for this day
                    curriculum_concept_ids = day.get("concept_ids", [])
                    # Map to database UUIDs
                    database_concept_ids = [
                        concept_ids_map.get(cid)
                        for cid in curriculum_concept_ids
                        if concept_ids_map.get(cid)
                    ]

                    if database_concept_ids:
                        # Update day with database concept UUIDs (with retry)
                        def update_day(concept_ids=database_concept_ids, day=day_id):
                            return (
                                supabase.table("roadmap_days")
                                .update({"concept_ids": concept_ids})
                                .eq("day_id", day)
                                .execute()
                            )

                        execute_with_retry(update_day)
                        logger.debug(
                            f"   Updated day {day_number} with {len(database_concept_ids)} concept UUIDs"
                        )

        # Update concepts with database UUIDs for depends_on field
        # Map curriculum depends_on IDs to database UUIDs
        if isinstance(curriculum, dict):
            concepts_dict = curriculum.get("concepts", {})
            for curriculum_concept_id, concept_metadata in concepts_dict.items():
                database_concept_id = concept_ids_map.get(curriculum_concept_id)
                if not database_concept_id:
                    continue

                # Get curriculum-level depends_on IDs
                curriculum_depends_on = concept_metadata.get("depends_on", [])
                if curriculum_depends_on:
                    # Map to database UUIDs
                    database_depends_on = [
                        concept_ids_map.get(dep_id)
                        for dep_id in curriculum_depends_on
                        if concept_ids_map.get(dep_id)
                    ]

                    if database_depends_on:
                        # Update concept with database UUIDs for depends_on (with retry)
                        def update_concept(
                            depends_on=database_depends_on, concept_id=database_concept_id
                        ):
                            return (
                                supabase.table("concepts")
                                .update({"depends_on": depends_on})
                                .eq("concept_id", concept_id)
                                .execute()
                            )

                        execute_with_retry(update_concept)
                        logger.debug(
                            f"   Updated concept {curriculum_concept_id} with {len(database_depends_on)} dependencies"
                        )

        # Update state
        state["concept_ids_map"] = concept_ids_map
        # Note: generation_queue is derived on demand, not stored

        # Determine user's current concept from user_concept_progress table
        project_id = state.get("project_id")
        user_id = state.get("_user_id")
        if user_id and project_id:
            try:
                user_current_concept_id = get_user_current_concept_from_progress(
                    project_id=project_id,
                    user_id=user_id,
                    concept_ids_map=concept_ids_map,
                )
                if user_current_concept_id:
                    state["user_current_concept_id"] = user_current_concept_id
                    logger.info(f"📍 Set user_current_concept_id to: {user_current_concept_id}")
            except Exception as e:
                # Don't fail the workflow if we can't determine user's current concept
                logger.warning(
                    f"⚠️  Failed to determine user's current concept: {e}. Continuing without it."
                )

        return state

    except Exception as e:
        logger.error(f"❌ Failed to save concepts: {e}", exc_info=True)
        state["error"] = f"Failed to save all concepts: {str(e)}"
        state["concept_ids_map"] = {}
        return state


# DEPRECATED: Use save_all_concepts_to_db instead
def save_concepts_to_db(state: RoadmapAgentState) -> RoadmapAgentState:
    """
    Save concept titles to database (before generating content/tasks).
    """
    day_id = state.get("current_day_id")
    current_concepts = state.get("current_concepts", [])

    if not day_id:
        raise ValueError("current_day_id not found in state")

    if not current_concepts:
        raise ValueError("current_concepts is empty")

    logger.info(f"💾 Saving {len(current_concepts)} concepts to database...")

    supabase = get_supabase_client()

    try:
        # Insert concepts with generating status
        concepts_to_insert = []
        for concept in current_concepts:
            concepts_to_insert.append(
                {
                    "day_id": day_id,
                    "order_index": concept["order_index"],
                    "title": concept["title"],
                    "description": concept.get("description", ""),
                    "estimated_minutes": concept.get("estimated_minutes", 10),
                    "generated_status": "generating",
                }
            )

        response = supabase.table("concepts").insert(concepts_to_insert).execute()

        if not response.data:
            raise ValueError("Failed to insert concepts")

        # Store concept_ids in state
        concept_ids_map: dict[int, str] = {}
        for concept_data in response.data:
            order_idx = concept_data["order_index"]
            concept_id = concept_data["concept_id"]
            concept_ids_map[order_idx] = concept_id

        logger.info(f"✅ Inserted {len(response.data)} concepts")

        state["concept_ids_map"] = concept_ids_map
        state["current_concept_index"] = 0

        return state

    except Exception as e:
        logger.error(f"❌ Failed to save concepts: {e}", exc_info=True)
        state["error"] = f"Failed to save concepts: {str(e)}"
        return state


def save_concept_content(state: RoadmapAgentState) -> RoadmapAgentState:
    """
    Save content and tasks for the current concept.
    Content is saved directly to the concept, not to sub_concepts table.
    """
    current_concepts = state.get("current_concepts", [])
    current_concept_index = state.get("current_concept_index", 0)
    concept_ids_map = state.get("concept_ids_map") or {}

    if current_concept_index >= len(current_concepts):
        logger.warning("⚠️  No more concepts to save")
        return state

    concept = current_concepts[current_concept_index]
    concept_id = concept_ids_map.get(concept["order_index"])

    if not concept_id:
        raise ValueError(f"Concept ID not found for order_index {concept['order_index']}")

    logger.info(f"💾 Saving content for concept: {concept['title']}")

    supabase = get_supabase_client()

    try:
        # Update concept with content (sanitize markdown before saving)
        raw_content = concept.get("content", "")
        content = sanitize_markdown_content(raw_content)
        estimated_minutes = concept.get("estimated_minutes", 10)

        supabase.table("concepts").update(
            {
                "content": content,
                "estimated_minutes": estimated_minutes,
                "generated_status": "generated",
            }
        ).eq("concept_id", concept_id).execute()

        # Insert tasks
        if concept.get("tasks"):
            tasks_to_insert = []
            for task in concept["tasks"]:
                if not isinstance(task, dict):
                    logger.warning(f"⚠️  Skipping invalid task (type: {type(task).__name__})")
                    continue

                try:
                    tasks_to_insert.append(
                        {
                            "concept_id": concept_id,
                            "order_index": int(task.get("order_index", 0)),
                            "title": str(task.get("title", "")),
                            "description": str(task.get("description", "")),
                            "task_type": str(task.get("task_type", "coding")),
                            "estimated_minutes": int(task.get("estimated_minutes", 15)),
                            "difficulty": str(task.get("difficulty", "medium")),
                            "hints": task.get("hints", []),
                            "solution": task.get("solution"),
                            "generated_status": "generated",
                        }
                    )
                except (KeyError, TypeError, ValueError) as e:
                    logger.warning(f"⚠️  Error processing task: {e}, skipping")
                    continue

            if tasks_to_insert:
                try:
                    supabase.table("tasks").insert(tasks_to_insert).execute()
                    logger.debug(f"   Inserted {len(tasks_to_insert)} tasks")
                except PostgrestAPIError as db_error:
                    error_msg = str(db_error)
                    if (
                        "duplicate key" in error_msg.lower()
                        or "unique constraint" in error_msg.lower()
                    ):
                        logger.warning("⚠️  Duplicate tasks detected, skipping")
                    else:
                        raise
                except httpx.HTTPStatusError as http_error:
                    if http_error.response.status_code == 429:
                        logger.warning("⚠️  Rate limit hit, will retry later")
                        supabase.table("concepts").update(
                            {"generated_status": "generating_with_errors"}
                        ).eq("concept_id", concept_id).execute()
                        state["current_concept_index"] = current_concept_index + 1
                        return state
                    else:
                        raise

        logger.info(f"✅ Concept content saved: {concept['title']}")

        # Increment concept index
        state["current_concept_index"] = current_concept_index + 1

        return state

    except (PostgrestAPIError, httpx.HTTPStatusError, httpx.RemoteProtocolError) as e:
        error_msg = str(e)
        logger.error(f"❌ Database error for concept '{concept['title']}': {e}")
        try:
            supabase.table("concepts").update({"generated_status": "generated_with_errors"}).eq(
                "concept_id", concept_id
            ).execute()
        except Exception:
            pass
        state["current_concept_index"] = current_concept_index + 1
        return state
    except Exception as e:
        logger.error(f"❌ Unexpected error for concept '{concept['title']}': {e}", exc_info=True)
        try:
            supabase.table("concepts").update({"generated_status": "generated_with_errors"}).eq(
                "concept_id", concept_id
            ).execute()
        except Exception:
            pass
        state["current_concept_index"] = current_concept_index + 1
        return state


def mark_concept_complete(state: RoadmapAgentState) -> RoadmapAgentState:
    """
    Mark a single concept as complete and update related state.

    This replaces mark_day_generated for concept-level tracking.

    This node:
    1. Updates concept status to 'ready' (or 'generated_with_errors') in database
    2. Updates concept_status_map in state
    3. Checks if all concepts in the day are complete
    4. If day complete, marks day as 'generated'
    5. Checks if all concepts are complete for workflow end

    Expects state to have:
    - _last_generated_concept_id: The concept that was just generated (from generate_content)
    - concept_ids_map: Map of curriculum_id -> database_id
    - concept_status_map: Status tracking for concepts

    Args:
        state: Current agent state

    Returns:
        Updated state with status tracking updated
    """
    # Get the concept that was just generated (not user's current position)
    current_concept_id = state.get("_last_generated_concept_id")
    concept_ids_map = state.get("concept_ids_map", {})
    concept_status_map = state.get("concept_status_map", {})
    curriculum = state.get("curriculum", {})
    day_ids_map = state.get("day_ids_map", {})

    if not current_concept_id:
        # Debug: log all state keys to help diagnose
        logger.warning(
            f"⚠️  No _last_generated_concept_id in state. "
            f"Available keys: {list(state.keys())}. Skipping mark_concept_complete"
        )
        return state

    logger.debug(f"📍 Marking concept complete: {current_concept_id}")

    # Get database ID
    database_concept_id = concept_ids_map.get(current_concept_id)
    if not database_concept_id:
        logger.warning(f"⚠️  Database ID not found for concept {current_concept_id}")
        return state

    # Get current status from status map
    current_status = concept_status_map.get(current_concept_id, {})
    status_value = current_status.get("status", "ready")

    # Map internal status to database status
    if status_value == "failed":
        db_status = "generated_with_errors"
    elif status_value == "generated_with_errors":
        db_status = "generated_with_errors"
    else:
        db_status = "generated"  # 'ready' -> 'generated' in database

    logger.info(f"✅ Marking concept {current_concept_id} as {db_status}...")

    supabase = get_supabase_client()

    try:
        # Update concept status in database
        # Note: Only include columns that exist in minimal schema (generated_status).
        # attempt_count, failure_reason require a DB migration if you need them.
        update_data: dict[str, Any] = {"generated_status": db_status}

        supabase.table("concepts").update(update_data).eq(
            "concept_id", database_concept_id
        ).execute()

        logger.info(f"✅ Concept {current_concept_id} marked as {db_status}")

        # Update status in state
        concept_status_map[current_concept_id] = {
            "status": "ready" if db_status == "generated" else "generated_with_errors",
            "attempt_count": current_status.get("attempt_count", 1),
            "failure_reason": current_status.get("failure_reason"),
        }
        state["concept_status_map"] = concept_status_map

        # Check if all concepts in the day are complete
        _check_and_mark_day_complete(
            state=state,
            concept_id=current_concept_id,
            curriculum=curriculum,
            day_ids_map=day_ids_map,
            concept_status_map=concept_status_map,
            supabase=supabase,
        )

        # Check if all concepts are complete (derive from curriculum)
        from app.agents.utils.concept_order import (
            SLIDING_WINDOW_AHEAD,
            are_all_concepts_complete,
            get_ordered_concept_ids,
            get_user_current_index,
            has_generated_up_to_window,
        )

        ordered_concept_ids = get_ordered_concept_ids(curriculum)
        all_complete = are_all_concepts_complete(ordered_concept_ids, concept_status_map)

        if all_complete and ordered_concept_ids:
            state["is_complete"] = True
            logger.info(f"🎉 All {len(ordered_concept_ids)} concepts generated!")
        else:
            # Check if sliding window is full (lazy loading pause condition)
            user_current_concept_id = state.get("user_current_concept_id")
            user_current_index = get_user_current_index(
                ordered_concept_ids, user_current_concept_id
            )

            # Use updated concept_status_map from state (includes the concept we just marked complete)
            updated_concept_status_map = state.get("concept_status_map", {})
            window_full = has_generated_up_to_window(
                ordered_concept_ids, updated_concept_status_map, user_current_index
            )

            if window_full:
                state["is_paused"] = True
                logger.info(
                    f"⏸️  Sliding window full (n+{SLIDING_WINDOW_AHEAD}). "
                    f"User at index {user_current_index}, generated up to index {user_current_index + SLIDING_WINDOW_AHEAD}. "
                    f"Pausing generation. Waiting for user progress."
                )

        return state

    except Exception as e:
        logger.error(f"❌ Failed to mark concept complete: {e}", exc_info=True)
        # Don't fail the workflow, continue
        return state


def _check_and_mark_day_complete(
    state: RoadmapAgentState,
    concept_id: str,
    curriculum: dict,
    day_ids_map: dict[int, str],
    concept_status_map: dict[str, ConceptStatus],
    supabase: Any,
) -> None:
    """
    Check if all concepts in a day are complete and mark day as generated.

    Args:
        state: Agent state
        concept_id: The concept that was just completed
        curriculum: Curriculum structure
        day_ids_map: Map of day_number -> day_id
        concept_status_map: Status tracking for concepts
        supabase: Supabase client
    """
    days_list = curriculum.get("days", [])

    # Find which day this concept belongs to
    day_number = None
    day_concept_ids = []

    for day in days_list:
        if concept_id in day.get("concept_ids", []):
            day_number = day["day_number"]
            day_concept_ids = day.get("concept_ids", [])
            break

    if not day_number or not day_concept_ids:
        return

    # Check if all concepts in the day are complete
    all_day_complete = all(
        concept_status_map.get(cid, {}).get("status")
        in ("ready", "generated_with_errors", "failed")
        for cid in day_concept_ids
    )

    if all_day_complete:
        day_id = day_ids_map.get(day_number)
        if day_id:
            try:
                supabase.table("roadmap_days").update({"generated_status": "generated"}).eq(
                    "day_id", day_id
                ).execute()

                logger.info(f"✅ Day {day_number} marked as generated (all concepts complete)")
            except Exception as e:
                logger.warning(f"⚠️  Failed to mark day {day_number} as generated: {e}")


# DEPRECATED: Use mark_concept_complete instead
def mark_day_generated(state: RoadmapAgentState) -> RoadmapAgentState:
    """
    DEPRECATED: Mark the current day as fully generated.
    Use mark_concept_complete instead for concept-level tracking.
    """
    day_id = state.get("current_day_id")
    current_day_number = state.get("current_day_number", 0)

    if not day_id:
        raise ValueError("current_day_id not found in state")

    logger.info(f"✅ Marking Day {current_day_number} as generated...")

    supabase = get_supabase_client()

    try:
        supabase.table("roadmap_days").update({"generated_status": "generated"}).eq(
            "day_id", day_id
        ).execute()

        logger.info(f"✅ Day {current_day_number} marked as generated")

        # Check if all days are complete
        target_days = state["target_days"]
        if current_day_number >= target_days - 1:
            state["is_complete"] = True
            logger.info(f"🎉 All {target_days} days generated!")

        return state

    except Exception as e:
        logger.error(f"❌ Failed to mark day as generated: {e}", exc_info=True)
        state["error"] = f"Failed to mark day as generated: {str(e)}"
        return state
