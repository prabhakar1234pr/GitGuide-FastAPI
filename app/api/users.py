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
            existing_role = existing.get("role")

            # Reject if user tries to sign in with different role
            if role and existing_role and role != existing_role:
                raise HTTPException(
                    status_code=403,
                    detail=f"Your account is registered as a {existing_role}. Use the {existing_role} sign-in page.",
                )

            update_data = {
                "email": email,
                "name": name,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            # Set role only on first sign-up when provided (existing users keep their role)
            if role and existing_role is None:
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
            # Before creating: reject if email already exists with any role (prevents same email as manager and employee)
            if email:
                email_lower = email.strip().lower()
                existing_by_email = (
                    supabase.table("User").select("id, role").ilike("email", email_lower).execute()
                )
                data = existing_by_email.data if existing_by_email.data is not None else []
                if data and len(data) > 0:
                    existing_role = data[0].get("role", "user")
                    raise HTTPException(
                        status_code=403,
                        detail=f"This email is already registered as a {existing_role}. Sign in with your existing account—you cannot create a new account with a different role.",
                    )

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

            new_user_id = new_user_response.data[0]["id"]

            # Grant project_access for any pending invites matching this email
            if email:
                invites = (
                    supabase.table("project_invites")
                    .select("project_id, granted_by")
                    .eq("email", email.lower())
                    .execute()
                )
                for inv in invites.data or []:
                    try:
                        supabase.table("project_access").insert(
                            {
                                "project_id": inv["project_id"],
                                "user_id": new_user_id,
                                "granted_by": inv["granted_by"],
                            }
                        ).execute()
                        logger.info(
                            f"Granted project access from pending invite: {inv['project_id']}"
                        )
                    except Exception:
                        pass  # Ignore duplicate/unique errors

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

        data = user_response.data if user_response.data is not None else []
        if not data or len(data) == 0:
            raise HTTPException(status_code=404, detail="User not found in database")

        return {"success": True, "user": data[0]}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching user: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch user: {str(e)}") from e
