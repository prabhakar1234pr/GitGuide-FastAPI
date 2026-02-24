"""
Retry wrapper utilities for concept generation in LangGraph agent.
Handles disciplined retry logic with exponential backoff and status tracking.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3  # Maximum number of retry attempts (total attempts = MAX_RETRIES + 1)
BASE_BACKOFF_SECONDS = 5  # Base wait time for exponential backoff


# Type variable for generic function wrapping
T = TypeVar("T")


# ============================================
# Exception Hierarchy
# ============================================


class ConceptGenerationError(Exception):
    """Base exception for concept generation failures."""

    pass


class LLMError(ConceptGenerationError):
    """LLM-specific errors (rate limits, timeouts, API errors)."""

    pass


class JSONParseError(ConceptGenerationError):
    """JSON parsing errors from LLM responses."""

    pass


class ContentValidationError(ConceptGenerationError):
    """Content validation errors (empty content, missing fields)."""

    pass


# ============================================
# Error Classification
# ============================================


def classify_error(error: Exception) -> type[ConceptGenerationError]:
    """
    Classify an exception into the appropriate error type.

    Args:
        error: The exception to classify

    Returns:
        Classified error type
    """
    error_str = str(error).lower()
    error_type = type(error).__name__

    # Check for LLM-related errors
    if any(
        keyword in error_str
        for keyword in [
            "rate limit",
            "resource_exhausted",
            "resource exhausted",
            "timeout",
            "connection",
            "429",
            "503",
            "500",
            "groq",
            "api error",
            "http error",
            "modelhttperror",
        ]
    ):
        return LLMError

    # Check for JSON parsing errors
    if any(
        keyword in error_str or keyword in error_type
        for keyword in [
            "json",
            "parse",
            "decode",
            "syntax",
            "jsondecodeerror",
            "invalid json",
        ]
    ):
        return JSONParseError

    # Check for validation errors
    if any(
        keyword in error_str
        for keyword in [
            "empty",
            "missing",
            "invalid",
            "validation",
            "required",
            "valueerror",
        ]
    ):
        return ContentValidationError

    # Default to base error
    return ConceptGenerationError


# ============================================
# Retry Wrapper
# ============================================


async def generate_with_retry(
    generate_func: Callable[[], Any],
    concept_id: str | None = None,
    concept_title: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    """
    Execute a concept generation function with disciplined retry logic.

    Retry Policy:
    - Maximum MAX_RETRIES retries (total attempts = MAX_RETRIES + 1)
    - Exponential backoff: BASE_BACKOFF_SECONDS * (2 ^ attempt)
    - Only retries retryable errors (LLMError, JSONParseError)
    - Tracks attempt count and failure reason

    Args:
        generate_func: Async function that generates concept content
        concept_id: Optional concept ID for logging
        concept_title: Optional concept title for logging

    Returns:
        Tuple of (result, status_dict)
        status_dict contains:
        - content_status: "ready" | "failed"
        - failure_reason: str | None
        - attempt_count: int

    Example:
        async def generate_concept():
            return await some_generation_logic()

        result, status = await generate_with_retry(
            generate_concept,
            concept_id="c1",
            concept_title="Introduction"
        )
    """
    status = {
        "content_status": "generating",
        "failure_reason": None,
        "attempt_count": 0,
    }

    concept_label = (
        f"'{concept_title}'" if concept_title else f"ID {concept_id}" if concept_id else "concept"
    )

    for attempt in range(MAX_RETRIES + 1):
        status["attempt_count"] = attempt + 1

        try:
            logger.debug(f"   Attempt {attempt + 1}/{MAX_RETRIES + 1} for concept {concept_label}")

            # Execute generation function
            result = await generate_func()

            # If we get here, generation succeeded
            status["content_status"] = "ready"
            logger.info(f"✅ Successfully generated {concept_label} on attempt {attempt + 1}")
            return result, status

        except (JSONParseError, ValueError) as e:
            # Retryable errors - JSON parsing issues
            error_class = classify_error(e)
            if not isinstance(error_class, JSONParseError):
                # Check if it's actually a JSON error
                if "json" in str(e).lower() or "parse" in str(e).lower():
                    error_class = JSONParseError

            if attempt == MAX_RETRIES:
                # Max retries exceeded
                status["content_status"] = "failed"
                status["failure_reason"] = f"json_parse: {str(e)}"
                logger.error(
                    f"❌ Failed to generate {concept_label} after {MAX_RETRIES + 1} attempts: {e}"
                )
                return None, status

            # Calculate exponential backoff
            wait_time = BASE_BACKOFF_SECONDS * (2**attempt)
            logger.warning(
                f"⚠️  Attempt {attempt + 1} failed for {concept_label} (JSON parse error). "
                f"Retrying in {wait_time}s: {e}"
            )
            await asyncio.sleep(wait_time)

        except LLMError as e:
            # LLM-specific errors (rate limits, timeouts)
            if attempt == MAX_RETRIES:
                status["content_status"] = "failed"
                status["failure_reason"] = f"llm_error: {str(e)}"
                logger.error(
                    f"❌ Failed to generate {concept_label} after {MAX_RETRIES + 1} attempts "
                    f"(LLM error): {e}"
                )
                return None, status

            wait_time = BASE_BACKOFF_SECONDS * (2**attempt)
            logger.warning(
                f"⚠️  Attempt {attempt + 1} failed for {concept_label} (LLM error). "
                f"Retrying in {wait_time}s: {e}"
            )
            await asyncio.sleep(wait_time)

        except ConceptGenerationError as e:
            # Other concept generation errors
            if attempt == MAX_RETRIES:
                status["content_status"] = "failed"
                status["failure_reason"] = f"generation_error: {str(e)}"
                logger.error(
                    f"❌ Failed to generate {concept_label} after {MAX_RETRIES + 1} attempts: {e}"
                )
                return None, status

            wait_time = BASE_BACKOFF_SECONDS * (2**attempt)
            logger.warning(
                f"⚠️  Attempt {attempt + 1} failed for {concept_label}. "
                f"Retrying in {wait_time}s: {e}"
            )
            await asyncio.sleep(wait_time)

        except Exception as e:
            classified = classify_error(e)
            if classified is LLMError and attempt < MAX_RETRIES:
                wait_time = BASE_BACKOFF_SECONDS * (2 ** (attempt + 1))
                logger.warning(
                    f"⚠️  Attempt {attempt + 1} failed for {concept_label} "
                    f"(retryable {type(e).__name__}). Retrying in {wait_time}s: {e}"
                )
                await asyncio.sleep(wait_time)
                continue

            status["content_status"] = "failed"
            status["failure_reason"] = f"unexpected: {str(e)}"
            logger.error(
                f"❌ Unexpected error generating {concept_label}, not retrying: {e}",
                exc_info=True,
            )
            return None, status

    # Should never reach here, but safety net
    status["content_status"] = "failed"
    status["failure_reason"] = "max_retries_exceeded"
    return None, status


def wrap_with_retry(
    max_retries: int = MAX_RETRIES,
    base_backoff: int = BASE_BACKOFF_SECONDS,
):
    """
    Decorator version of generate_with_retry (for convenience).

    Args:
        max_retries: Maximum number of retries
        base_backoff: Base backoff time in seconds

    Example:
        @wrap_with_retry()
        async def generate_concept():
            return await some_generation_logic()
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            result, status = await generate_with_retry(
                lambda: func(*args, **kwargs),
            )
            if status["content_status"] == "failed":
                raise ConceptGenerationError(f"Generation failed: {status['failure_reason']}")
            return result

        return wrapper

    return decorator
