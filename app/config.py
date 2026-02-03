from functools import lru_cache
from pathlib import Path

from pydantic import ConfigDict, field_validator
from pydantic_settings import BaseSettings

# Get the project root directory (one level up from app/)
PROJECT_ROOT = Path(__file__).parent.parent

# Export these for app-wide use
__all__ = ["Settings", "settings", "get_settings"]


class Settings(BaseSettings):
    # App Settings
    app_name: str = "AI Tutor for GitHub Repositories"
    debug: bool | str = True

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug(cls, v):
        """Parse debug from various formats"""
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            v_lower = v.lower().strip()
            if v_lower in ("true", "1", "yes", "on"):
                return True
            if v_lower in ("false", "0", "no", "off"):
                return False
            # If it's something else (like 'WARN'), default to True for development
            return True
        return bool(v)

    # Server Settings
    host: str = "127.0.0.1"
    port: int = 8000

    # CORS Settings - Allowed frontend URLs
    cors_origins: str | list[str] = ["*"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        """Parse CORS origins from comma-separated string or list"""
        if isinstance(v, str):
            # Handle comma-separated string from .env
            if v.strip() == "*":
                return ["*"]
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    # Database Settings - Supabase connection string
    database_url: str | None = None
    supabase_anon_key: str | None = None
    supabase_service_key: str | None = None
    supabase_url: str | None = None

    # PostgreSQL individual connection parameters
    db_user: str | None = None
    db_password: str | None = None
    db_host: str | None = None
    db_port: str | None = None
    db_name: str | None = None

    # Vector Database Settings - Qdrant connection
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None

    # Embedding Settings
    embedding_provider: str = "vertex_ai"  # Options: "vertex_ai", "openai", "huggingface", "local"
    embedding_model_name: str = "gemini-embedding-001"  # Vertex AI model name (state-of-the-art, supports English, multilingual, and code)
    openai_embedding_model: str = "text-embedding-3-small"  # OpenAI model name
    openai_api_key: str | None = None  # Maps to OPENAI_API_KEY (for embeddings)
    huggingface_token: str | None = None  # Maps to HUGGINGFACE_TOKEN (for API access)
    chunk_size: int = 1000  # tokens per chunk
    chunk_overlap: int = 200  # overlap between chunks
    max_files_per_project: int = 500  # maximum files to process per project
    max_text_size_mb: float = 2.5  # maximum total text size in MB per project
    max_chunks_per_project: int = 500  # maximum chunks per project

    # Environment (development or production)
    environment: str = "development"

    # Workspace/Preview base URL for terminal previews (e.g., https://api.gitguide.com)
    workspace_public_base_url: str | None = None

    # LLM API Keys - Azure OpenAI (Production)
    azure_openai_key: str | None = None  # Maps to AZURE_OPENAI_KEY
    azure_openai_endpoint: str | None = None
    azure_openai_deployment_gpt_4_1: str | None = None  # Maps to AZURE_OPENAI_DEPLOYMENT_GPT_4_1
    azure_openai_api_version: str = "2024-02-15-preview"
    azure_openai_timeout: int = 180  # Timeout in seconds

    # Authentication
    clerk_secret_key: str | None = None  # Add this line
    jwt_secret: str | None = None
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 60  # For JWT token expiration

    # LLM API Keys - Groq (Development)
    groq_api_key: str | None = None
    groq_api_key2: str | None = (
        None  # Maps to GROQ_API_KEY2 - for task verification (higher limits)
    )
    # Default to a model available on Groq's OpenAI-compatible API for most keys.
    # (If you set GROQ_MODEL in .env, that will override this.)
    groq_model: str = "llama-3.3-70b-versatile"
    groq_sanitizer_model: str = (
        "openai/gpt-oss-120b"  # Model for JSON sanitization (when main model returns markdown/code)
    )
    groq_sanitizer_enabled: bool = True  # Enable/disable sanitizer

    # LLM API Keys - Gemini (Google Cloud)
    gemini_api_key: str | None = None  # Maps to GEMINI_API_KEY (direct API key method)
    google_application_credentials: str | None = (
        None  # Maps to GOOGLE_APPLICATION_CREDENTIALS (service account JSON path)
    )
    gcp_project_id: str | None = None  # Maps to GCP_PROJECT_ID (required for service account)
    gcp_location: str = (
        "global"  # Maps to GCP_LOCATION (default: global - required for Gemini models)
    )
    gemini_model: str = "gemini-2.0-flash-exp"  # Maps to GEMINI_MODEL (Vertex AI: gemini-2.0-flash-exp, gemini-2.5-flash, gemini-2.5-pro)

    # Verification (optional overrides to avoid sharing the same Gemini setup)
    # If set, the verification agent will use these instead of the default Gemini settings.
    verification_gemini_api_key: str | None = None  # VERIFICATION_GEMINI_API_KEY
    verification_google_application_credentials: str | None = (
        None  # VERIFICATION_GOOGLE_APPLICATION_CREDENTIALS
    )
    verification_gcp_project_id: str | None = None  # VERIFICATION_GCP_PROJECT_ID
    verification_gcp_location: str | None = None  # VERIFICATION_GCP_LOCATION
    verification_gemini_model: str | None = None  # VERIFICATION_GEMINI_MODEL

    # GitHub API
    git_access_token: str | None = None  # Maps to GIT_ACCESS_TOKEN
    git_client_id: str | None = None  # Maps to GIT_CLIENT_ID
    git_client_secret: str | None = None  # Maps to GIT_CLIENT_SECRET
    git_redirect_uri: str | None = None  # Maps to GIT_REDIRECT_URI

    # Redis (for rate limiting and caching)
    redis_url: str | None = None  # Maps to REDIS_URL (e.g., redis://localhost:6379/0)

    # Logging
    log_level: str = "INFO"

    # Roadmap Service (for service-to-service communication)
    roadmap_service_url: str | None = (
        None  # Maps to ROADMAP_SERVICE_URL (e.g., https://gitguide-roadmap-xxx.run.app)
    )
    internal_auth_token: str | None = (
        None  # Maps to INTERNAL_AUTH_TOKEN (shared secret for service-to-service calls)
    )

    model_config = ConfigDict(
        # Look for .env in project root (ai_tutor_for_github_repositories/)
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore extra fields in .env that aren't defined
    )


@lru_cache
def get_settings() -> Settings:
    """
    Create and cache a single Settings instance (Singleton pattern).
    Returns the same instance on subsequent calls.
    """
    return Settings()


# Create a global settings instance for convenience
settings = get_settings()
