"""
Tests for Task Sessions API endpoints.
"""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.task_sessions import router as task_sessions_router
from app.services.workspace_manager import Workspace
from app.utils.clerk_auth import verify_clerk_token


@pytest.fixture
def task_sessions_client():
    app = FastAPI()
    app.include_router(task_sessions_router, prefix="/api/task-sessions")
    return TestClient(app)


def test_start_task_session_success_employee(task_sessions_client, monkeypatch):
    """Employee (has access via project_access, not owner) can start task session."""

    async def mock_verify_token(authorization=None):
        return {"clerk_user_id": "clerk_1"}

    task_sessions_client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

    monkeypatch.setattr("app.api.task_sessions.get_user_id_from_clerk", lambda *_: "user_1")
    # Employee: has_access=True, is_owner=False (access via project_access table)
    monkeypatch.setattr("app.api.task_sessions.user_has_project_access", lambda *_: (True, False))

    mock_workspace = Workspace(
        workspace_id="ws_1",
        user_id="user_1",
        project_id="proj_1",
        container_id="container_123",
        container_status="running",
        created_at=datetime.now(UTC),
        last_active_at=datetime.now(UTC),
    )
    mock_wm = type("MockWM", (), {})()
    mock_wm.get_workspace = lambda wid: mock_workspace if wid == "ws_1" else None
    mock_wm.start_workspace = lambda wid: True
    mock_wm.initialize_git_repo = lambda *a, **k: {"success": True}
    monkeypatch.setattr("app.api.task_sessions.get_workspace_manager", lambda: mock_wm)

    class DummyTaskSessionService:
        def __init__(self, *args, **kwargs):
            pass

        def start_task_session(self, task_id, user_id, workspace_id):
            assert task_id == "task_1"
            assert user_id == "user_1"
            assert workspace_id == "ws_1"
            return {"success": True, "session": {"session_id": "sess_1"}}

    monkeypatch.setattr("app.api.task_sessions.TaskSessionService", DummyTaskSessionService)

    response = task_sessions_client.post(
        "/api/task-sessions/start",
        headers={"Authorization": "Bearer token"},
        json={"task_id": "task_1", "workspace_id": "ws_1"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["session"]["session_id"] == "sess_1"


def test_start_task_session_manager_rejected(task_sessions_client, monkeypatch):
    """Manager (project owner) cannot start task session - returns 403."""

    async def mock_verify_token(authorization=None):
        return {"clerk_user_id": "clerk_1"}

    task_sessions_client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

    monkeypatch.setattr("app.api.task_sessions.get_user_id_from_clerk", lambda *_: "user_1")
    # Manager: has_access=True, is_owner=True (owns project via projects.user_id)
    monkeypatch.setattr("app.api.task_sessions.user_has_project_access", lambda *_: (True, True))

    mock_workspace = Workspace(
        workspace_id="ws_1",
        user_id="user_1",
        project_id="proj_1",
        container_id="container_123",
        container_status="running",
        created_at=datetime.now(UTC),
        last_active_at=datetime.now(UTC),
    )
    mock_wm = type("MockWM", (), {})()
    mock_wm.get_workspace = lambda wid: mock_workspace if wid == "ws_1" else None
    monkeypatch.setattr("app.api.task_sessions.get_workspace_manager", lambda: mock_wm)

    response = task_sessions_client.post(
        "/api/task-sessions/start",
        headers={"Authorization": "Bearer token"},
        json={"task_id": "task_1", "workspace_id": "ws_1"},
    )
    assert response.status_code == 403
    assert "Managers cannot start task sessions" in response.json().get("detail", "")


def test_start_task_session_no_access_rejected(task_sessions_client, monkeypatch):
    """User without project access cannot start task session - returns 403."""

    async def mock_verify_token(authorization=None):
        return {"clerk_user_id": "clerk_1"}

    task_sessions_client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

    monkeypatch.setattr("app.api.task_sessions.get_user_id_from_clerk", lambda *_: "user_1")
    # No access: has_access=False, is_owner=False
    monkeypatch.setattr("app.api.task_sessions.user_has_project_access", lambda *_: (False, False))

    mock_workspace = Workspace(
        workspace_id="ws_1",
        user_id="user_1",
        project_id="proj_1",
        container_id="container_123",
        container_status="running",
        created_at=datetime.now(UTC),
        last_active_at=datetime.now(UTC),
    )
    mock_wm = type("MockWM", (), {})()
    mock_wm.get_workspace = lambda wid: mock_workspace if wid == "ws_1" else None
    monkeypatch.setattr("app.api.task_sessions.get_workspace_manager", lambda: mock_wm)

    response = task_sessions_client.post(
        "/api/task-sessions/start",
        headers={"Authorization": "Bearer token"},
        json={"task_id": "task_1", "workspace_id": "ws_1"},
    )
    assert response.status_code == 403
    assert "Project access required" in response.json().get("detail", "")


def test_complete_task_session_success(task_sessions_client, monkeypatch):
    async def mock_verify_token(authorization=None):
        return {"clerk_user_id": "clerk_1"}

    task_sessions_client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

    monkeypatch.setattr("app.api.task_sessions.get_user_id_from_clerk", lambda *_: "user_1")

    class DummyTaskSessionService:
        def __init__(self, *args, **kwargs):
            pass

        def get_session_by_id(self, session_id):
            return {"success": True, "session": {"session_id": session_id, "user_id": "user_1"}}

        def complete_task_session(self, session_id, current_commit=None):
            return {
                "success": True,
                "session": {"session_id": session_id, "current_commit": current_commit},
            }

    monkeypatch.setattr("app.api.task_sessions.TaskSessionService", DummyTaskSessionService)

    response = task_sessions_client.post(
        "/api/task-sessions/sess_1/complete",
        headers={"Authorization": "Bearer token"},
        json={"current_commit": "abc123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["session"]["current_commit"] == "abc123"


def test_get_task_session_diff_success(task_sessions_client, monkeypatch):
    async def mock_verify_token(authorization=None):
        return {"clerk_user_id": "clerk_1"}

    task_sessions_client.app.dependency_overrides[verify_clerk_token] = mock_verify_token
    monkeypatch.setattr("app.api.task_sessions.get_user_id_from_clerk", lambda *_: "user_1")

    class DummyTaskSessionService:
        def __init__(self, *args, **kwargs):
            pass

        def get_session_by_id(self, session_id):
            return {"success": True, "session": {"session_id": session_id, "user_id": "user_1"}}

        def get_diff_for_verification(self, session_id):
            return {"success": True, "diff": "diff --git a/a b/a", "base_commit": "abc123"}

    monkeypatch.setattr("app.api.task_sessions.TaskSessionService", DummyTaskSessionService)

    response = task_sessions_client.get(
        "/api/task-sessions/sess_1/diff",
        headers={"Authorization": "Bearer token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "diff --git" in data["diff"]
