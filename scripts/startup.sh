#!/bin/sh
# Startup script for Cloud Run
# Writes service account credentials from environment variable to file

set -ex

# Create credentials directory if it doesn't exist
mkdir -p /app/credentials

# Write service account JSON to file if GCP_SA_KEY is set
# GCP_SA_KEY can be either base64-encoded or plain JSON
if [ -n "$GCP_SA_KEY" ]; then
  # Try to decode as base64 first (if it fails, treat as plain JSON)
  if echo "$GCP_SA_KEY" | base64 -d > /app/credentials/service-account.json 2>/dev/null; then
    echo "✓ Service account credentials decoded from base64"
  else
    # If base64 decode fails, treat as plain JSON
    echo "$GCP_SA_KEY" > /app/credentials/service-account.json
    echo "✓ Service account credentials written (plain JSON)"
  fi
  chmod 600 /app/credentials/service-account.json
  export GOOGLE_APPLICATION_CREDENTIALS=/app/credentials/service-account.json
  echo "✓ GOOGLE_APPLICATION_CREDENTIALS set to /app/credentials/service-account.json"
else
  # When GCP_SA_KEY not set: use first .json in mounted credentials (for local Docker with ./credentials volume)
  if [ -f /app/credentials/service-account.json ]; then
    export GOOGLE_APPLICATION_CREDENTIALS=/app/credentials/service-account.json
    echo "✓ Using mounted credentials/service-account.json"
  else
    CRED_FILE=$(find /app/credentials -maxdepth 1 -name "*.json" 2>/dev/null | head -1)
    if [ -n "$CRED_FILE" ]; then
      export GOOGLE_APPLICATION_CREDENTIALS="$CRED_FILE"
      echo "✓ Using mounted credentials: $CRED_FILE"
    fi
  fi
fi

# Use PORT from environment (Cloud Run sets this), default to 8080
# Cloud Run REQUIRES the app to listen on the PORT environment variable
export PORT=${PORT:-8080}

# Use APP_MODULE from environment, default to main app
export APP_MODULE=${APP_MODULE:-app.main:app}

echo "✓ PORT: $PORT"
echo "✓ APP_MODULE: $APP_MODULE"

# Verify Python and uvicorn are available
echo "✓ Python version: $(python3 --version)"
if ! python3 -c "import uvicorn" 2>/dev/null; then
  echo "✗ ERROR: uvicorn not found in Python environment!"
  echo "Installed packages:"
  python3 -m pip list | grep -i uvicorn || echo "uvicorn not in pip list"
  exit 1
fi
echo "✓ uvicorn is available"

echo "✓ Starting $APP_MODULE on 0.0.0.0:$PORT"

# Start uvicorn using python3 -m (most reliable method)
# Use exec to replace shell process with uvicorn
# --workers 1 for Cloud Run (single-process friendly)
exec python3 -m uvicorn "$APP_MODULE" \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers 1
