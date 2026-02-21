"""
GitHub Consent API
Handles GitHub PAT storage and consent management for projects.
"""

import logging
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from supabase import Client

from app.core.supabase_client import get_supabase_client
from app.utils.clerk_auth import verify_clerk_token
from app.utils.db_helpers import get_project_if_accessible, get_user_id_from_clerk

router = APIRouter()
logger = logging.getLogger(__name__)


class GitHubConsentRequest(BaseModel):
    token: str = Field(..., description="GitHub Personal Access Token (PAT)")
    consent_accepted: bool = Field(..., description="Whether user accepted consent terms")
    github_username: str = Field(..., description="GitHub username")
    project_id: str = Field(..., description="Project ID")


class GitHubConsentCheck(BaseModel):
    label: str
    passed: bool
    detail: str | None = None


class GitHubConsentResponse(BaseModel):
    success: bool
    message: str
    github_username: str
    checks: list[GitHubConsentCheck]


async def validate_github_token(token: str) -> dict:
    """
    Validate GitHub PAT by calling GitHub API.
    Returns user info if token is valid.

    Args:
        token: GitHub PAT

    Returns:
        dict with 'login' (username) and 'id' (user ID)

    Raises:
        HTTPException if token is invalid
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )

            if response.status_code == 401:
                raise HTTPException(
                    status_code=401,
                    detail="Invalid GitHub token. Please check your Personal Access Token.",
                )

            if response.status_code == 403:
                raise HTTPException(
                    status_code=403,
                    detail="GitHub token lacks required permissions. Ensure your PAT has 'Contents' read/write access.",
                )

            if response.status_code != 200:
                logger.error(f"GitHub API error: {response.status_code} - {response.text}")
                raise HTTPException(status_code=500, detail="Failed to validate GitHub token")

            user_data = response.json()
            return {
                "login": user_data.get("login"),
                "id": user_data.get("id"),
                "name": user_data.get("name"),
                "email": user_data.get("email"),
            }

    except httpx.TimeoutException as e:
        raise HTTPException(status_code=500, detail="GitHub API timeout") from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error validating GitHub token: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to validate token: {str(e)}") from e


async def verify_token_has_repo_access(token: str, repo_url: str) -> bool:
    """
    Verify that the token has access to the specified repository.

    Args:
        token: GitHub PAT
        repo_url: Repository URL (e.g., https://github.com/username/repo)

    Returns:
        True if token has access, False otherwise
    """
    try:
        # Extract owner/repo from URL
        import re

        match = re.search(r"github\.com/([^/]+)/([^/]+)", repo_url)
        if not match:
            return False

        owner = match.group(1)
        repo = match.group(2).replace(".git", "")

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}",
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )

            return response.status_code == 200

    except Exception as e:
        logger.error(f"Error verifying repo access: {e}")
        return False


async def verify_repo_permissions_and_commit_access(
    token: str, repo_url: str
) -> tuple[bool, bool, str | None]:
    """
    Verify that the token can read the latest commit and has push permissions.
    """
    try:
        import re

        match = re.search(r"github\.com/([^/]+)/([^/]+)", repo_url)
        if not match:
            return False, False, None

        owner = match.group(1)
        repo = match.group(2).replace(".git", "")

        async with httpx.AsyncClient(timeout=10.0) as client:
            repo_response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}",
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            if repo_response.status_code != 200:
                return False, False, None

            repo_data = repo_response.json()
            permissions = repo_data.get("permissions", {})
            can_push = bool(permissions.get("push"))

            commits_response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/commits?per_page=1",
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            if commits_response.status_code != 200:
                return False, can_push, None

            commits = commits_response.json()
            latest_sha = commits[0].get("sha") if isinstance(commits, list) and commits else None
            can_read_commit = latest_sha is not None

            return can_read_commit, can_push, latest_sha
    except Exception as e:
        logger.error(f"Error verifying repo permissions/commits: {e}")
        return False, False, None


@router.post("/consent", response_model=GitHubConsentResponse)
async def store_github_consent(
    request: GitHubConsentRequest,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Store GitHub PAT and consent for a project.

    Flow:
    1. Verify user authentication
    2. Verify project ownership
    3. Validate GitHub token
    4. Extract username from token (override with provided username if different)
    5. Verify token has access to project's repository (if user_repo_url exists)
    6. Store token, username, consent, and timestamp in projects table
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])

        # Verify access (owner or employee) and get project/employee data
        project_data, is_owner = get_project_if_accessible(
            supabase,
            request.project_id,
            user_id,
            select_fields="project_id, github_username, user_repo_url, user_repo_first_commit, github_access_token, github_consent_accepted",
        )
        if not is_owner:
            pa = (
                supabase.table("project_access")
                .select("github_username, user_repo_url, user_repo_first_commit")
                .eq("project_id", request.project_id)
                .eq("user_id", user_id)
                .execute()
            )
            if pa.data:
                project_data = {
                    **project_data,
                    **{k: v for k, v in pa.data[0].items() if v is not None},
                }

        logger.info(f"Storing GitHub consent for project {request.project_id}, user {user_id}")

        # Get stored username from Task 1 (source of truth)
        stored_username = project_data.get("github_username")
        if not stored_username:
            raise HTTPException(
                status_code=400,
                detail="GitHub username not found. Please complete Task 1 (Verify Your GitHub Profile) first.",
            )

        # Validate GitHub token
        github_user_data = await validate_github_token(request.token)
        github_username_from_token = github_user_data.get("login")

        if not github_username_from_token:
            raise HTTPException(
                status_code=400, detail="Could not determine GitHub username from token"
            )

        # Validate username matches Task 1 username (source of truth)
        if github_username_from_token.lower() != stored_username.lower():
            raise HTTPException(
                status_code=403,
                detail=f"GitHub username mismatch. Token belongs to '@{github_username_from_token}', "
                f"but Task 1 verified '@{stored_username}'. "
                f"Please use a PAT token from the same GitHub account (@{stored_username}).",
            )

        # Verify token has access to user's repository (Task 2)
        user_repo_url = project_data.get("user_repo_url")
        if not user_repo_url:
            raise HTTPException(
                status_code=400,
                detail="Repository URL not found. Please complete Task 2 (Create Your Project Repository) first.",
            )

        has_access = await verify_token_has_repo_access(request.token, user_repo_url)
        if not has_access:
            raise HTTPException(
                status_code=403,
                detail=f"Token does not have access to repository: {user_repo_url}. "
                "Ensure your PAT is scoped to this repository with 'Contents' read/write permissions.",
            )

        (
            can_read_commit,
            can_push,
            latest_commit_sha,
        ) = await verify_repo_permissions_and_commit_access(request.token, user_repo_url)
        if not can_read_commit:
            raise HTTPException(
                status_code=403,
                detail="Token cannot read commits for the repository. "
                "Ensure your PAT has at least 'Contents: read' permission.",
            )
        if not can_push:
            raise HTTPException(
                status_code=403,
                detail="Token does not have push (write) permission for the repository. "
                "Ensure your PAT has 'Contents: read and write' permission.",
            )

        stored_commit_sha = project_data.get("user_repo_first_commit")
        if not stored_commit_sha:
            raise HTTPException(
                status_code=400,
                detail="Commit SHA not found. Please complete Task 3 (Make Your First Commit) first.",
            )
        if not latest_commit_sha or latest_commit_sha.lower() != stored_commit_sha.lower():
            raise HTTPException(
                status_code=403,
                detail="Latest repository commit does not match the commit you verified in Task 3. "
                "Please ensure your most recent commit is the one you submitted.",
            )

        # Validate consent
        if not request.consent_accepted:
            raise HTTPException(status_code=400, detail="Consent must be accepted to continue")

        update_data = {
            "github_access_token": request.token,
            "github_consent_accepted": True,
            "github_consent_timestamp": datetime.now(UTC).isoformat(),
        }

        if is_owner:
            result = (
                supabase.table("projects")
                .update(update_data)
                .eq("project_id", request.project_id)
                .eq("user_id", user_id)
                .execute()
            )
        else:
            result = (
                supabase.table("project_access")
                .update(update_data)
                .eq("project_id", request.project_id)
                .eq("user_id", user_id)
                .execute()
            )

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to update with GitHub consent")

        logger.info(
            f"✅ GitHub consent stored for project {request.project_id}, "
            f"username: {stored_username} (validated against PAT token)"
        )

        checks = [
            GitHubConsentCheck(label="Token validated", passed=True, detail="PAT verified"),
            GitHubConsentCheck(
                label="Username matches Task 1",
                passed=True,
                detail=f"@{github_username_from_token}",
            ),
            GitHubConsentCheck(
                label="Repository access verified", passed=True, detail=user_repo_url
            ),
            GitHubConsentCheck(
                label="Commit read access", passed=True, detail="Latest commit readable"
            ),
            GitHubConsentCheck(
                label="Latest commit matches Task 3",
                passed=True,
                detail=latest_commit_sha[:7] if latest_commit_sha else None,
            ),
            GitHubConsentCheck(
                label="Push permission", passed=True, detail="Contents: read and write"
            ),
            GitHubConsentCheck(label="Consent recorded", passed=True, detail="Terms accepted"),
        ]

        return GitHubConsentResponse(
            success=True,
            message="GitHub account connected successfully",
            github_username=stored_username,
            checks=checks,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error storing GitHub consent: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to store GitHub consent: {str(e)}"
        ) from e


@router.get("/consent/status")
async def get_consent_status(
    project_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Get GitHub consent status for a project.

    Returns:
        {
            "has_consent": bool,
            "has_token": bool,
            "github_username": str | null,
            "consent_timestamp": str | null
        }
    """
    try:
        user_id = get_user_id_from_clerk(supabase, user_info["clerk_user_id"])

        project_data, is_owner = get_project_if_accessible(
            supabase,
            project_id,
            user_id,
            select_fields="github_access_token, github_username, github_consent_accepted, github_consent_timestamp",
        )
        if not is_owner:
            pa = (
                supabase.table("project_access")
                .select(
                    "github_access_token, github_username, github_consent_accepted, github_consent_timestamp"
                )
                .eq("project_id", project_id)
                .eq("user_id", user_id)
                .execute()
            )
            if pa.data:
                project_data = {
                    **project_data,
                    **{k: v for k, v in pa.data[0].items() if v is not None},
                }

        return {
            "has_consent": project_data.get("github_consent_accepted", False),
            "has_token": bool(project_data.get("github_access_token")),
            "github_username": project_data.get("github_username"),
            "consent_timestamp": project_data.get("github_consent_timestamp"),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting consent status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get consent status: {str(e)}"
        ) from e
