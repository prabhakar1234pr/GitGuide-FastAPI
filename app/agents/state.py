"""
Agent state definition for roadmap generation.
This defines all the data that flows through the LangGraph nodes.
"""

from typing import Literal, TypedDict


class RepoAnalysis(TypedDict):
    """Structured analysis of the GitHub repository"""

    summary: str
    primary_language: str
    frameworks: list[str]
    architecture_patterns: list[str]
    difficulty: str


class ConceptStatus(TypedDict):
    """Status tracking for concept generation"""

    status: Literal["empty", "generating", "ready", "generated_with_errors", "failed"]
    attempt_count: int
    failure_reason: str | None


class MemoryLedger(TypedDict):
    """Structured memory tracking for completed concepts"""

    completed_concepts: list[str]  # concept IDs that are completed
    files_touched: list[str]  # file paths from repo_anchors
    skills_unlocked: list[str]  # skills acquired from concepts


class ConceptMetadata(TypedDict):
    """Metadata for a learning concept (from curriculum planning)"""

    title: str
    objective: str
    repo_anchors: list[str]
    depends_on: list[str]
    difficulty: Literal["easy", "medium", "hard"]


class DayTheme(TypedDict):
    """Theme for a single day in the curriculum"""

    day_number: int
    theme: str
    description: str
    concept_ids: list[str]  # IDs of concepts in this day


class Curriculum(TypedDict, total=False):
    """Complete curriculum structure with days, concepts, and dependency graph"""

    days: list[DayTheme]
    concepts: dict[str, ConceptMetadata]
    dependency_graph: dict[str, list[str]]


class TaskData(TypedDict):
    """A single task for users to complete"""

    order_index: int
    title: str
    description: str
    task_type: Literal[
        "coding", "reading", "research", "quiz", "github_profile", "create_repo", "verify_commit"
    ]
    estimated_minutes: int
    difficulty: Literal["easy", "medium", "hard"]


class ConceptData(TypedDict):
    """A major learning concept with content and tasks"""

    order_index: int
    title: str
    description: str
    content: str  # Rich markdown documentation
    estimated_minutes: int
    tasks: list[TaskData]


class RoadmapAgentState(TypedDict):
    """
    Complete state for the roadmap generation agent.
    This state is passed between all LangGraph nodes.
    """

    # ===== PRE-RETRIEVED RAG CHUNKS (from API, avoids embedding init in roadmap) =====
    rag_chunks: list[dict] | None

    # ===== INPUT (Immutable) =====
    project_id: str
    github_url: str
    skill_level: str
    target_days: int

    # ===== ANALYSIS RESULTS =====
    repo_analysis: RepoAnalysis | None

    # ===== CURRICULUM (Generated once upfront) =====
    # Expanded structure: {days: [], concepts: {}, dependency_graph: {}}
    curriculum: Curriculum

    # ===== CONCEPT STATUS TRACKING =====
    concept_status_map: dict[str, ConceptStatus]  # concept_id -> status tracking

    # ===== STATE-BASED MEMORY =====
    concept_summaries: dict[str, str]  # concept_id -> summary text
    memory_ledger: MemoryLedger  # Structured memory tracking

    # ===== LAZY LOADING TRACKING =====
    user_current_concept_id: str | None  # Current concept user is on
    # Note: generation_queue is DERIVED from curriculum, not stored in state

    # ===== CURRENT GENERATION CONTEXT =====
    current_day_number: int
    current_day_id: str | None
    current_concepts: list[ConceptData]  # DEPRECATED: concepts now in curriculum
    current_concept_index: int

    # ===== MEMORY CONTEXT =====
    memory_context: str | None  # Aggregated summaries from previous days

    # ===== INTERNAL STATE (Database IDs) =====
    day_ids_map: dict[int, str] | None
    concept_ids_map: dict[str, str] | None  # Updated: concept_id (str) -> database_id (str)

    # ===== STATUS TRACKING =====
    is_complete: bool
    is_paused: bool  # True when generation paused due to sliding window being full
    error: str | None

    # ===== INTERNAL TRACKING (for node communication) =====
    _last_generated_concept_id: (
        str | None
    )  # Concept ID that was just generated (for mark_concept_complete)
    _user_id: str | None  # User ID from project (for querying user_concept_progress)
