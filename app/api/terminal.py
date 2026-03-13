"""
Terminal WebSocket API
Real-time terminal connections for Docker containers.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from supabase import Client

import docker
from app.core.supabase_client import get_supabase_client
from app.services.terminal_service import TerminalSession, get_terminal_service
from app.services.workspace_manager import get_workspace_manager
from app.utils.clerk_auth import verify_clerk_token, verify_clerk_token_from_string
from app.utils.db_helpers import get_user_id_from_clerk

router = APIRouter()
logger = logging.getLogger(__name__)


class CreateSessionRequest(BaseModel):
    workspace_id: str
    name: str = "Terminal"


class SessionResponse(BaseModel):
    session_id: str
    workspace_id: str
    name: str
    is_active: bool
    created_at: str


def _session_to_response(session: TerminalSession) -> SessionResponse:
    """Convert TerminalSession to response model."""
    return SessionResponse(
        session_id=session.session_id,
        workspace_id=session.workspace_id,
        name=session.name,
        is_active=session.is_active,
        created_at=session.created_at.isoformat(),
    )


@router.post("/sessions")
async def create_terminal_session(
    request: CreateSessionRequest,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Create a new terminal session for a workspace.
    """
    try:
        clerk_user_id = user_info["clerk_user_id"]
        user_id = get_user_id_from_clerk(supabase, clerk_user_id)

        # Verify workspace ownership
        workspace_manager = get_workspace_manager()
        workspace = workspace_manager.get_workspace(request.workspace_id)

        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        if workspace.user_id != user_id:
            raise HTTPException(status_code=403, detail="Access denied")

        if not workspace.container_id:
            raise HTTPException(status_code=400, detail="Workspace has no container")

        # Check if container is running, start it if not
        if workspace.container_status != "running":
            logger.info(
                f"Container {workspace.container_id[:12]} is not running (status: {workspace.container_status}), starting..."
            )
            success = workspace_manager.start_workspace(request.workspace_id)
            if not success:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to start workspace container (status: {workspace.container_status})",
                )
            # Refresh workspace to get updated status
            workspace = workspace_manager.get_workspace(request.workspace_id)
            if not workspace or workspace.container_status != "running":
                raise HTTPException(status_code=500, detail="Container failed to start")

        # Create terminal session
        terminal_service = get_terminal_service()
        session = terminal_service.create_session(
            workspace_id=request.workspace_id,
            container_id=workspace.container_id,
            name=request.name,
        )

        return {
            "success": True,
            "session": _session_to_response(session),
        }

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Error creating terminal session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/sessions/{workspace_id}")
async def list_terminal_sessions(
    workspace_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    List all active terminal sessions for a workspace.
    """
    try:
        clerk_user_id = user_info["clerk_user_id"]
        user_id = get_user_id_from_clerk(supabase, clerk_user_id)

        # Verify workspace ownership
        workspace_manager = get_workspace_manager()
        workspace = workspace_manager.get_workspace(workspace_id)

        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")

        if workspace.user_id != user_id:
            raise HTTPException(status_code=403, detail="Access denied")

        terminal_service = get_terminal_service()
        sessions = terminal_service.get_sessions_for_workspace(workspace_id)

        return {
            "success": True,
            "sessions": [_session_to_response(s) for s in sessions],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing terminal sessions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/sessions/{session_id}")
async def delete_terminal_session(
    session_id: str,
    user_info: dict = Depends(verify_clerk_token),
    supabase: Client = Depends(get_supabase_client),
):
    """
    Delete a terminal session.
    """
    try:
        clerk_user_id = user_info["clerk_user_id"]
        user_id = get_user_id_from_clerk(supabase, clerk_user_id)

        terminal_service = get_terminal_service()
        session = terminal_service.get_session(session_id)

        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Verify ownership through workspace
        workspace_manager = get_workspace_manager()
        workspace = workspace_manager.get_workspace(session.workspace_id)

        if not workspace or workspace.user_id != user_id:
            raise HTTPException(status_code=403, detail="Access denied")

        terminal_service.delete_session(session_id)

        return {
            "success": True,
            "message": "Session deleted",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting terminal session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.websocket("/{workspace_id}/connect")
async def terminal_websocket(
    websocket: WebSocket,
    workspace_id: str,
    token: str = Query(...),
    session_id: str | None = Query(None),
):
    """
    WebSocket endpoint for terminal connections.

    Query params:
        token: Clerk JWT token for authentication
        session_id: Optional existing session ID to reconnect to

    Message protocol:
        Client -> Server:
            {"type": "input", "data": "..."}  - Keyboard input
            {"type": "resize", "cols": 80, "rows": 24}  - Terminal resize

        Server -> Client:
            {"type": "output", "data": "..."}  - Terminal output
            {"type": "error", "message": "..."}  - Error message
            {"type": "connected", "session_id": "..."}  - Connection established
    """
    logger.info(
        f"[WS_CONNECT] New WebSocket connection for workspace: {workspace_id}, session: {session_id}"
    )

    # Accept connection first (required before sending any messages or closing)
    await websocket.accept()
    logger.debug(f"[WS_CONNECT] WebSocket accepted for workspace: {workspace_id}")

    supabase = get_supabase_client()

    # Authenticate
    try:
        user_info = await verify_clerk_token_from_string(token)
        clerk_user_id = user_info["clerk_user_id"]
        user_id = get_user_id_from_clerk(supabase, clerk_user_id)
    except Exception as e:
        logger.warning(f"WebSocket auth failed: {e}")
        await websocket.send_json({"type": "error", "message": "Authentication failed"})
        await websocket.close(code=4001, reason="Authentication failed")
        return

    # Verify workspace ownership
    workspace_manager = get_workspace_manager()
    workspace = workspace_manager.get_workspace(workspace_id)

    if not workspace:
        await websocket.send_json({"type": "error", "message": "Workspace not found"})
        await websocket.close(code=4004, reason="Workspace not found")
        return

    if workspace.user_id != user_id:
        await websocket.send_json({"type": "error", "message": "Access denied"})
        await websocket.close(code=4003, reason="Access denied")
        return

    if not workspace.container_id:
        await websocket.send_json({"type": "error", "message": "Workspace has no container"})
        await websocket.close(code=4000, reason="Workspace has no container")
        return

    # Check actual Docker container status (not just database)
    logger.info(f"[TERMINAL_WS] Checking container status for {workspace.container_id[:12]}")
    try:
        docker_client = docker.from_env()
        container = docker_client.containers.get(workspace.container_id)
        actual_status = container.status
        logger.info(
            f"[TERMINAL_WS] Container {workspace.container_id[:12]} status: {actual_status}"
        )

        # If container is not running, start it
        if actual_status != "running":
            logger.warning(
                f"[TERMINAL_WS] Container {workspace.container_id[:12]} is not running (actual status: {actual_status}), starting..."
            )
            success = workspace_manager.start_workspace(workspace_id)
            logger.info(f"[TERMINAL_WS] Start workspace result: {success}")
            if not success:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": f"Failed to start workspace container (status: {actual_status})",
                    }
                )
                await websocket.close(code=4005, reason="Failed to start container")
                return
            # Wait a moment for container to start, then verify
            await asyncio.sleep(0.5)  # Use async sleep instead of blocking sleep
            container.reload()
            logger.info(f"[TERMINAL_WS] Container status after start: {container.status}")
            if container.status != "running":
                await websocket.send_json({"type": "error", "message": "Container failed to start"})
                await websocket.close(code=4005, reason="Container failed to start")
                return
        else:
            logger.info(
                f"[TERMINAL_WS] Container {workspace.container_id[:12]} is running, proceeding..."
            )
    except docker.errors.NotFound:
        logger.warning(
            f"[TERMINAL_WS] Container {workspace.container_id[:12]} not found in Docker, recreating..."
        )
        success = workspace_manager.start_workspace(workspace_id)
        if not success:
            await websocket.send_json({"type": "error", "message": "Failed to recreate container"})
            await websocket.close(code=4005, reason="Failed to recreate container")
            return
    except Exception as e:
        logger.error(f"[TERMINAL_WS] Error checking container status: {e}", exc_info=True)
        # Try to start anyway
        success = workspace_manager.start_workspace(workspace_id)
        if not success:
            await websocket.send_json(
                {"type": "error", "message": f"Container check failed: {str(e)}"}
            )
            await websocket.close(code=4005, reason="Container check failed")
            return

    terminal_service = get_terminal_service()
    session: TerminalSession | None = None

    try:
        # Get or create session
        if session_id:
            session = terminal_service.get_session(session_id)
            if not session or session.workspace_id != workspace_id:
                session = None

        if not session:
            session = terminal_service.create_session(
                workspace_id=workspace_id,
                container_id=workspace.container_id,
            )

        # Output callback
        async def send_output(data: bytes):
            try:
                await websocket.send_json(
                    {
                        "type": "output",
                        "data": data.decode("utf-8", errors="replace"),
                    }
                )
            except Exception as e:
                logger.debug(f"Failed to send output: {e}")

        # Wrap callback for async
        output_queue: asyncio.Queue = asyncio.Queue()

        def output_callback(data: bytes):
            try:
                output_queue.put_nowait(data)
            except Exception:
                pass

        # Start the terminal stream
        socket = await terminal_service.start_stream(
            session_id=session.session_id,
            container_id=workspace.container_id,
            on_output=output_callback,
        )

        if not socket:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "Failed to start terminal stream",
                }
            )
            await websocket.close(code=4500, reason="Stream failed")
            return

        # Send connected message
        logger.info(f"[WS_CONNECT] Sending connected message for session: {session.session_id}")
        await websocket.send_json(
            {
                "type": "connected",
                "session_id": session.session_id,
            }
        )

        # Task to forward output from queue to websocket
        async def forward_output():
            while True:
                try:
                    data = await asyncio.wait_for(output_queue.get(), timeout=0.1)
                    await websocket.send_json(
                        {
                            "type": "output",
                            "data": data.decode("utf-8", errors="replace"),
                        }
                    )
                except TimeoutError:
                    continue
                except Exception as e:
                    logger.debug(f"Forward output error: {e}")
                    break

        # Start output forwarding task
        output_task = asyncio.create_task(forward_output())

        try:
            # Handle incoming messages
            logger.debug(f"[WS_CONNECT] Starting message loop for session: {session.session_id}")
            while True:
                try:
                    message = await websocket.receive_json()
                    msg_type = message.get("type")
                    logger.debug(
                        f"[WS_MSG] Received message type: {msg_type} for session: {session.session_id}"
                    )

                    if msg_type == "input":
                        data = message.get("data", "")
                        if data:
                            logger.debug(
                                f"[WS_INPUT] Input for session {session.session_id}: {len(data)} chars"
                            )
                            terminal_service.write_input(
                                session.session_id,
                                data.encode("utf-8"),
                            )

                    elif msg_type == "resize":
                        cols = message.get("cols", 80)
                        rows = message.get("rows", 24)
                        logger.debug(
                            f"[WS_RESIZE] Resize for session {session.session_id}: {cols}x{rows}"
                        )
                        terminal_service.resize_terminal(
                            session.session_id,
                            cols,
                            rows,
                        )

                    elif msg_type == "ping":
                        # Keepalive from client - respond to prevent load balancer timeout
                        await websocket.send_json({"type": "pong"})

                except WebSocketDisconnect:
                    logger.info(
                        f"[WS_DISCONNECT] WebSocket disconnected for session {session.session_id}"
                    )
                    break
                except json.JSONDecodeError:
                    logger.warning("[WS_MSG] Invalid JSON received")
                    continue

        finally:
            output_task.cancel()
            try:
                await output_task
            except asyncio.CancelledError:
                pass

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for workspace {workspace_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        try:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": str(e),
                }
            )
        except Exception:
            pass
    finally:
        # Close session on disconnect
        if session:
            terminal_service.close_session(session.session_id)
