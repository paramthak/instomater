from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, status
from PIL import Image

from models.session import CreateSessionRequest, SessionMetadata, SessionListItem
from pipeline.orchestrator import continue_after_photo_confirm
from routers.ws import manager
from services import session_svc, cost_svc

# Keep references so tasks aren't garbage-collected before they finish
_bg_tasks: set = set()

import logging
_log = logging.getLogger(__name__)

def _fire(coro) -> None:
    async def _wrap():
        try:
            await coro
        except Exception as exc:
            _log.exception("Background task failed: %s", exc)
    task = asyncio.create_task(_wrap())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)

router = APIRouter(prefix="/sessions", tags=["sessions"])

_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_MIN_SIDE_PX = 512


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(body: CreateSessionRequest):
    try:
        meta = session_svc.create_session(body.name, body.context)
    except IOError as exc:
        raise HTTPException(status_code=507, detail=str(exc))
    return meta.model_dump()


@router.get("")
async def list_sessions():
    sessions = session_svc.list_sessions()
    return [SessionListItem(
        session_id=s.session_id,
        person_name=s.person_name,
        current_stage=s.current_stage.value,
        created_at=s.created_at,
        updated_at=s.updated_at,
    ).model_dump() for s in sessions]


@router.get("/{session_id}")
async def get_session(session_id: str):
    try:
        meta = session_svc.get_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    history = session_svc.get_chat_history(session_id)
    return {
        "metadata": meta.model_dump(),
        "chat_history": history.messages,
    }


@router.get("/{session_id}/costs")
async def get_costs(session_id: str):
    try:
        session_svc.get_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    return cost_svc.get_ledger(session_id)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: str):
    try:
        session_svc.delete_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")


@router.post("/{session_id}/photo")
async def upload_photo(session_id: str, file: UploadFile = File(...)):
    try:
        session_svc.get_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    content_type = file.content_type or ""
    if content_type not in _ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type: {content_type}. Must be JPEG, PNG, WebP, or HEIC.",
        )

    data = await file.read()
    if len(data) > _MAX_BYTES:
        raise HTTPException(status_code=400, detail=f"File too large: {len(data) // (1024*1024):.1f} MB. Max 10 MB.")

    # Validate dimensions and convert HEIC → JPEG
    try:
        img = Image.open(io.BytesIO(data))
        w, h = img.size
        # Convert HEIC or ensure JPEG/PNG
        ext = "jpg"
        if content_type in ("image/png", "image/webp"):
            ext = "png" if content_type == "image/png" else "webp"
        if content_type in ("image/heic", "image/heif"):
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=95)
            data = buf.getvalue()
            ext = "jpg"
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read image: {exc}")

    session_svc.save_asset(session_id, f"uploaded_photo.{ext}", data)
    meta = session_svc.update_metadata(session_id, photo_ext=ext)

    # Append confirmation chat message
    from models.session import AppQuestion
    question = AppQuestion(
        question="Photo uploaded successfully. Use this photo?",
        widget={"type": "photo_confirm", "photo_path": f"uploaded_photo.{ext}"},
    ).model_dump()
    session_svc.append_chat_message(session_id, question)

    return {"status": "ok", "photo_path": f"uploaded_photo.{ext}", "dimensions": {"width": w, "height": h}}


@router.post("/{session_id}/photo/confirm")
async def confirm_photo(session_id: str):
    """User clicked 'Use this photo' after script approval."""
    try:
        meta = session_svc.get_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    if not meta.approval_state.script.approved:
        raise HTTPException(status_code=409, detail="Approve the script before confirming a photo.")

    _fire(continue_after_photo_confirm(session_id, manager))
    return {"status": "ok"}
