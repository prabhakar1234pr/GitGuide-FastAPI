"""
Tests for application configuration (config.py)
"""

from app.config import Settings, get_settings


class TestConfig:
    """Test configuration loading and parsing"""

    def test_default_settings(self):
        """Test that default settings are properly set"""
        settings = Settings(
            _env_file=None,
        )
        assert settings.app_name == "AI Tutor for GitHub Repositories"
        assert settings.host == "127.0.0.1"
        assert settings.port == 8000
        assert settings.jwt_algorithm == "HS256"
        assert settings.jwt_expiration_minutes == 60
        assert settings.environment == "development"
        assert settings.embedding_provider == "gemini_api"
        assert settings.smtp_port == 587
        assert settings.smtp_use_tls is True
        assert settings.frontend_base_url == "http://localhost:3000"

    def test_debug_parsing_true_values(self):
        """Test debug field accepts various true-like values"""
        for val in [True, "true", "1", "yes", "on", "True", "TRUE"]:
            settings = Settings(debug=val, _env_file=None)
            assert settings.debug is True, f"Failed for value: {val}"

    def test_debug_parsing_false_values(self):
        """Test debug field accepts various false-like values"""
        for val in [False, "false", "0", "no", "off"]:
            settings = Settings(debug=val, _env_file=None)
            assert settings.debug is False, f"Failed for value: {val}"

    def test_cors_origins_string_parsing(self):
        """Test CORS origins parses comma-separated strings"""
        settings = Settings(
            cors_origins="https://example.com,https://api.example.com",
            _env_file=None,
        )
        assert isinstance(settings.cors_origins, list)
        assert len(settings.cors_origins) == 2
        assert "https://example.com" in settings.cors_origins
        assert "https://api.example.com" in settings.cors_origins

    def test_cors_origins_wildcard(self):
        """Test CORS origins handles wildcard"""
        settings = Settings(cors_origins="*", _env_file=None)
        assert settings.cors_origins == ["*"]

    def test_cors_origins_list(self):
        """Test CORS origins accepts list directly"""
        origins = ["https://example.com"]
        settings = Settings(cors_origins=origins, _env_file=None)
        assert settings.cors_origins == origins

    def test_optional_fields_default_none(self):
        """Test that optional fields default to None"""
        settings = Settings(_env_file=None)
        assert settings.database_url is None
        assert settings.supabase_url is None
        assert settings.qdrant_url is None
        assert settings.groq_api_key is None
        assert settings.gemini_api_key is None
        assert settings.clerk_secret_key is None
        assert settings.redis_url is None
        assert settings.roadmap_service_url is None

    def test_get_settings_singleton(self):
        """Test that get_settings returns cached instance"""
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_embedding_defaults(self):
        """Test embedding configuration defaults"""
        settings = Settings(_env_file=None)
        assert settings.chunk_size == 1000
        assert settings.chunk_overlap == 200
        assert settings.max_files_per_project == 500
        assert settings.max_text_size_mb == 2.5
        assert settings.max_chunks_per_project == 500
