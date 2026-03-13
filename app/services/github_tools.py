"""
GitHub API Tools
Function implementations for GitHub API operations used by verification agent.
"""

import base64
import logging
from typing import Any

import httpx

from app.services.github_service import extract_repo_info

logger = logging.getLogger(__name__)

# Hardcoded filtering for obvious build artifacts (node_modules, .git, etc.)
# These are always filtered out. LLM will filter the remaining files based on task relevance.
IGNORED_PATTERNS = [
    "node_modules/",
    ".git/",
    ".next/",
    "dist/",
    "build/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    "coverage/",
    ".coverage",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
    "Thumbs.db",
]


def _should_ignore_file(file_path: str) -> bool:
    """Check if a file should be ignored (hardcoded patterns only)."""
    file_path_lower = file_path.lower()
    for pattern in IGNORED_PATTERNS:
        if pattern.endswith("/"):
            # Directory pattern (e.g., "node_modules/")
            if pattern[:-1] in file_path_lower.split("/"):
                return True
        elif pattern.startswith("*"):
            # Extension pattern (e.g., "*.pyc")
            if file_path_lower.endswith(pattern[1:]):
                return True
        else:
            # Exact match
            if pattern in file_path_lower:
                return True
    return False


def _filter_build_artifacts(files: list[str | dict]) -> tuple[list, int]:
    """
    Filter out hardcoded build artifacts (node_modules, .git, etc.).
    Returns filtered files and count of ignored files.

    Args:
        files: List of file paths (strings) or file dicts with 'filename' key

    Returns:
        Tuple of (filtered_files, ignored_count)
    """
    filtered = []
    ignored_count = 0

    for item in files:
        if isinstance(item, dict):
            filename = item.get("filename", "")
        else:
            filename = item

        if _should_ignore_file(filename):
            ignored_count += 1
            continue

        filtered.append(item)

    return filtered, ignored_count


def get_github_tools() -> list[dict[str, Any]]:
    """
    Get list of GitHub API tools in OpenAI function calling format.

    Returns:
        List of tool definitions for Groq agent (OpenAI-compatible format)
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "compare_commits",
                "description": "Compare two commits in a GitHub repository. Returns metadata (filename, status, additions/deletions) for changed files (build artifacts like node_modules are automatically filtered). You should analyze which files are relevant to the task and use get_file_contents to fetch specific file contents as needed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo_url": {
                            "type": "string",
                            "description": "Full GitHub repository URL (e.g., https://github.com/owner/repo)",
                        },
                        "base_commit": {
                            "type": "string",
                            "description": "Base commit SHA or branch name (e.g., 'abc123' or 'main')",
                        },
                        "head_commit": {
                            "type": "string",
                            "description": "Head commit SHA or branch name (e.g., 'def456' or 'HEAD')",
                        },
                    },
                    "required": ["repo_url", "base_commit", "head_commit"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_file_contents",
                "description": "Get the contents of a specific file from a GitHub repository at a given commit. Use this after analyzing file metadata from compare_commits or get_commit_details to fetch only files relevant to the task verification.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo_url": {
                            "type": "string",
                            "description": "Full GitHub repository URL",
                        },
                        "file_path": {
                            "type": "string",
                            "description": "Path to the file in the repository (e.g., 'src/main.py')",
                        },
                        "commit_sha": {
                            "type": "string",
                            "description": "Optional commit SHA or branch name. If not provided, uses default branch.",
                        },
                    },
                    "required": ["repo_url", "file_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_commit_details",
                "description": "Get detailed information about a specific commit including message, author, files changed (with metadata), and statistics. Returns metadata for ALL files. You should analyze which files are relevant to the task and use get_file_contents to fetch specific file contents as needed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo_url": {
                            "type": "string",
                            "description": "Full GitHub repository URL",
                        },
                        "commit_sha": {
                            "type": "string",
                            "description": "Commit SHA to get details for",
                        },
                    },
                    "required": ["repo_url", "commit_sha"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_changed_files",
                "description": "List all files that changed between two commits with their status (added/modified/deleted) and metadata. Returns metadata for files (build artifacts like node_modules are automatically filtered). You should analyze which files are relevant to the task and use get_file_contents to fetch specific file contents as needed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo_url": {
                            "type": "string",
                            "description": "Full GitHub repository URL",
                        },
                        "base_commit": {
                            "type": "string",
                            "description": "Base commit SHA or branch name",
                        },
                        "head_commit": {
                            "type": "string",
                            "description": "Head commit SHA or branch name",
                        },
                    },
                    "required": ["repo_url", "base_commit", "head_commit"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_repository_files",
                "description": "List all files in the repository at a specific commit/branch. Returns file paths only (metadata). Use this to see what files exist in the repository, even if they weren't changed. Build artifacts like node_modules are automatically filtered.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo_url": {
                            "type": "string",
                            "description": "Full GitHub repository URL",
                        },
                        "commit_sha": {
                            "type": "string",
                            "description": "Optional commit SHA or branch name. If not provided, uses default branch.",
                        },
                        "path": {
                            "type": "string",
                            "description": "Optional path to list files from (e.g., 'src' or 'routes'). If not provided, lists from repository root.",
                        },
                    },
                    "required": ["repo_url"],
                },
            },
        },
    ]


async def execute_github_tool(
    tool_name: str, arguments: dict[str, Any], github_token: str | None = None
) -> dict[str, Any]:
    """
    Execute a GitHub API tool and return the result.

    Uses app's GitHub token (GIT_ACCESS_TOKEN from .env) for authenticated calls.
    Agent NEVER receives user's PAT (stored in DB).
    All tools operate on the notebook repo (user_repo_url).

    Args:
        tool_name: Name of the tool to execute
        arguments: Tool arguments (from agent's tool call)
        github_token: App's GitHub token from .env (GIT_ACCESS_TOKEN), not user's PAT

    Returns:
        Tool execution result dict

    Raises:
        ValueError: If tool_name is unknown or arguments are invalid
        httpx.HTTPError: If GitHub API request fails
    """
    # Use app's GitHub token for authenticated calls (better rate limits)
    headers = {
        "Accept": "application/vnd.github.v3+json",
    }
    if github_token:
        headers["Authorization"] = f"token {github_token}"

    logger.info(f"🔨 Executing GitHub tool: {tool_name}")
    logger.debug(f"   Repository: {arguments.get('repo_url', 'N/A')}")

    try:
        if tool_name == "compare_commits":
            logger.info(
                f"   📊 Comparing commits: {arguments.get('base_commit', '')[:8]}...{arguments.get('head_commit', '')[:8]}"
            )
            result = await _compare_commits(
                repo_url=arguments["repo_url"],
                base_commit=arguments["base_commit"],
                head_commit=arguments["head_commit"],
                headers=headers,
            )
            logger.debug("   ✅ Compare commits completed")
            return result
        elif tool_name == "get_file_contents":
            logger.info(
                f"   📄 Getting file: {arguments.get('file_path', 'N/A')} at commit {arguments.get('commit_sha', 'HEAD')[:8] if arguments.get('commit_sha') else 'HEAD'}"
            )
            result = await _get_file_contents(
                repo_url=arguments["repo_url"],
                file_path=arguments["file_path"],
                commit_sha=arguments.get("commit_sha"),
                headers=headers,
            )
            logger.debug("   ✅ Get file contents completed")
            return result
        elif tool_name == "get_commit_details":
            logger.info(f"   📝 Getting commit details: {arguments.get('commit_sha', '')[:8]}")
            result = await _get_commit_details(
                repo_url=arguments["repo_url"],
                commit_sha=arguments["commit_sha"],
                headers=headers,
            )
            logger.debug("   ✅ Get commit details completed")
            return result
        elif tool_name == "list_changed_files":
            logger.info(
                f"   📁 Listing changed files between {arguments.get('base_commit', '')[:8]}...{arguments.get('head_commit', '')[:8]}"
            )
            result = await _list_changed_files(
                repo_url=arguments["repo_url"],
                base_commit=arguments["base_commit"],
                head_commit=arguments["head_commit"],
                headers=headers,
            )
            logger.debug("   ✅ List changed files completed")
            return result
        elif tool_name == "list_repository_files":
            logger.info(
                f"   📂 Listing repository files at {arguments.get('commit_sha', 'default branch')[:8] if arguments.get('commit_sha') else 'default branch'}"
            )
            result = await _list_repository_files(
                repo_url=arguments["repo_url"],
                commit_sha=arguments.get("commit_sha"),
                path=arguments.get("path", ""),
                headers=headers,
            )
            logger.debug("   ✅ List repository files completed")
            return result
        else:
            logger.error(f"   ❌ Unknown tool: {tool_name}")
            raise ValueError(f"Unknown tool: {tool_name}")

    except KeyError as e:
        raise ValueError(f"Missing required argument: {e}") from e
    except Exception as e:
        logger.error(f"Error executing GitHub tool {tool_name}: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "tool": tool_name,
        }


async def _compare_commits(
    repo_url: str, base_commit: str, head_commit: str, headers: dict[str, str]
) -> dict[str, Any]:
    """
    Compare two commits using GitHub API.

    Returns:
        {
            "success": bool,
            "diff": str,
            "files_changed": list[str],
            "stats": dict,
            "commits": list[dict]
        }
    """
    try:
        owner, repo = extract_repo_info(repo_url)
    except Exception as e:
        return {"success": False, "error": f"Invalid repo URL: {e}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # GitHub compare API: GET /repos/{owner}/{repo}/compare/{base}...{head}
            compare_url = (
                f"https://api.github.com/repos/{owner}/{repo}/compare/{base_commit}...{head_commit}"
            )
            response = await client.get(compare_url, headers=headers)
            response.raise_for_status()

            data = response.json()

            # Extract file changes (metadata only)
            all_files = []
            for file in data.get("files", []):
                all_files.append(
                    {
                        "filename": file.get("filename", ""),
                        "status": file.get("status", ""),  # added, modified, removed, renamed
                        "additions": file.get("additions", 0),
                        "deletions": file.get("deletions", 0),
                        "changes": file.get("changes", 0),
                    }
                )

            # Filter out build artifacts (node_modules, .git, etc.)
            files_changed, ignored_count = _filter_build_artifacts(all_files)

            # Extract stats
            stats = data.get("stats", {})

            # Get diff (if available)
            diff = ""
            if files_changed:
                # For large diffs, GitHub API doesn't include full diff in compare endpoint
                diff = f"Diff metadata available for {len(files_changed)} files (after filtering build artifacts). Use get_file_contents to fetch specific file contents."

            logger.info(
                f"   📋 Returning metadata for {len(files_changed)} files (filtered {ignored_count} build artifacts, LLM will decide which to examine)"
            )

            return {
                "success": True,
                "diff": diff,
                "files_changed": files_changed,  # Filtered files with metadata - LLM filters further
                "stats": {
                    "additions": stats.get("additions", 0),
                    "deletions": stats.get("deletions", 0),
                    "total": stats.get("total", 0),
                },
                "commits": [
                    {
                        "sha": c.get("sha", ""),
                        "message": c.get("commit", {}).get("message", ""),
                        "author": c.get("commit", {}).get("author", {}).get("name", ""),
                    }
                    for c in data.get("commits", [])
                ],
            }

        except httpx.HTTPStatusError as e:
            logger.error(f"GitHub API error: {e.response.status_code} - {e.response.text}")
            error_msg = f"GitHub API error: {e.response.status_code}"

            if e.response.status_code == 401:
                error_msg = "Authentication failed (401). The GitHub token is invalid or expired. Please check your GitHub connection."
            elif e.response.status_code == 403:
                error_msg = "Access denied (403). The GitHub token does not have permission to access this repository. If the repository is private, please ensure you have connected your GitHub account with a Personal Access Token (PAT) that has 'repo' scope."
            elif e.response.status_code == 404:
                error_msg = "Repository or commit not found (404). Please check if the repository exists and is accessible."

            return {
                "success": False,
                "error": error_msg,
                "details": e.response.text[:500],
            }
        except Exception as e:
            logger.error(f"Failed to compare commits: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


async def _get_file_contents(
    repo_url: str, file_path: str, commit_sha: str | None, headers: dict[str, str]
) -> dict[str, Any]:
    """
    Get file contents from GitHub repository.

    Returns:
        {
            "success": bool,
            "content": str,
            "encoding": str,
            "size": int,
            "sha": str
        }
    """
    try:
        owner, repo = extract_repo_info(repo_url)
    except Exception as e:
        return {"success": False, "error": f"Invalid repo URL: {e}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # GitHub contents API: GET /repos/{owner}/{repo}/contents/{path}
            url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"
            params = {}
            if commit_sha:
                params["ref"] = commit_sha

            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()

            data = response.json()

            # Decode content if base64 encoded
            content = ""
            encoding = data.get("encoding", "")
            if encoding == "base64":
                content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="ignore")
            elif encoding == "none":
                content = data.get("content", "")

            return {
                "success": True,
                "content": content,
                "encoding": encoding,
                "size": data.get("size", 0),
                "sha": data.get("sha", ""),
            }

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {"success": False, "error": "File not found"}

            logger.error(f"GitHub API error: {e.response.status_code} - {e.response.text}")
            error_msg = f"GitHub API error: {e.response.status_code}"

            if e.response.status_code == 401:
                error_msg = "Authentication failed (401). The GitHub token is invalid or expired."
            elif e.response.status_code == 403:
                error_msg = "Access denied (403). The GitHub token does not have permission to access this file."

            return {
                "success": False,
                "error": error_msg,
                "details": e.response.text[:500],
            }
        except Exception as e:
            logger.error(f"Failed to get file contents: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


async def _get_commit_details(
    repo_url: str, commit_sha: str, headers: dict[str, str]
) -> dict[str, Any]:
    """
    Get detailed information about a commit.

    Returns:
        {
            "success": bool,
            "sha": str,
            "message": str,
            "author": dict,
            "files": list[dict],
            "stats": dict
        }
    """
    try:
        owner, repo = extract_repo_info(repo_url)
    except Exception as e:
        return {"success": False, "error": f"Invalid repo URL: {e}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # GitHub commit API: GET /repos/{owner}/{repo}/commits/{sha}
            url = f"https://api.github.com/repos/{owner}/{repo}/commits/{commit_sha}"
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            data = response.json()

            # Extract file changes (metadata only)
            all_files = []
            for file in data.get("files", []):
                all_files.append(
                    {
                        "filename": file.get("filename", ""),
                        "status": file.get("status", ""),  # added, modified, deleted
                        "additions": file.get("additions", 0),
                        "deletions": file.get("deletions", 0),
                        "changes": file.get("changes", 0),
                    }
                )

            # Filter out build artifacts (node_modules, .git, etc.)
            files, ignored_count = _filter_build_artifacts(all_files)

            commit = data.get("commit", {})
            author = commit.get("author", {})

            logger.info(
                f"   📋 Returning metadata for {len(files)} files (filtered {ignored_count} build artifacts, LLM will decide which to examine)"
            )

            return {
                "success": True,
                "sha": data.get("sha", ""),
                "message": commit.get("message", ""),
                "author": {
                    "name": author.get("name", ""),
                    "email": author.get("email", ""),
                    "date": author.get("date", ""),
                },
                "files": files,  # Filtered files with metadata - LLM filters further
                "stats": data.get("stats", {}),
            }

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {"success": False, "error": "Commit not found"}

            logger.error(f"GitHub API error: {e.response.status_code} - {e.response.text}")
            error_msg = f"GitHub API error: {e.response.status_code}"

            if e.response.status_code == 401:
                error_msg = "Authentication failed (401). The GitHub token is invalid or expired."
            elif e.response.status_code == 403:
                error_msg = "Access denied (403). The GitHub token does not have permission to access this commit."

            return {
                "success": False,
                "error": error_msg,
                "details": e.response.text[:500],
            }
        except Exception as e:
            logger.error(f"Failed to get commit details: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


async def _list_changed_files(
    repo_url: str, base_commit: str, head_commit: str, headers: dict[str, str]
) -> dict[str, Any]:
    """
    List all changed files between two commits.

    Returns:
        {
            "success": bool,
            "files": list[dict],
            "total_changes": int
        }
    """
    try:
        owner, repo = extract_repo_info(repo_url)
    except Exception as e:
        return {"success": False, "error": f"Invalid repo URL: {e}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # Use compare API to get changed files
            compare_url = (
                f"https://api.github.com/repos/{owner}/{repo}/compare/{base_commit}...{head_commit}"
            )
            response = await client.get(compare_url, headers=headers)
            response.raise_for_status()

            data = response.json()

            # Extract file changes (metadata only)
            all_files = []
            for file in data.get("files", []):
                all_files.append(
                    {
                        "filename": file.get("filename", ""),
                        "status": file.get("status", ""),  # added, modified, removed, renamed
                        "additions": file.get("additions", 0),
                        "deletions": file.get("deletions", 0),
                        "changes": file.get("changes", 0),
                    }
                )

            # Filter out build artifacts (node_modules, .git, etc.)
            files, ignored_count = _filter_build_artifacts(all_files)

            stats = data.get("stats", {})

            logger.info(
                f"   📋 Returning metadata for {len(files)} files (filtered {ignored_count} build artifacts, LLM will decide which to examine)"
            )

            return {
                "success": True,
                "files": files,  # Filtered files with metadata - LLM filters further
                "total_changes": len(files),
                "additions": stats.get("additions", 0),
                "deletions": stats.get("deletions", 0),
            }

        except httpx.HTTPStatusError as e:
            logger.error(f"GitHub API error: {e.response.status_code} - {e.response.text}")
            error_msg = f"GitHub API error: {e.response.status_code}"

            if e.response.status_code == 401:
                error_msg = "Authentication failed (401). The GitHub token is invalid or expired."
            elif e.response.status_code == 403:
                error_msg = "Access denied (403). The GitHub token does not have permission to list changed files."

            return {
                "success": False,
                "error": error_msg,
                "details": e.response.text[:500],
            }
        except Exception as e:
            logger.error(f"Failed to list changed files: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


async def _list_repository_files(
    repo_url: str, commit_sha: str | None, path: str, headers: dict[str, str]
) -> dict[str, Any]:
    """
    List all files in the repository at a specific commit/branch.

    Returns:
        {
            "success": bool,
            "files": list[str],
            "total_files": int
        }
    """
    try:
        owner, repo = extract_repo_info(repo_url)
    except Exception as e:
        return {"success": False, "error": f"Invalid repo URL: {e}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # Get default branch if commit_sha not provided
            if not commit_sha:
                repo_resp = await client.get(
                    f"https://api.github.com/repos/{owner}/{repo}", headers=headers
                )
                repo_resp.raise_for_status()
                commit_sha = repo_resp.json()["default_branch"]

            # Get repository tree (recursive)
            tree_url = (
                f"https://api.github.com/repos/{owner}/{repo}/git/trees/{commit_sha}?recursive=1"
            )
            response = await client.get(tree_url, headers=headers)
            response.raise_for_status()

            tree_data = response.json()
            all_files = []

            for item in tree_data.get("tree", []):
                if item.get("type") == "blob":  # Only files, not directories
                    file_path = item.get("path", "")
                    # Filter by path if provided
                    if path and not file_path.startswith(path):
                        continue
                    all_files.append(file_path)

            # Filter out build artifacts
            files, ignored_count = _filter_build_artifacts(all_files)

            logger.info(
                f"   📋 Returning {len(files)} file(s) (filtered {ignored_count} build artifacts)"
            )

            return {
                "success": True,
                "files": files,
                "total_files": len(files),
            }

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {"success": False, "error": "Commit or branch not found"}

            logger.error(f"GitHub API error: {e.response.status_code} - {e.response.text}")
            error_msg = f"GitHub API error: {e.response.status_code}"

            if e.response.status_code == 401:
                error_msg = "Authentication failed (401). The GitHub token is invalid or expired."
            elif e.response.status_code == 403:
                error_msg = "Access denied (403). The GitHub token does not have permission to list repository files."

            return {
                "success": False,
                "error": error_msg,
                "details": e.response.text[:500],
            }
        except Exception as e:
            logger.error(f"Failed to list repository files: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
