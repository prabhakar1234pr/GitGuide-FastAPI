"""
Tests for QdrantService
"""

from unittest.mock import Mock, patch

# Embedding dim from text-embedding-005 (default)
EMBEDDING_DIM = 768


class TestQdrantService:
    """Test cases for QdrantService"""

    @patch("app.services.embedding_service.get_embedding_service")
    def test_ensure_collection_creates_new(self, mock_get_embedding, mock_qdrant_client):
        """Test collection creation when it doesn't exist"""
        from app.services.qdrant_service import QdrantService

        mock_embedding = Mock()
        mock_embedding.embed_texts.return_value = [[0.1] * EMBEDDING_DIM]
        mock_get_embedding.return_value = mock_embedding

        # Mock: collection doesn't exist
        mock_qdrant_client.get_collections.return_value.collections = []

        QdrantService()

        # Verify collection was created
        mock_qdrant_client.create_collection.assert_called_once()
        call_args = mock_qdrant_client.create_collection.call_args
        assert call_args[1]["collection_name"] == "gitguide_chunks"
        assert call_args[1]["vectors_config"]["size"] == EMBEDDING_DIM

    @patch("app.services.embedding_service.get_embedding_service")
    def test_ensure_collection_exists(self, mock_get_embedding, mock_qdrant_client):
        """Test collection check when it already exists"""
        from app.services.qdrant_service import QdrantService

        mock_embedding = Mock()
        mock_embedding.embed_texts.return_value = [[0.1] * EMBEDDING_DIM]
        mock_get_embedding.return_value = mock_embedding

        # Mock: collection exists with matching dimension
        mock_collection = Mock()
        mock_collection.name = "gitguide_chunks"
        mock_qdrant_client.get_collections.return_value.collections = [mock_collection]
        mock_coll_info = Mock()
        mock_coll_info.config.params.vectors.size = EMBEDDING_DIM
        mock_qdrant_client.get_collection.return_value = mock_coll_info

        QdrantService()

        # Verify collection was not created
        mock_qdrant_client.create_collection.assert_not_called()

    def test_upsert_embeddings_success(self, mock_qdrant_client):
        """Test upsert_embeddings - successful upsert"""
        import uuid

        from app.services.qdrant_service import QdrantService

        # Setup
        mock_qdrant_client.get_collections.return_value.collections = []
        service = QdrantService()

        project_id = str(uuid.uuid4())
        chunk_ids = [str(uuid.uuid4()), str(uuid.uuid4())]  # Valid UUIDs
        embeddings = [[0.1] * 384, [0.2] * 384]
        metadatas = [
            {"file_path": "test.py", "language": "python"},
            {"file_path": "test2.py", "language": "python"},
        ]

        service.upsert_embeddings(project_id, chunk_ids, embeddings, metadatas)

        # Verify upsert was called
        mock_qdrant_client.upsert.assert_called_once()
        call_args = mock_qdrant_client.upsert.call_args
        assert call_args[1]["collection_name"] == "gitguide_chunks"
        assert len(call_args[1]["points"]) == 2

    def test_upsert_embeddings_empty(self, mock_qdrant_client):
        """Test upsert_embeddings - empty input"""
        import uuid

        from app.services.qdrant_service import QdrantService

        # Setup
        mock_qdrant_client.get_collections.return_value.collections = []
        service = QdrantService()

        service.upsert_embeddings(str(uuid.uuid4()), [], [], [])

        # Verify upsert was not called
        mock_qdrant_client.upsert.assert_not_called()

    def test_delete_points_by_project_id_with_filter(self, mock_qdrant_client):
        """Test delete_points_by_project_id - using filter"""
        from app.services.qdrant_service import QdrantService

        # Setup
        mock_qdrant_client.get_collections.return_value.collections = []
        service = QdrantService()

        # Mock: filter operations work
        # First count returns 5, second count (after deletion) returns 0
        mock_count_result_before = Mock()
        mock_count_result_before.count = 5
        mock_count_result_after = Mock()
        mock_count_result_after.count = 0
        mock_qdrant_client.count.side_effect = [mock_count_result_before, mock_count_result_after]

        project_id = "project_123"
        result = service.delete_points_by_project_id(project_id)

        # Verify deletion was called
        assert mock_qdrant_client.delete.called or mock_qdrant_client.count.called
        assert result == 5

    def test_delete_points_by_project_id_scroll_method(self, mock_qdrant_client):
        """Test delete_points_by_project_id - using scroll method"""
        from app.services.qdrant_service import QdrantService

        # Setup
        mock_qdrant_client.get_collections.return_value.collections = []
        service = QdrantService()

        # Mock: filter operations fail (no index)
        mock_qdrant_client.count.side_effect = Exception("Index required")

        # Mock: scroll returns points
        mock_point = Mock()
        mock_point.id = "point_1"
        mock_point.payload = {"project_id": "project_123"}
        mock_qdrant_client.scroll.return_value = ([mock_point], None)

        project_id = "project_123"
        service.delete_points_by_project_id(project_id)

        # Verify scroll was called
        assert mock_qdrant_client.scroll.called

    def test_search_success(self, mock_qdrant_client):
        """Test search - successful search"""
        from app.services.qdrant_service import QdrantService

        # Setup
        mock_qdrant_client.get_collections.return_value.collections = []
        service = QdrantService()

        # Mock search results
        mock_result = Mock()
        mock_result.id = "chunk_1"
        mock_result.score = 0.95
        mock_query_response = Mock()
        mock_query_response.points = [mock_result]
        mock_qdrant_client.query_points.return_value = mock_query_response

        project_id = "project_123"
        query_embedding = [0.1] * 384

        result = service.search(project_id, query_embedding, limit=5)

        # Verify search was called
        mock_qdrant_client.query_points.assert_called_once()
        call_args = mock_qdrant_client.query_points.call_args
        assert call_args[1]["collection_name"] == "gitguide_chunks"
        assert call_args[1]["limit"] == 5
        assert call_args[1]["query"] == query_embedding
        assert len(result) == 1
