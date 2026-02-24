"""
Comprehensive tests for the roadmap agent robustness features.
Tests input validation, state management, error handling, and optimizations.
"""

import pytest

from app.agents.roadmap_agent import (
    build_roadmap_graph,
    get_roadmap_graph,
    run_roadmap_agent,
    should_continue_after_concept,
    should_continue_concept_generation,
)
from app.agents.state import RoadmapAgentState
from app.agents.utils import (
    calculate_recursion_limit,
    clean_completed_day_data,
    get_error_context,
    update_progress,
    validate_inputs,
    validate_state,
)


class TestInputValidation:
    """Test input validation functions."""

    def test_validate_inputs_success(self):
        """Test successful input validation."""
        validate_inputs(
            project_id="test-project-id",
            github_url="https://github.com/user/repo",
            skill_level="beginner",
            target_days=14,
        )
        # Should not raise

    def test_validate_inputs_invalid_project_id(self):
        """Test validation fails with invalid project_id."""
        with pytest.raises(ValueError, match="project_id must be a non-empty string"):
            validate_inputs("", "https://github.com/user/repo", "beginner", 14)

        with pytest.raises(ValueError, match="project_id must be a non-empty string"):
            validate_inputs(None, "https://github.com/user/repo", "beginner", 14)

    def test_validate_inputs_invalid_github_url(self):
        """Test validation fails with invalid GitHub URL."""
        with pytest.raises(ValueError, match="Invalid GitHub URL format"):
            validate_inputs("test-id", "not-a-url", "beginner", 14)

        with pytest.raises(ValueError, match="Invalid GitHub URL format"):
            validate_inputs("test-id", "https://gitlab.com/user/repo", "beginner", 14)

    def test_validate_inputs_invalid_skill_level(self):
        """Test validation fails with invalid skill_level."""
        with pytest.raises(ValueError, match="Invalid skill_level"):
            validate_inputs("test-id", "https://github.com/user/repo", "", 14)

    def test_validate_inputs_invalid_target_days(self):
        """Test validation fails with invalid target_days."""
        with pytest.raises(ValueError, match="target_days must be an integer"):
            validate_inputs("test-id", "https://github.com/user/repo", "beginner", "14")

        with pytest.raises(ValueError, match="target_days must be at least 1"):
            validate_inputs("test-id", "https://github.com/user/repo", "beginner", 0)

        with pytest.raises(ValueError, match="target_days must be at most 100"):
            validate_inputs("test-id", "https://github.com/user/repo", "beginner", 101)


class TestStateValidation:
    """Test state validation functions."""

    def test_validate_state_success(self):
        """Test successful state validation."""
        state: RoadmapAgentState = {
            "project_id": "test-id",
            "github_url": "https://github.com/user/repo",
            "skill_level": "beginner",
            "target_days": 14,
            "repo_analysis": None,
            "curriculum": [],
            "current_day_number": 0,
            "current_day_id": None,
            "current_concepts": [],
            "current_concept_index": 0,
            "day_ids_map": None,
            "concept_ids_map": None,
            "is_complete": False,
            "error": None,
            "progress": None,
            "start_time": None,
            "last_updated": None,
        }

        validate_state(state, ["project_id", "github_url"])
        # Should not raise

    def test_validate_state_missing_field(self):
        """Test validation fails with missing field."""
        state: RoadmapAgentState = {
            "project_id": "test-id",
            "github_url": "https://github.com/user/repo",
            "skill_level": "beginner",
            "target_days": 14,
            "repo_analysis": None,
            "curriculum": [],
            "current_day_number": 0,
            "current_day_id": None,
            "current_concepts": [],
            "current_concept_index": 0,
            "day_ids_map": None,
            "concept_ids_map": None,
            "is_complete": False,
            "error": None,
            "progress": None,
            "start_time": None,
            "last_updated": None,
        }

        with pytest.raises(ValueError, match="Missing required state fields"):
            validate_state(state, ["missing_field"])

    def test_validate_state_none_field(self):
        """Test validation fails with None field."""
        state: RoadmapAgentState = {
            "project_id": None,  # type: ignore
            "github_url": "https://github.com/user/repo",
            "skill_level": "beginner",
            "target_days": 14,
            "repo_analysis": None,
            "curriculum": [],
            "current_day_number": 0,
            "current_day_id": None,
            "current_concepts": [],
            "current_concept_index": 0,
            "day_ids_map": None,
            "concept_ids_map": None,
            "is_complete": False,
            "error": None,
            "progress": None,
            "start_time": None,
            "last_updated": None,
        }

        with pytest.raises(ValueError, match="Missing required state fields"):
            validate_state(state, ["project_id"])


class TestRecursionLimitCalculation:
    """Test recursion limit calculation."""

    def test_calculate_recursion_limit_small(self):
        """Test recursion limit for small roadmap."""
        limit = calculate_recursion_limit(target_days=5, avg_concepts_per_day=3)
        assert limit >= 50  # Minimum limit
        assert limit <= 500  # Maximum limit

    def test_calculate_recursion_limit_medium(self):
        """Test recursion limit for medium roadmap."""
        limit = calculate_recursion_limit(target_days=14, avg_concepts_per_day=4)
        assert limit >= 50
        assert limit <= 500

    def test_calculate_recursion_limit_large(self):
        """Test recursion limit for large roadmap."""
        limit = calculate_recursion_limit(target_days=30, avg_concepts_per_day=5)
        assert limit >= 50
        assert limit <= 1000  # Implementation caps at 1000

    def test_calculate_recursion_limit_scales(self):
        """Test recursion limit scales with target_days."""
        limit_5 = calculate_recursion_limit(target_days=5)
        limit_10 = calculate_recursion_limit(target_days=10)
        assert limit_10 > limit_5


class TestProgressTracking:
    """Test progress tracking functions."""

    def test_update_progress(self):
        """Test updating progress in state."""
        state: RoadmapAgentState = {
            "project_id": "test-id",
            "github_url": "https://github.com/user/repo",
            "skill_level": "beginner",
            "target_days": 14,
            "repo_analysis": None,
            "curriculum": [],
            "current_day_number": 5,
            "current_day_id": None,
            "current_concepts": [],
            "current_concept_index": 0,
            "day_ids_map": None,
            "concept_ids_map": None,
            "is_complete": False,
            "error": None,
            "progress": None,
            "start_time": None,
            "last_updated": None,
        }

        updated_state = update_progress(state, phase="test_phase", status="running")

        assert updated_state["progress"] is not None
        assert updated_state["progress"]["phase"] == "test_phase"
        assert updated_state["progress"]["status"] == "running"
        assert updated_state["last_updated"] is not None
        assert "completion_percentage" in updated_state["progress"]

    def test_get_error_context(self):
        """Test getting error context from state."""
        state: RoadmapAgentState = {
            "project_id": "test-id",
            "github_url": "https://github.com/user/repo",
            "skill_level": "beginner",
            "target_days": 14,
            "repo_analysis": None,
            "curriculum": [],
            "current_day_number": 5,
            "current_day_id": "day-id",
            "current_concepts": [
                {
                    "order_index": 1,
                    "title": "Test Concept",
                    "description": "Test",
                    "subconcepts": [],
                    "tasks": [],
                }
            ],
            "current_concept_index": 0,
            "day_ids_map": None,
            "concept_ids_map": None,
            "is_complete": False,
            "error": None,
            "progress": None,
            "start_time": None,
            "last_updated": None,
        }

        context = get_error_context(state)

        assert context["project_id"] == "test-id"
        assert context["day_number"] == 5
        assert context["concept_index"] == 0
        assert context["concept_title"] == "Test Concept"


class TestMemoryOptimization:
    """Test memory optimization functions."""

    def test_clean_completed_day_data(self):
        """Test cleaning completed day data."""
        state: RoadmapAgentState = {
            "project_id": "test-id",
            "github_url": "https://github.com/user/repo",
            "skill_level": "beginner",
            "target_days": 14,
            "repo_analysis": None,
            "curriculum": [],
            "current_day_number": 5,
            "current_day_id": "day-id",
            "current_concepts": [
                {
                    "order_index": 1,
                    "title": "Test Concept",
                    "description": "Test",
                    "subconcepts": [],
                    "tasks": [],
                }
            ],
            "current_concept_index": 2,
            "day_ids_map": {1: "id1", 2: "id2"},
            "concept_ids_map": {1: "cid1", 2: "cid2"},
            "is_complete": False,
            "error": None,
            "progress": None,
            "start_time": None,
            "last_updated": None,
        }

        cleaned_state = clean_completed_day_data(state)

        assert cleaned_state["current_concepts"] == []
        assert cleaned_state["current_concept_index"] == 0
        assert cleaned_state["concept_ids_map"] == {}
        # day_ids_map should be preserved
        assert cleaned_state["day_ids_map"] is not None


def _minimal_roadmap_state(**overrides) -> RoadmapAgentState:
    """Build minimal RoadmapAgentState for conditional edge tests."""
    base: RoadmapAgentState = {
        "project_id": "test-id",
        "github_url": "https://github.com/user/repo",
        "skill_level": "beginner",
        "target_days": 14,
        "repo_analysis": None,
        "curriculum": {
            "days": [
                {"day_number": 1, "theme": "Test", "description": "Test", "concept_ids": ["c1"]}
            ],
            "concepts": {
                "c1": {
                    "title": "C1",
                    "objective": "X",
                    "repo_anchors": [],
                    "depends_on": [],
                    "difficulty": "easy",
                }
            },
            "dependency_graph": {},
        },
        "concept_status_map": {},
        "concept_summaries": {},
        "memory_ledger": {"completed_concepts": [], "files_touched": [], "skills_unlocked": []},
        "user_current_concept_id": None,
        "current_day_number": 0,
        "current_day_id": None,
        "current_concepts": [],
        "current_concept_index": 0,
        "memory_context": None,
        "day_ids_map": None,
        "concept_ids_map": None,
        "is_complete": False,
        "is_paused": False,
        "error": None,
        "_last_generated_concept_id": None,
        "_user_id": None,
        "rag_chunks": None,
    }
    base.update(overrides)
    return base


class TestConditionalEdges:
    """Test conditional edge functions."""

    def test_should_continue_concept_generation_complete(self):
        """Test should_continue_concept_generation when complete."""
        state = _minimal_roadmap_state(is_complete=True)
        result = should_continue_concept_generation(state)
        assert result == "end"

    def test_should_continue_concept_generation_has_error(self):
        """Test should_continue_concept_generation when error exists."""
        state = _minimal_roadmap_state(error="Test error")
        result = should_continue_concept_generation(state)
        assert result == "end"

    def test_should_continue_concept_generation_continue(self):
        """Test should_continue_concept_generation when concepts remain."""
        state = _minimal_roadmap_state(
            concept_status_map={
                "c1": {"status": "empty", "attempt_count": 0, "failure_reason": None}
            }
        )
        result = should_continue_concept_generation(state)
        assert result == "build_memory_context"

    def test_should_continue_concept_generation_all_done(self):
        """Test should_continue_concept_generation when all concepts done."""
        state = _minimal_roadmap_state(
            concept_status_map={
                "c1": {"status": "ready", "attempt_count": 1, "failure_reason": None}
            }
        )
        result = should_continue_concept_generation(state)
        assert result == "end"

    def test_should_continue_after_concept_complete(self):
        """Test should_continue_after_concept when complete."""
        state = _minimal_roadmap_state(is_complete=True)
        result = should_continue_after_concept(state)
        assert result == "end"

    def test_should_continue_after_concept_has_error(self):
        """Test should_continue_after_concept when error exists."""
        state = _minimal_roadmap_state(error="Test error")
        result = should_continue_after_concept(state)
        assert result == "end"


class TestGraphBuilding:
    """Test graph building functions."""

    def test_build_roadmap_graph(self):
        """Test building the roadmap graph."""
        graph = build_roadmap_graph()
        assert graph is not None

    def test_get_roadmap_graph_singleton(self):
        """Test that get_roadmap_graph returns singleton."""
        graph1 = get_roadmap_graph()
        graph2 = get_roadmap_graph()
        assert graph1 is graph2


class TestRunRoadmapAgent:
    """Test running the roadmap agent."""

    @pytest.mark.asyncio
    async def test_run_roadmap_agent_invalid_input(self):
        """Test run_roadmap_agent with invalid input."""
        result = await run_roadmap_agent(
            project_id="",
            github_url="https://github.com/user/repo",
            skill_level="beginner",
            target_days=14,
        )

        assert result["success"] is False
        assert "Invalid input" in result["error"]

    @pytest.mark.asyncio
    async def test_run_roadmap_agent_invalid_url(self):
        """Test run_roadmap_agent with invalid GitHub URL."""
        result = await run_roadmap_agent(
            project_id="test-id", github_url="not-a-url", skill_level="beginner", target_days=14
        )

        assert result["success"] is False
        assert "Invalid input" in result["error"]

    @pytest.mark.asyncio
    async def test_run_roadmap_agent_invalid_skill_level(self):
        """Test run_roadmap_agent with invalid skill level."""
        result = await run_roadmap_agent(
            project_id="test-id",
            github_url="https://github.com/user/repo",
            skill_level="guru",
            target_days=14,
        )

        assert result["success"] is False
        assert "Invalid input" in result["error"]

    @pytest.mark.asyncio
    async def test_run_roadmap_agent_invalid_target_days(self):
        """Test run_roadmap_agent with invalid target_days."""
        result = await run_roadmap_agent(
            project_id="test-id",
            github_url="https://github.com/user/repo",
            skill_level="beginner",
            target_days=0,
        )

        assert result["success"] is False
        assert "Invalid input" in result["error"]

    @pytest.mark.asyncio
    async def test_run_roadmap_agent_returns_progress(self):
        """Test that run_roadmap_agent returns progress information."""
        # This will fail early due to missing database/project, but should return progress
        result = await run_roadmap_agent(
            project_id="test-id-12345",
            github_url="https://github.com/user/repo",
            skill_level="beginner",
            target_days=14,
        )

        # Should have duration_seconds even if it fails
        assert "duration_seconds" in result
        assert isinstance(result["duration_seconds"], float)
        assert result["duration_seconds"] >= 0
