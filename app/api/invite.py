"""Public invite validation API (no auth required)."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client

from app.core.supabase_client import get_supabase_client

router = APIRouter()
logger = logging.getLogger(__name__)

INVITE_EXPIRY_HOURS = 168  # 7 days


@router.get("/validate")
async def validate_invite_token(
    token: str = Query(..., description="Invite token from access link"),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Validate invite token. Public endpoint.
    Returns redirect_to: 'sign-in' | 'sign-up', project_id, project_name.
    """
    if not token.strip():
        raise HTTPException(status_code=400, detail="Token required")

    invite = (
        supabase.table("project_invites")
        .select("project_id, email, expires_at")
        .eq("token", token.strip())
        .execute()
    )
    if not invite.data or len(invite.data) == 0:
        raise HTTPException(status_code=404, detail="Invalid or expired invite link")

    row = invite.data[0]
    expires_at = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
    if expires_at < datetime.now(UTC):
        raise HTTPException(status_code=410, detail="Invite link has expired")

    project_id = row["project_id"]
    email = row["email"]

    # Get project name
    proj = supabase.table("projects").select("project_name").eq("project_id", project_id).execute()
    project_name = proj.data[0]["project_name"] if proj.data else "Project"

    # Check if email is already a user
    user_res = supabase.table("User").select("id").eq("email", email).execute()
    is_existing_user = bool(user_res.data and len(user_res.data) > 0)

    redirect_to = "sign-in" if is_existing_user else "sign-up"

    return {
        "success": True,
        "redirect_to": redirect_to,
        "project_id": project_id,
        "project_name": project_name,
        "email": email,
    }
