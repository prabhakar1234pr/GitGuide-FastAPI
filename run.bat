@echo off
REM Start FastAPI backend server
cd /d "%~dp0"
echo 🚀 Starting FastAPI backend server...
uv run uvicorn app.main:app --reload
