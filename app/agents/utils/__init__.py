"""
Agent utilities package.
"""

# Import from retry_wrapper module
# Import from utils.py file (parent level)
# Use importlib to import from the file directly
import importlib.util
from pathlib import Path

# Import from concept_order module
from app.agents.utils.concept_order import (
    are_all_concepts_complete,
    compute_generation_window,
    get_ordered_concept_ids,
    get_user_current_index,
    select_next_concept_to_generate,
)

# Import from memory_context module
from app.agents.utils.memory_context import (
    build_structured_memory_context,
    format_memory_context_for_prompt,
)
from app.agents.utils.retry_wrapper import (
    ConceptGenerationError,
    ContentValidationError,
    JSONParseError,
    LLMError,
    classify_error,
    generate_with_retry,
    wrap_with_retry,
)

_utils_file = Path(__file__).parent.parent / "utils.py"
if _utils_file.exists():
    spec = importlib.util.spec_from_file_location("app.agents.utils_file", _utils_file)
    utils_file_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(utils_file_module)

    # Re-export functions from utils.py
    calculate_recursion_limit = utils_file_module.calculate_recursion_limit
    validate_inputs = utils_file_module.validate_inputs
    validate_state = utils_file_module.validate_state
    update_progress = utils_file_module.update_progress
    get_error_context = utils_file_module.get_error_context
    clean_completed_day_data = utils_file_module.clean_completed_day_data
else:
    # Fallback if file doesn't exist
    def calculate_recursion_limit(*args, **kwargs):
        raise ImportError("utils.py not found")

    def validate_inputs(*args, **kwargs):
        raise ImportError("utils.py not found")

    def validate_state(*args, **kwargs):
        raise ImportError("utils.py not found")

    def update_progress(*args, **kwargs):
        raise ImportError("utils.py not found")

    def get_error_context(*args, **kwargs):
        raise ImportError("utils.py not found")

    def clean_completed_day_data(*args, **kwargs):
        raise ImportError("utils.py not found")


__all__ = [
    "ConceptGenerationError",
    "LLMError",
    "JSONParseError",
    "ContentValidationError",
    "classify_error",
    "generate_with_retry",
    "wrap_with_retry",
    "calculate_recursion_limit",
    "validate_inputs",
    "validate_state",
    "update_progress",
    "get_error_context",
    "clean_completed_day_data",
    # Concept order utilities
    "get_ordered_concept_ids",
    "get_user_current_index",
    "compute_generation_window",
    "select_next_concept_to_generate",
    "are_all_concepts_complete",
    # Memory context utilities
    "build_structured_memory_context",
    "format_memory_context_for_prompt",
]
