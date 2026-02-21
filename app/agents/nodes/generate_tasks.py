"""
Task generation node using Gemini (Vertex AI).
Generates tasks with test files using notebook repo context.
Uses Gemini API for intelligent task and test file generation.
"""

import logging
from typing import Any

from app.agents.prompts.task_generation import TASK_GENERATION_PROMPT
from app.agents.pydantic_models import TasksBundleModel
from app.agents.state import RoadmapAgentState
from app.agents.utils.memory_context import (
    build_structured_memory_context,
    format_memory_context_for_prompt,
)
from app.agents.utils.pydantic_ai_client import run_gemini_structured
from app.agents.utils.repo_context import build_notebook_repo_context_for_task_generation
from app.agents.utils.retry_wrapper import generate_with_retry
from app.core.supabase_client import get_supabase_client
from app.utils.type_validator import validate_and_normalize_tasks

logger = logging.getLogger(__name__)


def _validate_test_language_match(
    task: dict[str, Any], project_language: str, concept_title: str
) -> str | None:
    """
    Validate that test file matches project language.

    Returns:
        Error message if mismatch found, None if valid
    """
    test_file_path = task.get("test_file_path", "")
    test_command = task.get("test_command", "")
    test_file_content = task.get("test_file_content", "")

    if not test_file_path:
        return "test_file_path is missing"

    # Check file extension
    is_js = project_language in ("javascript", "typescript")
    is_py = project_language == "python"

    if is_js:
        # JavaScript project - must have .test.js or .test.ts
        if not (test_file_path.endswith(".test.js") or test_file_path.endswith(".test.ts")):
            return f"JavaScript project but test_file_path has wrong extension: {test_file_path}"

        # Command must use jest
        if "jest" not in test_command.lower() and "npm test" not in test_command.lower():
            return f"JavaScript project but test_command doesn't use jest: {test_command}"

        # Content must use Jest syntax
        if "describe" not in test_file_content and "test(" not in test_file_content:
            if "import pytest" in test_file_content or "def test_" in test_file_content:
                return "JavaScript project but test_file_content uses Python syntax"

    elif is_py:
        # Python project - must have .py extension
        if not test_file_path.endswith(".py"):
            return f"Python project but test_file_path has wrong extension: {test_file_path}"

        # Command must use pytest
        if "pytest" not in test_command.lower():
            return f"Python project but test_command doesn't use pytest: {test_command}"

        # Content must use pytest syntax
        if "describe" in test_file_content or "expect(" in test_file_content:
            return "Python project but test_file_content uses JavaScript/Jest syntax"

    return None  # Valid


def _fix_test_language_mismatch(task: dict[str, Any], project_language: str) -> dict[str, Any]:
    """
    Fix test file to match project language.
    Generates a basic test file in the correct language.
    """
    is_js = project_language in ("javascript", "typescript")
    is_py = project_language == "python"

    task_title = task.get("title", "Task")
    order_index = task.get("order_index", 1)

    if is_js:
        # Generate JavaScript/Jest test
        test_file_path = f"tests/task_{order_index}.test.js"
        test_file_content = f"""const request = require('supertest');

describe('{task_title}', () => {{
  test('should complete the task', () => {{
    // TODO: Add test implementation
    expect(true).toBe(true);
  }});
}});
"""
        test_command = f"npx jest {test_file_path}"

    elif is_py:
        # Generate Python/pytest test
        test_file_path = f"tests/test_task_{order_index}.py"
        test_file_content = f"""import pytest

def test_task_{order_index}():
    \"\"\"Test for {task_title}\"\"\"
    # TODO: Add test implementation
    assert True
"""
        test_command = f"pytest {test_file_path} -v"
    else:
        # Unknown language - keep original
        return task

    return {
        **task,
        "test_file_path": test_file_path,
        "test_file_content": test_file_content,
        "test_command": test_command,
    }


def _detect_project_language(notebook_repo_context: dict[str, Any], test_structure_str: str) -> str:
    """
    Detect project language from repository context.

    Args:
        notebook_repo_context: Repository context with structure and code
        test_structure_str: Existing test structure info

    Returns:
        "javascript", "typescript", "python", or "unknown"
    """
    repo_structure = str(notebook_repo_context.get("repo_structure", "")).lower()
    repo_code = str(notebook_repo_context.get("repo_code_context", "")).lower()
    test_structure = test_structure_str.lower()

    # Check for explicit framework indicators
    if "jest" in test_structure or "mocha" in test_structure:
        return "javascript"
    if "pytest" in test_structure:
        return "python"

    # Check for package.json (JavaScript/Node.js)
    if "package.json" in repo_structure:
        # Check for TypeScript indicators
        if "tsconfig.json" in repo_structure or "typescript" in repo_code:
            return "typescript"
        return "javascript"

    # Check for Python indicators
    if "requirements.txt" in repo_structure or "pyproject.toml" in repo_structure:
        return "python"
    if "setup.py" in repo_structure:
        return "python"

    # Check file extensions in structure
    js_extensions = [".js", ".jsx", ".ts", ".tsx", ".mjs"]
    py_extensions = [".py"]

    js_count = sum(1 for ext in js_extensions if ext in repo_structure)
    py_count = sum(1 for ext in py_extensions if ext in repo_structure)

    if js_count > py_count:
        return "javascript"
    if py_count > 0:
        return "python"

    # Check code context
    if "require(" in repo_code or "import {" in repo_code or "express" in repo_code:
        return "javascript"
    if "import " in repo_code and "from " in repo_code:
        # Could be Python or ES6, check for more Python-specific patterns
        if "def " in repo_code or "class " in repo_code:
            return "python"

    # Default to javascript for web-focused projects
    return "javascript"


async def generate_tasks(state: RoadmapAgentState) -> RoadmapAgentState:
    """
    Generate tasks with test files for the concept that was just generated.

    This node:
    1. Gets the last generated concept_id from state
    2. Gets user_repo_url (notebook repo) from project
    3. Builds notebook repository context (structure + code)
    4. Calls LLM to generate tasks + tests matching notebook repo
    5. Validates and saves tasks to database
    6. Updates state

    Key: Uses notebook repo (user_repo_url), NOT textbook repo (github_url).

    Args:
        state: Current agent state

    Returns:
        Updated state with generated tasks
    """
    concept_id = state.get("_last_generated_concept_id")
    if not concept_id:
        logger.debug("No concept was generated, skipping task generation")
        return state

    concept_ids_map = state.get("concept_ids_map", {})
    curriculum = state.get("curriculum", {})
    skill_level = state.get("skill_level", "intermediate")
    project_id = state["project_id"]

    # Get concept metadata
    concepts_dict = curriculum.get("concepts", {}) if isinstance(curriculum, dict) else {}
    concept_metadata = concepts_dict.get(concept_id, {})
    concept_title = concept_metadata.get("title", concept_id)

    logger.info(f"🤖 Generating tasks using Gemini for concept: {concept_title} ({concept_id})")
    logger.info("   ✨ Using Gemini (Vertex AI) for task generation (descriptions only)")

    # Get project info to access user_repo_url (notebook repo) and github_url (textbook repo fallback)
    supabase = get_supabase_client()
    try:
        project_response = (
            supabase.table("projects")
            .select("user_repo_url, github_url, github_access_token")
            .eq("project_id", project_id)
            .execute()
        )

        if not project_response.data:
            logger.warning("Project not found, skipping task generation")
            return state

        project = project_response.data[0]
        user_repo_url = project.get("user_repo_url")  # NOTEBOOK REPO
        github_url = project.get("github_url")  # TEXTBOOK REPO (fallback)
        github_token = project.get("github_access_token")

        # If user_repo_url is not set, use textbook repo as fallback
        # This allows task generation even before Day 0 Task 2 is completed
        repo_url_for_tasks = user_repo_url or github_url
        is_using_fallback = not user_repo_url

        if not repo_url_for_tasks:
            logger.warning("Neither user_repo_url nor github_url set, skipping task generation")
            return state

        if is_using_fallback:
            logger.info(
                "user_repo_url not set, using textbook repo (github_url) as fallback for task generation"
            )

    except Exception as e:
        logger.error(f"Failed to get project info: {e}", exc_info=True)
        return state

    # Build memory context
    structured_memory = build_structured_memory_context(state, concept_id)
    memory_context_str = format_memory_context_for_prompt(structured_memory)

    # Build notebook repo context (or textbook repo if notebook not available)
    try:
        notebook_repo_context = await build_notebook_repo_context_for_task_generation(
            project_id=project_id,
            concept_metadata=concept_metadata,
            user_repo_url=repo_url_for_tasks,  # Use fallback if needed
            github_token=github_token,
        )
    except Exception as e:
        logger.error(f"Failed to build notebook repo context: {e}", exc_info=True)
        # Continue with minimal context
        notebook_repo_context = {
            "repo_structure": "Error fetching structure",
            "repo_code_context": "",
            "existing_test_structure": {
                "framework": "none",
                "test_directories": [],
                "config_files": [],
                "test_command": None,
                "has_test_setup": False,
            },
        }

    # Detect project language (used to tailor task instructions)
    project_language = _detect_project_language(notebook_repo_context, "")
    logger.info(f"📋 Detected project language for task generation: {project_language}")

    # Generate tasks with retry
    async def _generate():
        return await _llm_generate_tasks(
            concept_id=concept_id,
            concept_metadata=concept_metadata,
            skill_level=skill_level,
            memory_context_str=memory_context_str,
            notebook_repo_context=notebook_repo_context,
            project_language=project_language,
        )

    result, status_info = await generate_with_retry(
        generate_func=_generate,
        concept_id=concept_id,
        concept_title=concept_title,
    )

    if result:
        # Get raw tasks from LLM
        tasks_raw = result.get("tasks", [])

        # Validate basic task fields (title, description, etc.)
        tasks_validated = validate_and_normalize_tasks(tasks_raw)

        # Save tasks to database
        database_concept_id = concept_ids_map.get(concept_id)
        if database_concept_id and tasks_validated:
            await _save_tasks_to_db(database_concept_id, tasks_validated)
            logger.info(
                f"✅ Generated and saved {len(tasks_validated)} tasks with Gemini for concept: {concept_title}"
            )
        else:
            logger.warning(f"No database concept_id found for {concept_id}, tasks not saved")
    else:
        logger.error(f"❌ Failed to generate tasks with Gemini for concept: {concept_title}")

    return state


async def _llm_generate_tasks(
    concept_id: str,
    concept_metadata: dict[str, Any],
    skill_level: str,
    memory_context_str: str,
    notebook_repo_context: dict[str, Any],
    project_language: str,
) -> dict[str, Any]:
    """
    Call LLM to generate tasks with test files.

    Args:
        concept_id: Concept ID
        concept_metadata: Concept metadata from curriculum
        skill_level: User's skill level
        memory_context_str: Formatted memory context string
        notebook_repo_context: Notebook repository context
        test_structure_str: Formatted test structure string
        project_language: Detected project language (javascript/typescript/python)

    Returns:
        Raw LLM response dict with tasks array

    Raises:
        JSONParseError: If LLM response cannot be parsed
    """
    concept_title = concept_metadata.get("title", concept_id)
    concept_objective = concept_metadata.get("objective", "")

    # Format prompt with notebook repo context and detected language
    prompt = TASK_GENERATION_PROMPT.format(
        concept_title=concept_title,
        concept_objective=concept_objective,
        skill_level=skill_level,
        project_language=project_language,
        notebook_repo_structure=notebook_repo_context.get("repo_structure", ""),
        notebook_repo_code_context=notebook_repo_context.get("repo_code_context", ""),
        memory_context=memory_context_str or "No previous learning context.",
    )

    logger.info(f"   📤 Sending task generation request to Gemini for '{concept_title}'...")
    logger.debug("   🎯 Generating tasks (no tests) with Gemini (Vertex AI)...")

    bundle = await run_gemini_structured(
        user_prompt=prompt,
        system_prompt="You are an expert technical educator.",
        output_type=TasksBundleModel,
    )
    return bundle.model_dump()


async def _save_tasks_to_db(
    database_concept_id: str,
    tasks: list[dict[str, Any]],
) -> None:
    """
    Save generated tasks (no tests) to database.

    Args:
        database_concept_id: Database concept ID
        tasks: List of task dicts
    """
    from app.core.supabase_client import get_supabase_client

    supabase = get_supabase_client()

    try:
        tasks_to_insert = []
        for task in tasks:
            if isinstance(task, dict):
                tasks_to_insert.append(
                    {
                        "concept_id": database_concept_id,
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

        if tasks_to_insert:
            supabase.table("tasks").insert(tasks_to_insert).execute()
            logger.debug(f"   Saved {len(tasks_to_insert)} tasks to database")

    except Exception as e:
        logger.warning(f"⚠️  Failed to save tasks to database: {e}")
