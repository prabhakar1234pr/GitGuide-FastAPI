"""
Test Executor Service
Executes test files in workspace containers.
"""

import logging
from typing import Any

from app.services.docker_client import DockerClient, get_docker_client

logger = logging.getLogger(__name__)


class TestExecutor:
    """
    Executes test files in workspace containers.
    Supports multiple test frameworks (pytest, jest, mocha, unittest, etc.).
    """

    __test__ = False  # Prevent pytest from collecting this as a test class

    def __init__(self, docker_client: DockerClient | None = None):
        self.docker = docker_client or get_docker_client()

    def execute_test(
        self,
        container_id: str,
        test_file_path: str | None = None,
        test_command: str | None = None,
        workdir: str = "/workspace",
    ) -> dict[str, Any]:
        """
        Execute a test file or test command in container.

        Args:
            container_id: Container ID
            test_file_path: Path to test file (if using default test command)
            test_command: Full test command to execute (overrides test_file_path)
            workdir: Working directory

        Returns:
            {
                "success": bool,
                "exit_code": int,
                "output": str,
                "passed": bool,  # True if exit_code == 0
                "error": str | None
            }
        """
        if not test_command and not test_file_path:
            return {
                "success": False,
                "exit_code": -1,
                "output": "",
                "passed": False,
                "error": "Either test_command or test_file_path must be provided",
            }

        # Build command
        if test_command:
            command = test_command
        else:
            # Infer command from file path
            if test_file_path.endswith(".py"):
                command = f"pytest {test_file_path} -v"
            elif test_file_path.endswith((".js", ".ts", ".jsx", ".tsx")):
                command = f"npm test -- {test_file_path}"
            else:
                command = f"test {test_file_path}"  # Generic fallback

        logger.info(f"Executing test in container {container_id[:12]}: {command}")

        try:
            exit_code, output = self.docker.exec_command(
                container_id=container_id,
                command=command,
                workdir=workdir,
            )

            passed = exit_code == 0

            logger.info(
                f"Test execution complete: {'PASSED' if passed else 'FAILED'} "
                f"(exit_code: {exit_code})"
            )

            return {
                "success": True,
                "exit_code": exit_code,
                "output": output,
                "passed": passed,
                "error": None if passed else f"Test failed with exit code {exit_code}",
            }

        except Exception as e:
            logger.error(f"Test execution error: {e}", exc_info=True)
            return {
                "success": False,
                "exit_code": -1,
                "output": "",
                "passed": False,
                "error": str(e),
            }

    def execute_test_command(
        self, container_id: str, test_command: str, workdir: str = "/workspace"
    ) -> dict[str, Any]:
        """
        Execute a test command directly.

        Args:
            container_id: Container ID
            test_command: Full test command (e.g., "pytest tests/test_task_1.py -v")
            workdir: Working directory

        Returns:
            Same as execute_test()
        """
        return self.execute_test(
            container_id=container_id,
            test_command=test_command,
            workdir=workdir,
        )
