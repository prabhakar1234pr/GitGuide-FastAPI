"""
GitGuide Roadmap Service
Minimal FastAPI app for roadmap generation.
Deployed separately on Cloud Run with higher resources (2 vCPU, 2Gi RAM).

All LangGraph workflows execute here:
- Initial roadmap generation (full pipeline)
- Incremental concept generation (lazy loading)
- All agent nodes (analyze_repo, plan_curriculum, generate_content, generate_tasks, etc.)
"""

import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import settings
from app.services.roadmap_generation import (
    router as roadmap_gen_router,
)
from app.services.roadmap_generation import (
    trigger_incremental_generation_sync,
    trigger_roadmap_generation_sync,
)

# Configure logging
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup and shutdown events for the roadmap service."""
    logger.info("=" * 60)
    logger.info("🚀 GitGuide Roadmap Service starting up...")
    logger.info(f"   Environment: {settings.environment}")
    logger.info(f"   GCP Project: {settings.gcp_project_id}")
    logger.info(f"   Gemini Model: {settings.gemini_model}")
    logger.info("=" * 60)

    yield

    logger.info("🛑 GitGuide Roadmap Service shutting down...")


app = FastAPI(
    title="GitGuide Roadmap Service",
    description="Dedicated service for LLM-heavy roadmap generation",
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include roadmap generation router (public endpoint for initial generation)
app.include_router(roadmap_gen_router, prefix="/api/roadmap", tags=["roadmap-generation"])


# Internal auth dependency for service-to-service calls
async def verify_internal_auth(request: Request):
    """Verify internal auth token for service-to-service calls.

    Accepts either:
    1. X-Internal-Token header (shared secret)
    2. Authorization: Bearer <token> (Google Cloud Identity token)
    """
    # Try X-Internal-Token first (shared secret method)
    x_internal_token = (
        request.headers.get("X-Internal-Token")
        or request.headers.get("x-internal-token")
        or request.headers.get("X-INTERNAL-TOKEN")
    )

    # Try Authorization header (Google Cloud Identity token)
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
    identity_token = None
    if auth_header and auth_header.startswith("Bearer "):
        identity_token = auth_header.replace("Bearer ", "").strip()

    logger.info("=" * 70)
    logger.info("🔐 INTERNAL AUTH VERIFICATION")
    logger.info(f"   X-Internal-Token received: {x_internal_token is not None}")
    logger.info(f"   Authorization Bearer received: {identity_token is not None}")
    if x_internal_token:
        logger.info(f"   X-Internal-Token length: {len(x_internal_token)}")
    if identity_token:
        logger.info(f"   Identity token length: {len(identity_token)}")
    logger.info(f"   All headers keys: {list(request.headers.keys())}")
    logger.info("=" * 70)

    # If using X-Internal-Token (shared secret)
    if x_internal_token:
        if not settings.internal_auth_token:
            logger.error("❌ INTERNAL_AUTH_TOKEN not configured - internal endpoints disabled")
            raise HTTPException(status_code=503, detail="Internal auth not configured")

        if x_internal_token != settings.internal_auth_token:
            logger.error("❌ TOKEN MISMATCH (X-Internal-Token)")
            logger.error(f"   Expected length: {len(settings.internal_auth_token)}")
            logger.error(f"   Received length: {len(x_internal_token)}")
            raise HTTPException(status_code=403, detail="Invalid internal auth token")

        logger.info("✅ X-Internal-Token verified successfully")
        return True

    # If using Google Cloud Identity token
    if identity_token:
        # Verify the identity token is valid (not empty)
        if not identity_token or len(identity_token) < 10:
            logger.error("❌ Invalid identity token (too short or empty)")
            raise HTTPException(status_code=403, detail="Invalid identity token")

        # For identity tokens, Cloud Run automatically verifies them before the request reaches us
        # If we get here, the token was already validated by Cloud Run IAM
        logger.info("✅ Google Cloud Identity token verified (validated by Cloud Run)")
        return True

    # No valid token found
    logger.error("❌ NO TOKEN RECEIVED IN HEADERS")
    logger.error("   Expected either X-Internal-Token or Authorization: Bearer <token>")
    raise HTTPException(status_code=403, detail="No authentication token provided")


# Internal endpoints for LangGraph workflows
class IncrementalGenerateRequest(BaseModel):
    """Request to trigger incremental concept generation."""

    project_id: str
    user_id: str | None = None  # Per-user cursor; uses project owner if not provided


class GenerateRoadmapInternalRequest(BaseModel):
    """Internal request to trigger full roadmap generation."""

    project_id: str
    github_url: str
    skill_level: str
    target_days: int


@app.post("/api/roadmap/incremental-generate", dependencies=[Depends(verify_internal_auth)])
async def incremental_generate(
    request: IncrementalGenerateRequest, background_tasks: BackgroundTasks
):
    """
    Internal endpoint to trigger incremental concept generation.

    This endpoint is called by the main API service when a user completes
    a concept and needs more concepts generated ahead of their position.

    Requires internal auth token in X-Internal-Token header.
    """
    logger.info("=" * 70)
    logger.info("🔄 INCREMENTAL GENERATION REQUEST RECEIVED")
    logger.info("=" * 70)
    logger.info(f"   📦 Project ID: {request.project_id}")
    logger.info(
        f"   🕐 Timestamp: {__import__('datetime').datetime.now(__import__('datetime').UTC).isoformat()}"
    )
    logger.info("=" * 70)

    try:
        logger.info(f"🔄 Starting incremental generation for project_id={request.project_id}")
        logger.debug(f"   Request payload: {request.model_dump()}")

        # Use BackgroundTasks - sync wrapper runs in thread pool (asyncio.run works there)
        # Cannot call asyncio.run() from async endpoint (already in event loop)
        logger.info(
            f"📞 Adding incremental generation to background tasks for project_id={request.project_id}"
        )
        background_tasks.add_task(
            trigger_incremental_generation_sync,
            project_id=request.project_id,
            user_id=request.user_id,
        )
        logger.info(f"✅ Incremental generation task queued for project_id={request.project_id}")

        logger.info("=" * 70)
        logger.info("✅ INCREMENTAL GENERATION TRIGGERED SUCCESSFULLY")
        logger.info(f"   📦 Project ID: {request.project_id}")
        logger.info("=" * 70)

        return {
            "success": True,
            "message": "Incremental generation triggered",
            "project_id": request.project_id,
        }
    except Exception as e:
        logger.error("=" * 70)
        logger.error("❌ ERROR TRIGGERING INCREMENTAL GENERATION")
        logger.error(f"   📦 Project ID: {request.project_id}")
        logger.error(f"   ⚠️  Error Type: {type(e).__name__}")
        logger.error(f"   ⚠️  Error Message: {str(e)}")
        logger.error("=" * 70, exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to trigger incremental generation: {str(e)}"
        ) from e


@app.post("/api/roadmap/generate-internal", dependencies=[Depends(verify_internal_auth)])
async def generate_roadmap_internal(
    request: GenerateRoadmapInternalRequest, background_tasks: BackgroundTasks
):
    """
    Internal endpoint to trigger full roadmap generation.

    This endpoint is called by the main API service (e.g., from embedding pipeline)
    to trigger the complete LangGraph workflow for initial roadmap generation.

    Requires internal auth token in X-Internal-Token header.

    Uses FastAPI BackgroundTasks to ensure the task runs after the response is sent
    and is not cancelled by Cloud Run when the HTTP handler returns.
    """
    logger.info("=" * 70)
    logger.info("🚀 FULL ROADMAP GENERATION REQUEST RECEIVED")
    logger.info("=" * 70)
    logger.info(f"   📦 Project ID: {request.project_id}")
    logger.info(f"   🔗 GitHub URL: {request.github_url}")
    logger.info(f"   📊 Skill Level: {request.skill_level}")
    logger.info(f"   📅 Target Days: {request.target_days}")
    logger.info(
        f"   🕐 Timestamp: {__import__('datetime').datetime.now(__import__('datetime').UTC).isoformat()}"
    )
    logger.info("=" * 70)

    try:
        logger.info(f"🚀 Starting full roadmap generation for project_id={request.project_id}")
        logger.debug(f"   Request payload: {request.model_dump()}")

        # Use FastAPI BackgroundTasks instead of asyncio.create_task()
        # This ensures the task runs after the response is sent and won't be cancelled
        # by Cloud Run when the HTTP handler returns
        logger.info("📞 Adding roadmap generation to background tasks")
        logger.info(f"   Project ID: {request.project_id}")
        logger.info(f"   GitHub URL: {request.github_url}")
        logger.info(f"   Skill Level: {request.skill_level}")
        logger.info(f"   Target Days: {request.target_days}")

        # Use the sync wrapper which creates its own event loop
        # This ensures the task completes even if Cloud Run scales down the container
        background_tasks.add_task(
            trigger_roadmap_generation_sync,
            project_id=request.project_id,
            github_url=request.github_url,
            skill_level=request.skill_level,
            target_days=request.target_days,
        )

        logger.info("✅ Background task added successfully")
        logger.info("   ⚠️  Task will run after response is sent - check logs for progress")

        logger.info("=" * 70)
        logger.info("✅ FULL ROADMAP GENERATION TRIGGERED SUCCESSFULLY")
        logger.info(f"   📦 Project ID: {request.project_id}")
        logger.info("   ⚠️  Note: Generation runs in background task")
        logger.info("=" * 70)

        return {
            "success": True,
            "message": "Roadmap generation started",
            "project_id": request.project_id,
        }
    except Exception as e:
        logger.error("=" * 70)
        logger.error("❌ ERROR TRIGGERING ROADMAP GENERATION")
        logger.error(f"   📦 Project ID: {request.project_id}")
        logger.error(f"   ⚠️  Error Type: {type(e).__name__}")
        logger.error(f"   ⚠️  Error Message: {str(e)}")
        logger.error("=" * 70, exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to trigger roadmap generation: {str(e)}"
        ) from e


@app.get("/health")
async def health_check():
    """Health check endpoint for Cloud Run."""
    return {
        "status": "healthy",
        "service": "roadmap",
        "environment": settings.environment,
    }


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "GitGuide Roadmap Service",
        "version": "1.0.0",
        "docs": "/docs",
    }
