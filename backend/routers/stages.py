from __future__ import annotations

import asyncio
from fastapi import APIRouter, HTTPException, status

from models.session import ActionRequest
from pipeline.orchestrator import advance, StageGateError, _handle_assembly_start, _handle_redo_clip
from routers.ws import manager
from services import session_svc

router = APIRouter(prefix="/sessions", tags=["stages"])

# Keep references so tasks aren't garbage-collected before they finish
_bg_tasks: set = set()
_BACKGROUND_ACTIONS = {
    ("storyboard", "retry"),
    ("storyboard", "change"),
}

def _fire(coro) -> None:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)

    def _done(done_task: asyncio.Task) -> None:
        _bg_tasks.discard(done_task)
        try:
            exc = done_task.exception()
        except asyncio.CancelledError:
            return
        if exc:
            print(f"Background task failed: {exc}")

    task.add_done_callback(_done)


@router.post("/{session_id}/action")
async def session_action(session_id: str, body: ActionRequest):
    try:
        session_svc.get_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    if (body.stage, body.action) in _BACKGROUND_ACTIONS:
        _fire(advance(
            session_id=session_id,
            action=body.action,
            stage=body.stage,
            payload=body.payload,
            ws=manager,
        ))
        return {"status": "started"}

    try:
        result = await advance(
            session_id=session_id,
            action=body.action,
            stage=body.stage,
            payload=body.payload,
            ws=manager,
        )
    except StageGateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return result


@router.post("/{session_id}/assemble")
async def assemble(session_id: str):
    try:
        session_svc.get_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    # Fire in background so HTTP returns immediately; WS pushes status
    _fire(_handle_assembly_start(session_id, {}, manager))
    return {"status": "started"}


@router.post("/{session_id}/redo-clip/{clip_index}")
async def redo_clip(session_id: str, clip_index: int):
    try:
        session_svc.get_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    _fire(_handle_redo_clip(session_id, {"clip_index": clip_index}, manager))
    return {"status": "started"}


@router.post("/{session_id}/start")
async def start_session(session_id: str, body: dict):
    """Called with the initial 'Who is this person' message to kick off stage 1."""
    try:
        session_svc.get_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    name = body.get("name", "").strip()
    context = body.get("context", "").strip() or None
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    # Update person name in metadata
    session_svc.update_metadata(session_id, person_name=name)

    # Append user reply + start topic brief generation in background
    from models.session import UserReply
    session_svc.append_chat_message(session_id, UserReply(text=name + (f" — {context}" if context else "")).model_dump())

    _fire(advance(
        session_id=session_id,
        action="start",
        stage="topic_brief",
        payload={"name": name, "context": context},
        ws=manager,
    ))
    return {"status": "started"}
