#!/bin/bash
# =============================================================================
# GCP Setup Script for G1901 Project
# Run: gcloud config set project g1901-487423 (or your G1901 project ID)
#      ./scripts/setup-gcp-g1901.sh
# =============================================================================

set -e

PROJECT_ID="${GCP_PROJECT_ID:-g1901-487423}"
REGION="${GCP_REGION:-us-central1}"
ZONE="${REGION}-a"
AR_REPO="gitguide"
VM_NAME="gitguide-workspaces"
SA_RUNTIME="gemini-api-service"
SA_GHACTIONS="github-actions-deploy"

echo "=============================================="
echo "GCP Setup for G1901"
echo "Project: $PROJECT_ID | Region: $REGION"
echo "=============================================="

# 1. Set project
gcloud config set project "$PROJECT_ID"

# 2. Enable APIs
echo ""
echo "[1/6] Enabling APIs..."
gcloud services enable \
  run.googleapis.com \
  compute.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  aiplatform.googleapis.com

# 3. Create Artifact Registry
echo ""
echo "[2/6] Creating Artifact Registry repository..."
gcloud artifacts repositories create "$AR_REPO" \
  --repository-format=docker \
  --location="$REGION" \
  --description="GitGuide Docker images" \
  2>/dev/null || echo "  (repo may already exist)"

# 4. Create Service Accounts
echo ""
echo "[3/6] Creating service accounts..."

# Cloud Run runtime (Vertex AI / Gemini)
gcloud iam service-accounts create "$SA_RUNTIME" \
  --display-name="GitGuide Cloud Run Runtime" \
  2>/dev/null || echo "  (SA $SA_RUNTIME may already exist)"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_RUNTIME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user" --quiet

# GitHub Actions deploy
gcloud iam service-accounts create "$SA_GHACTIONS" \
  --display-name="GitHub Actions Deploy" \
  2>/dev/null || echo "  (SA $SA_GHACTIONS may already exist)"

SA_GH_EMAIL="${SA_GHACTIONS}@${PROJECT_ID}.iam.gserviceaccount.com"
for ROLE in "roles/run.admin" "roles/artifactregistry.writer" "roles/iam.serviceAccountUser" "roles/storage.objectViewer"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_GH_EMAIL}" \
    --role="$ROLE" --quiet
done

# 5. Create Compute Engine VM
echo ""
echo "[4/6] Creating Compute Engine VM..."
gcloud compute instances create "$VM_NAME" \
  --zone="$ZONE" \
  --machine-type=e2-small \
  --boot-disk-size=50GB \
  --boot-disk-type=pd-ssd \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --tags=http-server,https-server \
  2>/dev/null || echo "  (VM may already exist)"

# 6. Firewall rules
echo ""
echo "[5/6] Creating firewall rules..."
gcloud compute firewall-rules create allow-gitguide-workspaces \
  --allow=tcp:8080,tcp:30001-30010 \
  --target-tags=http-server \
  --source-ranges=0.0.0.0/0 \
  2>/dev/null || echo "  (firewall rule may already exist)"

# 7. Create service account key for GitHub Actions
echo ""
echo "[6/6] Creating GitHub Actions service account key..."
KEY_FILE="credentials/github-actions-sa-key.json"
mkdir -p credentials
gcloud iam service-accounts keys create "$KEY_FILE" \
  --iam-account="${SA_GH_EMAIL}" \
  2>/dev/null || echo "  (Key may already exist or limit reached - create manually in Console)"

# Summary
echo ""
echo "=============================================="
echo "Setup complete!"
echo "=============================================="
echo ""
echo "VM external IP (add to GitHub secret VM_HOST):"
gcloud compute instances describe "$VM_NAME" --zone="$ZONE" \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)' 2>/dev/null || echo "  Run: gcloud compute instances describe $VM_NAME --zone=$ZONE --format='get(networkInterfaces[0].accessConfigs[0].natIP)'"
echo ""
echo "GitHub Actions: Add GCP_SA_KEY secret = contents of $KEY_FILE"
echo "Cloud Run runtime SA: ${SA_RUNTIME}@${PROJECT_ID}.iam.gserviceaccount.com"
echo ""
