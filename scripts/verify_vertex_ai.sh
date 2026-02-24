#!/bin/bash
# Verify Vertex AI setup for GitGuide (project: g1901-487423)
# Run: ./scripts/verify_vertex_ai.sh

PROJECT="${GCP_PROJECT_ID:-g1901-487423}"
SA="gemini-api-service@${PROJECT}.iam.gserviceaccount.com"

echo "=============================================="
echo "Vertex AI GCP Verification"
echo "=============================================="
echo "Project: $PROJECT"
echo "Service Account: $SA"
echo ""

echo "1. Vertex AI API enabled?"
if gcloud services list --enabled --project="$PROJECT" 2>/dev/null | grep -q aiplatform.googleapis.com; then
  echo "   ✅ Vertex AI API is enabled"
else
  echo "   ❌ Vertex AI API NOT enabled"
  echo "   Fix: gcloud services enable aiplatform.googleapis.com --project=$PROJECT"
fi
echo ""

echo "2. Service account IAM roles:"
gcloud projects get-iam-policy "$PROJECT" \
  --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount:$SA" \
  --format="table(bindings.role)" 2>/dev/null || echo "   (Could not fetch - check project access)"
echo ""

echo "3. Required: roles/aiplatform.user for $SA"
if gcloud projects get-iam-policy "$PROJECT" \
  --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount:$SA" \
  --format="value(bindings.role)" 2>/dev/null | grep -q aiplatform.user; then
  echo "   ✅ Vertex AI User role found"
else
  echo "   ❌ Vertex AI User role NOT found"
  echo "   Fix: gcloud projects add-iam-policy-binding $PROJECT \\"
  echo "     --member=\"serviceAccount:$SA\" \\"
  echo "     --role=\"roles/aiplatform.user\""
fi
echo ""

echo "4. Generative Language API (for Gemini):"
if gcloud services list --enabled --project="$PROJECT" 2>/dev/null | grep -q generativelanguage.googleapis.com; then
  echo "   ✅ Generative Language API enabled"
else
  echo "   ⚠️  Generative Language API may need to be enabled for Gemini"
  echo "   Fix: gcloud services enable generativelanguage.googleapis.com --project=$PROJECT"
fi
echo ""

echo "=============================================="
echo "Run Python test: GCP_PROJECT_ID=$PROJECT python scripts/verify_vertex_ai.py"
echo "=============================================="
