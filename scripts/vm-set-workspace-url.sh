#!/bin/bash
# Set WORKSPACE_PUBLIC_BASE_URL on the workspace VM for preview URLs
# Run: gcloud compute ssh gitguide-workspaces --zone=us-central1-a --project=g1901-487423 --command="bash -s" < scripts/vm-set-workspace-url.sh

set -e
sudo mkdir -p /etc/systemd/system/gitguide-workspaces.service.d
sudo tee /etc/systemd/system/gitguide-workspaces.service.d/override.conf << 'EOF'
[Service]
Environment="WORKSPACE_PUBLIC_BASE_URL=https://workspaces.gitguide.dev"
Environment="ENVIRONMENT=production"
EOF
sudo systemctl daemon-reload
sudo systemctl restart gitguide-workspaces
echo "Done. WORKSPACE_PUBLIC_BASE_URL set."
systemctl status gitguide-workspaces --no-pager || true
