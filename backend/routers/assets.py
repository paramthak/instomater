from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from services import session_svc

router = APIRouter(prefix="/sessions", tags=["assets"])

_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mp3": "audio/mpeg",
    ".json": "application/json",
    ".txt": "text/plain",
    ".ass": "text/plain",
}


@router.get("/{session_id}/assets/{asset_path:path}")
async def get_asset(session_id: str, asset_path: str):
    try:
        session_svc.get_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    file_path = session_svc.get_asset_path(session_id, asset_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_path}")

    ext = file_path.suffix.lower()
    content_type = _CONTENT_TYPES.get(ext, "application/octet-stream")

    # For final reel downloads, set Content-Disposition
    headers = {}
    if "final/" in asset_path and asset_path.endswith(".mp4"):
        filename = file_path.name
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    return FileResponse(
        path=str(file_path),
        media_type=content_type,
        headers=headers,
    )
