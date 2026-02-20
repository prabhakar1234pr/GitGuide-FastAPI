#!/bin/bash

# Change to the directory where this script is located
cd "$(dirname "$0")"

# Start FastAPI backend server
echo "🚀 Starting FastAPI backend server..."
uv run uvicorn app.main:app --reload
