# Fix workspace VM scopes for Vertex AI verification
# Error: ACCESS_TOKEN_SCOPE_INSUFFICIENT - VM lacks cloud-platform scope
# Run: .\scripts\fix_verification_vm_scopes.ps1

$PROJECT_ID = "g1901-487423"
$ZONE = "us-central1-a"
$VM_NAME = "gitguide-workspaces"

Write-Host "1. Stopping VM (required to change scopes)..."
gcloud compute instances stop $VM_NAME --zone=$ZONE --project=$PROJECT_ID

Write-Host "`n2. Updating VM scopes to include cloud-platform..."
gcloud compute instances set-service-account $VM_NAME --zone=$ZONE --project=$PROJECT_ID `
  --scopes=https://www.googleapis.com/auth/cloud-platform

Write-Host "`n3. Starting VM..."
gcloud compute instances start $VM_NAME --zone=$ZONE --project=$PROJECT_ID

Write-Host "`nDone. Verification should work after VM restarts (~1 min)."
