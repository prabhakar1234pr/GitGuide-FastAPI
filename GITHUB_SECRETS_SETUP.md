# GitHub Secrets to Add

Go to your repo: **Settings → Secrets and variables → Actions → New repository secret**

---

## Required Secrets

| Secret Name | Value / Source |
|-------------|----------------|
| **GCP_SA_KEY** | Copy **entire contents** of `credentials/github-actions-sa-key.json` (the JSON file) |
| **VM_HOST** | `35.223.36.148` |
| **VM_USER** | Your SSH username on the VM (run `whoami` after SSH-ing in, or use `prabh` if that's your user) |
| **VM_SSH_KEY** | Your private SSH key (full content including `-----BEGIN` and `-----END` lines) |
| **SUPABASE_URL** | From your .env: `https://vfiqxmurerkkeykriaee.supabase.co` |
| **SUPABASE_SERVICE_KEY** | From your .env (the service_role JWT) |
| **DATABASE_URL** | From your .env: `postgresql://postgres:...@db.vfiqxmurerkkeykriaee.supabase.co:5432/postgres` |
| **QDRANT_URL** | From your .env: `https://a2d2c437-2f6c-431d-93e3-6c0517f729a0.us-east4-0.gcp.cloud.qdrant.io` |
| **QDRANT_API_KEY** | From your .env |
| **CLERK_SECRET_KEY** | From your .env |
| **JWT_SECRET** | From your .env (min 32 chars) |
| **GROQ_API_KEY** | From your .env |
| **GROQ_API_KEY2** | From your .env |
| **ROADMAP_SERVICE_URL** | URL of gitguide-roadmap Cloud Run service (after first deploy) |
| **INTERNAL_AUTH_TOKEN** | A random secret string for service-to-service auth (generate one) |

### Optional (Verification / separate Vertex AI project)

| Secret Name | Value |
|-------------|-------|
| **VERIFICATION_GCP_PROJECT_ID** | `g1901-487423` (or a separate project if you had one) |
| **VERIFICATION_GCP_LOCATION** | `global` or `us-central1` |
| **VERIFICATION_GEMINI_MODEL** | `gemini-2.5-flash` or `gemini-2.0-flash-exp` |

---

## Quick Copy Checklist

```
GCP_SA_KEY         = [contents of credentials/github-actions-sa-key.json]
VM_HOST            = 35.223.36.148
VM_USER            = [your VM username - run 'whoami' after SSH]
VM_SSH_KEY         = [your private SSH key]
SUPABASE_URL       = https://vfiqxmurerkkeykriaee.supabase.co
SUPABASE_SERVICE_KEY
DATABASE_URL
QDRANT_URL
QDRANT_API_KEY
CLERK_SECRET_KEY
JWT_SECRET
GROQ_API_KEY
GROQ_API_KEY2
ROADMAP_SERVICE_URL   (add after first Cloud Run deploy)
INTERNAL_AUTH_TOKEN   (generate: openssl rand -hex 32)
VERIFICATION_GCP_PROJECT_ID  = g1901-487423
VERIFICATION_GCP_LOCATION    = global
VERIFICATION_GEMINI_MODEL    = gemini-2.5-flash
```

---

## ✅ IAM Roles (Completed)

All required IAM roles have been granted.

---

## VM Setup (Before First Deploy)

SSH into the VM and complete setup:

```bash
gcloud compute ssh gitguide-workspaces --zone=us-central1-a --project=g1901-487423
```

Then follow the VM setup in `GCP_DEPLOYMENT.md` (Docker, clone repo, systemd service).
