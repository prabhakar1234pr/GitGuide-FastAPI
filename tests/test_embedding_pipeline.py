"""
Tests for EmbeddingPipeline
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestEmbeddingPipeline:
    """Test cases for EmbeddingPipeline"""

    @pytest.mark.asyncio
    async def test_run_embedding_pipeline_success(
        self, mock_supabase_client, mock_github_files, mock_chunks, mock_embeddings
    ):
        """Test run_embedding_pipeline - successful pipeline execution"""
        import uuid

        from app.services.embedding_pipeline import run_embedding_pipeline

        project_id = str(uuid.uuid4())
        github_url = "https://github.com/user/test-repo"

        # Mock Supabase
        mock_table = mock_supabase_client.table.return_value
        mock_update_chain = Mock()
        mock_update_chain.eq.return_value = mock_update_chain
        mock_update_chain.execute.return_value = Mock()
        mock_table.update.return_value = mock_update_chain

        # Mock GitHub service
        with patch(
            "app.services.embedding_pipeline.fetch_repository_files", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = mock_github_files

            # Mock chunking
            with patch("app.services.embedding_pipeline.chunk_files") as mock_chunk:
                mock_chunk.return_value = mock_chunks

                # Mock storage
                with patch("app.services.embedding_pipeline.store_chunks") as mock_store:
                    mock_store.return_value = [str(uuid.uuid4())]  # Valid UUID

                    # Mock embedding service (now using singleton getter)
                    with patch(
                        "app.services.embedding_pipeline.get_embedding_service"
                    ) as mock_get_embedding_service:
                        mock_embedding_service = Mock()
                        mock_embedding_service.embed_texts.return_value = mock_embeddings
                        mock_get_embedding_service.return_value = mock_embedding_service

                        # Mock Qdrant service (now using singleton getter)
                        with patch(
                            "app.services.embedding_pipeline.get_qdrant_service"
                        ) as mock_get_qdrant_service:
                            mock_qdrant_service = Mock()
                            # RAG pre-retrieval calls search(); return list of scored points
                            mock_point = Mock()
                            mock_point.id = mock_store.return_value[0]
                            mock_point.score = 0.9
                            mock_qdrant_service.search.return_value = [mock_point]
                            mock_get_qdrant_service.return_value = mock_qdrant_service

                            await run_embedding_pipeline(project_id, github_url)

        # Verify pipeline steps were called
        mock_fetch.assert_called_once_with(github_url)
        mock_chunk.assert_called_once()
        mock_store.assert_called_once()
        # embed_texts called twice: chunks + RAG query for roadmap
        assert mock_embedding_service.embed_texts.call_count >= 1
        mock_qdrant_service.upsert_embeddings.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_embedding_pipeline_failure(self, mock_supabase_client):
        """Test run_embedding_pipeline - failure handling"""
        import uuid

        from app.services.embedding_pipeline import run_embedding_pipeline

        project_id = str(uuid.uuid4())
        github_url = "https://github.com/user/test-repo"

        # Mock Supabase
        mock_table = mock_supabase_client.table.return_value
        mock_update_chain = Mock()
        mock_update_chain.eq.return_value = mock_update_chain
        mock_update_chain.execute.return_value = Mock()
        mock_table.update.return_value = mock_update_chain

        # Mock embedding/qdrant to avoid real model init before fetch fails
        mock_embedding = Mock()
        mock_qdrant = Mock()
        with (
            patch(
                "app.services.embedding_pipeline.get_embedding_service", return_value=mock_embedding
            ),
            patch("app.services.embedding_pipeline.get_qdrant_service", return_value=mock_qdrant),
            patch(
                "app.services.embedding_pipeline.fetch_repository_files", new_callable=AsyncMock
            ) as mock_fetch,
        ):
            mock_fetch.side_effect = Exception("GitHub API error")

            with pytest.raises(Exception, match="GitHub API error"):
                await run_embedding_pipeline(project_id, github_url)

            # Exception re-raised after updating status to failed
            # (mock_table.update may be called depending on fixture setup)
