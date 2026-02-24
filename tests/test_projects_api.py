"""
Tests for Projects API (projects.py)
"""

from unittest.mock import Mock, patch
from uuid import uuid4

from app.utils.clerk_auth import verify_clerk_token


class TestProjectsAPI:
    """Test cases for /api/projects endpoints"""

    def test_create_project_success(self, client, mock_supabase_client, mock_clerk_user):
        """Test POST /api/projects/create - successful project creation"""
        project_id = str(uuid4())

        # Setup mocks
        mock_table = mock_supabase_client.table.return_value

        # Mock: user exists (first select) - role=manager required to create projects
        mock_select_chain1 = Mock()
        mock_select_chain1.select.return_value = mock_select_chain1
        mock_select_chain1.eq.return_value = mock_select_chain1
        mock_select_chain1.execute.return_value.data = [{"id": "user_123", "role": "manager"}]
        mock_table.select.return_value = mock_select_chain1

        # Mock: insert project
        mock_insert_chain = Mock()
        mock_insert_response = Mock()
        mock_insert_response.data = [
            {
                "project_id": project_id,
                "user_id": "user_123",
                "project_name": "test-repo",
                "github_url": "https://github.com/user/test-repo",
                "skill_level": "beginner",
                "target_days": 7,
                "status": "created",
                "created_at": "2024-01-01T00:00:00Z",
            }
        ]
        mock_insert_chain.execute.return_value = mock_insert_response
        mock_table.insert.return_value = mock_insert_chain

        # Override FastAPI dependency - match the exact signature
        async def mock_verify_token(authorization=None):
            return mock_clerk_user

        client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

        # Mock the background task to prevent actual execution
        with patch("app.api.projects.run_embedding_pipeline"):
            try:
                response = client.post(
                    "/api/projects/create",
                    headers={"Authorization": "Bearer fake_token"},
                    json={
                        "github_url": "https://github.com/user/test-repo",
                        "skill_level": "beginner",
                        "target_days": 7,
                    },
                )

                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                assert "project" in data
                assert data["project"]["project_name"] == "test-repo"
            finally:
                client.app.dependency_overrides.clear()

    def test_create_project_invalid_url(self, client, mock_supabase_client, mock_clerk_user):
        """Test POST /api/projects/create - invalid GitHub URL"""

        async def mock_verify_token(authorization=None):
            return mock_clerk_user

        client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

        try:
            response = client.post(
                "/api/projects/create",
                headers={"Authorization": "Bearer fake_token"},
                json={"github_url": "invalid-url", "skill_level": "beginner", "target_days": 7},
            )

            assert response.status_code == 422  # Validation error
        finally:
            client.app.dependency_overrides.clear()

    def test_create_project_invalid_target_days(
        self, client, mock_supabase_client, mock_clerk_user
    ):
        """Test POST /api/projects/create - invalid target_days"""

        async def mock_verify_token(authorization=None):
            return mock_clerk_user

        client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

        try:
            response = client.post(
                "/api/projects/create",
                headers={"Authorization": "Bearer fake_token"},
                json={
                    "github_url": "https://github.com/user/test-repo",
                    "skill_level": "beginner",
                    "target_days": 5,  # Less than 7
                },
            )

            assert response.status_code == 422  # Validation error
        finally:
            client.app.dependency_overrides.clear()

    def test_get_project_success(self, client, mock_supabase_client, mock_clerk_user):
        """Test GET /api/projects/{project_id} - successful retrieval"""

        project_id = str(uuid4())

        # Setup mocks with proper query chains
        mock_table = mock_supabase_client.table.return_value

        # Mock: user exists (first select)
        mock_select_chain1 = Mock()
        mock_select_chain1.select.return_value = mock_select_chain1
        mock_select_chain1.eq.return_value = mock_select_chain1
        mock_select_chain1.execute.return_value.data = [{"id": "user_123"}]
        mock_table.select.return_value = mock_select_chain1

        # Mock: project exists (second select with chained eq)
        mock_select_chain2 = Mock()
        mock_select_chain2.select.return_value = mock_select_chain2
        mock_select_chain2.eq.return_value = mock_select_chain2
        mock_select_chain2.execute.return_value.data = [
            {
                "project_id": project_id,
                "project_name": "test-repo",
                "github_url": "https://github.com/user/test-repo",
                "status": "ready",
            }
        ]

        # Override for second call
        def table_side_effect(table_name):
            if table_name == "projects":
                mock_table2 = Mock()
                mock_table2.select.return_value = mock_select_chain2
                return mock_table2
            return mock_table

        mock_supabase_client.table.side_effect = table_side_effect

        # Override FastAPI dependency - match the exact signature
        async def mock_verify_token(authorization=None):
            return mock_clerk_user

        client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

        try:
            response = client.get(
                f"/api/projects/{project_id}", headers={"Authorization": "Bearer fake_token"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "project" in data
        finally:
            client.app.dependency_overrides.clear()
            if hasattr(mock_supabase_client.table, "side_effect"):
                mock_supabase_client.table.side_effect = None

    def test_get_project_not_found(self, client, mock_supabase_client, mock_clerk_user):
        """Test GET /api/projects/{project_id} - project not found"""
        project_id = str(uuid4())

        # Setup mocks
        mock_table = mock_supabase_client.table.return_value
        mock_select_chain1 = Mock()
        mock_select_chain1.select.return_value = mock_select_chain1
        mock_select_chain1.eq.return_value = mock_select_chain1
        mock_select_chain1.execute.return_value.data = [{"id": "user_123"}]
        mock_table.select.return_value = mock_select_chain1

        mock_select_chain2 = Mock()
        mock_select_chain2.select.return_value = mock_select_chain2
        mock_select_chain2.eq.return_value = mock_select_chain2
        mock_select_chain2.execute.return_value.data = []

        def table_side_effect(table_name):
            if table_name == "projects":
                mock_table2 = Mock()
                mock_table2.select.return_value = mock_select_chain2
                return mock_table2
            return mock_table

        mock_supabase_client.table.side_effect = table_side_effect

        async def mock_verify_token(authorization=None):
            return mock_clerk_user

        client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

        try:
            response = client.get(
                f"/api/projects/{project_id}", headers={"Authorization": "Bearer fake_token"}
            )

            assert response.status_code == 404
        finally:
            client.app.dependency_overrides.clear()
            if hasattr(mock_supabase_client.table, "side_effect"):
                mock_supabase_client.table.side_effect = None

    def test_list_user_projects(self, client, mock_supabase_client, mock_clerk_user):
        """Test GET /api/projects/user/list - list all user projects"""
        # Setup mocks
        mock_table = mock_supabase_client.table.return_value
        mock_select_chain1 = Mock()
        mock_select_chain1.select.return_value = mock_select_chain1
        mock_select_chain1.eq.return_value = mock_select_chain1
        mock_select_chain1.execute.return_value.data = [{"id": "user_123"}]
        mock_table.select.return_value = mock_select_chain1

        mock_select_chain2 = Mock()
        mock_select_chain2.select.return_value = mock_select_chain2
        mock_select_chain2.eq.return_value = mock_select_chain2
        mock_select_chain2.order.return_value = mock_select_chain2
        mock_select_chain2.execute.return_value.data = [
            {"project_id": str(uuid4()), "project_name": "repo1"},
            {"project_id": str(uuid4()), "project_name": "repo2"},
        ]

        project_access_chain = Mock()
        project_access_chain.select.return_value = project_access_chain
        project_access_chain.eq.return_value = project_access_chain
        project_access_chain.execute.return_value.data = []

        def table_side_effect(table_name):
            if table_name == "projects":
                mock_table2 = Mock()
                mock_table2.select.return_value = mock_select_chain2
                mock_table2.eq.return_value = mock_select_chain2
                mock_table2.order.return_value = mock_select_chain2
                mock_table2.in_ = Mock(return_value=mock_select_chain2)
                return mock_table2
            if table_name == "project_access":
                mock_pa = Mock()
                mock_pa.select.return_value = project_access_chain
                return mock_pa
            return mock_table

        mock_supabase_client.table.side_effect = table_side_effect

        async def mock_verify_token(authorization=None):
            return mock_clerk_user

        client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

        try:
            response = client.get(
                "/api/projects/user/list", headers={"Authorization": "Bearer fake_token"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "projects" in data
            assert len(data["projects"]) == 2
        finally:
            client.app.dependency_overrides.clear()
            if hasattr(mock_supabase_client.table, "side_effect"):
                mock_supabase_client.table.side_effect = None

    def test_delete_project_success(
        self, client, mock_supabase_client, mock_clerk_user, mock_qdrant_client
    ):
        """Test DELETE /api/projects/{project_id} - successful deletion"""
        project_id = str(uuid4())

        # Setup mocks
        mock_table = mock_supabase_client.table.return_value
        mock_select_chain1 = Mock()
        mock_select_chain1.select.return_value = mock_select_chain1
        mock_select_chain1.eq.return_value = mock_select_chain1
        mock_select_chain1.execute.return_value.data = [{"id": "user_123"}]
        mock_table.select.return_value = mock_select_chain1

        mock_select_chain2 = Mock()
        mock_select_chain2.select.return_value = mock_select_chain2
        mock_select_chain2.eq.return_value = mock_select_chain2
        mock_select_chain2.execute.return_value.data = [
            {"project_id": project_id, "project_name": "test-repo"}
        ]

        mock_delete_chain = Mock()
        mock_delete_chain.eq.return_value = mock_delete_chain
        mock_delete_chain.execute.return_value = Mock()

        workspaces_chain = Mock()
        workspaces_chain.select.return_value = workspaces_chain
        workspaces_chain.eq.return_value = workspaces_chain
        workspaces_chain.execute.return_value.data = []

        def table_side_effect(table_name):
            if table_name == "projects":
                mock_table2 = Mock()
                mock_table2.select.return_value = mock_select_chain2
                mock_table2.delete.return_value = mock_delete_chain
                return mock_table2
            if table_name == "workspaces":
                mock_ws = Mock()
                mock_ws.select.return_value = workspaces_chain
                return mock_ws
            return mock_table

        mock_supabase_client.table.side_effect = table_side_effect

        # Mock QdrantService
        mock_qdrant_service = Mock()
        mock_qdrant_service.delete_points_by_project_id.return_value = 10

        with patch("app.api.projects.get_qdrant_service", return_value=mock_qdrant_service):

            async def mock_verify_token(authorization=None):
                return mock_clerk_user

            client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

            try:
                response = client.delete(
                    f"/api/projects/{project_id}", headers={"Authorization": "Bearer fake_token"}
                )

                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                assert data["deleted_embeddings"] == 10
            finally:
                client.app.dependency_overrides.clear()
                if hasattr(mock_supabase_client.table, "side_effect"):
                    mock_supabase_client.table.side_effect = None

    def test_delete_project_not_found(self, client, mock_supabase_client, mock_clerk_user):
        """Test DELETE /api/projects/{project_id} - project not found"""
        project_id = str(uuid4())

        # Setup mocks
        mock_table = mock_supabase_client.table.return_value
        mock_select_chain1 = Mock()
        mock_select_chain1.select.return_value = mock_select_chain1
        mock_select_chain1.eq.return_value = mock_select_chain1
        mock_select_chain1.execute.return_value.data = [{"id": "user_123"}]
        mock_table.select.return_value = mock_select_chain1

        mock_select_chain2 = Mock()
        mock_select_chain2.select.return_value = mock_select_chain2
        mock_select_chain2.eq.return_value = mock_select_chain2
        mock_select_chain2.execute.return_value.data = []

        def table_side_effect(table_name):
            if table_name == "projects":
                mock_table2 = Mock()
                mock_table2.select.return_value = mock_select_chain2
                return mock_table2
            return mock_table

        mock_supabase_client.table.side_effect = table_side_effect

        async def mock_verify_token(authorization=None):
            return mock_clerk_user

        client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

        try:
            response = client.delete(
                f"/api/projects/{project_id}", headers={"Authorization": "Bearer fake_token"}
            )

            assert response.status_code == 404
        finally:
            client.app.dependency_overrides.clear()
            if hasattr(mock_supabase_client.table, "side_effect"):
                mock_supabase_client.table.side_effect = None
