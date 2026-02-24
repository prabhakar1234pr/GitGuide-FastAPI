"""
Pytest configuration and shared fixtures
"""

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def reset_singletons(monkeypatch):
    """Reset singleton clients before each test"""
    # Reset Supabase singleton
    import app.core.supabase_client

    monkeypatch.setattr(app.core.supabase_client, "_supabase_client", None)

    # Reset Qdrant singleton
    import app.core.qdrant_client

    monkeypatch.setattr(app.core.qdrant_client, "_qdrant_client", None)


@pytest.fixture(autouse=True)
def mock_groq_service_default(monkeypatch, request):
    """Prevent real Groq API calls during tests."""
    if "test_groq_service.py" in request.node.nodeid:
        return

    class DummyGroqService:
        async def generate_response_async(self, user_query, system_prompt="", context=""):
            system_prompt_lower = (system_prompt or "").lower()
            if "json array" in system_prompt_lower:
                return '[{"order_index": 1, "title": "Sample Item", "description": "Sample description"}]'
            return (
                '{"summary": "ok", "primary_language": "Python", "frameworks": [], '
                '"architecture_patterns": [], "difficulty": "beginner", '
                '"content": "Sample content", "estimated_minutes": 10}'
            )

        def generate_response(self, *args, **kwargs):
            return "final answer"

    dummy_service = DummyGroqService()

    # Patch all modules that import get_groq_service directly
    monkeypatch.setattr("app.services.groq_service.get_groq_service", lambda: dummy_service)
    # Only patch if the attribute exists (some modules may not have it)
    try:
        import app.agents.nodes.analyze_repo

        if hasattr(app.agents.nodes.analyze_repo, "get_groq_service"):
            monkeypatch.setattr(
                "app.agents.nodes.analyze_repo.get_groq_service", lambda: dummy_service
            )
    except (AttributeError, ImportError):
        pass
    try:
        import app.agents.nodes.plan_curriculum

        if hasattr(app.agents.nodes.plan_curriculum, "get_groq_service"):
            monkeypatch.setattr(
                "app.agents.nodes.plan_curriculum.get_groq_service", lambda: dummy_service
            )
    except (AttributeError, ImportError):
        pass
    try:
        import app.agents.nodes.generate_content

        if hasattr(app.agents.nodes.generate_content, "get_groq_service"):
            monkeypatch.setattr(
                "app.agents.nodes.generate_content.get_groq_service", lambda: dummy_service
            )
    except (AttributeError, ImportError):
        pass
    monkeypatch.setattr("app.services.rag_pipeline.get_groq_service", lambda: dummy_service)


@pytest.fixture
def client():
    """FastAPI test client with dependency overrides"""
    # Create a fresh app instance for each test
    from fastapi import FastAPI

    from app.api.project_chunks_embeddings import router as project_chunks_embeddings_router
    from app.api.projects import router as projects_router
    from app.api.routes import router
    from app.api.users import router as users_router
    from app.config import settings

    test_app = FastAPI(title=settings.app_name, debug=settings.debug)
    test_app.include_router(router, prefix="/api")
    test_app.include_router(users_router, prefix="/api/users", tags=["users"])
    test_app.include_router(projects_router, prefix="/api/projects", tags=["projects"])
    test_app.include_router(
        project_chunks_embeddings_router, prefix="/api/project_chunks_embeddings"
    )

    return TestClient(test_app)


@pytest.fixture
def mock_supabase_client(monkeypatch):
    """Mock Supabase client"""
    mock_client = Mock()

    # Create a helper function to build query chains
    def create_query_chain():
        chain = Mock()
        chain.select = Mock(return_value=chain)
        chain.eq = Mock(return_value=chain)
        chain.ilike = Mock(return_value=chain)
        chain.order = Mock(return_value=chain)
        chain.range = Mock(return_value=chain)
        chain.insert = Mock(return_value=chain)
        chain.update = Mock(return_value=chain)
        chain.delete = Mock(return_value=chain)
        exec_result = Mock()
        exec_result.data = []
        chain.execute = Mock(return_value=exec_result)
        return chain

    # Create default mock table
    mock_table = Mock()
    mock_table.select = Mock(return_value=create_query_chain())
    mock_table.insert = Mock(return_value=create_query_chain())
    mock_table.update = Mock(return_value=create_query_chain())
    mock_table.delete = Mock(return_value=create_query_chain())

    # Default table return
    mock_client.table = Mock(return_value=mock_table)

    # Override the function AND reset singleton
    def get_mock_client():
        return mock_client

    monkeypatch.setattr("app.core.supabase_client.get_supabase_client", get_mock_client)
    monkeypatch.setattr("app.core.supabase_client._supabase_client", mock_client)

    return mock_client


@pytest.fixture
def mock_qdrant_client(monkeypatch):
    """Mock Qdrant client"""
    mock_client = Mock()

    # Mock collections
    mock_collection = Mock()
    mock_collection.name = "gitguide_chunks"
    mock_collections = Mock()
    mock_collections.collections = [mock_collection]  # Collection exists
    mock_client.get_collections.return_value = mock_collections

    # Mock other methods
    mock_client.create_collection = Mock()
    mock_client.create_payload_index = Mock()
    mock_client.upsert = Mock()
    mock_client.delete = Mock()

    # Mock count with proper structure
    mock_count_result = Mock()
    mock_count_result.count = 0
    mock_client.count = Mock(return_value=mock_count_result)

    # Mock scroll
    mock_client.scroll = Mock(return_value=([], None))

    # Mock search
    mock_search_result = Mock()
    mock_search_result.id = str(uuid.uuid4())
    mock_search_result.score = 0.95
    mock_client.search = Mock(return_value=[mock_search_result])

    # Override the function AND reset singleton
    def get_mock_client():
        return mock_client

    monkeypatch.setattr("app.core.qdrant_client.get_qdrant_client", get_mock_client)
    monkeypatch.setattr("app.core.qdrant_client._qdrant_client", mock_client)

    return mock_client


@pytest.fixture
def mock_clerk_user():
    """Mock Clerk user info"""
    return {"clerk_user_id": "user_123", "email": "test@example.com", "name": "Test User"}


@pytest.fixture
def mock_github_files():
    """Mock GitHub repository files"""
    return [
        {
            "file_path": "test.py",
            "content": "def hello():\n    print('Hello, World!')",
            "language": "python",
        },
        {
            "file_path": "README.md",
            "content": "# Test Project\n\nThis is a test project.",
            "language": "markdown",
        },
    ]


@pytest.fixture
def mock_chunks():
    """Mock text chunks with valid UUID project_id"""
    return [
        {
            "project_id": str(uuid.uuid4()),
            "file_path": "test.py",
            "chunk_index": 0,
            "language": "python",
            "content": "def hello():\n    print('Hello, World!')",
            "token_count": 10,
        }
    ]


@pytest.fixture
def mock_embeddings():
    """Mock embeddings"""
    return [[0.1] * 384]  # 384-dimensional vector


@pytest.fixture
def mock_verify_clerk_token(mock_clerk_user):
    """Mock Clerk token verification"""

    async def verify_token(*args, **kwargs):
        return mock_clerk_user

    return verify_token
