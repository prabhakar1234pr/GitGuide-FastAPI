#!/bin/bash
# Check workspace VM setup for Vertex AI verification
# Run: ./scripts/check_verification_setup.sh

set -e
PROJECT_ID="${GCP_PROJECT_ID:-g1901-487423}"
REGION="${GCP_LOCATION:-us-central1}"
ZONE="${REGION}-a"
VM_NAME="gitguide-workspaces"

echo "=== 1. Workspace VM service account and scopes ==="
VM_SA=$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --project="$PROJECT_ID" \
  --format='get(serviceAccounts[0].email)' 2>/dev/null || echo "NOT_FOUND")
VM_SCOPES=$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --project="$PROJECT_ID" \
  --format='get(serviceAccounts[0].scopes)' 2>/dev/null || echo "NOT_FOUND")

echo "VM: $VM_NAME"
echo "Service Account: $VM_SA"
echo "Scopes: $VM_SCOPES"
echo ""

if [[ "$VM_SCOPES" != *"cloud-platform"* ]] && [[ "$VM_SCOPES" != *"https://www.googleapis.com/auth/cloud-platform"* ]]; then
  echo "⚠️  PROBLEM: VM lacks cloud-platform scope (required for Vertex AI)"
  echo "   Fix: Stop VM, update scopes, start VM:"
  echo "   gcloud compute instances stop $VM_NAME --zone=$ZONE --project=$PROJECT_ID"
  echo "   gcloud compute instances set-service-account $VM_NAME --zone=$ZONE --project=$PROJECT_ID \\"
  echo "     --scopes=https://www.googleapis.com/auth/cloud-platform"
  echo "   gcloud compute instances start $VM_NAME --zone=$ZONE --project=$PROJECT_ID"
  echo ""
fi

echo "=== 2. Service account IAM roles (Vertex AI) ==="
if [[ "$VM_SA" != "NOT_FOUND" ]] && [[ -n "$VM_SA" ]]; then
  ROLES=$(gcloud projects get-iam-policy "$PROJECT_ID" --flatten="bindings[].members" \
    --filter="bindings.members:serviceAccount:$VM_SA" --format="value(bindings.role)" 2>/dev/null || true)
  echo "Roles for $VM_SA:"
  echo "$ROLES" | tr ' ' '\n' | grep -v '^$' || echo "  (none or error)"
  if ! echo "$ROLES" | grep -q "aiplatform.user"; then
    echo ""
    echo "⚠️  PROBLEM: Service account lacks roles/aiplatform.user"
    echo "   Fix:"
    echo "   gcloud projects add-iam-policy-binding $PROJECT_ID \\"
    echo "     --member=\"serviceAccount:$VM_SA\" \\"
    echo "     --role=\"roles/aiplatform.user\""
    echo ""
  fi
fi

echo "=== 3. Vertex AI API enabled? ==="
if gcloud services list --enabled --project="$PROJECT_ID" 2>/dev/null | grep -q aiplatform.googleapis.com; then
  echo "✅ Vertex AI API is enabled"
else
  echo "⚠️  Vertex AI API not enabled. Run:"
  echo "   gcloud services enable aiplatform.googleapis.com --project=$PROJECT_ID"
fi

echo ""
echo "=== 4. Recent workspace service logs (SSH required) ==="
echo "To view logs on the VM, run:"
echo "  gcloud compute ssh $VM_NAME --zone=$ZONE --project=$PROJECT_ID --command='sudo journalctl -u gitguide-workspaces -n 100 --no-pager'"
echo ""
echo "Or to tail live:"
echo "  gcloud compute ssh $VM_NAME --zone=$ZONE --project=$PROJECT_ID --command='sudo journalctl -u gitguide-workspaces -f'"
