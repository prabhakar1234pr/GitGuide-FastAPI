"""
Tests for Users API (users.py)
"""

from datetime import UTC, datetime
from unittest.mock import Mock

from app.utils.clerk_auth import verify_clerk_token


class TestUsersAPI:
    """Test cases for /api/users endpoints"""

    def test_sync_user_create_new(self, client, mock_supabase_client, mock_clerk_user):
        """Test POST /api/users/sync - creating new user"""
        # Setup mocks
        mock_table = mock_supabase_client.table.return_value

        # Mock: user doesn't exist (first select by clerk_user_id)
        mock_select_chain1 = Mock()
        mock_select_chain1.select.return_value = mock_select_chain1
        mock_select_chain1.eq.return_value = mock_select_chain1
        mock_select_chain1.ilike.return_value = mock_select_chain1
        mock_execute_result = Mock()
        mock_execute_result.data = []
        mock_select_chain1.execute.return_value = mock_execute_result
        mock_table.select.return_value = mock_select_chain1

        # Mock: insert new user
        mock_insert_chain = Mock()
        mock_insert_response = Mock()
        mock_insert_response.data = [
            {
                "id": "user_123",
                "clerk_user_id": "user_123",
                "email": "test@example.com",
                "name": "Test User",
                "created_at": datetime.now(UTC).isoformat(),
            }
        ]
        mock_insert_chain.execute.return_value = mock_insert_response
        mock_table.insert.return_value = mock_insert_chain

        # Override FastAPI dependency - match the exact signature
        async def mock_verify_token(authorization=None):
            return mock_clerk_user

        client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

        try:
            response = client.post(
                "/api/users/sync", headers={"Authorization": "Bearer fake_token"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["action"] == "created"
            assert "user" in data
        finally:
            client.app.dependency_overrides.clear()

    def test_sync_user_update_existing(self, client, mock_supabase_client, mock_clerk_user):
        """Test POST /api/users/sync - updating existing user"""
        # Setup mocks
        mock_table = mock_supabase_client.table.return_value

        # Mock: user exists (first select)
        mock_select_chain1 = Mock()
        mock_select_chain1.select.return_value = mock_select_chain1
        mock_select_chain1.eq.return_value = mock_select_chain1
        mock_select_chain1.execute.return_value.data = [
            {"id": "user_123", "clerk_user_id": "user_123"}
        ]
        mock_table.select.return_value = mock_select_chain1

        # Mock: update user
        mock_update_chain = Mock()
        mock_update_response = Mock()
        mock_update_response.data = [
            {
                "id": "user_123",
                "clerk_user_id": "user_123",
                "email": "updated@example.com",
                "name": "Updated User",
                "updated_at": datetime.now(UTC).isoformat(),
            }
        ]
        mock_update_chain.eq.return_value = mock_update_chain
        mock_update_chain.execute.return_value = mock_update_response
        mock_table.update.return_value = mock_update_chain

        # Override FastAPI dependency - match the exact signature
        async def mock_verify_token(authorization=None):
            return mock_clerk_user

        client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

        try:
            response = client.post(
                "/api/users/sync", headers={"Authorization": "Bearer fake_token"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["action"] == "updated"
            assert "user" in data
        finally:
            client.app.dependency_overrides.clear()

    def test_get_current_user_success(self, client, mock_supabase_client, mock_clerk_user):
        """Test GET /api/users/me - successful retrieval"""
        # Setup mocks
        mock_table = mock_supabase_client.table.return_value
        mock_select_chain = Mock()
        mock_select_chain.select.return_value = mock_select_chain
        mock_select_chain.eq.return_value = mock_select_chain
        mock_select_chain.execute.return_value.data = [
            {
                "id": "user_123",
                "clerk_user_id": "user_123",
                "email": "test@example.com",
                "name": "Test User",
            }
        ]
        mock_table.select.return_value = mock_select_chain

        # Override FastAPI dependency - match the exact signature
        async def mock_verify_token(authorization=None):
            return mock_clerk_user

        client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

        try:
            response = client.get("/api/users/me", headers={"Authorization": "Bearer fake_token"})

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "user" in data
            assert data["user"]["clerk_user_id"] == "user_123"
        finally:
            client.app.dependency_overrides.clear()

    def test_get_current_user_not_found(self, client, mock_supabase_client, mock_clerk_user):
        """Test GET /api/users/me - user not found"""
        # Setup mocks
        mock_table = mock_supabase_client.table.return_value
        mock_select_chain = Mock()
        mock_select_chain.select.return_value = mock_select_chain
        mock_select_chain.eq.return_value = mock_select_chain
        mock_select_chain.execute.return_value.data = []
        mock_table.select.return_value = mock_select_chain

        # Override FastAPI dependency - match the exact signature
        async def mock_verify_token(authorization=None):
            return mock_clerk_user

        client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

        try:
            response = client.get("/api/users/me", headers={"Authorization": "Bearer fake_token"})

            assert response.status_code == 404
            assert "not found" in response.json()["detail"].lower()
        finally:
            client.app.dependency_overrides.clear()

    def test_sync_user_missing_auth(self, client):
        """Test POST /api/users/sync - missing authorization header"""
        response = client.post("/api/users/sync")
        assert response.status_code == 401

    def test_get_current_user_missing_auth(self, client):
        """Test GET /api/users/me - missing authorization header"""
        response = client.get("/api/users/me")
        assert response.status_code == 401
