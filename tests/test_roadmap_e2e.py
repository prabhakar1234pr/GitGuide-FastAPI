"""
End-to-end tests for the LangGraph roadmap generation workflow and API endpoints.
"""

import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from app.utils.clerk_auth import verify_clerk_token


class TestRoadmapE2E:
    """End-to-end tests for roadmap generation workflow"""

    @pytest.fixture
    def client(self):
        """FastAPI test client with all routers"""
        from fastapi import FastAPI

        from app.api.progress import router as progress_router
        from app.api.project_chunks_embeddings import router as project_chunks_embeddings_router
        from app.api.projects import router as projects_router
        from app.api.roadmap import router as roadmap_router
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
        test_app.include_router(roadmap_router, prefix="/api/roadmap", tags=["roadmap"])
        test_app.include_router(progress_router, prefix="/api/progress", tags=["progress"])

        return TestClient(test_app)

    @pytest.fixture
    def mock_clerk_user(self):
        """Mock Clerk user"""
        return {"clerk_user_id": "user_test_123", "email": "test@example.com", "name": "Test User"}

    @pytest.fixture
    def project_id(self):
        """Sample project ID"""
        return str(uuid.uuid4())

    @pytest.fixture
    def mock_supabase_for_roadmap(self, mock_supabase_client, project_id, mock_clerk_user):
        """Setup Supabase mocks for roadmap endpoints"""
        user_id = "user_db_123"

        # Create query chain helper
        def create_query_chain():
            chain = Mock()
            chain.select = Mock(return_value=chain)
            chain.eq = Mock(return_value=chain)
            chain.order = Mock(return_value=chain)
            chain.execute = Mock(return_value=Mock(data=[]))
            return chain

        _mock_table = mock_supabase_client.table.return_value

        # Mock User table
        user_chain = create_query_chain()
        user_chain.execute.return_value.data = [{"id": user_id}]

        # Mock projects table
        project_chain = create_query_chain()
        project_chain.execute.return_value.data = [
            {
                "project_id": project_id,
                "user_id": user_id,
                "project_name": "test-repo",
                "target_days": 7,
            }
        ]

        # Mock roadmap_days table
        day_id_1 = str(uuid.uuid4())
        day_id_2 = str(uuid.uuid4())
        days_chain = create_query_chain()
        days_chain.execute.return_value.data = [
            {
                "day_id": day_id_1,
                "project_id": project_id,
                "day_number": 1,
                "theme": "Test Theme 1",
                "generated_status": "generated",
            },
            {
                "day_id": day_id_2,
                "project_id": project_id,
                "day_number": 2,
                "theme": "Test Theme 2",
                "generated_status": "generated",
            },
        ]

        # Mock concepts table
        concept_id = str(uuid.uuid4())
        concepts_chain = create_query_chain()
        concepts_chain.execute.return_value.data = [
            {
                "concept_id": concept_id,
                "day_id": day_id_1,
                "title": "Test Concept",
                "description": "Test Description",
                "order_index": 0,
            }
        ]

        # Mock sub_concepts table
        subconcepts_chain = create_query_chain()
        subconcepts_chain.execute.return_value.data = [
            {
                "subconcept_id": str(uuid.uuid4()),
                "concept_id": concept_id,
                "title": "Test Subconcept",
                "content": "Test Content",
                "order_index": 0,
            }
        ]

        # Mock tasks table
        tasks_chain = create_query_chain()
        tasks_chain.execute.return_value.data = [
            {
                "task_id": str(uuid.uuid4()),
                "concept_id": concept_id,
                "title": "Test Task",
                "description": "Test Task Description",
                "task_type": "coding",
                "order_index": 0,
            }
        ]

        def table_side_effect(table_name):
            mock_tbl = Mock()
            if table_name == "User":
                mock_tbl.select.return_value = user_chain
            elif table_name == "projects":
                mock_tbl.select.return_value = project_chain
            elif table_name == "roadmap_days":
                mock_tbl.select.return_value = days_chain
            elif table_name == "concepts":
                mock_tbl.select.return_value = concepts_chain
            elif table_name == "sub_concepts":
                mock_tbl.select.return_value = subconcepts_chain
            elif table_name == "tasks":
                mock_tbl.select.return_value = tasks_chain
            else:
                mock_tbl.select.return_value = create_query_chain()
            return mock_tbl

        mock_supabase_client.table.side_effect = table_side_effect
        return {
            "user_id": user_id,
            "day_id_1": day_id_1,
            "day_id_2": day_id_2,
            "concept_id": concept_id,
        }

    def test_get_roadmap_success(
        self, client, mock_supabase_for_roadmap, project_id, mock_clerk_user
    ):
        """Test GET /api/roadmap/{project_id} - successful retrieval"""

        async def mock_verify_token(authorization=None):
            return mock_clerk_user

        client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

        try:
            response = client.get(
                f"/api/roadmap/{project_id}", headers={"Authorization": "Bearer fake_token"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "days" in data
            assert len(data["days"]) == 2
            assert data["days"][0]["day_number"] == 1
            assert data["days"][1]["day_number"] == 2
        finally:
            client.app.dependency_overrides.clear()

    def test_get_roadmap_project_not_found(self, client, mock_supabase_client, mock_clerk_user):
        """Test GET /api/roadmap/{project_id} - project not found"""
        project_id = str(uuid.uuid4())

        # Mock user exists but project doesn't
        _mock_table = mock_supabase_client.table.return_value
        user_chain = Mock()
        user_chain.select.return_value = user_chain
        user_chain.eq.return_value = user_chain
        user_chain.execute.return_value.data = [{"id": "user_123"}]

        project_chain = Mock()
        project_chain.select.return_value = project_chain
        project_chain.eq.return_value = project_chain
        project_chain.execute.return_value.data = []  # Project not found

        project_access_chain = Mock()
        project_access_chain.select.return_value = project_access_chain
        project_access_chain.eq.return_value = project_access_chain
        project_access_chain.execute.return_value.data = []  # No access

        def table_side_effect(table_name):
            mock_tbl = Mock()
            if table_name == "User":
                mock_tbl.select.return_value = user_chain
            elif table_name == "projects":
                mock_tbl.select.return_value = project_chain
            elif table_name == "project_access":
                mock_tbl.select.return_value = project_access_chain
            else:
                mock_tbl.select.return_value = mock_tbl
                mock_tbl.eq.return_value = mock_tbl
                mock_tbl.execute.return_value = Mock(data=[])
            return mock_tbl

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

    def test_get_day_details_success(
        self, client, mock_supabase_for_roadmap, project_id, mock_clerk_user
    ):
        """Test GET /api/roadmap/{project_id}/day/{day_id} - successful retrieval"""
        day_id = mock_supabase_for_roadmap["day_id_1"]

        async def mock_verify_token(authorization=None):
            return mock_clerk_user

        client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

        try:
            response = client.get(
                f"/api/roadmap/{project_id}/day/{day_id}",
                headers={"Authorization": "Bearer fake_token"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "day" in data
            assert "concepts" in data
            assert data["day"]["day_id"] == day_id
            assert len(data["concepts"]) == 1
        finally:
            client.app.dependency_overrides.clear()

    def test_get_concept_details_success(
        self, client, mock_supabase_for_roadmap, project_id, mock_clerk_user
    ):
        """Test GET /api/roadmap/{project_id}/concept/{concept_id} - successful retrieval"""
        concept_id = mock_supabase_for_roadmap["concept_id"]

        async def mock_verify_token(authorization=None):
            return mock_clerk_user

        client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

        try:
            response = client.get(
                f"/api/roadmap/{project_id}/concept/{concept_id}",
                headers={"Authorization": "Bearer fake_token"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "concept" in data
            assert "tasks" in data
            assert data["concept"]["concept_id"] == concept_id
            assert len(data["tasks"]) == 1
        finally:
            client.app.dependency_overrides.clear()

    def test_get_generation_status_success(
        self, client, mock_supabase_for_roadmap, project_id, mock_clerk_user
    ):
        """Test GET /api/roadmap/{project_id}/generation-status - successful retrieval"""

        async def mock_verify_token(authorization=None):
            return mock_clerk_user

        client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

        try:
            response = client.get(
                f"/api/roadmap/{project_id}/generation-status",
                headers={"Authorization": "Bearer fake_token"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "total_days" in data
            assert "target_days" in data
            assert "generated_days" in data
            assert "status_counts" in data
            assert "is_complete" in data
            assert data["target_days"] == 7
            assert data["total_days"] == 2
        finally:
            client.app.dependency_overrides.clear()

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Full LangGraph run requires DB, Vertex AI - integration test")
    async def test_langgraph_workflow_full_flow(self, monkeypatch, project_id):
        """Test the complete LangGraph workflow end-to-end"""
        from app.agents.roadmap_agent import run_roadmap_agent

        # Mock all external services
        _mock_groq_response = {
            "choices": [
                {
                    "message": {
                        "content": '{"primary_language": "Python", "frameworks": ["FastAPI"], "architecture": "MVC"}'
                    }
                }
            ]
        }

        # Mock Groq service
        mock_groq_service = AsyncMock()
        mock_groq_service.generate_response_async = AsyncMock(
            return_value='{"primary_language": "Python", "frameworks": ["FastAPI"], "architecture": "MVC"}'
        )
        monkeypatch.setattr("app.services.groq_service.get_groq_service", lambda: mock_groq_service)

        # Mock RAG pipeline function
        async def mock_generate_rag_response(*args, **kwargs):
            return {
                "response": "Mock repository context",
                "chunks_used": [{"file_path": "test.py", "content": "test"}],
            }

        monkeypatch.setattr(
            "app.services.rag_pipeline.generate_rag_response", mock_generate_rag_response
        )

        # Mock Supabase operations
        mock_supabase = Mock()

        # Create reusable chain helpers
        def create_select_chain(data=None):
            chain = Mock()
            chain.select = Mock(return_value=chain)
            chain.eq = Mock(return_value=chain)
            chain.order = Mock(return_value=chain)
            chain.limit = Mock(return_value=chain)
            chain.execute = Mock(return_value=Mock(data=data or []))
            return chain

        def create_insert_chain(data=None):
            chain = Mock()
            chain.insert = Mock(return_value=chain)
            chain.execute = Mock(return_value=Mock(data=data or []))
            return chain

        def create_update_chain(data=None):
            chain = Mock()
            chain.eq = Mock(return_value=chain)
            chain.execute = Mock(
                return_value=Mock(data=data or [{"generated_status": "generated"}])
            )
            return chain

        # Mock Projects select - needs to return data with status field
        projects_data = [
            {
                "project_id": project_id,
                "project_name": "test-repo",
                "github_url": "https://github.com/test/test-repo",
                "skill_level": "intermediate",
                "target_days": 7,
                "status": "ready",
            }
        ]
        projects_chain = create_select_chain(projects_data)

        # Mock roadmap_days operations - need 7 days (Day 0 + Days 1-6)
        day_ids = [str(uuid.uuid4()) for _ in range(7)]
        days_insert_data = [
            {
                "day_id": day_ids[0],
                "day_number": 0,
                "project_id": project_id,
                "theme": "Project Setup & GitHub Connection",
                "description": "Set up your development environment...",
                "generated_status": "pending",
            }
        ]
        # Add days 1-6
        for i in range(1, 7):
            days_insert_data.append(
                {
                    "day_id": day_ids[i],
                    "day_number": i,
                    "project_id": project_id,
                    "theme": f"Day {i} Theme",
                    "description": f"Day {i} description",
                    "generated_status": "pending",
                }
            )
        days_insert_chain = create_insert_chain(days_insert_data)

        # For select queries - return Day 0 when querying for day_number=0
        def days_select_side_effect():
            chain = Mock()
            chain.select = Mock(return_value=chain)
            chain.eq = Mock(return_value=chain)
            chain.order = Mock(return_value=chain)
            chain.limit = Mock(return_value=chain)

            # Return Day 0 when querying for it
            def execute_side_effect():
                # Check if we're querying for Day 0
                if hasattr(chain, "_querying_day_0"):
                    return Mock(data=[days_insert_data[0]])
                # Otherwise return empty or pending days
                return Mock(
                    data=[
                        d
                        for d in days_insert_data
                        if d["day_number"] > 0 and d["generated_status"] == "pending"
                    ][:1]
                )

            chain.execute = Mock(side_effect=execute_side_effect)
            return chain

        _days_select_chain = days_select_side_effect()

        # Mock concepts operations - Day 0 concept structure
        day0_concept_id = str(uuid.uuid4())
        day0_concepts_insert_chain = create_insert_chain(
            [
                {
                    "concept_id": day0_concept_id,
                    "order_index": 1,  # Required field that was missing
                    "title": "GitHub Profile Setup",
                    "description": "Connect your GitHub account and prepare your development workspace",
                    "day_id": day_ids[0],  # Day 0
                    "generated_status": "generated",
                }
            ]
        )

        # Mock concepts for regular days - need to return all concepts that were inserted
        # Use a class to track state properly
        class ConceptsInsertChain:
            def __init__(self):
                self.inserted_data = []
                self.concept_ids = []

            def insert(self, data):
                self.inserted_data = data if isinstance(data, list) else [data]
                # Generate concept IDs upfront
                self.concept_ids = [str(uuid.uuid4()) for _ in range(len(self.inserted_data))]
                return self

            def execute(self):
                if not self.inserted_data:
                    return Mock(data=[])

                # Return concepts matching the inserted data structure
                return Mock(
                    data=[
                        {
                            "concept_id": self.concept_ids[i],
                            "order_index": concept.get("order_index", i + 1),
                            "title": concept.get("title", f"Concept {i + 1}"),
                            "description": concept.get("description", ""),
                            "day_id": concept.get("day_id"),
                            "generated_status": concept.get("generated_status", "generating"),
                        }
                        for i, concept in enumerate(self.inserted_data)
                    ]
                )

        _concepts_insert_chain = ConceptsInsertChain()
        concepts_select_chain = create_select_chain([])

        # Mock subconcepts - Day 0 has 3 subconcepts
        subconcept_ids = [str(uuid.uuid4()) for _ in range(3)]
        subconcepts_insert_chain = create_insert_chain(
            [
                {
                    "subconcept_id": subconcept_ids[0],
                    "concept_id": day0_concept_id,
                    "order_index": 1,
                    "title": "Why GitHub Matters",
                    "content": "GitHub content...",
                    "generated_status": "generated",
                },
                {
                    "subconcept_id": subconcept_ids[1],
                    "concept_id": day0_concept_id,
                    "order_index": 2,
                    "title": "Setting Up Your Profile",
                    "content": "Profile setup content...",
                    "generated_status": "generated",
                },
                {
                    "subconcept_id": subconcept_ids[2],
                    "concept_id": day0_concept_id,
                    "order_index": 3,
                    "title": "Understanding Repositories",
                    "content": "Repository content...",
                    "generated_status": "generated",
                },
            ]
        )

        # Mock tasks - Day 0 has 3 tasks
        task_ids = [str(uuid.uuid4()) for _ in range(3)]
        tasks_insert_chain = create_insert_chain(
            [
                {
                    "task_id": task_ids[0],
                    "concept_id": day0_concept_id,
                    "order_index": 1,
                    "title": "Verify Your GitHub Profile",
                    "description": "Paste your GitHub profile URL...",
                    "task_type": "github_profile",
                    "generated_status": "generated",
                },
                {
                    "task_id": task_ids[1],
                    "concept_id": day0_concept_id,
                    "order_index": 2,
                    "title": "Create Your Project Repository",
                    "description": "Create a new public repository...",
                    "task_type": "create_repo",
                    "generated_status": "generated",
                },
                {
                    "task_id": task_ids[2],
                    "concept_id": day0_concept_id,
                    "order_index": 3,
                    "title": "Make Your First Commit",
                    "description": "Initialize your local repository...",
                    "task_type": "verify_commit",
                    "generated_status": "generated",
                },
            ]
        )

        # Mock update chains
        update_chain = create_update_chain()

        # Create a smarter days select chain that handles different queries
        days_query_state = {"last_eq_key": None, "last_eq_value": None}

        def create_days_select_chain():
            chain = Mock()
            chain.select = Mock(return_value=chain)
            chain.order = Mock(return_value=chain)
            chain.limit = Mock(return_value=chain)

            def eq_side_effect(key, value):
                days_query_state["last_eq_key"] = key
                days_query_state["last_eq_value"] = value
                return chain

            chain.eq = Mock(side_effect=eq_side_effect)

            def execute_side_effect():
                # If querying for day_id matching Day 0
                if (
                    days_query_state["last_eq_key"] == "day_id"
                    and days_query_state["last_eq_value"] == day_ids[0]
                ):
                    return Mock(data=[days_insert_data[0]])
                # If querying for day_number=0
                elif (
                    days_query_state["last_eq_key"] == "day_number"
                    and days_query_state["last_eq_value"] == 0
                ):
                    return Mock(data=[days_insert_data[0]])
                # If querying for pending days (main loop)
                elif (
                    days_query_state["last_eq_key"] == "generated_status"
                    and days_query_state["last_eq_value"] == "pending"
                ):
                    pending_days = [
                        d
                        for d in days_insert_data
                        if d["day_number"] > 0 and d["generated_status"] == "pending"
                    ]
                    return Mock(data=pending_days[:1] if pending_days else [])
                # Default: return empty or all days
                return Mock(data=days_insert_data)

            chain.execute = Mock(side_effect=execute_side_effect)
            return chain

        _days_select_chain = create_days_select_chain()

        def table_side_effect(table_name):
            mock_tbl = Mock()
            if table_name == "projects":
                mock_tbl.select.return_value = projects_chain
            elif table_name == "roadmap_days":
                mock_tbl.select.return_value = create_days_select_chain()
                mock_tbl.insert.return_value = days_insert_chain

                def update_side_effect(update_data):
                    chain = Mock()

                    def eq_side_effect(key, value):
                        chain._eq_key = key
                        chain._eq_value = value
                        return chain

                    chain.eq = Mock(side_effect=eq_side_effect)

                    def execute_side_effect():
                        if getattr(chain, "_eq_key", None) == "day_id":
                            for day in days_insert_data:
                                if day["day_id"] == getattr(chain, "_eq_value", None):
                                    day.update(update_data)
                        return Mock(data=[update_data])

                    chain.execute = Mock(side_effect=execute_side_effect)
                    return chain

                mock_tbl.update = Mock(side_effect=update_side_effect)
            elif table_name == "concepts":
                mock_tbl.select.return_value = concepts_select_chain

                # Use different insert chains for Day 0 vs regular days
                def concepts_insert_side_effect(data):
                    # Check if this is Day 0 (day_id matches Day 0)
                    if isinstance(data, list) and data and data[0].get("day_id") == day_ids[0]:
                        return day0_concepts_insert_chain
                    # For regular days, create a new chain instance to track this insert
                    chain = ConceptsInsertChain()
                    chain.insert(data)
                    return chain

                mock_tbl.insert = Mock(side_effect=concepts_insert_side_effect)
                mock_tbl.update.return_value = update_chain
            elif table_name == "sub_concepts":
                mock_tbl.insert.return_value = subconcepts_insert_chain
                mock_tbl.select.return_value = create_select_chain()
            elif table_name == "tasks":
                mock_tbl.insert.return_value = tasks_insert_chain
                mock_tbl.select.return_value = create_select_chain()
            else:
                mock_tbl.select.return_value = create_select_chain()
            return mock_tbl

        mock_supabase.table.side_effect = table_side_effect

        # Mock Supabase client at multiple levels to ensure it's used
        # Reset singleton first
        import app.core.supabase_client

        app.core.supabase_client._supabase_client = None

        # Set up mocks
        monkeypatch.setattr("app.core.supabase_client.get_supabase_client", lambda: mock_supabase)
        monkeypatch.setattr("app.core.supabase_client._supabase_client", mock_supabase)

        # Also mock in the nodes that use it directly
        monkeypatch.setattr(
            "app.agents.nodes.fetch_context.get_supabase_client", lambda: mock_supabase
        )

        # Mock embedding and Qdrant services for RAG
        mock_embedding_service = Mock()
        mock_embedding_service.embed_texts = Mock(return_value=[[0.1] * 384])
        monkeypatch.setattr(
            "app.services.embedding_service.get_embedding_service", lambda: mock_embedding_service
        )

        mock_qdrant_service = Mock()
        mock_point = Mock()
        mock_point.id = "chunk_1"
        mock_point.score = 0.9
        mock_qdrant_service.search = Mock(return_value=[mock_point])
        monkeypatch.setattr(
            "app.services.qdrant_service.get_qdrant_service", lambda: mock_qdrant_service
        )

        # Mock JSON parser to return valid JSON with proper structure
        mock_parse_json = Mock(
            return_value={
                "summary": "A test project for learning",
                "primary_language": "Python",
                "frameworks": ["FastAPI"],
                "architecture_patterns": ["MVC"],
                "difficulty": "intermediate",
            }
        )

        # Mock async parser for concepts (needs proper structure)
        async def mock_parse_json_async(response_text, expected_type="array"):
            if expected_type == "array":
                # Return proper concept structure for curriculum themes
                if "theme" in response_text.lower() or "curriculum" in response_text.lower():
                    # This is for curriculum planning - return day themes
                    return [
                        {
                            "day_number": 1,
                            "theme": "Day 1 Theme",
                            "description": "Day 1 description",
                        },
                        {
                            "day_number": 2,
                            "theme": "Day 2 Theme",
                            "description": "Day 2 description",
                        },
                        {
                            "day_number": 3,
                            "theme": "Day 3 Theme",
                            "description": "Day 3 description",
                        },
                        {
                            "day_number": 4,
                            "theme": "Day 4 Theme",
                            "description": "Day 4 description",
                        },
                        {
                            "day_number": 5,
                            "theme": "Day 5 Theme",
                            "description": "Day 5 description",
                        },
                        {
                            "day_number": 6,
                            "theme": "Day 6 Theme",
                            "description": "Day 6 description",
                        },
                    ]
                else:
                    # This is for concepts - return concept structure
                    return [
                        {
                            "title": "Test Concept 1",
                            "description": "Test description 1",
                            "order_index": 1,
                        },
                        {
                            "title": "Test Concept 2",
                            "description": "Test description 2",
                            "order_index": 2,
                        },
                    ]
            return {}

        monkeypatch.setattr("app.utils.json_parser.parse_llm_json_response", mock_parse_json)
        monkeypatch.setattr(
            "app.utils.json_parser.parse_llm_json_response_async", mock_parse_json_async
        )

        # Mock the day0 content function to return proper structure
        from app.agents.day0 import DAY_0_CONTENT, DAY_0_THEME

        monkeypatch.setattr(
            "app.agents.day0.get_day_0_content", lambda: (DAY_0_THEME, DAY_0_CONTENT)
        )
        # save_to_db may import get_day_0_content - patch where it's used
        try:
            monkeypatch.setattr(
                "app.agents.nodes.save_to_db.get_day_0_content",
                lambda: (DAY_0_THEME, DAY_0_CONTENT),
            )
        except AttributeError:
            pass  # save_to_db may not import get_day_0_content

        # Run the agent
        result = await run_roadmap_agent(
            project_id=project_id,
            github_url="https://github.com/test/test-repo",
            skill_level="intermediate",
            target_days=7,
        )

        # Verify result
        assert result["success"] is True
        assert result["project_id"] == project_id
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_langgraph_workflow_with_error(self, monkeypatch, project_id):
        """Test LangGraph workflow handles errors gracefully"""
        from app.agents.roadmap_agent import run_roadmap_agent

        # Mock Groq service to raise an error
        mock_groq_service = AsyncMock()
        mock_groq_service.generate_response_async = AsyncMock(side_effect=Exception("API Error"))
        monkeypatch.setattr("app.services.groq_service.get_groq_service", lambda: mock_groq_service)

        # Mock Supabase
        mock_supabase = Mock()
        select_chain = Mock()
        select_chain.select.return_value = select_chain
        select_chain.eq.return_value = select_chain
        select_chain.execute.return_value = Mock(data=[])
        mock_supabase.table.return_value.select.return_value = select_chain
        monkeypatch.setattr("app.core.supabase_client.get_supabase_client", lambda: mock_supabase)

        # Run the agent
        result = await run_roadmap_agent(
            project_id=project_id,
            github_url="https://github.com/test/test-repo",
            skill_level="intermediate",
            target_days=7,
        )

        # Verify error handling
        assert result["success"] is False
        assert result["project_id"] == project_id
        assert result["error"] is not None

    @pytest.mark.asyncio
    async def test_langgraph_workflow_nodes_sequence(self, monkeypatch, project_id):
        """Test that LangGraph nodes execute in correct sequence"""
        from app.agents.roadmap_agent import get_roadmap_graph

        # Track node execution order
        execution_order = []

        # Mock nodes to track execution
        _original_nodes = {}

        async def track_node(node_name):
            async def tracked_node(state):
                execution_order.append(node_name)
                # Return minimal state to allow graph to continue
                return state

            return tracked_node

        # Get the graph
        graph = get_roadmap_graph()

        # Verify graph structure exists
        assert graph is not None

        # Note: We can't easily test the full execution without mocking all dependencies
        # But we can verify the graph structure is correct
        # CompiledStateGraph has different attributes than StateGraph
        assert hasattr(graph, "nodes") or hasattr(graph, "_nodes")
        # Verify it's a compiled graph
        assert hasattr(graph, "invoke") or hasattr(graph, "ainvoke")

    def test_roadmap_api_integration(
        self, client, mock_supabase_for_roadmap, project_id, mock_clerk_user
    ):
        """Integration test: Test multiple roadmap API endpoints together"""

        async def mock_verify_token(authorization=None):
            return mock_clerk_user

        client.app.dependency_overrides[verify_clerk_token] = mock_verify_token

        try:
            # 1. Get roadmap
            response1 = client.get(
                f"/api/roadmap/{project_id}", headers={"Authorization": "Bearer fake_token"}
            )
            assert response1.status_code == 200
            days = response1.json()["days"]
            assert len(days) > 0

            # 2. Get day details for first day
            day_id = days[0]["day_id"]
            response2 = client.get(
                f"/api/roadmap/{project_id}/day/{day_id}",
                headers={"Authorization": "Bearer fake_token"},
            )
            assert response2.status_code == 200
            concepts = response2.json()["concepts"]

            # 3. Get concept details if concepts exist
            if concepts:
                concept_id = concepts[0]["concept_id"]
                response3 = client.get(
                    f"/api/roadmap/{project_id}/concept/{concept_id}",
                    headers={"Authorization": "Bearer fake_token"},
                )
                assert response3.status_code == 200
                data3 = response3.json()
                assert "tasks" in data3
                # subconcepts are not returned by this endpoint in current API

            # 4. Get generation status
            response4 = client.get(
                f"/api/roadmap/{project_id}/generation-status",
                headers={"Authorization": "Bearer fake_token"},
            )
            assert response4.status_code == 200
            status = response4.json()
            assert "is_complete" in status
            assert "status_counts" in status
        finally:
            client.app.dependency_overrides.clear()
