"""
Tests for Git API endpoints.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.git import router as git_router
from app.services.workspace_manager import get_workspace_manager
from app.utils.clerk_auth import verify_clerk_token


@pytest.fixture
def git_client():
    app = FastAPI()
    app.include_router(git_router, prefix="/api/git")
    return TestClient(app)


def _mock_workspace_manager():
    manager = Mock()
    manager.get_workspace.return_value = SimpleNamespace(
        workspace_id="ws_1",
        user_id="user_1",
        project_id="proj_1",
        container_id="container_1",
        container_status="running",
    )
    return manager


def test_git_status_success(git_client, monkeypatch):
    async def mock_verify_token(authorization=None):
        return {"clerk_user_id": "clerk_1"}

    git_client.app.dependency_overrides[verify_clerk_token] = mock_verify_token
    git_client.app.dependency_overrides[get_workspace_manager] = _mock_workspace_manager

    monkeypatch.setattr("app.api.git.get_user_id_from_clerk", lambda *_: "user_1")

    class DummyGitService:
        def git_status(self, container_id):
            assert container_id == "container_1"
            return {"success": True, "branch": "main", "ahead": 0, "behind": 0, "modified": []}

    monkeypatch.setattr("app.api.git.GitService", DummyGitService)

    response = git_client.get(
        "/api/git/ws_1/status",
        headers={"Authorization": "Bearer token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["branch"] == "main"


@pytest.mark.skip(reason="Requires real Supabase/Postgres - postgrest UUID validation")
def test_git_pull_uncommitted_conflict(git_client, monkeypatch):
    async def mock_verify_token(authorization=None):
        return {"clerk_user_id": "clerk_1", "name": "Test", "email": "test@example.com"}

    git_client.app.dependency_overrides[verify_clerk_token] = mock_verify_token
    git_client.app.dependency_overrides[get_workspace_manager] = _mock_workspace_manager

    monkeypatch.setattr("app.api.git.get_user_id_from_clerk", lambda *_: "user_1")
    monkeypatch.setattr("app.api.git._get_project_token", lambda *_: "token")

    class DummyGitService:
        def git_check_uncommitted(self, container_id):
            return {"success": True, "has_changes": True, "files": ["README.md"]}

    monkeypatch.setattr("app.api.git.GitService", DummyGitService)

    response = git_client.post(
        "/api/git/ws_1/pull",
        headers={"Authorization": "Bearer token"},
        json={"branch": "main"},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "Uncommitted" in detail["message"]
    assert "README.md" in detail["files"]
