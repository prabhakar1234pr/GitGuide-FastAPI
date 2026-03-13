"""
Google Gemini Service for high-quality content generation.
Supports both API key and service account authentication.
Used for critical content generation tasks that require better quality.
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
from google.api_core import exceptions as google_exceptions
from tenacity import (
    after_log,
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import PROJECT_ROOT, settings
from app.services.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)

# Gemini API endpoint
GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent"
)

# Lazy singleton instances (per purpose)
_gemini_service_instances: dict[str, "GeminiService"] = {}


def get_gemini_service(purpose: str = "default") -> "GeminiService":
    """
    Get or create singleton GeminiService instance (lazy initialization).

    Returns:
        GeminiService: Singleton instance
    """
    global _gemini_service_instances

    key = purpose or "default"
    if key not in _gemini_service_instances:
        logger.info(f"🤖 Initializing GeminiService (purpose={key})...")
        _gemini_service_instances[key] = GeminiService(purpose=key)
        logger.info(f"✅ GeminiService ready (purpose={key}, will reuse for future requests)")

    return _gemini_service_instances[key]


class GeminiService:
    """Google Gemini service for high-quality content generation."""

    def __init__(self, purpose: str = "default"):
        """
        Initialize GeminiService with rate limiting and retry logic.
        Supports both API key and service account authentication.
        """
        self.purpose = purpose or "default"
        self.use_service_account = False
        self.api_key = None
        self.project_id = None
        self.location = None

        # Purpose-specific overrides (used to avoid sharing the same Gemini configuration)
        if self.purpose == "verification":
            effective_model = settings.verification_gemini_model or settings.gemini_model
            effective_project_id = settings.verification_gcp_project_id or settings.gcp_project_id
            effective_location = (
                settings.verification_gcp_location or settings.gcp_location or "global"
            )
            effective_creds = (
                settings.verification_google_application_credentials
                or settings.google_application_credentials
            )
            effective_api_key = settings.verification_gemini_api_key or settings.gemini_api_key
        else:
            effective_model = settings.gemini_model
            effective_project_id = settings.gcp_project_id
            effective_location = settings.gcp_location or "global"
            effective_creds = settings.google_application_credentials
            effective_api_key = settings.gemini_api_key

        # Default model: gemini-2.x/2.5.x for Vertex AI
        self.model = effective_model if effective_model else "gemini-2.0-flash-001"
        self.timeout = 180.0  # 180 seconds timeout
        self.rate_limiter = get_rate_limiter()

        # Check for service account (preferred method - uses GCP free credits)
        if effective_creds:
            creds_path = Path(effective_creds)
            if not creds_path.is_absolute():
                creds_path = PROJECT_ROOT / creds_path

            if creds_path.exists():
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(creds_path)
                self.use_service_account = True
                self.project_id = effective_project_id
                # Default to 'global' - required for Gemini publisher models
                self.location = effective_location

                if not self.project_id:
                    logger.warning("⚠️ GCP_PROJECT_ID not set. Reading from JSON file...")
                    # Try to read project_id from JSON
                    try:
                        with open(creds_path) as f:
                            creds_data = json.load(f)
                            self.project_id = creds_data.get("project_id")
                            if self.project_id:
                                logger.info(f"✅ Found project_id in JSON: {self.project_id}")
                    except Exception as e:
                        logger.error(f"❌ Failed to read project_id from JSON: {e}")

                if not self.project_id:
                    raise ValueError(
                        "GCP_PROJECT_ID is required when using service account. "
                        "Set it in .env or ensure it's in your JSON file."
                    )

                logger.info("✅ Using Gemini with Service Account (uses GCP free credits)")
                logger.debug(f"   Credentials: {creds_path}")
                logger.debug(f"   Project ID: {self.project_id}")
                logger.debug(f"   Location: {self.location}")
            else:
                logger.warning(f"⚠️ Service account file not found: {creds_path}")
                logger.warning("   Falling back to API key method if available")

        # Fallback: Use Application Default Credentials (ADC) on GCP (Cloud Run / GCE)
        # This works when the Cloud Run service is configured with a runtime service account
        # that has Vertex AI permissions (no JSON file needed).
        if not self.use_service_account:
            try:
                import google.auth

                creds, adc_project_id = google.auth.default()
                if creds:
                    self.use_service_account = True
                    self.project_id = effective_project_id or adc_project_id
                    self.location = effective_location

                    if not self.project_id:
                        raise ValueError(
                            "GCP_PROJECT_ID is required when using Application Default Credentials. "
                            "Set it as an environment variable (GCP_PROJECT_ID)."
                        )

                    logger.info(
                        "✅ Using Gemini with Application Default Credentials (GCP runtime)"
                    )
                    logger.debug(f"   Project ID: {self.project_id}")
                    logger.debug(f"   Location: {self.location}")
            except Exception as e:
                logger.info(
                    f"ℹ️ Application Default Credentials not available ({type(e).__name__}). "
                    "Falling back to API key method if available."
                )

        # Fallback to API key method
        if not self.use_service_account:
            if not effective_api_key:
                raise ValueError(
                    "Neither GOOGLE_APPLICATION_CREDENTIALS nor GEMINI_API_KEY is configured. "
                    "Please set one of them in your .env file."
                )
            self.api_key = effective_api_key
            logger.info("✅ Using Gemini with API Key")
            logger.debug(f"   Model: {self.model}")

        logger.info("✅ GeminiService initialized")
        logger.info(f"   🎯 Purpose: {self.purpose}")
        logger.info(f"   🤖 Model: {self.model}")
        logger.info(f"   🌍 Location: {self.location} (will use 'global' for Gemini models)")
        logger.info(
            f"   🔐 Auth: {'Service Account (Vertex AI)' if self.use_service_account else 'API Key (Direct)'}"
        )
        logger.debug(f"   ⏱️  Timeout: {self.timeout}s")
        logger.debug(f"   🚦 Rate limiter: {'enabled' if self.rate_limiter else 'disabled'}")

    async def generate_response_async(
        self,
        user_query: str,
        system_prompt: str,
        context: str = "",
        conversation_history: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        """
        Async version with rate limiting and retry logic.

        Args:
            user_query: User's query/prompt
            system_prompt: System prompt/instructions
            context: Additional context (e.g., retrieved chunks)
            conversation_history: List of previous messages (not fully supported by Gemini API)
            temperature: Sampling temperature (0.0 to 1.0)
            max_tokens: Maximum tokens to generate (None for model default)

        Returns:
            Generated response string
        """
        # Acquire rate limit permission
        await self.rate_limiter.acquire(identifier=f"gemini:{self.purpose}")

        # Additional delay for better rate limit compliance
        await asyncio.sleep(0.5)

        # Use appropriate method based on authentication type
        if self.use_service_account:
            return await self._generate_with_vertex_ai(
                user_query=user_query,
                system_prompt=system_prompt,
                context=context,
                conversation_history=conversation_history,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        else:
            return await self._generate_with_retry(
                user_query=user_query,
                system_prompt=system_prompt,
                context=context,
                conversation_history=conversation_history,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    @retry(
        stop=stop_after_attempt(5),  # More attempts for rate limits
        wait=wait_exponential(multiplier=2, min=5, max=120),  # 5s, 10s, 20s, 40s, 80s
        retry=retry_if_exception_type(
            (
                google_exceptions.TooManyRequests,
                google_exceptions.ResourceExhausted,
                ValueError,
            )
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        after=after_log(logger, logging.INFO),
        reraise=True,
    )
    async def _generate_with_vertex_ai(
        self,
        user_query: str,
        system_prompt: str,
        context: str = "",
        conversation_history: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        """
        Generate response using Vertex AI (service account method).
        This method uses your GCP free credits.

        Note: Vertex AI uses different model names than direct Gemini API:
        - gemini-2.0-flash-001 (fast, cheap, supports function calling)
        - gemini-2.5-pro (more capable, supports function calling)
        - gemini-2.5-flash (latest fast model)
        """
        try:
            import vertexai
            from vertexai.generative_models import GenerativeModel

            # Map old model names to Vertex AI compatible names
            # Note: Gemini 1.5 models were retired Sept 2025, use 2.0+ models
            model_name = self.model
            model_mapping = {
                "gemini-pro": "gemini-2.0-flash-001",
                "gemini-1.5-flash": "gemini-2.0-flash-001",
                "gemini-2.0-flash-exp": "gemini-2.0-flash-001",  # Retired
                "gemini-1.5-pro": "gemini-2.5-pro",
            }

            if model_name in model_mapping:
                new_model = model_mapping[model_name]
                logger.warning(
                    f"⚠️ Model '{model_name}' is deprecated/retired. Using '{new_model}' instead."
                )
                model_name = new_model

            # Gemini publisher models REQUIRE 'global' location
            # Always use 'global' for Gemini models, ignore configured location
            location = "global"
            if self.location != "global":
                logger.info(
                    f"📍 Overriding location '{self.location}' to 'global' (required for Gemini models)"
                )

            # Initialize Vertex AI with global location (required for Gemini)
            logger.info("🔍 Initializing Vertex AI for Gemini:")
            logger.info(f"   📦 Project: {self.project_id}")
            logger.info(f"   🌍 Location: {location} (required for Gemini publisher models)")
            logger.info(f"   🤖 Model: {model_name}")
            vertexai.init(project=self.project_id, location=location)

            # Load model
            model = GenerativeModel(model_name)
            logger.debug(f"   ✅ Gemini model '{model_name}' loaded successfully")

            # Build prompt
            full_prompt_parts = []
            if system_prompt:
                full_prompt_parts.append(f"System Instructions: {system_prompt}")
            if context:
                full_prompt_parts.append(f"Context: {context}")
            if conversation_history:
                history_text = "\n".join(
                    f"{msg['role']}: {msg['content']}" for msg in conversation_history
                )
                full_prompt_parts.append(f"Previous Conversation:\n{history_text}")
            full_prompt_parts.append(f"User Query: {user_query}")
            full_prompt = "\n\n".join(full_prompt_parts)

            # Prepare generation config
            generation_config = {
                "temperature": temperature,
            }
            if max_tokens:
                generation_config["max_output_tokens"] = max_tokens

            # Generate content (run in thread pool to avoid blocking)
            start_time = time.time()
            logger.debug("   📤 Sending request to Gemini API (Vertex AI)...")
            logger.debug(f"   📏 Prompt length: {len(full_prompt)} chars")

            response = await asyncio.to_thread(
                model.generate_content,
                full_prompt,
                generation_config=generation_config,
            )

            duration = time.time() - start_time
            logger.info(f"✅ Gemini (Vertex AI) response generated in {duration:.2f}s")
            logger.debug(
                f"   📝 Response length: {len(response.text) if hasattr(response, 'text') else 'N/A'} chars"
            )

            return response.text

        except ImportError:
            logger.error(
                "❌ google-cloud-aiplatform not installed. "
                "Install with: pip install google-cloud-aiplatform"
            )
            raise
        except google_exceptions.TooManyRequests as e:
            logger.warning(
                f"⏳ Gemini API rate limit exceeded (429). Retrying with exponential backoff... "
                f"Error: {e}"
            )
            raise  # Let retry decorator handle it
        except google_exceptions.ResourceExhausted as e:
            logger.warning(
                f"⏳ Gemini API resource exhausted (429). Retrying with exponential backoff... "
                f"Error: {e}"
            )
            raise  # Let retry decorator handle it
        except Exception as e:
            error_msg = str(e)
            if "404" in error_msg or "NOT_FOUND" in error_msg:
                logger.error(
                    f"❌ Model '{model_name}' not found in Vertex AI.\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔧 QUICK FIX - Enable Vertex AI API:\n"
                    f"   1. Go to: https://console.cloud.google.com/apis/library/aiplatform.googleapis.com\n"
                    f"   2. Select project: {self.project_id}\n"
                    f"   3. Click 'Enable' button\n"
                    f"   4. Wait 1-2 minutes, then restart your server\n"
                    f"\n"
                    f"🔐 GRANT PERMISSIONS:\n"
                    f"   1. Go to: https://console.cloud.google.com/iam-admin/iam?project={self.project_id}\n"
                    f"   2. Find: gemini-api-service@{self.project_id}.iam.gserviceaccount.com\n"
                    f"   3. Add role: 'Vertex AI User' (roles/aiplatform.user)\n"
                    f"\n"
                    f"📖 Full setup guide: See SETUP_VERTEX_AI.md\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                )
            else:
                logger.error(f"❌ Error generating response with Vertex AI: {e}", exc_info=True)
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        retry=retry_if_exception_type((ValueError, httpx.HTTPStatusError, httpx.TimeoutException)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        after=after_log(logger, logging.INFO),
        reraise=True,
    )
    async def _generate_with_retry(
        self,
        user_query: str,
        system_prompt: str,
        context: str = "",
        conversation_history: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        """
        Generate response with retry logic.

        Args:
            user_query: User's query/prompt
            system_prompt: System prompt/instructions
            context: Additional context
            conversation_history: Previous messages (limited support)
            temperature: Sampling temperature
            max_tokens: Maximum tokens

        Returns:
            Generated response string
        """
        start_time = time.time()

        # Build prompt (Gemini uses a single prompt format)
        # Combine system prompt, context, and user query
        full_prompt_parts = []

        if system_prompt:
            full_prompt_parts.append(f"System Instructions: {system_prompt}")

        if context:
            full_prompt_parts.append(f"Context: {context}")

        if conversation_history:
            # Convert conversation history to text format
            history_text = "\n".join(
                f"{msg['role']}: {msg['content']}" for msg in conversation_history
            )
            full_prompt_parts.append(f"Previous Conversation:\n{history_text}")

        full_prompt_parts.append(f"User Query: {user_query}")

        full_prompt = "\n\n".join(full_prompt_parts)

        # Prepare request payload
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": full_prompt,
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": temperature,
            },
        }

        if max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = max_tokens

        # Construct API URL with API key
        api_url = f"{GEMINI_API_URL}?key={self.api_key}"

        # Make API request
        headers = {
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(api_url, json=payload, headers=headers)
            response.raise_for_status()

            result = response.json()
            generated_text = result["candidates"][0]["content"]["parts"][0]["text"]

            duration = time.time() - start_time
            logger.debug(f"✅ Gemini response generated in {duration:.2f}s")

            return generated_text

    async def generate_with_tools_async(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4000,
    ) -> dict[str, Any]:
        """
        Generate response with function calling support (OpenAI-compatible format).

        Args:
            messages: List of message dicts with 'role' and 'content' keys
            tools: List of tool/function definitions (OpenAI format)
            temperature: LLM temperature (0.0-2.0, default 0.0 for deterministic)
            max_tokens: Maximum tokens in response (default 4000 for agent workflows)

        Returns:
            {
                "content": str | None,  # Text content if no tool calls
                "tool_calls": list[dict] | None,  # Tool calls if agent wants to call tools
                "finish_reason": str,  # "stop", "tool_calls", etc.
                "usage": dict | None  # Token usage info
            }
        """
        # Acquire rate limit permission
        await self.rate_limiter.acquire(identifier=f"gemini:{self.purpose}")
        await asyncio.sleep(0.5)

        # Use Vertex AI for function calling (better support)
        if self.use_service_account:
            return await self._generate_with_tools_vertex_ai(
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        else:
            # Direct API doesn't support function calling well, use Vertex AI method
            logger.warning(
                "Function calling requires Vertex AI (service account). Falling back to basic generation."
            )
            # Convert messages to simple prompt
            user_query = ""
            for msg in messages:
                if msg.get("role") == "user":
                    user_query = msg.get("content", "")
                    break

            response_text = await self.generate_response_async(
                user_query=user_query,
                system_prompt="You are a helpful assistant.",
                context="",
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return {
                "content": response_text,
                "tool_calls": None,
                "finish_reason": "stop",
                "usage": None,
            }

    @retry(
        stop=stop_after_attempt(5),  # More attempts for rate limits
        wait=wait_exponential(multiplier=2, min=5, max=120),  # 5s, 10s, 20s, 40s, 80s
        retry=retry_if_exception_type(
            (
                google_exceptions.TooManyRequests,
                google_exceptions.ResourceExhausted,
            )
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        after=after_log(logger, logging.INFO),
        reraise=True,
    )
    async def _generate_with_tools_vertex_ai(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4000,
    ) -> dict[str, Any]:
        """
        Generate response with function calling using Vertex AI.

        Args:
            messages: List of message dicts
            tools: List of tool definitions (OpenAI format)
            temperature: Sampling temperature
            max_tokens: Maximum tokens

        Returns:
            Response dict with content, tool_calls, finish_reason, usage
        """
        try:
            import vertexai
            from vertexai.generative_models import FunctionDeclaration, GenerativeModel, Tool

            start_time = time.time()

            # Map model name
            model_name = self.model
            model_mapping = {
                "gemini-pro": "gemini-2.0-flash-001",
                "gemini-1.5-flash": "gemini-2.0-flash-001",
                "gemini-2.0-flash-exp": "gemini-2.0-flash-001",  # Retired
                "gemini-1.5-pro": "gemini-2.5-pro",
            }
            if model_name in model_mapping:
                new_model = model_mapping[model_name]
                logger.warning(
                    f"⚠️ Model '{model_name}' is deprecated/retired. Using '{new_model}' instead."
                )
                model_name = new_model

            # Gemini publisher models REQUIRE 'global' location
            location = "global"
            if self.location != "global":
                logger.info(
                    f"📍 Overriding location '{self.location}' to 'global' (required for Gemini models)"
                )

            logger.info("🤖 Calling Gemini API with function calling (Vertex AI)")
            logger.info(f"   🤖 Model: {model_name}")
            logger.info(f"   💬 Messages: {len(messages)}")
            logger.info(f"   🛠️  Tools: {len(tools) if tools else 0}")
            logger.debug(f"   🌍 Location: {location}")

            # Initialize Vertex AI with global location
            logger.debug(
                f"🔍 Initializing Vertex AI: project={self.project_id}, location={location}, model={model_name}"
            )
            vertexai.init(project=self.project_id, location=location)
            model = GenerativeModel(model_name)

            # Convert OpenAI-format tools to Gemini format
            gemini_tools = []
            if tools:
                function_declarations = []
                for tool in tools:
                    if tool.get("type") == "function":
                        func_def = tool.get("function", {})
                        func_name = func_def.get("name", "")
                        func_desc = func_def.get("description", "")
                        func_params = func_def.get("parameters", {})

                        # Convert OpenAI schema to Gemini FunctionDeclaration
                        function_decl = FunctionDeclaration(
                            name=func_name,
                            description=func_desc,
                            parameters=func_params,  # Gemini accepts OpenAPI schema format
                        )
                        function_declarations.append(function_decl)

                if function_declarations:
                    gemini_tools = [Tool(function_declarations=function_declarations)]

            # Convert messages to Gemini format
            # Gemini uses a different message format - we need to convert
            # For now, build a single prompt from messages
            prompt_parts = []
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                tool_calls = msg.get("tool_calls")
                tool_call_id = msg.get("tool_call_id")

                if role == "system":
                    prompt_parts.append(f"System: {content}")
                elif role == "user":
                    prompt_parts.append(f"User: {content}")
                elif role == "assistant":
                    if content:
                        prompt_parts.append(f"Assistant: {content}")
                    if tool_calls:
                        for tc in tool_calls:
                            func_name = tc.get("function", {}).get("name", "")
                            func_args = tc.get("function", {}).get("arguments", "{}")
                            prompt_parts.append(
                                f"Assistant called function {func_name} with args: {func_args}"
                            )
                elif role == "tool":
                    prompt_parts.append(f"Tool result ({tool_call_id}): {content}")

            full_prompt = "\n\n".join(prompt_parts)

            # Prepare generation config
            generation_config = {
                "temperature": temperature,
            }
            if max_tokens:
                generation_config["max_output_tokens"] = max_tokens

            # Generate with tools
            response = await asyncio.to_thread(
                model.generate_content,
                full_prompt,
                tools=gemini_tools if gemini_tools else None,
                generation_config=generation_config,
            )

            duration = time.time() - start_time

            # Parse response
            content = None
            tool_calls = None
            finish_reason = "stop"

            # Check if response has function calls
            if hasattr(response, "candidates") and response.candidates:
                candidate = response.candidates[0]
                if hasattr(candidate, "content") and candidate.content:
                    parts = candidate.content.parts
                    for part in parts:
                        # Check for function call
                        if hasattr(part, "function_call") and part.function_call is not None:
                            func_call = part.function_call
                            if func_call and hasattr(func_call, "name"):
                                tool_calls = [
                                    {
                                        "id": f"call_{func_call.name}_{int(time.time())}",
                                        "type": "function",
                                        "function": {
                                            "name": func_call.name,
                                            "arguments": (
                                                json.dumps(dict(func_call.args))
                                                if hasattr(func_call, "args")
                                                else "{}"
                                            ),
                                        },
                                    }
                                ]
                                finish_reason = "tool_calls"
                        elif hasattr(part, "text"):
                            content = part.text

            # If no function call found but we have text, use that
            if not content and not tool_calls and hasattr(response, "text"):
                content = response.text

            logger.info(
                f"✅ Gemini function calling response generated in {duration:.3f}s "
                f"(finish_reason: {finish_reason}, tool_calls: {len(tool_calls) if tool_calls else 0})"
            )
            if tool_calls:
                logger.info(f"   🔧 Gemini requested {len(tool_calls)} tool call(s)")
            else:
                logger.info("   💬 Gemini provided text response (no tool calls)")

            return {
                "content": content,
                "tool_calls": tool_calls,
                "finish_reason": finish_reason,
                "usage": None,  # Vertex AI doesn't always provide usage in this format
            }

        except ImportError:
            logger.error(
                "❌ google-cloud-aiplatform not installed. "
                "Install with: pip install google-cloud-aiplatform"
            )
            raise
        except google_exceptions.TooManyRequests as e:
            logger.warning(
                f"⏳ Gemini API rate limit exceeded (429). Retrying with exponential backoff... "
                f"Error: {e}"
            )
            raise  # Let retry decorator handle it
        except google_exceptions.ResourceExhausted as e:
            logger.warning(
                f"⏳ Gemini API resource exhausted (429). Retrying with exponential backoff... "
                f"Error: {e}"
            )
            raise  # Let retry decorator handle it
        except Exception as e:
            error_msg = str(e)
            if "404" in error_msg or "NOT_FOUND" in error_msg:
                logger.error(
                    f"❌ Model '{self.model}' not found in Vertex AI.\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔧 QUICK FIX - Enable Vertex AI API:\n"
                    f"   1. Go to: https://console.cloud.google.com/apis/library/aiplatform.googleapis.com\n"
                    f"   2. Select project: {self.project_id}\n"
                    f"   3. Click 'Enable' button\n"
                    f"   4. Wait 1-2 minutes, then restart your server\n"
                    f"\n"
                    f"🔐 GRANT PERMISSIONS:\n"
                    f"   1. Go to: https://console.cloud.google.com/iam-admin/iam?project={self.project_id}\n"
                    f"   2. Find: gemini-api-service@{self.project_id}.iam.gserviceaccount.com\n"
                    f"   3. Add role: 'Vertex AI User' (roles/aiplatform.user)\n"
                    f"\n"
                    f"📖 Full setup guide: See SETUP_VERTEX_AI.md\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                )
            else:
                logger.error(f"❌ Error generating response with tools: {e}", exc_info=True)
            raise
