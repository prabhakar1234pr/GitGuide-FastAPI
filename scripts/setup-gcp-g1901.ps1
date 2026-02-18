# =============================================================================
# GCP Setup Script for G1901 Project (PowerShell)
# Run: gcloud config set project g1901-487423
#      .\scripts\setup-gcp-g1901.ps1
# =============================================================================

$ErrorActionPreference = "Stop"

$PROJECT_ID = if ($env:GCP_PROJECT_ID) { $env:GCP_PROJECT_ID } else { "g1901-487423" }
$REGION = if ($env:GCP_REGION) { $env:GCP_REGION } else { "us-central1" }
$ZONE = "$REGION-a"
$AR_REPO = "gitguide"
$VM_NAME = "gitguide-workspaces"
$SA_RUNTIME = "gemini-api-service"
$SA_GHACTIONS = "github-actions-deploy"

Write-Host "=============================================="
Write-Host "GCP Setup for G1901"
Write-Host "Project: $PROJECT_ID | Region: $REGION"
Write-Host "=============================================="

# 1. Set project
gcloud config set project $PROJECT_ID

# 2. Enable APIs
Write-Host ""
Write-Host "[1/6] Enabling APIs..."
gcloud services enable `
  run.googleapis.com `
  compute.googleapis.com `
  artifactregistry.googleapis.com `
  secretmanager.googleapis.com `
  aiplatform.googleapis.com

# 3. Create Artifact Registry
Write-Host ""
Write-Host "[2/6] Creating Artifact Registry repository..."
gcloud artifacts repositories create $AR_REPO `
  --repository-format=docker `
  --location=$REGION `
  --description="GitGuide Docker images" 2>$null; if ($LASTEXITCODE -ne 0) { Write-Host "  (repo may already exist)" }

# 4. Create Service Accounts
Write-Host ""
Write-Host "[3/6] Creating service accounts..."

gcloud iam service-accounts create $SA_RUNTIME `
  --display-name="GitGuide Cloud Run Runtime" 2>$null; if ($LASTEXITCODE -ne 0) { Write-Host "  (SA may already exist)" }

gcloud projects add-iam-policy-binding $PROJECT_ID `
  --member="serviceAccount:${SA_RUNTIME}@${PROJECT_ID}.iam.gserviceaccount.com" `
  --role="roles/aiplatform.user" --quiet

gcloud iam service-accounts create $SA_GHACTIONS `
  --display-name="GitHub Actions Deploy" 2>$null; if ($LASTEXITCODE -ne 0) { Write-Host "  (SA may already exist)" }

$SA_GH_EMAIL = "${SA_GHACTIONS}@${PROJECT_ID}.iam.gserviceaccount.com"
@("roles/run.admin", "roles/artifactregistry.writer", "roles/iam.serviceAccountUser", "roles/storage.objectViewer") | ForEach-Object {
  gcloud projects add-iam-policy-binding $PROJECT_ID `
    --member="serviceAccount:$SA_GH_EMAIL" `
    --role=$_ --quiet
}

# 5. Create Compute Engine VM
Write-Host ""
Write-Host "[4/6] Creating Compute Engine VM..."
gcloud compute instances create $VM_NAME `
  --zone=$ZONE `
  --machine-type=e2-small `
  --boot-disk-size=50GB `
  --boot-disk-type=pd-ssd `
  --image-family=ubuntu-2204-lts `
  --image-project=ubuntu-os-cloud `
  --tags=http-server,https-server 2>$null; if ($LASTEXITCODE -ne 0) { Write-Host "  (VM may already exist)" }

# 6. Firewall rules
Write-Host ""
Write-Host "[5/6] Creating firewall rules..."
gcloud compute firewall-rules create allow-gitguide-workspaces `
  --allow=tcp:8080,tcp:30001-30010 `
  --target-tags=http-server `
  --source-ranges=0.0.0.0/0 2>$null; if ($LASTEXITCODE -ne 0) { Write-Host "  (firewall rule may already exist)" }

# 7. Create service account key for GitHub Actions
Write-Host ""
Write-Host "[6/6] Creating GitHub Actions service account key..."
$KEY_DIR = "credentials"
if (-not (Test-Path $KEY_DIR)) { New-Item -ItemType Directory -Path $KEY_DIR }
$KEY_FILE = "$KEY_DIR\github-actions-sa-key.json"
gcloud iam service-accounts keys create $KEY_FILE `
  --iam-account=$SA_GH_EMAIL 2>$null; if ($LASTEXITCODE -ne 0) { Write-Host "  (Key limit or exists - create manually in Console)" }

# Summary
Write-Host ""
Write-Host "=============================================="
Write-Host "Setup complete!"
Write-Host "=============================================="
Write-Host ""
Write-Host "VM external IP (add to GitHub secret VM_HOST):"
gcloud compute instances describe $VM_NAME --zone=$ZONE --format="get(networkInterfaces[0].accessConfigs[0].natIP)" 2>$null
Write-Host ""
Write-Host "GitHub Actions: Add GCP_SA_KEY secret = contents of $KEY_FILE"
Write-Host "Cloud Run runtime SA: ${SA_RUNTIME}@${PROJECT_ID}.iam.gserviceaccount.com"
Write-Host ""
