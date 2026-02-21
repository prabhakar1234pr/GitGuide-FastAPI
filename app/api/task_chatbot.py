"""
Task chatbot API endpoint.
Provides context-aware chat assistance for coding tasks with teaching-focused responses.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from supabase import Client

from app.core.supabase_client import get_supabase_client
from app.services.groq_service import get_groq_service
from app.services.task_chatbot_context import build_task_context
from app.utils.clerk_auth import verify_clerk_token
from app.utils.db_helpers import get_user_id_from_clerk, user_has_project_access

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health")
async def health_check():
    """Health check endpoint for task chatbot."""
    return {"status": "ok", "service": "task-chatbot"}


# Teaching-focused system prompt
TEACHING_SYSTEM_PROMPT = """You are an expert coding tutor helping a student learn by doing. Your role is to GUIDE and TEACH, not to give direct answers.

CRITICAL RULES:
1. NEVER provide complete code solutions or copy-paste ready code
2. Guide the student to discover the answer themselves through questions and hints
3. Explain concepts, patterns, and reasoning behind the task
4. If the student asks "how do I...", respond with "What have you tried so far?" or "Let's break this down step by step..."
5. Reference the task description and concept content to provide context
6. If verification feedback is available, use it to guide the student toward fixing issues
7. Encourage experimentation and learning from mistakes

TEACHING APPROACH:
- Ask leading questions: "What do you think happens if you...?"
- Break down complex problems into smaller steps
- Explain WHY something works, not just HOW
- Point to relevant parts of the concept documentation
- Use analogies when helpful
- Celebrate progress and encourage persistence

Remember: The goal is learning, not completion. Guide them to understand, not just copy code."""


class UserCodeFile(BaseModel):
    """User code file structure."""

    path: str = Field(..., description="File path")
    content: str = Field(..., description="File content")


class TaskChatRequest(BaseModel):
    """Request for task chatbot."""

    message: str = Field(..., min_length=1, max_length=2000, description="User's message")
    conversation_id: str | None = Field(
        None, description="Existing conversation ID. If None, creates new conversation."
    )
    user_code: list[UserCodeFile] = Field(default=[], description="User's open files with code")
    verification: dict | None = Field(
        None, description="Verification feedback if user has verified the task"
    )
    is_manager: bool | None = Field(
        None, description="True if user is project owner (manager). Backend verifies."
    )

    @field_validator("user_code")
    @classmethod
    def validate_user_code(cls, v):
        """Validate user code size to avoid token overflow."""
        if len(v) > 10:
            raise ValueError("Maximum 10 files allowed")
        total_chars = sum(len(f.content) for f in v)
        if total_chars > 50000:  # ~50KB limit
            raise ValueError("User code too large. Please close some files.")
        return v


class TaskChatResponse(BaseModel):
    """Response from task chatbot."""

    response: str = Field(..., description="AI assistant's response")
    conversation_id: str = Field(..., description="Conversation ID (new or existing)")


class ConversationResponse(BaseModel):
    """Response for loading conversation."""

    conversation_id: str | None
    messages: list[dict] = Field(..., description="List of messages with role, content, created_at")


class ConversationListItem(BaseModel):
    id: str
    title: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


async def verify_task_access(supabase: Client, task_id: str, user_id: str) -> tuple[dict, bool]:
    """Verify task exists and user has access to it. Returns (task, is_owner)."""
    # Get task
    task_response = supabase.table("tasks").select("*").eq("task_id", task_id).execute()

    if not task_response.data:
        raise HTTPException(status_code=404, detail="Task not found")

    task = task_response.data[0]
    concept_id = task.get("concept_id")

    if not concept_id:
        raise HTTPException(status_code=400, detail="Task has no associated concept")

    # Get concept to find project
    concept_response = (
        supabase.table("concepts").select("day_id").eq("concept_id", concept_id).execute()
    )

    if not concept_response.data:
        raise HTTPException(status_code=404, detail="Concept not found")

    day_id = concept_response.data[0].get("day_id")
    if not day_id:
        raise HTTPException(status_code=400, detail="Concept has no associated day")

    # Get day to find project
    day_response = (
        supabase.table("roadmap_days").select("project_id").eq("day_id", day_id).execute()
    )

    if not day_response.data:
        raise HTTPException(status_code=404, detail="Day not found")

    project_id = day_response.data[0].get("project_id")

    # Verify user has access (owner or project_access)
    has_access, is_owner = user_has_project_access(supabase, project_id, user_id)
    if not has_access:
        raise HTTPException(status_code=403, detail="You don't have permission to access this task")

    # Add project_id to task for convenience
    task["project_id"] = project_id
    return task, is_owner


async def create_new_conversation(
    supabase: Client, user_id: str, project_id: str, task_id: str
) -> str:
    """Create a brand-new conversation (supports multiple conversations per task)."""
    new_conv = {
        "user_id": user_id,
        "project_id": project_id,
        "task_id": task_id,
        "title": None,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }

    create_response = supabase.table("chat_conversations").insert(new_conv).execute()

    if not create_response.data:
        raise HTTPException(status_code=500, detail="Failed to create conversation")

    return create_response.data[0]["id"]


async def load_conversation_history(
    supabase: Client, conversation_id: str, limit: int = 20
) -> list[dict]:
    """Load conversation history as list of {role, content} dicts."""
    messages_response = (
        supabase.table("chat_messages")
        .select("role, content")
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )

    if not messages_response.data:
        return []

    # Convert to format expected by Groq service
    return [{"role": msg["role"], "content": msg["content"]} for msg in messages_response.data]


async def store_messages(
    supabase: Client, conversation_id: str, user_message: str, assistant_message: str
) -> None:
    """Store user and assistant messages in database."""
    now = datetime.now(UTC).isoformat()

    messages = [
        {
            "conversation_id": conversation_id,
            "role": "user",
            "content": user_message,
            "created_at": now,
        },
        {
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": assistant_message,
            "created_at": now,
        },
    ]

    supabase.table("chat_messages").insert(messages).execute()

    # Update conversation updated_at
    supabase.table("chat_conversations").update({"updated_at": now}).eq(
        "id", conversation_id
    ).execute()


@router.post("/task/{task_id}/chat", response_model=TaskChatResponse)
async def chat_task(
    task_id: str,
    request: TaskChatRequest,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Task chatbot with full context: concept, task, user code, verification, progress.

    Flow:
    1. Verify user and task access
    2. Get or create conversation
    3. Load conversation history
    4. Build rich context (concept, task, user code, verification, progress)
    5. Generate teaching-focused response using Groq
    6. Store messages in database
    7. Return response
    """
    try:
        clerk_user_id = user_info["clerk_user_id"]
        logger.info(f"💬 Task chat request for task_id={task_id} from user: {clerk_user_id}")

        # 1. Get user_id
        user_id = get_user_id_from_clerk(supabase, clerk_user_id)

        # 2. Verify task exists and user has access (owner or project_access)
        task, is_owner = await verify_task_access(supabase, task_id, user_id)
        project_id = task["project_id"]
        is_manager = request.is_manager if request.is_manager is not None else is_owner

        # 3. Get or create conversation
        conversation_id = request.conversation_id
        if not conversation_id:
            conversation_id = await create_new_conversation(supabase, user_id, project_id, task_id)
            logger.info(f"   Created new conversation: {conversation_id}")
        else:
            # Verify conversation belongs to user
            conv_check = (
                supabase.table("chat_conversations")
                .select("id")
                .eq("id", conversation_id)
                .eq("user_id", user_id)
                .execute()
            )
            if not conv_check.data:
                raise HTTPException(
                    status_code=403, detail="Conversation not found or access denied"
                )

        # 4. Load conversation history (last 20 messages)
        conversation_history = await load_conversation_history(supabase, conversation_id, limit=20)
        logger.info(f"   Loaded {len(conversation_history)} previous messages")

        # 5. Build rich context (include user role)
        user_code_list = [{"path": f.path, "content": f.content} for f in request.user_code]
        context = await build_task_context(
            task_id=task_id,
            user_id=user_id,
            user_code=user_code_list,
            supabase=supabase,
            verification=request.verification,
            is_manager=is_manager,
        )

        # 6. Generate response with role-aware prompt
        role_instruction = (
            "IMPORTANT: The user is a MANAGER (project owner) reviewing this task. "
            "They cannot perform or verify tasks—view only. Focus on explaining concepts, "
            "overview, and what employees would need to do. Do not suggest they implement or verify."
            if is_manager
            else ""
        )
        system_prompt = TEACHING_SYSTEM_PROMPT + (
            f"\n\n{role_instruction}" if role_instruction else ""
        )

        groq_service = get_groq_service()
        logger.info(
            f"   Generating response with Groq (message length: {len(request.message)}, is_manager={is_manager})"
        )

        response = await groq_service.generate_response_async(
            user_query=request.message,
            system_prompt=system_prompt,
            context=context,
            conversation_history=conversation_history,
            temperature=0.7,
        )

        logger.info(f"✅ Generated response ({len(response)} chars)")

        # 7. Store messages
        await store_messages(supabase, conversation_id, request.message, response)

        # If this is the first user message in this conversation, set title = first user message
        if len(conversation_history) == 0:
            title = request.message.strip().replace("\n", " ")
            if len(title) > 80:
                title = title[:77] + "..."
            supabase.table("chat_conversations").update({"title": title}).eq(
                "id", conversation_id
            ).execute()

        return TaskChatResponse(response=response, conversation_id=conversation_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error in task chat endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Chat request failed: {str(e)}") from e


@router.get("/task/{task_id}/conversation", response_model=ConversationResponse)
async def get_task_conversation(
    task_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
    conversation_id: str | None = None,
):
    """
    Get conversation for a task (loads existing conversation and messages).

    Returns conversation_id (or None if no conversation exists) and messages.
    """
    try:
        clerk_user_id = user_info["clerk_user_id"]
        logger.info(f"📥 Loading conversation for task_id={task_id} from user: {clerk_user_id}")

        user_id = get_user_id_from_clerk(supabase, clerk_user_id)

        # Verify task access
        task, _ = await verify_task_access(supabase, task_id, user_id)
        project_id = task["project_id"]
        logger.info(f"   Task verified, project_id={project_id}")

        messages: list[dict] = []

        # If a conversation_id is provided, load that specific conversation (and verify ownership)
        if conversation_id:
            conv_check = (
                supabase.table("chat_conversations")
                .select("id")
                .eq("id", conversation_id)
                .eq("user_id", user_id)
                .eq("project_id", project_id)
                .eq("task_id", task_id)
                .execute()
            )
            if not conv_check.data:
                raise HTTPException(status_code=404, detail="Conversation not found")
            logger.info(f"   Loading requested conversation: {conversation_id}")
        else:
            # Load most recent conversation for this task (if any)
            conv_response = (
                supabase.table("chat_conversations")
                .select("id")
                .eq("user_id", user_id)
                .eq("project_id", project_id)
                .eq("task_id", task_id)
                .order("updated_at", desc=True)
                .limit(1)
                .execute()
            )

            if conv_response.data and len(conv_response.data) > 0:
                conversation_id = conv_response.data[0]["id"]
                logger.info(f"   Found most recent conversation: {conversation_id}")
            else:
                logger.info("   No conversation found")
                return ConversationResponse(conversation_id=None, messages=[])

            # Load messages
            messages_response = (
                supabase.table("chat_messages")
                .select("role, content, created_at")
                .eq("conversation_id", conversation_id)
                .order("created_at", desc=False)
                .execute()
            )

            if messages_response.data:
                messages = [
                    {
                        "role": msg["role"],
                        "content": msg["content"],
                        "created_at": msg["created_at"],
                    }
                    for msg in messages_response.data
                ]
                logger.info(f"   Loaded {len(messages)} messages")
        # When conversation_id is provided and valid, load messages for it too
        if conversation_id and not messages:
            messages_response = (
                supabase.table("chat_messages")
                .select("role, content, created_at")
                .eq("conversation_id", conversation_id)
                .order("created_at", desc=False)
                .execute()
            )
            if messages_response.data:
                messages = [
                    {
                        "role": msg["role"],
                        "content": msg["content"],
                        "created_at": msg["created_at"],
                    }
                    for msg in messages_response.data
                ]

        return ConversationResponse(conversation_id=conversation_id, messages=messages)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error loading conversation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to load conversation: {str(e)}") from e


@router.get("/task/{task_id}/conversations", response_model=list[ConversationListItem])
async def list_task_conversations(
    task_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """List all conversations for this task (most recent first)."""
    clerk_user_id = user_info["clerk_user_id"]
    user_id = get_user_id_from_clerk(supabase, clerk_user_id)

    task, _ = await verify_task_access(supabase, task_id, user_id)
    project_id = task["project_id"]

    convs = (
        supabase.table("chat_conversations")
        .select("id,title,created_at,updated_at")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("task_id", task_id)
        .order("updated_at", desc=True)
        .execute()
    )

    return [
        ConversationListItem(
            id=row["id"],
            title=row.get("title"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )
        for row in (convs.data or [])
    ]
