"""
Tests for Verification Evidence Collector service.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

pytest.importorskip(
    "app.services.verification_evidence", reason="verification_evidence module not implemented"
)

from app.services.verification_evidence import VerificationEvidenceCollector


class TestVerificationEvidenceCollector:
    """Test Verification Evidence Collector functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.collector = VerificationEvidenceCollector()

    @pytest.mark.asyncio
    async def test_collect_all_evidence_basic(self):
        """Test basic evidence collection."""
        # Mock all dependencies
        mock_workspace = Mock()
        mock_workspace.container_id = "test-container"
        mock_workspace.project_id = "test-project"

        # Mock supabase query chain properly
        mock_task_response = Mock()
        mock_task_response.data = [
            {"test_file_path": "test.py", "test_command": "pytest", "verification_patterns": {}}
        ]

        def create_query_chain():
            chain = Mock()
            chain.select = Mock(return_value=chain)
            chain.eq = Mock(return_value=chain)
            chain.execute = Mock(return_value=mock_task_response)
            return chain

        mock_table = Mock()
        mock_table.select = Mock(return_value=create_query_chain())

        mock_supabase = Mock()
        mock_supabase.table = Mock(return_value=mock_table)

        with patch.object(
            self.collector.workspace_manager, "get_workspace", return_value=mock_workspace
        ):
            with patch.object(self.collector, "supabase", mock_supabase):
                with patch.object(
                    self.collector.git_service, "git_diff", return_value={"diff": "test diff"}
                ):
                    with patch.object(
                        self.collector.git_service,
                        "git_status",
                        return_value={"modified": ["file1.py"]},
                    ):
                        with patch.object(
                            self.collector.file_system,
                            "read_file",
                            return_value={"success": True, "content": "code"},
                        ):
                            with patch.object(
                                self.collector.test_executor,
                                "execute_test",
                                return_value={"success": True, "passed": True},
                            ):
                                with patch.object(
                                    self.collector.github_collector,
                                    "get_repo_baseline",
                                    new_callable=AsyncMock,
                                    return_value={"files": {}, "repo_structure": []},
                                ):
                                    with patch.object(
                                        self.collector.ast_analyzer,
                                        "analyze_python_code",
                                        return_value={"functions": []},
                                    ):
                                        with patch.object(
                                            self.collector.pattern_matcher,
                                            "match_patterns",
                                            return_value={"all_required_matched": True},
                                        ):
                                            evidence = await self.collector.collect_all_evidence(
                                                task_id="test-task",
                                                workspace_id="test-workspace",
                                            )

                                            assert "git_diff" in evidence
                                            assert "git_status" in evidence
                                            assert "file_contents" in evidence
                                            assert "test_results" in evidence

    @pytest.mark.asyncio
    async def test_collect_all_evidence_with_base_commit(self):
        """Test evidence collection with base commit."""
        mock_workspace = Mock()
        mock_workspace.container_id = "test-container"
        mock_workspace.project_id = "test-project"

        # Mock supabase query chain properly
        mock_task_response = Mock()
        mock_task_response.data = [
            {"test_file_path": None, "test_command": None, "verification_patterns": {}}
        ]

        def create_query_chain():
            chain = Mock()
            chain.select = Mock(return_value=chain)
            chain.eq = Mock(return_value=chain)
            chain.execute = Mock(return_value=mock_task_response)
            return chain

        mock_table = Mock()
        mock_table.select = Mock(return_value=create_query_chain())

        mock_supabase = Mock()
        mock_supabase.table = Mock(return_value=mock_table)

        with patch.object(
            self.collector.workspace_manager, "get_workspace", return_value=mock_workspace
        ):
            with patch.object(self.collector, "supabase", mock_supabase):
                with patch.object(
                    self.collector.git_service,
                    "git_diff",
                    return_value={"diff": "diff from base"},
                ):
                    with patch.object(
                        self.collector.git_service,
                        "git_status",
                        return_value={},
                    ):
                        with patch.object(
                            self.collector.file_system,
                            "read_file",
                            return_value={"success": False},
                        ):
                            evidence = await self.collector.collect_all_evidence(
                                task_id="test-task",
                                workspace_id="test-workspace",
                                base_commit="abc123",
                            )

                            # Verify git_diff was called with base_commit
                            assert "git_diff" in evidence

    @pytest.mark.asyncio
    async def test_collect_all_evidence_no_test_file(self):
        """Test evidence collection when no test file exists."""
        mock_workspace = Mock()
        mock_workspace.container_id = "test-container"
        mock_workspace.project_id = "test-project"

        # Mock supabase query chain properly
        mock_task_response = Mock()
        mock_task_response.data = [
            {"test_file_path": None, "test_command": None, "verification_patterns": {}}
        ]

        def create_query_chain():
            chain = Mock()
            chain.select = Mock(return_value=chain)
            chain.eq = Mock(return_value=chain)
            chain.execute = Mock(return_value=mock_task_response)
            return chain

        mock_table = Mock()
        mock_table.select = Mock(return_value=create_query_chain())

        mock_supabase = Mock()
        mock_supabase.table = Mock(return_value=mock_table)

        with patch.object(
            self.collector.workspace_manager, "get_workspace", return_value=mock_workspace
        ):
            with patch.object(self.collector, "supabase", mock_supabase):
                with patch.object(
                    self.collector.git_service,
                    "git_diff",
                    return_value={"diff": ""},
                ):
                    with patch.object(
                        self.collector.git_service,
                        "git_status",
                        return_value={},
                    ):
                        evidence = await self.collector.collect_all_evidence(
                            task_id="test-task",
                            workspace_id="test-workspace",
                        )

                        # Test results should be None when no test file
                        assert evidence.get("test_results") is None
