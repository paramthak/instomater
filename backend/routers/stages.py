from __future__ import annotations

import asyncio
from fastapi import APIRouter, HTTPException, status

from models.session import ActionRequest
from pipeline.orchestrator import advance, StageGateError, _handle_assembly_start, _handle_redo_clip, start_script_generation
from routers.ws import manager
from services import session_svc

router = APIRouter(prefix="/sessions", tags=["stages"])

# Keep references so tasks aren't garbage-collected before they finish
_bg_tasks: set = set()
_BACKGROUND_ACTIONS = {
    ("storyboard", "retry"),
    ("storyboard", "change"),
    ("image_generation", "retry"),
    ("image_generation", "change"),
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
    """Called with the initial person/context text to kick off script generation."""
    try:
        session_svc.get_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    raw_input = body.get("name", "").strip()
    context = body.get("context", "").strip() or None
    if context:
        raw_input = f"{raw_input}\n{context}".strip()
    if not raw_input:
        raise HTTPException(status_code=400, detail="input is required")

    session_svc.update_metadata(session_id, person_name=raw_input)
    session_svc.save_text_asset(session_id, "script_prompt.txt", raw_input)

    from models.session import UserReply
    session_svc.append_chat_message(session_id, UserReply(text=raw_input).model_dump())

    _fire(start_script_generation(session_id, manager))
    return {"status": "started"}
