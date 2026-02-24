"""
Tests for Roadmap API (roadmap.py)
"""

from unittest.mock import Mock
from uuid import uuid4

from app.utils.clerk_auth import verify_clerk_token


class TestRoadmapAPI:
    """Test cases for /api/roadmap endpoints"""

    def _setup_user_access(self, client, mock_supabase_client, mock_clerk_user, project_id):
        """Helper to set up user access mocks for roadmap endpoints."""
        call_count = {"n": 0}

        def table_side_effect(table_name):
            chain = Mock()
            chain.select = Mock(return_value=chain)
            chain.eq = Mock(return_value=chain)
            chain.order = Mock(return_value=chain)
            chain.in_ = Mock(return_value=chain)

            if table_name == "User":
                chain.execute.return_value.data = [{"id": "user_123"}]
            elif table_name == "projects":
                chain.execute.return_value.data = [
                    {"project_id": project_id, "user_id": "user_123"}
                ]
            elif table_name == "project_access":
                chain.execute.return_value.data = []
            elif table_name == "roadmap_days":
                call_count["n"] += 1
                if call_count["n"] == 1:
                    chain.execute.return_value.data = [
                        {
                            "day_id": str(uuid4()),
                            "project_id": project_id,
                            "day_number": 0,
                            "theme": "Getting Started",
                            "description": "Introduction to the project",
                            "estimated_minutes": 30,
                            "generated_status": "generated",
                            "created_at": "2024-01-01T00:00:00Z",
                        },
                        {
                            "day_id": str(uuid4()),
                            "project_id": project_id,
                            "day_number": 1,
                            "theme": "Core Concepts",
                            "description": "Learn the basics",
                            "estimated_minutes": 60,
                            "generated_status": "generated",
                            "created_at": "2024-01-02T00:00:00Z",
                        },
                    ]
                else:
                    chain.execute.return_value.data = [
                        {
                            "day_id": str(uuid4()),
                            "project_id": project_id,
                            "day_number": 0,
                            "theme": "Getting Started",
                            "description": "Introduction to the project",
                            "estimated_minutes": 30,
                            "generated_status": "generated",
                            "created_at": "2024-01-01T00:00:00Z",
                        }
                    ]
            else:
                chain.execute.return_value.data = []

            return chain

        mock_supabase_client.table.side_effect = table_side_effect

        async def mock_verify_token(authorization=None):
            return mock_clerk_user

        client.app.dependency_overrides[verify_clerk_token] = mock_verify_token
        return call_count

    def test_get_roadmap_success(self, client, mock_supabase_client, mock_clerk_user):
        """Test GET /api/roadmap/{project_id} - successful retrieval"""
        from app.api.roadmap import router as roadmap_router

        client.app.include_router(roadmap_router, prefix="/api/roadmap", tags=["roadmap"])

        project_id = str(uuid4())
        self._setup_user_access(client, mock_supabase_client, mock_clerk_user, project_id)

        try:
            response = client.get(
                f"/api/roadmap/{project_id}", headers={"Authorization": "Bearer fake_token"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "days" in data
            assert len(data["days"]) >= 1
        finally:
            client.app.dependency_overrides.clear()
            mock_supabase_client.table.side_effect = None

    def test_get_roadmap_no_access(self, client, mock_supabase_client, mock_clerk_user):
        """Test GET /api/roadmap/{project_id} - no access"""
        from app.api.roadmap import router as roadmap_router

        client.app.include_router(roadmap_router, prefix="/api/roadmap", tags=["roadmap"])

        project_id = str(uuid4())

        def table_side_effect(table_name):
            chain = Mock()
            chain.select = Mock(return_value=chain)
            chain.eq = Mock(return_value=chain)
            chain.order = Mock(return_value=chain)
            chain.in_ = Mock(return_value=chain)

            if table_name == "User":
                chain.execute.return_value.data = [{"id": "user_123"}]
            elif table_name == "projects":
                chain.execute.return_value.data = []
            elif table_name == "project_access":
                chain.execute.return_value.data = []
            else:
                chain.execute.return_value.data = []

            return chain

        mock_supabase_client.table.side_effect = table_side_effect

        async def mock_verify_token(authorization=None):
            return mock_clerk_user

        client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

        try:
            response = client.get(
                f"/api/roadmap/{project_id}", headers={"Authorization": "Bearer fake_token"}
            )
            assert response.status_code == 404
        finally:
            client.app.dependency_overrides.clear()
            mock_supabase_client.table.side_effect = None

    def test_get_roadmap_missing_auth(self, client):
        """Test GET /api/roadmap/{project_id} - missing auth"""
        from app.api.roadmap import router as roadmap_router

        client.app.include_router(roadmap_router, prefix="/api/roadmap", tags=["roadmap"])
        response = client.get(f"/api/roadmap/{uuid4()}")
        assert response.status_code == 401
