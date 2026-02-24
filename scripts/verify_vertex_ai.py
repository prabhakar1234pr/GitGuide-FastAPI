#!/usr/bin/env python3
"""
Verify Vertex AI setup for GitGuide.
Run this locally with gcloud auth, or in Cloud Run to diagnose embedding/LLM issues.

Usage:
  python scripts/verify_vertex_ai.py
  # Or with explicit project:
  GCP_PROJECT_ID=g1901-487423 GCP_LOCATION=us-central1 python scripts/verify_vertex_ai.py
"""

import os
import sys


def check_env():
    """Check required env vars."""
    project = os.environ.get("GCP_PROJECT_ID", "g1901-487423")
    location = os.environ.get("GCP_LOCATION", "us-central1")
    return project, location


def verify_embedding_model(project: str, location: str) -> bool:
    """Test embedding model loading (gemini-embedding-001 or textembedding-gecko)."""
    try:
        import vertexai
        from vertexai.language_models import TextEmbeddingModel

        vertexai.init(project=project, location=location)
        print(f"   vertexai.init(project={project}, location={location}) OK")

        # Try gemini-embedding-001 first (default in config)
        model_name = os.environ.get("EMBEDDING_MODEL_NAME", "gemini-embedding-001")
        print(f"   Loading model: {model_name}...")

        model = TextEmbeddingModel.from_pretrained(model_name)
        embeddings = model.get_embeddings(["test"])
        if embeddings and embeddings[0].values:
            print(f"[OK] Embedding model '{model_name}' works (dim={len(embeddings[0].values)})")
            return True
        else:
            print("[FAIL] Embedding returned empty")
            return False
    except Exception as e:
        print(f"[FAIL] Embedding model failed: {type(e).__name__}: {e}")
        if "404" in str(e) or "not found" in str(e).lower():
            print("   → Model may not be available in this project/region.")
            print("   → Try: EMBEDDING_MODEL_NAME=textembedding-gecko@003")
        if "403" in str(e) or "permission" in str(e).lower():
            print("   → Service account needs roles/aiplatform.user")
        return False


def verify_credentials() -> bool:
    """Check Application Default Credentials."""
    try:
        import google.auth

        creds, project = google.auth.default()
        if creds:
            print(f"[OK] Application Default Credentials OK (project={project})")
            return True
        print("[FAIL] No credentials found")
        return False
    except Exception as e:
        print(f"[FAIL] Credentials check failed: {e}")
        return False


def main():
    project, location = check_env()
    print("=" * 60)
    print("Vertex AI Verification for GitGuide")
    print("=" * 60)
    print(f"Project: {project}")
    print(f"Location: {location}")
    print()

    ok = True
    ok &= verify_credentials()
    print()
    ok &= verify_embedding_model(project, location)
    # Embedding test also validates Vertex AI API is enabled

    print()
    print("=" * 60)
    if ok:
        print("[OK] All checks passed. Vertex AI should work.")
    else:
        print("[FAIL] Some checks failed. Fix the issues above before deploying.")
        print()
        print("Common fixes:")
        print(
            "  1. Enable Vertex AI API: gcloud services enable aiplatform.googleapis.com --project="
            + project
        )
        print(
            "  2. Grant Vertex AI User: gcloud projects add-iam-policy-binding " + project + " \\"
        )
        print(
            "       --member='serviceAccount:gemini-api-service@"
            + project
            + ".iam.gserviceaccount.com' \\"
        )
        print("       --role='roles/aiplatform.user'")
        print(
            "  3. If gemini-embedding-001 fails, set EMBEDDING_MODEL_NAME=textembedding-gecko@003"
        )
        sys.exit(1)
    print("=" * 60)


if __name__ == "__main__":
    main()
