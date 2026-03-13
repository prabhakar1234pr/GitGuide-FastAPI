# Cloud Load Balancer Status

## ✅ What's Already Configured

1. **Cloud Load Balancer**: ✅ Active
   - Domain: `workspaces.gitguide.dev`
   - IP Address: `136.110.145.242`
   - SSL Certificate: `gitguide-workspaces-cert` (Active, expires 2026-04-23)
   - Status: DNS correctly pointing to load balancer IP

2. **Backend Service**: ✅ Configured
   - Name: `gitguide-workspaces-backend`
   - Instance Group: `gitguide-workspaces-group` (zone: us-central1-a)
   - Health Check: `gitguide-workspaces-health` (port 8080, path `/health`)
   - ⚠️ **Issue**: Backend service port is set to 80, but VM runs on 8080

3. **VM Instance**: ✅ Running
   - Name: `gitguide-workspaces`
   - Zone: `us-central1-a`
   - Status: RUNNING
   - External IP: `35.192.92.135`
   - Internal IP: `10.128.0.2`

4. **URL Map**: ✅ Configured
   - Name: `gitguide-workspaces-map`
   - Default Service: `gitguide-workspaces-backend`

## ✅ Port Configuration

The instance group named port is correctly configured:
- Named Port: `http:8080` ✅

The backend service uses the named port `http`, which correctly maps to port 8080.

## ⚠️ Action Required

### Set Environment Variable on VM
The VM needs `WORKSPACE_PUBLIC_BASE_URL` environment variable set.

**Fix:** SSH into the VM and add to `.env` or systemd service:

```bash
# SSH into VM
gcloud compute ssh gitguide-workspaces --zone=us-central1-a

# Edit the .env file or systemd service
sudo nano /opt/gitguide-backend/.env
# OR
sudo systemctl edit gitguide-workspaces

# Add these lines:
WORKSPACE_PUBLIC_BASE_URL=https://workspaces.gitguide.dev
ENVIRONMENT=production

# Restart the service
sudo systemctl restart gitguide-workspaces
```

## ✅ Testing

After fixing the above, test:

1. **Health Check:**
   ```bash
   curl https://workspaces.gitguide.dev/health
   ```
   Should return: `{"status":"healthy","service":"workspaces"}`

2. **Preview Proxy:**
   ```bash
   # Replace {workspace_id} and {port} with actual values
   curl -H "Authorization: Bearer YOUR_TOKEN" \
     https://workspaces.gitguide.dev/api/preview/{workspace_id}/3000/
   ```

## Summary

Your Cloud Load Balancer is **already set up and working**! You just need to:

1. ✅ Update backend service port from 80 → 8080
2. ✅ Set `WORKSPACE_PUBLIC_BASE_URL=https://workspaces.gitguide.dev` on the VM
3. ✅ Restart the workspace service

After that, the preview proxy URLs will automatically use:
- **Production**: `https://workspaces.gitguide.dev/api/preview/{workspace_id}/{port}/`
- **Local**: `http://localhost:{host_port}` (when `ENVIRONMENT != "production"`)
