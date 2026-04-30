"""WebSocket log streaming endpoint."""
import asyncio
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

router = APIRouter(prefix="/logs", tags=["logs"])

logger = logging.getLogger(__name__)


@router.websocket("/stream")
async def stream_logs(websocket: WebSocket, token: str = Query(default="")):
    """
    WebSocket endpoint that streams xray log lines in real-time.
    Auth via ?token=<jwt> query parameter.
    """
    # Validate JWT token
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return
    try:
        from app.core.auth import verify_token
        verify_token(token)
    except Exception:
        await websocket.close(code=4001, reason="Invalid token")
        return

    from app.core.xray import log_queue

    await websocket.accept()
    logger.debug("Log stream client connected: %s", websocket.client)

    try:
        while True:
            try:
                # Wait up to 1 second for a new log line
                line = await asyncio.wait_for(log_queue.get(), timeout=1.0)
                await websocket.send_text(line)
            except asyncio.TimeoutError:
                # Send a ping to keep connection alive
                try:
                    await websocket.send_text("")
                except Exception:
                    break
    except WebSocketDisconnect:
        logger.debug("Log stream client disconnected")
    except Exception as exc:
        logger.warning("Log stream error: %s", exc)
    finally:
        logger.debug("Log stream closed")
