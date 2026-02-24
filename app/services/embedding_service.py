"""
Embedding Service with support for multiple providers:
- vertex_ai: Google Vertex AI (requires GCP project + service account with Vertex AI User)
- gemini_api: Gemini API with API key (no GCP permissions needed)
- openai: OpenAI embeddings
- huggingface / local: Hugging Face / sentence-transformers
"""

import logging
import os
import time
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Lazy singleton instance
_embedding_service_instance = None


def get_embedding_service() -> "EmbeddingService":
    """
    Get or create singleton EmbeddingService instance (lazy initialization).

    Returns:
        EmbeddingService: Singleton instance with loaded model/provider
    """
    global _embedding_service_instance

    if _embedding_service_instance is None:
        _embedding_service_instance = EmbeddingService()
        logger.info("✅ EmbeddingService ready")

    return _embedding_service_instance


class EmbeddingService:
    """
    Embedding service supporting multiple providers:
    - vertex_ai: Google Vertex AI (requires GCP + service account)
    - gemini_api: Gemini API with API key (no GCP permissions)
    - openai: OpenAI embeddings
    - huggingface / local: Hugging Face / sentence-transformers
    """

    def __init__(self):
        """Initialize EmbeddingService based on configured provider."""
        self.provider = settings.embedding_provider.lower()
        self.model = None
        self._vertex_ai_client = None
        self._gemini_api_client = None
        self._openai_client = None

        logger.info(f"🤖 Initializing EmbeddingService with provider: {self.provider}")

        if self.provider == "vertex_ai":
            self._init_vertex_ai()
        elif self.provider == "gemini_api":
            self._init_gemini_api()
        elif self.provider == "openai":
            self._init_openai()
        elif self.provider == "huggingface":
            self._init_huggingface()
        elif self.provider == "local":
            self._init_local()
        else:
            raise ValueError(
                f"Unknown embedding provider: {self.provider}. "
                "Supported: vertex_ai, gemini_api, openai, huggingface, local"
            )

    def _init_vertex_ai(self):
        """Initialize Vertex AI embeddings (recommended for GCP)."""
        try:
            import json
            from pathlib import Path

            import vertexai
            from vertexai.language_models import TextEmbeddingModel

            from app.config import PROJECT_ROOT

            # Check for service account (preferred method - uses GCP free credits)
            # First check environment variable (set by startup.sh in Cloud Run)
            creds_path = None
            if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
                creds_path = Path(os.environ["GOOGLE_APPLICATION_CREDENTIALS"])
                logger.info(f"📁 Found GOOGLE_APPLICATION_CREDENTIALS env var: {creds_path}")
            elif settings.google_application_credentials:
                creds_path = Path(settings.google_application_credentials)
                if not creds_path.is_absolute():
                    creds_path = PROJECT_ROOT / creds_path
                logger.info(f"📁 Found google_application_credentials in settings: {creds_path}")

            if creds_path and creds_path.exists():
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(creds_path)
                project_id = settings.gcp_project_id
                if not project_id:
                    # Try to read project_id from JSON
                    try:
                        with open(creds_path) as f:
                            creds_data = json.load(f)
                            project_id = creds_data.get("project_id")
                            if project_id:
                                logger.info(f"✅ Found project_id in JSON: {project_id}")
                    except Exception as e:
                        logger.error(f"❌ Failed to read project_id from JSON: {e}")

                if not project_id:
                    raise ValueError(
                        "GCP_PROJECT_ID is required when using service account. "
                        "Set it in .env or ensure it's in your JSON file."
                    )

                vertexai.init(project=project_id, location=settings.gcp_location)
                logger.info(f"✅ Using Vertex AI with Service Account (project: {project_id})")
            else:
                if creds_path:
                    logger.warning(f"⚠️ Service account file not found: {creds_path}")
                logger.info("   Falling back to Application Default Credentials")

            # Fallback: Use Application Default Credentials (ADC) on GCP (Cloud Run / GCE)
            # This works when the Cloud Run service is configured with a runtime service account
            if not creds_path or not creds_path.exists():
                try:
                    import google.auth

                    creds, adc_project_id = google.auth.default()
                    if creds:
                        project_id = settings.gcp_project_id or adc_project_id
                        if not project_id:
                            raise ValueError(
                                "GCP_PROJECT_ID is required when using Application Default Credentials. "
                                "Set it as an environment variable (GCP_PROJECT_ID)."
                            )
                        vertexai.init(project=project_id, location=settings.gcp_location)
                        logger.info(
                            f"✅ Using Vertex AI with Application Default Credentials (project: {project_id})"
                        )
                    else:
                        raise ValueError("Failed to get Application Default Credentials")
                except ImportError as import_err:
                    # If google.auth not available, require explicit config
                    if not settings.gcp_project_id:
                        raise ValueError(
                            "GCP_PROJECT_ID is required for Vertex AI embeddings. Set it in your .env file."
                        ) from import_err
                    vertexai.init(project=settings.gcp_project_id, location=settings.gcp_location)
                    logger.info(
                        f"✅ Using Vertex AI (project: {settings.gcp_project_id}, "
                        "assuming credentials are configured)"
                    )

            # Initialize the embedding model
            logger.info(f"🔧 Loading embedding model: {settings.embedding_model_name}...")
            try:
                self._vertex_ai_client = TextEmbeddingModel.from_pretrained(
                    settings.embedding_model_name
                )
                logger.info(f"✅ Vertex AI embeddings initialized: {settings.embedding_model_name}")
            except Exception as model_error:
                error_msg = str(model_error).lower()
                logger.error(
                    f"❌ Failed to load embedding model '{settings.embedding_model_name}': {model_error}"
                )

                # Provide helpful error messages based on error type
                if "not found" in error_msg or "404" in error_msg or "does not exist" in error_msg:
                    logger.error("=" * 70)
                    logger.error("🔧 TROUBLESHOOTING: Model Not Found")
                    logger.error("=" * 70)
                    logger.error("Possible issues:")
                    logger.error("1. Vertex AI API not enabled in your GCP project")
                    logger.error(
                        f"   → Enable at: https://console.cloud.google.com/apis/library/aiplatform.googleapis.com?project={project_id}"
                    )
                    logger.error("2. Model name might be incorrect or not available in your region")
                    logger.error("   Valid model names:")
                    logger.error("   - gemini-embedding-001 (recommended, latest)")
                    logger.error("   - text-embedding-005 (English + code)")
                    logger.error("   - text-multilingual-embedding-002 (multilingual)")
                    logger.error("3. Check if the model is available in your GCP location")
                    logger.error(f"   Current location: {settings.gcp_location}")
                    logger.error("=" * 70)
                elif "permission" in error_msg or "403" in error_msg:
                    logger.error("=" * 70)
                    logger.error("🔐 TROUBLESHOOTING: Permission Denied")
                    logger.error("=" * 70)
                    logger.error("The service account needs 'Vertex AI User' role")
                    logger.error(
                        f"   → Grant at: https://console.cloud.google.com/iam-admin/iam?project={project_id}"
                    )
                    logger.error("=" * 70)
                else:
                    logger.error(f"   Error details: {type(model_error).__name__}: {model_error}")

                raise ValueError(
                    f"Failed to load embedding model '{settings.embedding_model_name}'. "
                    f"Error: {model_error}. See logs above for troubleshooting steps."
                ) from model_error
        except ImportError as e:
            raise ImportError(
                "google-cloud-aiplatform is required for Vertex AI embeddings. "
                "Install it: pip install google-cloud-aiplatform"
            ) from e
        except Exception as e:
            logger.error(
                f"❌ Failed to initialize Vertex AI: {type(e).__name__}: {e}", exc_info=True
            )
            raise

    def _init_gemini_api(self):
        """Initialize Gemini API embeddings (uses GEMINI_API_KEY, no GCP permissions)."""
        try:
            from google import genai

            api_key = settings.gemini_api_key
            if not api_key:
                raise ValueError(
                    "GEMINI_API_KEY is required for Gemini API embeddings. Set it in your .env file."
                )

            self._gemini_api_client = genai.Client(api_key=api_key)
            model_name = (
                settings.embedding_model_name
                if settings.embedding_model_name.startswith("models/")
                else f"models/{settings.embedding_model_name}"
            )
            logger.info(f"✅ Gemini API embeddings initialized: {model_name}")
        except ImportError as e:
            raise ImportError(
                "google-genai is required for Gemini API embeddings. "
                "Install it: pip install google-genai"
            ) from e
        except Exception as e:
            logger.error(f"Failed to initialize Gemini API: {e}")
            raise

    def _init_openai(self):
        """Initialize OpenAI embeddings."""
        try:
            from openai import OpenAI

            api_key = settings.openai_api_key
            if not api_key:
                raise ValueError(
                    "OPENAI_API_KEY is required for OpenAI embeddings. Set it in your .env file."
                )

            self._openai_client = OpenAI(api_key=api_key)
            logger.info(f"✅ OpenAI embeddings initialized: {settings.openai_embedding_model}")
        except ImportError as e:
            raise ImportError(
                "openai package is required for OpenAI embeddings. Install it: pip install openai"
            ) from e
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI: {e}")
            raise

    def _init_huggingface(self):
        """Initialize Hugging Face embeddings with API token."""
        try:
            from sentence_transformers import SentenceTransformer

            # Set HF token if provided
            if settings.huggingface_token:
                os.environ["HF_TOKEN"] = settings.huggingface_token
                logger.info("✅ Using Hugging Face API token")

            model_name = (
                settings.embedding_model_name
                if settings.embedding_model_name.startswith("sentence-transformers/")
                else f"sentence-transformers/{settings.embedding_model_name}"
            )

            logger.info(f"Loading Hugging Face model: {model_name}")
            self.model = SentenceTransformer(model_name)
            logger.info(f"✅ Hugging Face embeddings initialized: {model_name}")
        except ImportError as e:
            raise ImportError(
                "sentence-transformers is required for Hugging Face embeddings. "
                "Install it: pip install sentence-transformers"
            ) from e
        except Exception as e:
            logger.error(f"Failed to initialize Hugging Face: {e}")
            raise

    def _init_local(self):
        """Initialize local sentence-transformers model."""
        try:
            from sentence_transformers import SentenceTransformer

            model_name = (
                settings.embedding_model_name
                if settings.embedding_model_name.startswith("sentence-transformers/")
                else f"sentence-transformers/{settings.embedding_model_name}"
            )

            logger.info(f"Loading local model: {model_name}")
            self.model = SentenceTransformer(model_name)
            logger.info(f"✅ Local embeddings initialized: {model_name}")
        except ImportError as e:
            raise ImportError(
                "sentence-transformers is required for local embeddings. "
                "Install it: pip install sentence-transformers"
            ) from e
        except Exception as e:
            logger.error(f"Failed to initialize local model: {e}")
            raise

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for a list of texts.

        Args:
            texts: List of text strings to embed

        Returns:
            List of embedding vectors (each is a list of floats)
        """
        if not texts:
            logger.warning("⚠️  No texts provided for embedding generation")
            return []

        logger.info(f"🧮 Generating embeddings for {len(texts)} texts using {self.provider}")
        start_time = time.time()

        # Calculate total text size
        total_chars = sum(len(text) for text in texts)
        logger.debug(f"   Total characters: {total_chars:,}")

        try:
            if self.provider == "vertex_ai":
                embeddings = self._embed_vertex_ai(texts)
            elif self.provider == "gemini_api":
                embeddings = self._embed_gemini_api(texts)
            elif self.provider == "openai":
                embeddings = self._embed_openai(texts)
            elif self.provider in ("huggingface", "local"):
                embeddings = self._embed_local(texts)
            else:
                raise ValueError(f"Unknown provider: {self.provider}")

            duration = time.time() - start_time
            embedding_dim = len(embeddings[0]) if len(embeddings) > 0 else 0

            logger.info(
                f"✅ Generated {len(embeddings)} embeddings (dim={embedding_dim}) in {duration:.2f}s"
            )
            if duration > 0:
                logger.debug(f"   Generation rate: {len(embeddings) / duration:.1f} embeddings/sec")
                logger.debug(f"   Throughput: {total_chars / duration / 1000:.1f}K chars/sec")

            return embeddings

        except Exception as e:
            logger.error(f"Failed to generate embeddings: {e}", exc_info=True)
            raise

    def _embed_vertex_ai(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings using Vertex AI with batching to stay under token limits.

        text-embedding-005 has a 20,000 token per-request limit.
        With ~1,000 token chunks we batch conservatively at 5 texts per request.
        """
        batch_size = 5
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            embeddings = self._vertex_ai_client.get_embeddings(batch)
            all_embeddings.extend(emb.values for emb in embeddings)
        return all_embeddings

    def _embed_gemini_api(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings using Gemini API (API key, no GCP permissions)."""
        model_name = (
            settings.embedding_model_name
            if settings.embedding_model_name.startswith("models/")
            else f"models/{settings.embedding_model_name}"
        )
        batch_size = 50
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            result = self._gemini_api_client.models.embed_content(
                model=model_name,
                contents=batch,
            )

            # Response has .embeddings (list) or .embedding (single)
            def _extract_values(emb) -> list[float]:
                if hasattr(emb, "values"):
                    v = emb.values
                elif isinstance(emb, (list, tuple)):
                    v = emb
                else:
                    v = list(emb)
                return list(v) if not isinstance(v, list) else v

            if hasattr(result, "embeddings") and result.embeddings:
                for emb in result.embeddings:
                    all_embeddings.append(_extract_values(emb))
            elif hasattr(result, "embedding") and result.embedding:
                all_embeddings.append(_extract_values(result.embedding))
            else:
                raise ValueError(
                    f"Unexpected embed_content response: {type(result)}, "
                    "expected .embeddings or .embedding"
                )
        return all_embeddings

    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings using OpenAI API."""
        # OpenAI API supports batching, but we'll process in chunks to avoid token limits
        batch_size = 100  # OpenAI recommends batching
        all_embeddings = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            response = self._openai_client.embeddings.create(
                model=settings.openai_embedding_model, input=batch
            )
            batch_embeddings = [item.embedding for item in response.data]
            all_embeddings.extend(batch_embeddings)

        return all_embeddings

    def _embed_local(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings using local sentence-transformers model."""
        embeddings = self.model.encode(
            texts,
            batch_size=32,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        return embeddings.tolist()
