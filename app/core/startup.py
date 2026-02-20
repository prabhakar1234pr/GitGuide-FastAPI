"""
Application startup initialization.
Call this on app startup to initialize services.
"""

import asyncio
import logging
import os
import subprocess
import time
from urllib.parse import urlparse

from app.config import PROJECT_ROOT, settings
from app.core.supabase_client import get_supabase_client
from app.services.embedding_pipeline import run_embedding_pipeline
from app.services.rate_limiter import initialize_rate_limiter

logger = logging.getLogger(__name__)


def _is_service_reachable(base_url: str) -> bool:
    """Check if a service is reachable via its /health endpoint (sync, for use in thread)."""
    try:
        import httpx

        health_url = f"{base_url.rstrip('/')}/health"
        resp = httpx.get(health_url, timeout=3.0)
        return resp.status_code == 200
    except Exception:
        return False


def _docker_available() -> bool:
    """Check if Docker daemon is available."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _ensure_container_sync(service_name: str, base_url: str) -> bool:
    """
    If service is unreachable, start it via docker-compose.
    Returns True if service is (or became) reachable, False otherwise.
    """
    if _is_service_reachable(base_url):
        return True

    if not _docker_available():
        logger.warning(
            f"⚠️  {service_name} not reachable and Docker not available. "
            f"Start manually: docker-compose up {service_name} -d"
        )
        return False

    compose_file = PROJECT_ROOT / "docker-compose.yml"
    if not compose_file.exists():
        logger.warning(f"⚠️  docker-compose.yml not found - cannot auto-start {service_name}")
        return False

    for cmd in [
        ["docker", "compose", "up", service_name, "-d"],
        ["docker-compose", "up", service_name, "-d"],
    ]:
        try:
            result = subprocess.run(
                cmd,
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=90,
                env=os.environ.copy(),
            )
            if result.returncode == 0:
                logger.info(f"🐳 Started {service_name} container via docker-compose")
                for _ in range(12):  # wait up to ~12 seconds
                    time.sleep(1)
                    if _is_service_reachable(base_url):
                        logger.info(f"✅ {service_name} service is now reachable")
                        return True
                logger.warning(
                    f"⚠️  {service_name} container started but health check not ready yet"
                )
                return True
            else:
                logger.debug(f"docker-compose failed (tried {cmd[0]}): {result.stderr}")
                continue
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.debug(f"Could not run {cmd}: {e}")
            continue

    logger.warning(
        f"⚠️  Could not start {service_name} container. Run manually: docker-compose up {service_name} -d"
    )
    return False


def _ensure_roadmap_container_sync() -> bool:
    """Auto-start roadmap if ROADMAP_SERVICE_URL points to localhost and service is unreachable."""
    url = settings.roadmap_service_url
    if not url or "localhost" not in url.lower() and "127.0.0.1" not in url:
        return False
    base = urlparse(url)
    base_url = f"{base.scheme}://{base.netloc}"
    return _ensure_container_sync("roadmap", base_url)


def _ensure_workspaces_container_sync() -> bool:
    """Auto-start workspaces when running backend locally (needed for editor/terminal/preview)."""
    base_url = "http://127.0.0.1:8002"
    return _ensure_container_sync("workspaces", base_url)


async def resume_stuck_projects():
    """
    Find and resume processing for projects stuck in 'created' or 'processing' status.
    This handles recovery after deployments or service restarts.

    Projects in 'processing' status for >30 minutes are considered stuck and reset to 'created'.
    """
    try:
        import datetime

        logger.info("🔍 Checking for stuck projects...")
        supabase = get_supabase_client()

        # Find all projects with status 'created' (never started)
        created_projects_response = (
            supabase.table("projects")
            .select("project_id, github_url, project_name, created_at, updated_at")
            .eq("status", "created")
            .execute()
        )

        # Find projects stuck in 'processing' status (likely interrupted)
        # Reset them to 'created' if they've been processing for >30 minutes
        processing_projects_response = (
            supabase.table("projects")
            .select("project_id, github_url, project_name, created_at, updated_at")
            .eq("status", "processing")
            .execute()
        )

        stuck_projects = []

        # Add projects with 'created' status
        if created_projects_response.data:
            stuck_projects.extend(created_projects_response.data)
            logger.info(
                f"📋 Found {len(created_projects_response.data)} project(s) with status 'created'"
            )

        # Check for projects stuck in 'processing' status
        if processing_projects_response.data:
            now = datetime.datetime.now(datetime.UTC)
            reset_count = 0

            for project in processing_projects_response.data:
                updated_at_str = project.get("updated_at")
                if updated_at_str:
                    try:
                        # Parse the timestamp
                        if isinstance(updated_at_str, str):
                            updated_at = datetime.datetime.fromisoformat(
                                updated_at_str.replace("Z", "+00:00")
                            )
                        else:
                            updated_at = updated_at_str

                        # Check if stuck for >30 minutes
                        time_diff = (now - updated_at).total_seconds() / 60  # minutes

                        if time_diff > 30:
                            # Reset to 'created' so it can be retried
                            project_id = project["project_id"]
                            logger.warning(
                                f"⚠️  Project {project.get('project_name', 'Unknown')} "
                                f"(project_id={project_id}) stuck in 'processing' for {time_diff:.1f} minutes. "
                                f"Resetting to 'created' status."
                            )
                            supabase.table("projects").update({"status": "created"}).eq(
                                "project_id", project_id
                            ).execute()
                            stuck_projects.append(project)
                            reset_count += 1
                        else:
                            logger.debug(
                                f"   Project {project.get('project_name', 'Unknown')} "
                                f"in 'processing' for {time_diff:.1f} minutes (still within limit)"
                            )
                    except Exception as parse_error:
                        logger.warning(
                            f"⚠️  Could not parse timestamp for project {project.get('project_id')}: {parse_error}"
                        )
                        # If we can't parse, assume it's stuck and reset
                        project_id = project["project_id"]
                        supabase.table("projects").update({"status": "created"}).eq(
                            "project_id", project_id
                        ).execute()
                        stuck_projects.append(project)
                        reset_count += 1

            if reset_count > 0:
                logger.info(
                    f"🔄 Reset {reset_count} stuck 'processing' project(s) to 'created' status"
                )

        if not stuck_projects:
            logger.info("✅ No stuck projects found")
            return

        logger.info(f"📋 Total stuck projects to resume: {len(stuck_projects)}")

        # Resume each stuck project
        for project in stuck_projects:
            project_id = project["project_id"]
            github_url = project["github_url"]
            project_name = project.get("project_name", "Unknown")
            created_at = project.get("created_at", "")

            try:
                logger.info(
                    f"🔄 Resuming project: {project_name} (project_id={project_id}, created_at={created_at})"
                )
                # Run pipeline in background (non-blocking)
                # Using asyncio.create_task ensures it runs asynchronously
                # The pipeline itself handles status updates and errors
                asyncio.create_task(
                    run_embedding_pipeline(
                        str(project_id),
                        github_url,
                        api_start_time=None,  # No API start time for recovery
                    )
                )
                logger.info(f"✅ Scheduled pipeline resume for project: {project_name}")
            except Exception as e:
                logger.error(
                    f"❌ Failed to resume project {project_name} (project_id={project_id}): {e}",
                    exc_info=True,
                )
                # Continue with other projects even if one fails

        logger.info(
            f"✅ Completed recovery check: {len(stuck_projects)} project(s) scheduled for resume"
        )

    except Exception as e:
        logger.error(f"❌ Error during stuck project recovery: {e}", exc_info=True)
        # Don't fail startup if recovery fails
        logger.warning("   Application will continue, but some projects may need manual recovery")


async def startup_services():
    """
    Initialize all services on application startup.
    Call this from FastAPI startup event.
    """
    logger.info("🚀 Initializing application services...")

    # Auto-start roadmap + workspaces containers when running backend locally
    if settings.roadmap_service_url:
        try:
            await asyncio.to_thread(_ensure_roadmap_container_sync)
        except Exception as e:
            logger.warning(f"⚠️  Roadmap auto-start check failed: {e}")
    try:
        await asyncio.to_thread(_ensure_workspaces_container_sync)
    except Exception as e:
        logger.warning(f"⚠️  Workspaces auto-start check failed: {e}")

    # Initialize rate limiter (will use Redis if available, fallback otherwise)
    try:
        await initialize_rate_limiter()
        logger.info("✅ Rate limiter initialized")
    except Exception as e:
        logger.warning(f"⚠️  Rate limiter initialization failed: {e}")
        logger.info("   Application will continue with reduced functionality")

    # Resume stuck projects (non-blocking, runs in background)
    try:
        await resume_stuck_projects()
    except Exception as e:
        logger.warning(f"⚠️  Stuck project recovery failed: {e}")
        # Don't block startup if recovery fails

    logger.info("✅ Startup services initialized")


async def shutdown_services():
    """
    Cleanup services on application shutdown.
    Call this from FastAPI shutdown event.
    """
    try:
        logger.info("🛑 Shutting down application services...")
        # Add any cleanup logic here
        logger.info("✅ Services shut down")
    except Exception as e:
        # Ignore cancellation errors during shutdown (normal when stopping with Ctrl+C)
        if "CancelledError" not in str(type(e).__name__):
            logger.warning(f"⚠️  Error during shutdown: {e}")
