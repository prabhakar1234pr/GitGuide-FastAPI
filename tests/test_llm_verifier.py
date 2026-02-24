"""
Tests for LLM Verifier service.
"""

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("app.services.llm_verifier", reason="llm_verifier module not implemented")

from app.services.llm_verifier import LLMVerifier


class TestLLMVerifier:
    """Test LLM Verifier functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.verifier = LLMVerifier()

    @pytest.mark.asyncio
    async def test_verify_with_evidence_passed(self):
        """Test verification when code passes."""
        evidence = {
            "git_diff": "diff content",
            "changed_files": ["file1.py"],
            "file_contents": {"file1.py": "def test(): pass"},
            "test_results": {"success": True, "passed": True, "output": "all tests passed"},
            "ast_analysis": {"functions": [{"name": "test"}]},
            "pattern_match_results": {"all_required_matched": True},
            "github_evidence": {},
        }

        mock_verification_result = {
            "passed": True,
            "overall_feedback": "Code looks good!",
            "requirements_check": {
                "requirement_1": {"met": True, "feedback": "Met"},
            },
            "hints": [],
            "issues_found": [],
            "suggestions": ["Consider adding comments"],
            "code_quality": "good",
            "test_status": "passed",
            "pattern_match_status": "all_matched",
        }

        with patch.object(
            self.verifier.groq_service,
            "generate_response_async",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = str(mock_verification_result).replace("'", '"')

            with patch(
                "app.utils.json_parser.parse_llm_json_response_async",
                new_callable=AsyncMock,
            ) as mock_parse:
                mock_parse.return_value = mock_verification_result

                result = await self.verifier.verify_with_evidence(
                    task_description="Test task",
                    task_requirements="Do something",
                    evidence=evidence,
                    temperature=0.0,
                )

                assert result["passed"]
                assert result["code_quality"] == "good"
                assert result["test_status"] == "passed"

    @pytest.mark.asyncio
    async def test_verify_with_evidence_failed(self):
        """Test verification when code fails."""
        evidence = {
            "git_diff": "diff content",
            "changed_files": ["file1.py"],
            "file_contents": {"file1.py": "incomplete code"},
            "test_results": {"success": True, "passed": False, "output": "tests failed"},
            "ast_analysis": {},
            "pattern_match_results": {"all_required_matched": False},
            "github_evidence": {},
        }

        mock_verification_result = {
            "passed": False,
            "overall_feedback": "Code is incomplete",
            "requirements_check": {
                "requirement_1": {"met": False, "feedback": "Missing implementation"},
            },
            "hints": ["Try implementing the function first"],
            "issues_found": ["Function not implemented"],
            "suggestions": [],
            "code_quality": "needs_improvement",
            "test_status": "failed",
            "pattern_match_status": "none",
        }

        with patch.object(
            self.verifier.groq_service,
            "generate_response_async",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = str(mock_verification_result).replace("'", '"')

            with patch(
                "app.utils.json_parser.parse_llm_json_response_async",
                new_callable=AsyncMock,
            ) as mock_parse:
                mock_parse.return_value = mock_verification_result

                result = await self.verifier.verify_with_evidence(
                    task_description="Test task",
                    task_requirements="Do something",
                    evidence=evidence,
                    temperature=0.0,
                )

                assert not result["passed"]
                assert len(result["hints"]) > 0
                assert len(result["issues_found"]) > 0

    @pytest.mark.asyncio
    async def test_verify_with_evidence_llm_error(self):
        """Test verification when LLM call fails."""
        evidence = {
            "git_diff": "",
            "changed_files": [],
            "file_contents": {},
            "test_results": None,
            "ast_analysis": {},
            "pattern_match_results": None,
            "github_evidence": {},
        }

        with patch.object(
            self.verifier.groq_service,
            "generate_response_async",
            new_callable=AsyncMock,
            side_effect=Exception("LLM API error"),
        ):
            result = await self.verifier.verify_with_evidence(
                task_description="Test task",
                task_requirements="Do something",
                evidence=evidence,
            )

            # Should return safe failure result
            assert not result["passed"]
            assert "error" in result["overall_feedback"].lower()

    def test_format_file_contents(self):
        """Test file contents formatting."""
        file_contents = {
            "file1.py": "code1",
            "file2.py": "code2",
        }

        formatted = self.verifier._format_file_contents(file_contents)
        assert "file1.py" in formatted
        assert "file2.py" in formatted
        assert "code1" in formatted

    def test_format_test_results_passed(self):
        """Test test results formatting when passed."""
        test_results = {
            "success": True,
            "passed": True,
            "exit_code": 0,
            "output": "all tests passed",
        }

        formatted = self.verifier._format_test_results(test_results)
        assert "PASSED" in formatted
        assert "all tests passed" in formatted

    def test_format_test_results_failed(self):
        """Test test results formatting when failed."""
        test_results = {
            "success": True,
            "passed": False,
            "exit_code": 1,
            "output": "test failed",
        }

        formatted = self.verifier._format_test_results(test_results)
        assert "FAILED" in formatted
        assert "test failed" in formatted

    def test_format_ast_analysis(self):
        """Test AST analysis formatting."""
        ast_analysis = {
            "has_syntax_errors": False,
            "functions": [{"name": "test_func"}],
            "classes": [{"name": "TestClass"}],
            "imports": [{"module": "os"}],
        }

        formatted = self.verifier._format_ast_analysis(ast_analysis)
        assert "test_func" in formatted
        assert "TestClass" in formatted
