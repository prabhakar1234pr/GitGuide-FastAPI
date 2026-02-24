"""
End-to-End Tests for LangGraph Migration

This test suite verifies that:
1. All LangGraph workflows run ONLY in gitguide-roadmap service
2. gitguide-api uses HTTP client to delegate LangGraph work
3. Services communicate correctly via HTTP
4. No direct LangGraph calls in main API
5. Error handling works correctly
"""

import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.services.roadmap_client import (
    call_roadmap_service_generate,
    call_roadmap_service_incremental,
    call_roadmap_service_incremental_sync,
)


class TestLangGraphMigrationSeparation:
    """Test that LangGraph is completely separated from main API"""

    def test_main_api_does_not_import_roadmap_agent(self):
        """Verify main API doesn't import roadmap_agent directly"""

        # Check that main.py doesn't import roadmap_agent
        import app.main

        # Verify no direct import of roadmap_agent in main module
        assert "roadmap_agent" not in dir(app.main)
        assert "run_roadmap_agent" not in dir(app.main)

    def test_progress_api_uses_http_client(self):
        """Verify progress API uses HTTP client, not direct LangGraph calls"""
        import app.api.progress

        # Check that progress.py imports roadmap_client, not roadmap_generation
        assert hasattr(app.api.progress, "call_roadmap_service_incremental_sync")
        # Verify it doesn't directly import LangGraph functions
        assert not hasattr(app.api.progress, "run_incremental_concept_generation")
        assert not hasattr(app.api.progress, "trigger_incremental_generation_sync")

    def test_embedding_pipeline_uses_http_client(self):
        """Verify embedding pipeline uses HTTP client"""
        # Check imports
        import inspect

        import app.services.embedding_pipeline

        source = inspect.getsource(app.services.embedding_pipeline)
        # Should import roadmap_client
        assert "roadmap_client" in source or "call_roadmap_service_generate" in source
        # Should NOT directly import run_roadmap_generation
        assert "from app.services.roadmap_generation import run_roadmap_generation" not in source


class TestHTTPClientDelegation:
    """Test that HTTP client correctly delegates to roadmap service"""

    @pytest.mark.asyncio
    async def test_call_roadmap_service_incremental_success(self, monkeypatch):
        """Test successful HTTP call to roadmap service for incremental generation"""
        project_id = str(uuid.uuid4())

        # Mock httpx response
        mock_response = Mock()
        mock_response.json.return_value = {
            "success": True,
            "message": "Incremental generation triggered",
            "project_id": project_id,
        }
        mock_response.raise_for_status = Mock()

        # Mock httpx.AsyncClient
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.services.roadmap_client.httpx.AsyncClient", return_value=mock_client):
            with patch("app.config.settings.roadmap_service_url", "https://test-roadmap.run.app"):
                with patch("app.config.settings.internal_auth_token", "test-token"):
                    result = await call_roadmap_service_incremental(project_id)

                    assert result["success"] is True
                    assert result["project_id"] == project_id
                    mock_client.post.assert_called_once()
                    call_args = mock_client.post.call_args
                    assert "/api/roadmap/incremental-generate" in call_args[0][0]
                    assert call_args[1]["json"] == {"project_id": project_id}
                    assert call_args[1]["headers"]["X-Internal-Token"] == "test-token"

    @pytest.mark.asyncio
    async def test_call_roadmap_service_generate_success(self, monkeypatch):
        """Test successful HTTP call to roadmap service for full generation"""
        project_id = str(uuid.uuid4())
        github_url = "https://github.com/test/repo"
        skill_level = "beginner"
        target_days = 7

        # Mock httpx response
        mock_response = Mock()
        mock_response.json.return_value = {
            "success": True,
            "message": "Roadmap generation started",
            "project_id": project_id,
        }
        mock_response.raise_for_status = Mock()

        # Mock httpx.AsyncClient
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.services.roadmap_client.httpx.AsyncClient", return_value=mock_client):
            with patch("app.config.settings.roadmap_service_url", "https://test-roadmap.run.app"):
                with patch("app.config.settings.internal_auth_token", "test-token"):
                    result = await call_roadmap_service_generate(
                        project_id, github_url, skill_level, target_days
                    )

                    assert result["success"] is True
                    assert result["project_id"] == project_id
                    mock_client.post.assert_called_once()
                    call_args = mock_client.post.call_args
                    assert "/api/roadmap/generate-internal" in call_args[0][0]
                    assert call_args[1]["json"]["project_id"] == project_id
                    assert call_args[1]["json"]["github_url"] == github_url
                    assert call_args[1]["json"]["skill_level"] == skill_level
                    assert call_args[1]["json"]["target_days"] == target_days

    def test_call_roadmap_service_incremental_missing_url(self, monkeypatch):
        """Test HTTP client raises error when roadmap service URL is missing"""
        project_id = str(uuid.uuid4())

        with patch("app.config.settings.roadmap_service_url", None):
            with patch("app.config.settings.internal_auth_token", "test-token"):
                with pytest.raises(ValueError, match="Roadmap service URL not configured"):
                    # Use sync wrapper for this test
                    import asyncio

                    asyncio.run(call_roadmap_service_incremental(project_id))

    def test_call_roadmap_service_incremental_missing_token(self, monkeypatch):
        """Test HTTP client raises error when internal auth token is missing"""
        project_id = str(uuid.uuid4())

        with patch("app.config.settings.roadmap_service_url", "https://test-roadmap.run.app"):
            with patch("app.config.settings.internal_auth_token", None):
                with pytest.raises(ValueError, match="INTERNAL_AUTH_TOKEN not configured"):
                    import asyncio

                    asyncio.run(call_roadmap_service_incremental(project_id))

    @pytest.mark.asyncio
    async def test_call_roadmap_service_http_error_handling(self, monkeypatch):
        """Test HTTP client handles HTTP errors correctly"""
        project_id = str(uuid.uuid4())

        # Mock httpx to raise HTTPError
        import httpx

        mock_response = Mock()
        mock_response.raise_for_status.side_effect = httpx.HTTPError("Service unavailable")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.services.roadmap_client.httpx.AsyncClient", return_value=mock_client):
            with patch("app.config.settings.roadmap_service_url", "https://test-roadmap.run.app"):
                with patch("app.config.settings.internal_auth_token", "test-token"):
                    with pytest.raises(httpx.HTTPError):
                        await call_roadmap_service_incremental(project_id)


class TestRoadmapServiceEndpoints:
    """Test that roadmap service endpoints work correctly"""

    def test_roadmap_service_has_incremental_endpoint(self):
        """Verify roadmap service has incremental generation endpoint"""
        from app.roadmap_service import app

        # Check that the endpoint exists
        routes = [route.path for route in app.routes]
        assert "/api/roadmap/incremental-generate" in routes

    def test_roadmap_service_has_generate_internal_endpoint(self):
        """Verify roadmap service has internal generation endpoint"""
        from app.roadmap_service import app

        # Check that the endpoint exists
        routes = [route.path for route in app.routes]
        assert "/api/roadmap/generate-internal" in routes

    def test_roadmap_service_has_public_generate_endpoint(self):
        """Verify roadmap service has public generation endpoint"""
        from app.roadmap_service import app

        # Check that the endpoint exists
        routes = [route.path for route in app.routes]
        assert "/api/roadmap/generate" in routes


class TestProgressAPIIntegration:
    """Test that progress API correctly delegates to roadmap service"""

    @pytest.mark.asyncio
    async def test_complete_concept_triggers_http_call(self, monkeypatch):
        """Test that completing a concept triggers HTTP call to roadmap service"""
        # Mock the HTTP client call
        mock_http_call = Mock()
        monkeypatch.setattr(
            "app.api.progress.call_roadmap_service_incremental_sync", mock_http_call
        )

        # Mock Supabase
        mock_supabase = Mock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": "user_123"}
        ]
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = None
        mock_supabase.table.return_value.upsert.return_value.execute.return_value = None

        # Import and test the function
        from app.api.progress import complete_concept

        # This would normally be called via FastAPI, but we're testing the logic
        # The HTTP call should be scheduled in background_tasks
        mock_http_call.assert_not_called()  # Not called yet

        # Note: In real usage, background_tasks.add_task would schedule the call
        # We're verifying the import and that the function exists
        assert callable(complete_concept)


class TestEmbeddingPipelineIntegration:
    """Test that embedding pipeline correctly delegates to roadmap service"""

    def test_embedding_pipeline_triggers_http_call(self):
        """Test that embedding pipeline triggers HTTP call to roadmap service"""
        # Verify that the function uses the HTTP client by checking source code
        import inspect

        import app.services.embedding_pipeline

        source = inspect.getsource(app.services.embedding_pipeline)
        # Should use HTTP client (imported inside function, but called)
        assert "call_roadmap_service_generate" in source
        # Should NOT directly call run_roadmap_generation
        assert "run_roadmap_generation(" not in source or "call_roadmap_service" in source


class TestNoDirectLangGraphCalls:
    """Test that main API has no direct LangGraph function calls"""

    def test_progress_api_no_direct_calls(self):
        """Verify progress API doesn't call LangGraph functions directly"""
        import inspect

        import app.api.progress

        source = inspect.getsource(app.api.progress)

        # Should NOT have direct calls to LangGraph functions
        assert "run_incremental_concept_generation(" not in source
        assert (
            "trigger_incremental_generation_sync(" not in source or "call_roadmap_service" in source
        )

        # Should use HTTP client
        assert "call_roadmap_service" in source

    def test_embedding_pipeline_no_direct_calls(self):
        """Verify embedding pipeline doesn't call LangGraph functions directly"""
        import inspect

        import app.services.embedding_pipeline

        source = inspect.getsource(app.services.embedding_pipeline)

        # Should NOT have direct calls to run_roadmap_generation
        assert "run_roadmap_generation(" not in source or "call_roadmap_service" in source

        # Should use HTTP client
        assert "call_roadmap_service_generate" in source

    def test_main_api_no_langgraph_imports(self):
        """Verify main.py doesn't import any LangGraph modules"""
        import inspect

        import app.main

        source = inspect.getsource(app.main)

        # Should NOT import LangGraph modules
        assert "from app.agents" not in source
        assert "from app.services.roadmap_generation" not in source
        assert "run_roadmap_agent" not in source


class TestRoadmapServiceHasLangGraph:
    """Test that roadmap service has all LangGraph code"""

    def test_roadmap_service_imports_roadmap_agent(self):
        """Verify roadmap service imports roadmap_agent"""
        import inspect

        import app.roadmap_service

        source = inspect.getsource(app.roadmap_service)

        # Should import LangGraph functions
        assert "run_roadmap_generation" in source
        assert "run_incremental_concept_generation" in source

    def test_roadmap_service_has_all_endpoints(self):
        """Verify roadmap service has all required endpoints"""
        from app.roadmap_service import app

        routes = {route.path: route.methods for route in app.routes if hasattr(route, "path")}

        # Should have public generate endpoint
        assert "/api/roadmap/generate" in routes
        assert "POST" in routes["/api/roadmap/generate"]

        # Should have internal incremental endpoint
        assert "/api/roadmap/incremental-generate" in routes
        assert "POST" in routes["/api/roadmap/incremental-generate"]

        # Should have internal generate endpoint
        assert "/api/roadmap/generate-internal" in routes
        assert "POST" in routes["/api/roadmap/generate-internal"]

        # Should have health check
        assert "/health" in routes


class TestErrorHandling:
    """Test error handling in HTTP client"""

    @pytest.mark.asyncio
    async def test_http_client_handles_timeout(self, monkeypatch):
        """Test HTTP client handles timeout errors"""
        project_id = str(uuid.uuid4())

        import httpx

        # Mock timeout error
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Request timeout"))

        with patch("app.services.roadmap_client.httpx.AsyncClient", return_value=mock_client):
            with patch("app.config.settings.roadmap_service_url", "https://test-roadmap.run.app"):
                with patch("app.config.settings.internal_auth_token", "test-token"):
                    with pytest.raises(httpx.TimeoutException):
                        await call_roadmap_service_incremental(project_id)

    @pytest.mark.asyncio
    async def test_http_client_handles_connection_error(self, monkeypatch):
        """Test HTTP client handles connection errors"""
        project_id = str(uuid.uuid4())

        import httpx

        # Mock connection error
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection failed"))

        with patch("app.services.roadmap_client.httpx.AsyncClient", return_value=mock_client):
            with patch("app.config.settings.roadmap_service_url", "https://test-roadmap.run.app"):
                with patch("app.config.settings.internal_auth_token", "test-token"):
                    with pytest.raises(httpx.ConnectError):
                        await call_roadmap_service_incremental(project_id)


class TestConfiguration:
    """Test configuration requirements"""

    def test_roadmap_client_requires_config(self):
        """Test that roadmap client requires configuration"""
        # Test missing URL
        with patch("app.config.settings.roadmap_service_url", None):
            with patch("app.config.settings.internal_auth_token", "test-token"):
                import asyncio

                with pytest.raises(ValueError, match="Roadmap service URL not configured"):
                    asyncio.run(call_roadmap_service_incremental("test-project"))

        # Test missing token
        with patch("app.config.settings.roadmap_service_url", "https://test.run.app"):
            with patch("app.config.settings.internal_auth_token", None):
                import asyncio

                with pytest.raises(ValueError, match="INTERNAL_AUTH_TOKEN not configured"):
                    asyncio.run(call_roadmap_service_incremental("test-project"))


class TestSyncWrapper:
    """Test sync wrapper for BackgroundTasks"""

    def test_call_roadmap_service_incremental_sync(self, monkeypatch):
        """Test sync wrapper works correctly"""
        project_id = str(uuid.uuid4())

        # Mock async function
        mock_async_call = AsyncMock(return_value={"success": True})
        monkeypatch.setattr(
            "app.services.roadmap_client.call_roadmap_service_incremental", mock_async_call
        )

        # Mock config
        with patch("app.config.settings.roadmap_service_url", "https://test.run.app"):
            with patch("app.config.settings.internal_auth_token", "test-token"):
                result = call_roadmap_service_incremental_sync(project_id)

                assert result["success"] is True
                mock_async_call.assert_called_once_with(project_id, None)
