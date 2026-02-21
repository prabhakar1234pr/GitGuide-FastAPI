import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.github_consent import router as github_consent_router
from app.api.invite import router as invite_router
from app.api.progress import router as progress_router
from app.api.project_chunks_embeddings import router as project_chunks_embeddings_router
from app.api.projects import router as projects_router
from app.api.roadmap import router as roadmap_router
from app.api.routes import router

# Task sessions router moved to workspace_service.py (requires Docker access)
from app.api.task_chatbot import router as task_chatbot_router
from app.api.task_verification import router as task_verification_router
from app.api.users import router as users_router

# Note: Workspace-related routes (workspaces, files, terminal, git, preview) are only
# available in workspace_service.py which runs on the VM with Docker access
from app.config import settings
from app.core.startup import shutdown_services, startup_services

# Configure logging from settings
logging.basicConfig(level=settings.log_level)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Startup: Code that runs when the app starts
    logging.info("=" * 60)
    logging.info(f"🚀 {settings.app_name} starting up...")
    logging.info("=" * 60)

    # App Settings
    logging.info("📋 App Configuration:")
    logging.info(f"  Environment: {settings.environment}")
    logging.info(f"  Debug mode: {settings.debug}")
    logging.info(f"  Log level: {settings.log_level}")

    # Server Settings
    logging.info("🌐 Server Configuration:")
    logging.info(f"  Host: {settings.host}")
    logging.info(f"  Port: {settings.port}")
    logging.info(f"  CORS Origins: {settings.cors_origins}")

    # Database Settings
    logging.info("💾 Database Configuration:")
    logging.info(f"  Supabase URL: {'✓ Configured' if settings.supabase_url else '✗ Not set'}")
    logging.info(f"  Database URL: {'✓ Configured' if settings.database_url else '✗ Not set'}")
    logging.info(f"  Qdrant URL: {'✓ Configured' if settings.qdrant_url else '✗ Not set'}")

    # LLM Settings
    logging.info("🤖 LLM Configuration:")
    if settings.environment == "production":
        logging.info(
            f"  Azure OpenAI API: {'✓ Configured' if settings.azure_openai_key else '✗ Not set'}"
        )
        logging.info(
            f"  Azure Endpoint: {settings.azure_openai_endpoint if settings.azure_openai_endpoint else '✗ Not set'}"
        )
        logging.info(
            f"  Azure Deployment: {settings.azure_openai_deployment_gpt_4_1 if settings.azure_openai_deployment_gpt_4_1 else '✗ Not set'}"
        )
        logging.info(f"  Azure Timeout: {settings.azure_openai_timeout}s")
    else:
        logging.info("  🌟 Primary LLM: Gemini (Vertex AI)")
        logging.info(
            f"  Gemini Model: {settings.gemini_model if hasattr(settings, 'gemini_model') else 'gemini-2.0-flash-exp'}"
        )
        logging.info(
            f"  Gemini Auth: {'✓ Service Account (Vertex AI)' if settings.google_application_credentials else '✗ Not configured'}"
        )
        logging.info(
            f"  GCP Project: {settings.gcp_project_id if hasattr(settings, 'gcp_project_id') else '✗ Not set'}"
        )
        logging.info(
            f"  GCP Location: {settings.gcp_location if hasattr(settings, 'gcp_location') else 'global'}"
        )
        logging.info(
            f"  Groq API (fallback/sanitizer): {'✓ Configured' if settings.groq_api_key else '✗ Not set'}"
        )
        logging.info(f"  Groq Model: {settings.groq_model}")

    # GitHub Settings
    logging.info("🔧 GitHub Configuration:")
    logging.info(f"  GitHub Token: {'✓ Configured' if settings.git_access_token else '✗ Not set'}")

    # Auth Settings
    logging.info("🔐 Authentication Configuration:")
    logging.info(
        f"  Clerk Secret Key: {'✓ Configured' if settings.clerk_secret_key else '✗ Not set'}"
    )
    logging.info(f"  JWT Secret: {'✓ Configured' if settings.jwt_secret else '✗ Not set'}")
    logging.info(f"  JWT Algorithm: {settings.jwt_algorithm}")
    logging.info(f"  JWT Expiration: {settings.jwt_expiration_minutes} minutes")

    # Initialize services (rate limiter, etc.)
    try:
        await startup_services()
    except Exception as e:
        logging.warning(f"⚠️  Service initialization warning: {e}")

    logging.info("=" * 60)
    logging.info("✅ Startup complete - Ready to accept requests")
    logging.info("=" * 60)

    yield  # App runs here

    # Shutdown: Code that runs when the app shuts down
    try:
        logging.info("=" * 60)
        logging.info("🛑 App is shutting down...")
        logging.info("=" * 60)

        # Shutdown services
        await shutdown_services()
    except Exception as e:
        # Ignore cancellation errors during shutdown (normal when stopping with Ctrl+C)
        if "CancelledError" not in str(type(e).__name__) and "KeyboardInterrupt" not in str(
            type(e).__name__
        ):
            logging.error(f"Error during shutdown: {e}", exc_info=True)


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

# Add CORS middleware - configured from settings
# In production with allow_credentials=True, "*" is invalid per CORS spec - use explicit origins
_cors_origins = settings.cors_origins
if settings.environment == "production" and (
    _cors_origins == ["*"] or (isinstance(_cors_origins, list) and "*" in _cors_origins)
):
    _cors_origins = [
        "https://gitguide.dev",
        "https://www.gitguide.dev",
    ]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
app.include_router(invite_router, prefix="/api/invite", tags=["invite"])
app.include_router(users_router, prefix="/api/users", tags=["users"])
app.include_router(projects_router, prefix="/api/projects", tags=["projects"])
app.include_router(project_chunks_embeddings_router, prefix="/api/project_chunks_embeddings")
app.include_router(roadmap_router, prefix="/api/roadmap", tags=["roadmap"])
app.include_router(progress_router, prefix="/api/progress", tags=["progress"])
app.include_router(github_consent_router, prefix="/api/github", tags=["github"])
# Task sessions router moved to workspace_service.py (requires Docker access)
app.include_router(task_verification_router, prefix="/api/tasks", tags=["task-verification"])
app.include_router(task_chatbot_router, prefix="/api/chatbot", tags=["task-chatbot"])
# Workspace routes (workspaces, files, terminal, git, preview) are only available
# on the VM service at http://35.222.130.245:8080
