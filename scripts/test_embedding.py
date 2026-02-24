#!/usr/bin/env python3
"""Test embedding service with Vertex AI (textembedding-gecko@003)."""

import os
import sys

# Set env before importing app (config reads on load)
os.environ.setdefault("EMBEDDING_PROVIDER", "vertex_ai")
os.environ.setdefault("EMBEDDING_MODEL_NAME", "text-embedding-005")
os.environ.setdefault("GCP_PROJECT_ID", "g1901-487423")
os.environ.setdefault("GCP_LOCATION", "us-central1")

# Add project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main():
    model = os.environ.get("EMBEDDING_MODEL_NAME", "text-embedding-005")
    print(f"Testing embedding service (vertex_ai, {model})...")
    try:
        from app.services.embedding_service import get_embedding_service

        svc = get_embedding_service()
        result = svc.embed_texts(["test embedding"])
        if result and len(result) > 0 and len(result[0]) > 0:
            print(f"[OK] Embedding works. Dim={len(result[0])}")
            return 0
        print("[FAIL] Empty embedding result")
        return 1
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
