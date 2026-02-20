import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from supabase import Client

from app.core.supabase_client import get_supabase_client
from app.utils.clerk_auth import verify_clerk_token

router = APIRouter()
logger = logging.getLogger(__name__)


class SyncUserRequest(BaseModel):
    role: Literal["manager", "employee"] | None = Field(
        default=None,
        description="User role from sign-up (manager or employee)",
    )


@router.post("/sync")
async def sync_user(
    body: SyncUserRequest | None = Body(None),
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Sync Clerk user to Supabase User table

    Flow:
    1. Verify token (via verify_clerk_token dependency)
    2. Check if user exists in Supabase
    3. If exists → update (email, name; role only if provided and user has none)
    4. If not → create with role from body (default: employee)
    5. Return user data
    """
    try:
        clerk_user_id = user_info["clerk_user_id"]
        email = user_info.get("email")
        name = user_info.get("name")
        role = body.role if body else None

        logger.info(f"Syncing user: {clerk_user_id}, role={role}")

        # Check if user exists
        existing_user_response = (
            supabase.table("User").select("*").eq("clerk_user_id", clerk_user_id).execute()
        )

        if existing_user_response.data and len(existing_user_response.data) > 0:
            existing = existing_user_response.data[0]
            update_data = {
                "email": email,
                "name": name,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            # Set role only on first sign-up when provided (existing users keep their role)
            if role and existing.get("role") is None:
                update_data["role"] = role

            updated_user_response = (
                supabase.table("User")
                .update(update_data)
                .eq("clerk_user_id", clerk_user_id)
                .execute()
            )

            if not updated_user_response.data:
                raise HTTPException(status_code=500, detail="Failed to update user")

            logger.info(f"Updated user: {clerk_user_id}")
            return {"success": True, "user": updated_user_response.data[0], "action": "updated"}
        else:
            # Create new user (default role: employee if not specified)
            new_user_data = {
                "clerk_user_id": clerk_user_id,
                "email": email,
                "name": name,
                "role": role or "employee",
            }
            new_user_response = supabase.table("User").insert(new_user_data).execute()

            if not new_user_response.data:
                raise HTTPException(status_code=500, detail="Failed to create user")

            logger.info(f"Created user: {clerk_user_id} with role {new_user_data['role']}")
            return {"success": True, "user": new_user_response.data[0], "action": "created"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error syncing user: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to sync user: {str(e)}") from e


@router.get("/me")
async def get_current_user(
    user_info: dict = Depends(verify_clerk_token), supabase: Client = Depends(get_supabase_client)
):
    """
    Get current authenticated user from Supabase
    """
    try:
        clerk_user_id = user_info["clerk_user_id"]

        user_response = (
            supabase.table("User").select("*").eq("clerk_user_id", clerk_user_id).execute()
        )

        if not user_response.data or len(user_response.data) == 0:
            raise HTTPException(status_code=404, detail="User not found in database")

        return {"success": True, "user": user_response.data[0]}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching user: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch user: {str(e)}") from e
