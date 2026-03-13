"""
Verification Agent
AI agent powered by Gemini (Vertex AI) that autonomously uses GitHub API tools to verify tasks.
Uses Gemini's function calling capabilities for intelligent tool use.
"""

import json
import logging
from typing import Any

# Pydantic validation for final structured output
from pydantic import BaseModel, Field, field_validator

# Using Gemini for verification
from app.services.github_tools import execute_github_tool, get_github_tools
from app.utils.json_parser import parse_llm_json_response_async

logger = logging.getLogger(__name__)


class RequirementCheckItem(BaseModel):
    met: bool = False
    feedback: str = ""


class VerificationResultModel(BaseModel):
    """
    Enforced output schema for verification results.
    Defaults ensure we always return a valid, structured object even if the LLM omits fields.
    """

    passed: bool = False
    overall_feedback: str = ""
    requirements_check: dict[str, RequirementCheckItem] = Field(default_factory=dict)
    hints: list[str] = Field(default_factory=list)
    issues_found: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    code_quality: str = "needs_improvement"

    # Backward-compatible optional fields (kept optional even if unused)
    test_status: str | None = None
    pattern_match_status: str | None = None

    model_config = {"extra": "ignore"}

    @field_validator("requirements_check", mode="before")
    @classmethod
    def _coerce_requirements_check(cls, v):
        # Accept various shapes and normalize into dict[str, {met, feedback}]
        if v is None:
            return {}
        if not isinstance(v, dict):
            return {"main_requirement": {"met": False, "feedback": str(v)}}
        coerced: dict[str, dict[str, Any]] = {}
        for k, item in v.items():
            if isinstance(item, dict):
                coerced[str(k)] = {
                    "met": bool(item.get("met", False)),
                    "feedback": str(item.get("feedback", "")),
                }
            else:
                coerced[str(k)] = {"met": bool(item), "feedback": ""}
        return coerced


# System prompt for verification agent
VERIFICATION_AGENT_SYSTEM_PROMPT = """You are a STRICT code reviewer verifying if a student's code fulfills task requirements.

Your task is to verify if code changes between two commits fulfill the given task requirements.

**Available Tools:**
- compare_commits: Compare two commits - returns metadata for changed files (build artifacts filtered). Analyze which files are relevant to the task, then use get_file_contents for specific files you need to examine.
- get_file_contents: Get the contents of specific files. Use this after analyzing file metadata to fetch only files relevant to verification.
- get_commit_details: Get commit details - returns metadata for files (build artifacts filtered). Analyze which files are relevant, then fetch specific file contents as needed.
- list_changed_files: List changed files with metadata (build artifacts filtered). Analyze relevance, then fetch specific file contents.
- list_repository_files: List all files in the repository at a specific commit. Use this if compare_commits returns 0 files - the task might have been completed in a different commit or the file might already exist.

**Verification Process:**
1. Use compare_commits or get_commit_details to get file metadata (filename, status, stats)
2. Analyze which files are relevant to the task (ignore node_modules, build artifacts, etc.)
3. Use get_file_contents to fetch contents of ONLY relevant files
4. Analyze the changes against the task requirements
5. Determine if the task is fulfilled
6. Provide detailed feedback

**Verification Rules (STRICT):**
- Only PASS if ALL requirements are clearly met
- Check if code implements what was asked
- Verify all specific requirements are met
- Look for critical bugs or issues
- Be thorough but fair

**CRITICAL DISTINCTION:**
- "issues_found": ONLY actual problems that prevent code from working (bugs, syntax errors, missing functionality)
- "suggestions": Optional improvements and best practices (NOT issues)

**When you have enough information, return ONLY valid JSON (no markdown, no extra text):**
{
  "passed": true/false,
  "overall_feedback": "2-3 sentence summary explaining decision",
  "requirements_check": {
    "code_implements_task": {"met": true/false, "feedback": "..."},
    "meets_all_requirements": {"met": true/false, "feedback": "..."},
    "no_critical_issues": {"met": true/false, "feedback": "..."}
  },
  "hints": ["Helpful hint if failed"],
  "issues_found": ["ONLY actual problems. If task PASSED, use empty array []."],
  "suggestions": ["Optional code quality improvements. These are NOT issues."],
  "code_quality": "good/acceptable/needs_improvement"
}

**IMPORTANT**: Use tools to gather information first, then provide your verification decision."""


class VerificationAgent:
    """
    AI agent that uses Gemini (Vertex AI) with GitHub API tools to verify tasks.
    The agent autonomously decides when and how to use GitHub API tools via Gemini's function calling.
    Powered by Gemini's advanced reasoning capabilities for code verification.
    """

    def __init__(self):
        from app.services.gemini_service import get_gemini_service

        # Use a separate GeminiService instance/config for verification so it can
        # be pointed at a different model/project/credentials via VERIFICATION_*
        # env vars (reduces contention with roadmap/content generation).
        self.gemini_service = get_gemini_service(purpose="verification")
        self.github_tools = get_github_tools()
        self.max_iterations = 5  # Maximum tool call iterations

    async def verify_task(
        self,
        task_description: str,
        base_commit: str,
        head_commit: str,
        repo_url: str,
        github_token: str | None = None,
        additional_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Verify task using agent with GitHub API tools.

        Agent uses app's GitHub token (GIT_ACCESS_TOKEN from .env) for authenticated calls.
        Agent NEVER sees user's PAT (stored in DB).

        Args:
            task_description: Task description and requirements
            base_commit: Base commit SHA (starting point)
            head_commit: Head commit SHA (current state)
            repo_url: GitHub repository URL (notebook repo - user_repo_url)
            github_token: App's GitHub token from .env (GIT_ACCESS_TOKEN), not user's PAT
            additional_context: Optional additional context (e.g., task title, type)

        Returns:
            Verification result dict with passed, feedback, requirements_check, etc.
        """
        logger.info(
            f"🤖 Starting Gemini verification agent for task: base={base_commit[:8]}, head={head_commit[:8]}"
        )
        logger.info("   ✨ Powered by Gemini (Vertex AI) with function calling")

        # Build initial user message
        user_message = f"""**Task Description:**
{task_description}

**Repository:** {repo_url}
**Base Commit:** {base_commit}
**Head Commit:** {head_commit}
"""

        if additional_context:
            if additional_context.get("task_title"):
                user_message += f"\n**Task Title:** {additional_context['task_title']}\n"
            if additional_context.get("task_type"):
                user_message += f"\n**Task Type:** {additional_context['task_type']}\n"

            # Current day + concept context (small, targeted)
            day_number = additional_context.get("day_number")
            day_theme = additional_context.get("day_theme")
            day_desc = additional_context.get("day_description")
            if day_number is not None or day_theme or day_desc:
                user_message += "\n**Current Day Context:**\n"
                if day_number is not None or day_theme:
                    user_message += f"- Day {day_number}: {day_theme or ''}\n"
                if day_desc:
                    user_message += f"- Day Description: {day_desc}\n"

            concept_title = additional_context.get("concept_title")
            concept_desc = additional_context.get("concept_description")
            if concept_title or concept_desc:
                user_message += "\n**Current Concept Context:**\n"
                if concept_title:
                    user_message += f"- Concept: {concept_title}\n"
                if concept_desc:
                    user_message += f"- Concept Description: {concept_desc}\n"

            # Add previous concept summaries (max 5, skip for 1st concept)
            previous_concept_summaries = additional_context.get("previous_concept_summaries", [])
            if previous_concept_summaries:
                user_message += "\n**Previous Concept Summaries (for context):**\n"
                for i, summary_data in enumerate(previous_concept_summaries, 1):
                    concept_title = summary_data.get("concept_title", "Unknown")
                    summary_text = summary_data.get("summary", "")
                    user_message += f"{i}. **{concept_title}**: {summary_text}\n"

            # Add previous task descriptions (max 5, exclude GitHub tasks, skip for 1st task)
            previous_task_descriptions = additional_context.get("previous_task_descriptions", [])
            if previous_task_descriptions:
                user_message += "\n**Previous Task Descriptions (for context):**\n"
                for i, task_data in enumerate(previous_task_descriptions, 1):
                    task_title = task_data.get("task_title", "Unknown")
                    task_desc = task_data.get("description", "")
                    user_message += f"{i}. **{task_title}**: {task_desc}\n"

        user_message += "\nPlease verify if the code changes fulfill the task requirements. Use the GitHub API tools to gather information as needed."

        # Log initial context
        logger.info("📋 Initial context provided to agent:")
        logger.info(
            f"   📝 Task: {additional_context.get('task_title', 'N/A') if additional_context else 'N/A'}"
        )
        logger.info(f"   🔗 Repository: {repo_url}")
        logger.info(f"   📍 Base commit: {base_commit[:8]}, Head commit: {head_commit[:8]}")
        if additional_context:
            prev_concepts = additional_context.get("previous_concept_summaries", [])
            prev_tasks = additional_context.get("previous_task_descriptions", [])
            if prev_concepts:
                logger.info(f"   📚 Previous concepts: {len(prev_concepts)}")
            if prev_tasks:
                logger.info(f"   📋 Previous tasks: {len(prev_tasks)}")
        logger.info(f"   🛠️  Available tools: {len(self.github_tools)}")

        # Initialize conversation
        messages = [
            {"role": "system", "content": VERIFICATION_AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        # Agent loop: handle tool calls with Gemini
        iteration = 0
        while iteration < self.max_iterations:
            iteration += 1
            logger.info(f"🔄 Gemini Agent iteration {iteration}/{self.max_iterations}")
            logger.debug("   🤖 Using Gemini with function calling (Vertex AI)")

            # Call Gemini with tools
            try:
                logger.debug(
                    f"   📤 Sending request to Gemini API with {len(self.github_tools)} tools..."
                )
                response = await self.gemini_service.generate_with_tools_async(
                    messages=messages,
                    tools=self.github_tools,
                    temperature=0.0,  # Strict mode
                    max_tokens=2000,
                )
                logger.debug("   ✅ Gemini API response received")
            except Exception as e:
                logger.error(f"❌ Error calling Gemini API: {e}", exc_info=True)
                return self._create_error_response(str(e))

            # Check if agent wants to call tools
            tool_calls = response.get("tool_calls")
            finish_reason = response.get("finish_reason", "stop")
            agent_content = response.get("content")

            # Log agent's reasoning/content
            if agent_content:
                logger.info(
                    f"💭 Agent reasoning: {agent_content[:200]}{'...' if len(agent_content) > 200 else ''}"
                )

            # Add assistant message to conversation
            assistant_message: dict[str, Any] = {"role": "assistant"}
            if agent_content:
                assistant_message["content"] = agent_content
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            messages.append(assistant_message)

            # If no tool calls, agent is done - parse final response
            if not tool_calls or finish_reason == "stop":
                logger.info("✅ Gemini Agent finished (no more tool calls)")
                if agent_content:
                    logger.info(
                        f"📝 Final Gemini agent response: {agent_content[:300]}{'...' if len(agent_content) > 300 else ''}"
                    )
                return await self._parse_final_response(messages, response)

            # Execute tool calls
            logger.info(f"🔧 Executing {len(tool_calls)} tool call(s)")
            tool_results = []
            for idx, tool_call in enumerate(tool_calls, 1):
                tool_id = tool_call.get("id")
                function_name = tool_call.get("function", {}).get("name")
                function_args_str = tool_call.get("function", {}).get("arguments", "{}")

                try:
                    # Parse function arguments
                    function_args = json.loads(function_args_str)
                except json.JSONDecodeError as e:
                    logger.error(f"❌ Failed to parse tool arguments for {function_name}: {e}")
                    logger.error(f"   Raw arguments: {function_args_str[:200]}")
                    tool_results.append(
                        {
                            "tool_call_id": tool_id,
                            "role": "tool",
                            "name": function_name,
                            "content": json.dumps(
                                {"success": False, "error": f"Invalid arguments: {e}"}
                            ),
                        }
                    )
                    continue

                # Log tool call details
                logger.info(f"   📞 Tool call {idx}/{len(tool_calls)}: {function_name}")
                logger.info(f"      🎯 Purpose: {self._get_tool_purpose(function_name)}")
                logger.info(f"      📋 Arguments: {json.dumps(function_args, indent=6)}")

                # Execute tool (using app's GitHub token from .env)
                try:
                    tool_result = await execute_github_tool(
                        tool_name=function_name,
                        arguments=function_args,
                        github_token=github_token,
                    )

                    # Prevent huge prompts: file contents can be very large and
                    # get echoed back into the next LLM call as tool messages.
                    # Truncate safely while preserving useful context.
                    if (
                        function_name == "get_file_contents"
                        and isinstance(tool_result, dict)
                        and tool_result.get("success") is True
                        and isinstance(tool_result.get("content"), str)
                    ):
                        content = tool_result["content"]
                        max_chars = 20000
                        if len(content) > max_chars:
                            head = content[:16000]
                            tail = content[-3000:]
                            tool_result["content"] = (
                                head
                                + "\n\n...[TRUNCATED tool output: file content was "
                                + str(len(content))
                                + " chars]...\n\n"
                                + tail
                            )

                    # Log tool result summary
                    success = tool_result.get("success", False)
                    if success:
                        logger.info("      ✅ Tool executed successfully")
                        # Log key information from result
                        self._log_tool_result_summary(function_name, tool_result)
                    else:
                        error_msg = tool_result.get("error", "Unknown error")
                        logger.warning(f"      ⚠️  Tool execution failed: {error_msg}")

                    tool_result_str = json.dumps(tool_result, indent=2)
                except Exception as e:
                    logger.error(
                        f"      ❌ Error executing tool {function_name}: {e}", exc_info=True
                    )
                    tool_result_str = json.dumps({"success": False, "error": str(e)})

                # Add tool result to conversation
                tool_results.append(
                    {
                        "tool_call_id": tool_id,
                        "role": "tool",
                        "name": function_name,
                        "content": tool_result_str,
                    }
                )

            # Add tool results to conversation
            messages.extend(tool_results)

        # Max iterations reached
        logger.warning(f"⚠️ Max iterations ({self.max_iterations}) reached")
        logger.warning(f"   Last response: {response.get('content', 'No content')[:200]}")
        return await self._parse_final_response(messages, response)

    def _get_tool_purpose(self, tool_name: str) -> str:
        """Get human-readable purpose description for a tool."""
        purposes = {
            "compare_commits": "Compare two commits to see what changed",
            "get_file_contents": "Get the contents of a specific file",
            "get_commit_details": "Get detailed information about a commit",
            "list_changed_files": "List all files changed between commits",
        }
        return purposes.get(tool_name, "Unknown tool")

    def _log_tool_result_summary(self, tool_name: str, result: dict[str, Any]) -> None:
        """Log a summary of tool execution results."""
        if tool_name == "compare_commits":
            files_changed = result.get("files_changed", [])
            stats = result.get("stats", {})

            logger.info(
                f"         📊 Found {len(files_changed)} file(s) with metadata (after filtering build artifacts)"
            )
            logger.info("         💡 LLM will analyze and decide which files to examine in detail")
            if stats:
                additions = stats.get("additions", 0)
                deletions = stats.get("deletions", 0)
                logger.info(f"         ➕ Additions: {additions}, ➖ Deletions: {deletions}")
            # Show sample of files
            if files_changed:
                sample = files_changed[:10]
                sample_names = [f.get("filename", f) if isinstance(f, dict) else f for f in sample]
                logger.info(
                    f"         📋 Sample files: {', '.join(sample_names)}{'...' if len(files_changed) > 10 else ''}"
                )
        elif tool_name == "get_file_contents":
            content = result.get("content", "")
            size = result.get("size", 0)
            logger.info(f"         📄 File size: {size} bytes, Content preview: {content[:100]}...")
        elif tool_name == "get_commit_details":
            message = result.get("message", "")
            files = result.get("files", [])

            logger.info(f"         📝 Commit message: {message[:100]}...")
            logger.info(
                f"         📁 Files in commit: {len(files)} file(s) with metadata (after filtering build artifacts)"
            )
            logger.info("         💡 LLM will analyze and decide which files to examine in detail")
            # Show sample of files
            if files:
                sample = files[:10]
                sample_names = [f.get("filename", "") for f in sample if isinstance(f, dict)]
                logger.info(
                    f"         📋 Sample files: {', '.join(sample_names)}{'...' if len(files) > 10 else ''}"
                )
        elif tool_name == "list_changed_files":
            files = result.get("files", [])

            logger.info(
                f"         📁 Changed files: {len(files)} file(s) with metadata (after filtering build artifacts)"
            )
            logger.info("         💡 LLM will analyze and decide which files to examine in detail")
            if files:
                sample = files[:10]
                sample_names = [f.get("filename", "") for f in sample if isinstance(f, dict)]
                logger.info(
                    f"         📋 Sample files: {', '.join(sample_names)}{'...' if len(files) > 10 else ''}"
                )

    def _extract_json_from_text(self, text: str) -> dict[str, Any] | None:
        """Extract a JSON object from text using brace matching."""
        start = text.find("{")
        if start < 0:
            return None
        depth = 0
        for i, c in enumerate(text[start:], start):
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        return None
        return None

    async def _parse_final_response(
        self, messages: list[dict[str, Any]], last_response: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Parse final verification result from agent response.

        Args:
            messages: Full conversation history
            last_response: Last Gemini API response

        Returns:
            Parsed verification result dict
        """
        # Get content from last assistant message or last response
        content = last_response.get("content")
        if not content:
            # Try to get from last message
            for msg in reversed(messages):
                if msg.get("role") == "assistant" and msg.get("content"):
                    content = msg["content"]
                    break

        logger.info(
            f"📄 Parsing final Gemini agent response ({len(content) if content else 0} chars)"
        )

        if not content:
            logger.error("❌ No content in Gemini agent response")
            return self._create_error_response("Gemini agent did not provide verification result")

        logger.debug(f"   🔍 Parsing Gemini agent response: {content[:200]}...")

        # Try to parse JSON from content
        try:
            verification_result = await parse_llm_json_response_async(
                content, expected_type="object"
            )
        except Exception as e:
            logger.error(f"Failed to parse agent response as JSON: {e}")
            logger.debug(f"Raw content: {content[:500]}...")
            # Fallback: extract JSON object by brace matching (handles nested JSON)
            verification_result = self._extract_json_from_text(content)
            if verification_result is None:
                return self._create_error_response(
                    "Agent response is not valid JSON. The model may have returned reasoning or markdown instead of raw JSON."
                )

        # Enforce output schema (still allows your JSON parser/sanitizer fallback above)
        try:
            verification_result = VerificationResultModel.model_validate(
                verification_result
            ).model_dump()
        except Exception as e:
            logger.error(f"Failed to validate verification result schema: {e}", exc_info=True)

        # Log parsed result
        if isinstance(verification_result, dict):
            passed = bool(verification_result.get("passed", False))
        else:
            passed = bool(getattr(verification_result, "passed", False))
        logger.info(f"🎯 Gemini Agent decision: {'✅ PASSED' if passed else '❌ FAILED'}")
        if verification_result.get("overall_feedback"):
            logger.info(
                f"💬 Gemini Agent feedback: {verification_result.get('overall_feedback', '')[:200]}..."
            )

        # Validate and normalize result
        normalized = self._normalize_verification_result(verification_result)

        # Log normalized result summary
        logger.info("📊 Gemini Verification Summary:")
        logger.info(f"   ✅ Status: {'PASSED' if normalized.get('passed') else 'FAILED'}")
        logger.info(f"   📈 Code Quality: {normalized.get('code_quality', 'N/A')}")
        logger.info(f"   🧪 Test Status: {normalized.get('test_status', 'N/A')}")
        logger.info(f"   🔍 Pattern Match: {normalized.get('pattern_match_status', 'N/A')}")
        logger.info("   ✨ Verification powered by Gemini (Vertex AI)")

        return normalized

    def _normalize_verification_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize verification result to expected format.

        Args:
            result: Raw verification result from agent

        Returns:
            Normalized verification result
        """
        # Ensure required fields
        normalized = {
            "passed": result.get("passed", False),
            "overall_feedback": result.get("overall_feedback", ""),
            "requirements_check": result.get("requirements_check", {}),
            "hints": result.get("hints", []),
            "issues_found": result.get("issues_found", []),
            "suggestions": result.get("suggestions", []),
            "code_quality": result.get("code_quality", "needs_improvement"),
            "test_status": result.get("test_status", "not_run"),
            "pattern_match_status": result.get("pattern_match_status", "none"),
        }

        # Ensure requirements_check is properly formatted
        if not normalized["requirements_check"]:
            normalized["requirements_check"] = {
                "main_requirement": {
                    "met": normalized["passed"],
                    "feedback": normalized["overall_feedback"],
                }
            }

        logger.info(
            f"✅ Gemini verification complete: {'PASSED' if normalized['passed'] else 'FAILED'}"
        )

        return normalized

    def _create_error_response(self, error_message: str) -> dict[str, Any]:
        """
        Create error response when verification fails.

        Args:
            error_message: Error message

        Returns:
            Error verification result dict
        """
        logger.error(f"Verification error: {error_message}")
        return {
            "passed": False,
            "overall_feedback": f"Verification error: {error_message}",
            "requirements_check": {
                "verification_error": {
                    "met": False,
                    "feedback": error_message,
                }
            },
            "hints": ["Please check your code and try again."],
            "issues_found": [f"Verification system error: {error_message}"],
            "suggestions": [],
            "code_quality": "needs_improvement",
            "test_status": "error",
            "pattern_match_status": "error",
        }
