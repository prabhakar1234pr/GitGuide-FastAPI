"""
Repository context utilities for task generation.
Builds context from NOTEBOOK REPO (user_repo_url), not textbook repo.

Token budgeting: repo_structure and repo_code_context are truncated to stay
under model input limits (e.g. 20K). Excludes node_modules, .git, etc.
"""

import base64
import logging
from typing import Any

import httpx

from app.services.github_service import extract_repo_info
from app.utils.text_chunking import count_tokens, truncate_to_tokens

logger = logging.getLogger(__name__)

# Exclude paths that bloat token count (node_modules, .git, venv, etc.)
_REPO_STRUCTURE_EXCLUDE_PREFIXES = (
    "node_modules/",
    ".git/",
    "venv/",
    ".venv/",
    "__pycache__/",
    "dist/",
    "build/",
    ".next/",
    ".nuxt/",
    "coverage/",
    ".cache/",
    ".pytest_cache/",
    ".mypy_cache/",
    "vendor/",
)

# Max tokens for repo_structure to avoid exceeding model input limit (20K)
REPO_STRUCTURE_MAX_TOKENS = 4_000
REPO_CODE_CONTEXT_MAX_TOKENS = 2_000


def _truncate_to_token_limit(text: str, max_tokens: int, label: str) -> str:
    """Truncate text to max_tokens to avoid model input limit errors."""
    if not text:
        return text
    tokens = count_tokens(text)
    if tokens <= max_tokens:
        return text
    truncated = truncate_to_tokens(text, max_tokens)
    logger.info(f"   Truncated {label}: {tokens} -> {max_tokens} tokens")
    return truncated


def _detect_test_structure(repo_files: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Detect existing test framework and structure from notebook repo files.

    Args:
        repo_files: List of files from repository with 'path' and optionally 'content'

    Returns:
        {
            "framework": "pytest" | "jest" | "mocha" | "vitest" | "unittest" | "none",
            "test_directories": ["tests/", "__tests__/"],
            "config_files": ["pytest.ini", "jest.config.js"],
            "test_command": "pytest tests/ -v",
            "test_file_pattern": "test_*.py",
            "has_test_setup": True,
            "package_manager": "pip" | "npm" | "yarn" | "none",
            "test_dependencies": ["pytest"],
            "examples": ["tests/test_example.py"]
        }
    """
    test_structure = {
        "framework": "none",
        "test_directories": [],
        "config_files": [],
        "test_command": None,
        "test_file_pattern": None,
        "has_test_setup": False,
        "package_manager": "none",
        "test_dependencies": [],
        "examples": [],
    }

    file_paths = [f.get("path", "") for f in repo_files]
    file_contents = {f.get("path", ""): f.get("content", "") for f in repo_files}

    # Detect Python test frameworks
    if any("pytest.ini" in path or "pyproject.toml" in path for path in file_paths):
        test_structure["framework"] = "pytest"
        test_structure["test_command"] = "pytest"
        test_structure["test_file_pattern"] = "test_*.py"
        test_structure["has_test_setup"] = True
        test_structure["package_manager"] = "pip"
        test_structure["test_dependencies"].append("pytest")

        if "pytest.ini" in file_contents:
            test_structure["config_files"].append("pytest.ini")
        if "pyproject.toml" in file_contents:
            test_structure["config_files"].append("pyproject.toml")

    # Check for unittest (Python standard library)
    elif any(path.endswith("test_*.py") or path.startswith("test_") for path in file_paths):
        test_structure["framework"] = "unittest"
        test_structure["test_command"] = "python -m unittest discover"
        test_structure["test_file_pattern"] = "test_*.py"
        test_structure["has_test_setup"] = True
        test_structure["package_manager"] = "pip"

    # Detect JavaScript/TypeScript test frameworks
    package_json_content = file_contents.get("package.json", "")
    if package_json_content:
        test_structure["package_manager"] = "npm"

        # Check for Jest
        if "jest" in package_json_content.lower() or '"jest"' in package_json_content:
            test_structure["framework"] = "jest"
            test_structure["test_command"] = "npm test"
            test_structure["test_file_pattern"] = "*.test.js"
            test_structure["has_test_setup"] = True
            test_structure["test_dependencies"].append("jest")

            if "jest.config.js" in file_paths:
                test_structure["config_files"].append("jest.config.js")

        # Check for Mocha
        elif "mocha" in package_json_content.lower():
            test_structure["framework"] = "mocha"
            test_structure["test_command"] = "npm test"
            test_structure["test_file_pattern"] = "*.test.js"
            test_structure["has_test_setup"] = True
            test_structure["test_dependencies"].append("mocha")

        # Check for Vitest
        elif "vitest" in package_json_content.lower():
            test_structure["framework"] = "vitest"
            test_structure["test_command"] = "npm test"
            test_structure["test_file_pattern"] = "*.test.ts"
            test_structure["has_test_setup"] = True
            test_structure["test_dependencies"].append("vitest")

    # Detect test directories
    test_dirs = ["tests/", "__tests__/", "test/", "spec/"]
    for dir_path in test_dirs:
        if any(path.startswith(dir_path) for path in file_paths):
            test_structure["test_directories"].append(dir_path)

    # Find example test files
    test_examples = [
        path
        for path in file_paths
        if any(
            path.startswith(test_dir) and ("test" in path.lower() or "spec" in path.lower())
            for test_dir in test_dirs
        )
    ]
    test_structure["examples"] = test_examples[:3]  # Limit to 3 examples

    # Infer framework from test directory structure if not detected
    if test_structure["framework"] == "none" and test_structure["test_directories"]:
        # Default to pytest for Python, jest for JS
        if any(path.endswith(".py") for path in file_paths):
            test_structure["framework"] = "pytest"
            test_structure["test_command"] = "pytest"
            test_structure["test_file_pattern"] = "test_*.py"
        elif any(path.endswith((".js", ".ts", ".jsx", ".tsx")) for path in file_paths):
            test_structure["framework"] = "jest"
            test_structure["test_command"] = "npm test"
            test_structure["test_file_pattern"] = "*.test.js"

    return test_structure


async def build_notebook_repo_context_for_task_generation(
    project_id: str,
    concept_metadata: dict[str, Any],
    user_repo_url: str,
    github_token: str | None = None,
) -> dict[str, Any]:
    """
    Build repository context from NOTEBOOK REPO (user_repo_url).

    This is the repository where the user is building their project,
    NOT the textbook repo used for curriculum planning.

    Args:
        project_id: Project ID
        concept_metadata: Concept metadata (includes repo_anchors from textbook repo - informational only)
        user_repo_url: User's notebook repository URL (where they're building)
        github_token: GitHub token for API access

    Returns:
        {
            "repo_structure": "...",  # File tree from notebook repo
            "repo_code_context": "...",  # File contents from notebook repo
            "existing_test_structure": {...}  # Test framework detection
        }
    """
    logger.info(f"Building notebook repo context from: {user_repo_url}")

    try:
        owner, repo = extract_repo_info(user_repo_url)

        headers = {}
        if github_token:
            headers["Authorization"] = f"token {github_token}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get default branch
            repo_resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}",
                headers=headers,
            )

            if repo_resp.status_code == 404:
                logger.warning(f"Notebook repo not found or empty: {user_repo_url}")
                return {
                    "repo_structure": "Repository is new/empty",
                    "repo_code_context": "No existing code",
                    "existing_test_structure": {
                        "framework": "none",
                        "test_directories": [],
                        "config_files": [],
                        "test_command": None,
                        "has_test_setup": False,
                    },
                }

            repo_resp.raise_for_status()
            repo_data = repo_resp.json()
            default_branch = repo_data.get("default_branch", "main")

            # Fetch repository tree
            tree_resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/git/trees/{default_branch}?recursive=1",
                headers=headers,
            )

            if tree_resp.status_code == 404:
                logger.warning(f"Branch {default_branch} not found in notebook repo")
                return {
                    "repo_structure": "Repository is new/empty",
                    "repo_code_context": "No existing code",
                    "existing_test_structure": {
                        "framework": "none",
                        "test_directories": [],
                        "config_files": [],
                        "test_command": None,
                        "has_test_setup": False,
                    },
                }

            tree_resp.raise_for_status()
            tree_data = tree_resp.json()

            # Build file structure tree
            repo_structure_parts = []
            files_by_path = {}

            for item in tree_data.get("tree", []):
                file_path = item.get("path", "")
                item_type = item.get("type", "")

                # Skip paths that bloat token count (node_modules, .git, etc.)
                if any(
                    file_path.startswith(p) or f"/{p}" in f"/{file_path}"
                    for p in _REPO_STRUCTURE_EXCLUDE_PREFIXES
                ):
                    continue

                if item_type == "tree":
                    repo_structure_parts.append(f"{file_path}/")
                elif item_type == "blob":
                    repo_structure_parts.append(file_path)
                    files_by_path[file_path] = item

            repo_structure_raw = "\n".join(sorted(repo_structure_parts))
            repo_structure = _truncate_to_token_limit(
                repo_structure_raw, REPO_STRUCTURE_MAX_TOKENS, "repo_structure"
            )

            # Get file contents for key files (limit to avoid token bloat)
            key_files = [
                "package.json",
                "requirements.txt",
                "pyproject.toml",
                "pytest.ini",
                "jest.config.js",
                "README.md",
            ]

            repo_code_context_parts = []
            repo_files_for_test_detection = []

            for file_path in key_files:
                if file_path in files_by_path:
                    item = files_by_path[file_path]
                    blob_sha = item.get("sha")
                    if blob_sha:
                        blob_resp = await client.get(
                            f"https://api.github.com/repos/{owner}/{repo}/git/blobs/{blob_sha}",
                            headers=headers,
                        )
                        blob_resp.raise_for_status()
                        blob_data = blob_resp.json()

                        if blob_data.get("encoding") == "base64":
                            content = base64.b64decode(blob_data.get("content", "")).decode("utf-8")
                            repo_code_context_parts.append(f"=== {file_path} ===\n{content}\n")
                            repo_files_for_test_detection.append(
                                {
                                    "path": file_path,
                                    "content": content,
                                }
                            )

            repo_code_context_raw = (
                "\n".join(repo_code_context_parts)
                if repo_code_context_parts
                else "No key configuration files found"
            )
            repo_code_context = _truncate_to_token_limit(
                repo_code_context_raw, REPO_CODE_CONTEXT_MAX_TOKENS, "repo_code_context"
            )

            # Detect test structure
            all_repo_files = [
                {"path": path, "content": ""}
                for path in repo_structure_parts
                if "/" not in path or path.endswith("/")
            ]
            all_repo_files.extend(repo_files_for_test_detection)
            existing_test_structure = _detect_test_structure(all_repo_files)

            logger.info(
                f"Notebook repo context built: {len(repo_structure_parts)} items, "
                f"test framework: {existing_test_structure['framework']}"
            )

            return {
                "repo_structure": repo_structure,
                "repo_code_context": repo_code_context,
                "existing_test_structure": existing_test_structure,
            }

    except Exception as e:
        logger.error(f"Failed to build notebook repo context: {e}", exc_info=True)
        # Return minimal context on error
        return {
            "repo_structure": "Error fetching repository structure",
            "repo_code_context": "Error fetching code context",
            "existing_test_structure": {
                "framework": "none",
                "test_directories": [],
                "config_files": [],
                "test_command": None,
                "has_test_setup": False,
            },
        }
