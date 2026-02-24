"""
Tests for EmbeddingService
"""

from unittest.mock import Mock, patch

import numpy as np


class TestEmbeddingService:
    """Test cases for EmbeddingService"""

    def test_embed_texts_success(self):
        """Test embed_texts - successful embedding generation (mocked provider)"""
        import app.services.embedding_service as emb_mod

        emb_mod._embedding_service_instance = None

        from app.services.embedding_service import EmbeddingService

        mock_embeddings = [[0.1] * 384, [0.2] * 384]
        with patch.object(EmbeddingService, "_embed_gemini_api", return_value=mock_embeddings):
            with patch.object(EmbeddingService, "_embed_vertex_ai", return_value=mock_embeddings):
                with patch.object(EmbeddingService, "_embed_local", return_value=mock_embeddings):
                    with patch.object(
                        EmbeddingService, "_embed_openai", return_value=mock_embeddings
                    ):
                        service = EmbeddingService.__new__(EmbeddingService)
                        service.provider = "gemini_api"
                        service._gemini_api_client = Mock()
                        texts = ["Hello world", "Test text"]
                        embeddings = service.embed_texts(texts)

        assert len(embeddings) == 2
        assert len(embeddings[0]) == 384

    def test_embed_texts_empty(self):
        """Test embed_texts - empty input returns empty list"""
        from app.services.embedding_service import EmbeddingService

        service = EmbeddingService.__new__(EmbeddingService)
        service.provider = "gemini_api"
        result = service.embed_texts([])
        assert result == []

    @patch("sentence_transformers.SentenceTransformer")
    def test_embed_texts_batch_processing(self, mock_st_class, monkeypatch):
        """Test embed_texts - batch processing uses batch_size=32 for local provider"""
        monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
        monkeypatch.setenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
        # Force config reload to pick up env (clear lru_cache)
        from app.config import get_settings

        get_settings.cache_clear()

        mock_model = Mock()
        mock_model.encode.return_value = np.array([[0.1] * 384] * 50)
        mock_st_class.return_value = mock_model

        import app.services.embedding_service as emb_mod

        emb_mod._embedding_service_instance = None

        from app.services.embedding_service import EmbeddingService

        service = EmbeddingService()
        texts = ["Text"] * 50
        embeddings = service.embed_texts(texts)

        assert len(embeddings) == 50
        call_kwargs = mock_model.encode.call_args[1]
        assert call_kwargs.get("batch_size") == 32
