"""
PydanticAI helper wrappers for structured LLM outputs.

Goal: centralize model/provider configuration and ensure nodes can request
validated structured outputs (Pydantic models) from the LLM.
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.groq import GroqModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.groq import GroqProvider

from app.config import settings
from app.services.rate_limiter import get_rate_limiter


@lru_cache
def _google_vertex_model() -> GoogleModel:
    """
    Gemini via Vertex AI using ADC/service account credentials.
    """
    if not settings.gcp_project_id:
        raise ValueError("GCP_PROJECT_ID is required for Vertex AI Gemini structured outputs")
    provider = GoogleProvider(
        vertexai=True, project=settings.gcp_project_id, location=settings.gcp_location
    )
    return GoogleModel(settings.gemini_model, provider=provider)


# Models that work with Gemini API (generativelanguage.googleapis.com).
# gemini-2.0-flash-exp, gemini-1.5-flash are Vertex-only or deprecated.
_GEMINI_API_AVAILABLE_MODELS = (
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-3-flash-preview",
    "gemini-3-pro-preview",
)


def _model_for_gemini_api() -> str:
    """Use a model available on Gemini API. Lite has separate/higher free-tier quota."""
    m = settings.gemini_model or ""
    if m in _GEMINI_API_AVAILABLE_MODELS:
        return m
    # Default to lite - often has quota when main flash is exhausted
    return "gemini-2.0-flash-lite"


@lru_cache
def _google_gla_model() -> GoogleModel:
    """
    Gemini via Generative Language API (API key).
    Used when Vertex AI (project/ADC) isn't configured.
    """
    if not settings.gemini_api_key:
        raise ValueError(
            "Neither Vertex AI (GCP_PROJECT_ID/ADC) nor GEMINI_API_KEY is configured for structured outputs"
        )
    provider = GoogleProvider(api_key=settings.gemini_api_key)
    model_name = _model_for_gemini_api()
    return GoogleModel(model_name, provider=provider)


def _google_provider() -> GoogleProvider:
    """
    Return the configured Google provider for Gemini (Vertex AI preferred).
    """
    if settings.gcp_project_id:
        return GoogleProvider(
            vertexai=True, project=settings.gcp_project_id, location=settings.gcp_location
        )
    if settings.gemini_api_key:
        return GoogleProvider(api_key=settings.gemini_api_key)
    raise ValueError(
        "Neither Vertex AI (GCP_PROJECT_ID/ADC) nor GEMINI_API_KEY is configured for structured outputs"
    )


@lru_cache
def _groq_model() -> GroqModel:
    provider = GroqProvider(api_key=settings.groq_api_key)
    return GroqModel(settings.groq_model, provider=provider)


logger = logging.getLogger(__name__)

# Lite models have separate quota - use when main model hits 429
_GEMINI_API_LITE_FALLBACK = "gemini-2.0-flash-lite"


def _google_gla_model_for(model_name: str) -> GoogleModel:
    """Gemini via API key with specific model (for fallbacks)."""
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY required")
    provider = GoogleProvider(api_key=settings.gemini_api_key)
    return GoogleModel(model_name, provider=provider)


async def run_gemini_structured[T](
    *,
    user_prompt: str,
    system_prompt: str,
    output_type: type[T],
    model_settings: dict[str, Any] | None = None,
) -> T:
    """
    Run Gemini (Vertex AI) and force structured output validated against `output_type`.
    """
    await get_rate_limiter().acquire()

    if settings.gemini_api_key:
        model = _google_gla_model()
    else:
        model = _google_vertex_model()

    def _run(agent_instance: Agent) -> T:
        import asyncio

        result = None
        loop = asyncio.get_event_loop()
        if hasattr(loop, "run_until_complete"):
            result = loop.run_until_complete(agent_instance.run(user_prompt))
        else:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(lambda: asyncio.run(agent_instance.run(user_prompt)))
                result = future.result()
        return result.output

    agent = Agent(
        model,
        system_prompt=system_prompt,
        output_type=output_type,
        model_settings=model_settings or {},
    )
    try:
        result = await agent.run(user_prompt)
        return result.output
    except ModelHTTPError as exc:
        if exc.status_code != 429:
            raise
        # Quota exceeded: retry after delay, then try lite model (separate quota)
        logger.warning("Gemini 429 quota exceeded, retrying after 12s then fallback to lite model")
        await asyncio.sleep(12)
        try:
            result = await agent.run(user_prompt)
            return result.output
        except ModelHTTPError:
            pass
        # Fallback to lite model (has separate quota)
        if settings.gemini_api_key:
            logger.info(f"Using fallback model: {_GEMINI_API_LITE_FALLBACK}")
            fallback = _google_gla_model_for(_GEMINI_API_LITE_FALLBACK)
            fallback_agent = Agent(
                fallback,
                system_prompt=system_prompt,
                output_type=output_type,
                model_settings=model_settings or {},
            )
            result = await fallback_agent.run(user_prompt)
            return result.output
        raise


async def run_groq_structured[T](
    *,
    user_prompt: str,
    system_prompt: str,
    output_type: type[T],
    model_settings: dict[str, Any] | None = None,
) -> T:
    """
    Run Groq and force structured output validated against `output_type`.
    """
    agent = Agent(
        _groq_model(),
        system_prompt=system_prompt,
        output_type=output_type,
        model_settings=model_settings or {},
    )
    result = await agent.run(user_prompt)
    return result.output
