"""
Tests for GitHub Evidence Collector service.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

pytest.importorskip("app.services.github_evidence", reason="github_evidence module not implemented")

from app.services.github_evidence import GitHubEvidenceCollector


class TestGitHubEvidenceCollector:
    """Test GitHub Evidence Collector functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.collector = GitHubEvidenceCollector(github_token="test_token")

    @pytest.mark.asyncio
    async def test_get_repo_baseline_success(self):
        """Test successful repo baseline retrieval."""
        mock_repo_data = {
            "default_branch": "main",
        }

        mock_tree_data = {
            "tree": [
                {"type": "blob", "path": "file1.py", "sha": "sha1"},
                {"type": "blob", "path": "file2.py", "sha": "sha2"},
                {"type": "tree", "path": "tests"},
            ]
        }

        mock_blob_data = {
            "encoding": "base64",
            "content": "ZGVmIGhlbGxvKCk6CiAgICBwYXNz",  # base64 for "def hello():\n    pass"
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock repo info call
            mock_repo_resp = Mock()
            mock_repo_resp.json.return_value = mock_repo_data
            mock_repo_resp.raise_for_status = Mock()

            # Mock tree call
            mock_tree_resp = Mock()
            mock_tree_resp.json.return_value = mock_tree_data
            mock_tree_resp.raise_for_status = Mock()

            # Mock blob calls
            mock_blob_resp = Mock()
            mock_blob_resp.json.return_value = mock_blob_data
            mock_blob_resp.raise_for_status = Mock()

            mock_client.get = AsyncMock(
                side_effect=[mock_repo_resp, mock_tree_resp, mock_blob_resp, mock_blob_resp]
            )

            result = await self.collector.get_repo_baseline(
                user_repo_url="https://github.com/user/repo",
                file_paths=["file1.py", "file2.py"],
            )

            assert "files" in result
            assert "repo_structure" in result
            assert result["default_branch"] == "main"

    @pytest.mark.asyncio
    async def test_get_repo_baseline_not_found(self):
        """Test repo baseline when repo not found."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock 404 response
            mock_resp = Mock()
            mock_resp.status_code = 404
            mock_resp.raise_for_status.side_effect = Exception("404 Not Found")

            mock_client.get = AsyncMock(side_effect=Exception("404 Not Found"))

            # Should handle gracefully
            result = await self.collector.get_repo_baseline(
                user_repo_url="https://github.com/user/nonexistent"
            )

            assert result["files"] == {}
            assert result["repo_structure"] == []

    @pytest.mark.asyncio
    async def test_compare_file_structure(self):
        """Test file structure comparison."""
        mock_baseline = {
            "files": {},
            "repo_structure": ["file1.py", "file2.py", "tests/"],
            "default_branch": "main",
        }

        with patch.object(
            self.collector, "get_repo_baseline", new_callable=AsyncMock, return_value=mock_baseline
        ):
            result = await self.collector.compare_file_structure(
                user_repo_url="https://github.com/user/repo",
                expected_files=["file1.py", "file2.py", "missing.py"],
            )

            assert "missing_files" in result
            assert "existing_files" in result
            assert "all_exist" in result
            assert "missing.py" in result["missing_files"]
            assert "file1.py" in result["existing_files"]
