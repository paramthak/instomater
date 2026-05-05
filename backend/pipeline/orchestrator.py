"""
Pipeline orchestrator — the central state machine.

ANTI-LOOP GUARANTEES (see inline comments):
- All polling uses explicit range() caps, never while True or recursion.
- Stage advancement only fires from explicit user REST actions, never WS events.
- Metadata is written to disk BEFORE any WS broadcast (crash-safe).
- Each retry has a hard upper bound.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any, Optional

# Background task references to prevent GC before completion
_bg_tasks: set = set()

def _fire_bg(coro) -> None:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)

from models.session import (
    StageEnum,
    CurrentSubstage,
    SubstageType,
    ImageApproval,
    VideoApproval,
    AssetCard,
    AppQuestion,
    StatusPill,
    ErrorCard,
)
from services import session_svc
from services import openai_svc
from services import elevenlabs_svc
from services import gemini_svc
from services import ffmpeg_svc
from routers.ws import ConnectionManager


class StageGateError(Exception):
    """Raised when an action targets a stage the session hasn't reached yet."""


def _pill_id() -> str:
    return f"pill_{uuid.uuid4().hex[:8]}"


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _asset_card(subtype: str, iteration: int, data: dict) -> dict:
    return AssetCard(
        subtype=subtype,
        iteration=iteration,
        data=data,
        status="pending_approval",
    ).model_dump()


def _status_pill(pill_id: str, message: str, stage: str, substage_index: Optional[int] = None) -> dict:
    return StatusPill(
        pill_id=pill_id,
        message=message,
        stage=stage,
        substage_index=substage_index,
    ).model_dump()


def _error_card(message: str, stage: str, substage_index: Optional[int] = None) -> dict:
    return ErrorCard(
        error_message=message,
        stage=stage,
        substage_index=substage_index,
    ).model_dump()


def _question(question: str, widget: Optional[dict] = None) -> dict:
    return AppQuestion(question=question, widget=widget).model_dump()


# ── Stage gate enforcement ───────────────────────────────────────────────────

_STAGE_ORDER = [
    StageEnum.topic_brief,
    StageEnum.photo_upload,
    StageEnum.script,
    StageEnum.voiceover,
    StageEnum.alignment,
    StageEnum.storyboard,
    StageEnum.clarifying_questions,
    StageEnum.image_generation,
    StageEnum.video_generation,
    StageEnum.assembly,
    StageEnum.final_review,
]


def _stage_index(stage: StageEnum) -> int:
    return _STAGE_ORDER.index(stage)


def assert_stage_reachable(session_id: str, required_stage: StageEnum) -> None:
    meta = session_svc.get_session(session_id)
    current_idx = _stage_index(meta.current_stage)
    required_idx = _stage_index(required_stage)
    if required_idx > current_idx + 1:
        raise StageGateError(
            f"Cannot run {required_stage.value}: session is at {meta.current_stage.value}"
        )


# ── Photo helpers ─────────────────────────────────────────────────────────────

def _get_photo_bytes(session_id: str) -> tuple[bytes, str]:
    meta = session_svc.get_session(session_id)
    ext = meta.photo_ext or "jpg"
    path = session_svc.get_asset_path(session_id, f"uploaded_photo.{ext}")
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    return path.read_bytes(), mime


def _get_image_bytes(session_id: str, slot: str) -> bytes:
    path = session_svc.get_asset_path(session_id, f"images/{slot}_approved.png")
    return path.read_bytes()


def _get_latest_image_bytes(session_id: str, slot: str, version: int) -> bytes:
    path = session_svc.get_asset_path(session_id, f"images/{slot}_v{version}.png")
    return path.read_bytes()


def _image_scene_context(storyboard: dict, slot: str, image_index: int) -> tuple[dict, dict]:
    scenes = storyboard["scenes"]
    total_images = storyboard["image_count"]
    starts = [s for s in scenes if s["image_slot_start"] == slot]
    ends = [s for s in scenes if s["image_slot_end"] == slot]

    if image_index == 1:
        role = "start_frame"
        primary_scene = starts[0] if starts else scenes[0]
        instruction = (
            "This is the opening start frame for clip 1. Make it a clear first beat, "
            "not a generic portrait. It should leave room for the next image to show "
            "a later, visibly displaced end beat."
        )
    elif image_index == total_images:
        role = "final_end_frame"
        primary_scene = ends[0] if ends else scenes[-1]
        instruction = (
            "This is the final end frame of the reel. It must be a later physical beat "
            "than the previous image, with changed pose, framing, and background detail."
        )
    else:
        role = "bridge_frame"
        primary_scene = ends[0] if ends else (starts[0] if starts else scenes[0])
        instruction = (
            "This image is both the end frame for the previous clip and the start frame "
            "for the next clip. It must be a clearly later physical beat than the previous "
            "approved image. Do not duplicate the previous composition. Show visible motion "
            "potential: changed location, changed body pose, changed object position, and "
            "changed camera framing while preserving identity, wardrobe, era, and color style."
        )

    context = {
        "slot": slot,
        "image_index": image_index,
        "total_images": total_images,
        "role": role,
        "primary_scene_id": primary_scene.get("scene_id"),
        "ends_scene": ends[0] if ends else None,
        "starts_scene": starts[0] if starts else None,
        "previous_image_slot": f"img_{image_index - 1:02d}" if image_index > 1 else None,
        "next_image_slot": f"img_{image_index + 1:02d}" if image_index < total_images else None,
        "motion_requirement": instruction,
        "duplicate_block": (
            "Adjacent frames must not share the same pose, same background, same camera "
            "distance, and same object placement. If the previous frame is at airport "
            "doors, this frame should move to a later beat such as curbside, taxi queue, "
            "suitcase being loaded, or a distinct close-up tied to the next scene."
        ),
    }
    return primary_scene, context


# ── Main advance function ────────────────────────────────────────────────────

async def advance(
    session_id: str,
    action: str,
    stage: str,
    payload: dict[str, Any],
    ws: ConnectionManager,
) -> dict:
    """
    Dispatch a user action to the correct handler.
    Returns a dict with 'status' and optional 'data'.
    Raises StageGateError for out-of-order actions.
    """
    key = f"{stage}.{action}"
    handlers = {
        "topic_brief.start": _handle_topic_brief_start,
        "topic_brief.change": _handle_topic_brief_change,
        "topic_brief.approve": _handle_topic_brief_approve,
        "script.retry": _handle_script_retry,
        "script.change": _handle_script_change,
        "script.approve": _handle_script_approve,
        "voiceover.select_voice": _handle_voice_select,
        "voiceover.regenerate": _handle_voiceover_regenerate,
        "voiceover.approve": _handle_voiceover_approve,
        "storyboard.retry": _handle_storyboard_retry,
        "storyboard.change": _handle_storyboard_change,
        "storyboard.approve": _handle_storyboard_approve,
        "clarifying_questions.answer": _handle_clarifying_answer,
        "image_generation.change": _handle_image_change,
        "image_generation.approve": _handle_image_approve,
        "video_generation.prompt_change": _handle_video_prompt_change,
        "video_generation.prompt_approve": _handle_video_prompt_approve,
        "video_generation.retry": _handle_video_prompt_approve,
        "video_generation.change": _handle_video_change,
        "video_generation.approve": _handle_video_approve,
        "assembly.start": _handle_assembly_start,
        "assembly.retry": _handle_assembly_start,
        "redo_clip.start": _handle_redo_clip,
    }

    handler = handlers.get(key)
    if not handler:
        raise ValueError(f"Unknown action key: {key}")

    # Stage gate: enforce sequential ordering
    stage_to_enum = {
        "topic_brief": StageEnum.topic_brief,
        "photo_upload": StageEnum.photo_upload,
        "script": StageEnum.script,
        "voiceover": StageEnum.voiceover,
        "alignment": StageEnum.alignment,
        "storyboard": StageEnum.storyboard,
        "clarifying_questions": StageEnum.clarifying_questions,
        "image_generation": StageEnum.image_generation,
        "video_generation": StageEnum.video_generation,
        "assembly": StageEnum.assembly,
        "redo_clip": StageEnum.video_generation,
    }
    required = stage_to_enum.get(stage)
    if required:
        assert_stage_reachable(session_id, required)

    return await handler(session_id, payload, ws)


# ── Topic Brief ──────────────────────────────────────────────────────────────

async def _handle_topic_brief_start(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    name = payload.get("name", "")
    context = payload.get("context")
    pill = _pill_id()

    session_svc.resolve_error_cards(session_id, "topic_brief")
    session_svc.resolve_status_pills(session_id, "topic_brief")
    session_svc.append_chat_message(session_id, _status_pill(pill, "Generating topic brief…", "topic_brief"))
    await ws.send_status(session_id, "Generating topic brief…", "topic_brief", pill_id=pill)

    try:
        brief = await openai_svc.generate_topic_brief(name, context)
    except Exception as exc:
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(str(exc), "topic_brief"))
        await ws.send_error(session_id, str(exc), "topic_brief")
        return {"status": "error", "message": str(exc)}

    version = 1
    session_svc.save_json_asset(session_id, f"topic_brief_v{version}.json", brief)
    meta = session_svc.get_session(session_id)
    meta.approval_state.topic_brief.iterations = version
    session_svc.update_metadata(session_id, approval_state=meta.approval_state)

    session_svc.resolve_status_pill(session_id, pill)
    card = _asset_card("topic_brief", version, brief)
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "topic_brief", pill_id=pill, data=brief)
    return {"status": "ok", "data": brief}


async def _handle_topic_brief_change(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    feedback = payload.get("feedback", "")
    meta = session_svc.get_session(session_id)
    current_version = meta.approval_state.topic_brief.iterations
    current = session_svc.load_json_asset(session_id, f"topic_brief_v{current_version}.json")

    pill = _pill_id()
    session_svc.resolve_error_cards(session_id, "topic_brief")
    session_svc.resolve_status_pills(session_id, "topic_brief")
    session_svc.append_chat_message(session_id, _status_pill(pill, "Rewriting topic brief…", "topic_brief"))
    await ws.send_status(session_id, "Rewriting topic brief…", "topic_brief", pill_id=pill)

    try:
        brief = await openai_svc.rewrite_topic_brief(current, feedback)
    except Exception as exc:
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(str(exc), "topic_brief"))
        await ws.send_error(session_id, str(exc), "topic_brief")
        return {"status": "error", "message": str(exc)}

    new_version = current_version + 1
    session_svc.save_json_asset(session_id, f"topic_brief_v{new_version}.json", brief)
    meta.approval_state.topic_brief.iterations = new_version
    session_svc.update_metadata(session_id, approval_state=meta.approval_state)

    session_svc.resolve_status_pill(session_id, pill)
    card = _asset_card("topic_brief", new_version, brief)
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "topic_brief", pill_id=pill, data=brief)
    return {"status": "ok", "data": brief}


async def _handle_topic_brief_approve(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    meta = session_svc.get_session(session_id)
    v = meta.approval_state.topic_brief.iterations
    session_svc.symlink_approved(session_id, f"topic_brief_v{v}.json", "topic_brief_approved.json")
    meta.approval_state.topic_brief.approved = True
    meta.approval_state.topic_brief.approved_version = v
    meta.current_stage = StageEnum.photo_upload
    if "topic_brief" not in meta.completed_stages:
        meta.completed_stages.append("topic_brief")
    session_svc.update_metadata(session_id, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})

    question = _question(
        "Upload a photo of the person — drag, paste, or click to select.",
        widget={"type": "photo_upload"},
    )
    session_svc.append_chat_message(session_id, question)
    await ws.send_status(session_id, "Topic brief approved. Upload a photo.", "photo_upload")
    return {"status": "ok"}


# ── Script ───────────────────────────────────────────────────────────────────

async def _handle_script_retry(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    return await start_script_generation(session_id, ws)


async def start_script_generation(session_id: str, ws: ConnectionManager) -> dict:
    """Called automatically after photo is confirmed."""
    brief = session_svc.load_json_asset(session_id, "topic_brief_approved.json")
    pill = _pill_id()
    session_svc.resolve_error_cards(session_id, "script")
    session_svc.resolve_status_pills(session_id, "script")
    session_svc.append_chat_message(session_id, _status_pill(pill, "Generating script…", "script"))
    await ws.send_status(session_id, "Generating script…", "script", pill_id=pill)

    try:
        script = await openai_svc.generate_script(brief)
    except Exception as exc:
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(str(exc), "script"))
        await ws.send_error(session_id, str(exc), "script", pill_id=pill)
        return {"status": "error", "message": str(exc)}
    if script.get("validation_error"):
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(script["validation_error"], "script"))
        await ws.send_error(session_id, script["validation_error"], "script", pill_id=pill)
        return {"status": "error", "message": script["validation_error"]}

    version = 1
    session_svc.save_json_asset(session_id, f"script_v{version}.json", script)
    meta = session_svc.get_session(session_id)
    meta.approval_state.script.iterations = version
    meta.current_stage = StageEnum.script
    session_svc.update_metadata(session_id, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})

    session_svc.resolve_status_pill(session_id, pill)
    card = _asset_card("script", version, script)
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "script", pill_id=pill, data=script)
    return {"status": "ok", "data": script}


async def _handle_script_change(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    feedback = payload.get("feedback", "")
    meta = session_svc.get_session(session_id)
    v = meta.approval_state.script.iterations
    current = session_svc.load_json_asset(session_id, f"script_v{v}.json")

    pill = _pill_id()
    session_svc.resolve_error_cards(session_id, "script")
    session_svc.resolve_status_pills(session_id, "script")
    session_svc.append_chat_message(session_id, _status_pill(pill, "Rewriting script…", "script"))
    await ws.send_status(session_id, "Rewriting script…", "script", pill_id=pill)

    try:
        script = await openai_svc.rewrite_script(current, feedback)
    except Exception as exc:
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(str(exc), "script"))
        await ws.send_error(session_id, str(exc), "script", pill_id=pill)
        return {"status": "error", "message": str(exc)}
    if script.get("validation_error"):
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(script["validation_error"], "script"))
        await ws.send_error(session_id, script["validation_error"], "script", pill_id=pill)
        return {"status": "error", "message": script["validation_error"]}

    new_v = v + 1
    session_svc.save_json_asset(session_id, f"script_v{new_v}.json", script)
    meta.approval_state.script.iterations = new_v
    session_svc.update_metadata(session_id, approval_state=meta.approval_state)

    session_svc.resolve_status_pill(session_id, pill)
    card = _asset_card("script", new_v, script)
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "script", pill_id=pill, data=script)
    return {"status": "ok", "data": script}


async def _handle_script_approve(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    meta = session_svc.get_session(session_id)
    v = meta.approval_state.script.iterations
    script = session_svc.load_json_asset(session_id, f"script_v{v}.json")
    if script.get("validation_error"):
        message = "This script failed validation and cannot be approved. Regenerate or request a rewrite."
        session_svc.append_chat_message(session_id, _error_card(message, "script"))
        await ws.send_error(session_id, message, "script")
        return {"status": "error", "message": message}
    session_svc.symlink_approved(session_id, f"script_v{v}.json", "script_approved.json")
    meta.approval_state.script.approved = True
    meta.approval_state.script.approved_version = v
    if "script" not in meta.completed_stages:
        meta.completed_stages.append("script")
    session_svc.update_metadata(session_id, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})

    # Ask for voice selection
    question = _question(
        "Male or female voice?",
        widget={"type": "buttons", "options": ["Male", "Female"]},
    )
    session_svc.append_chat_message(session_id, question)
    await ws.send_status(session_id, "Script approved. Choose a voice.", "voiceover")
    return {"status": "ok"}


# ── Voiceover ────────────────────────────────────────────────────────────────

async def _handle_voice_select(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    gender = payload.get("gender", "male").lower()
    if gender not in ("male", "female"):
        gender = "male"

    meta = session_svc.get_session(session_id)
    meta.settings.voice_gender = gender
    from config import get_elevenlabs_voice_ids
    meta.settings.voice_id = get_elevenlabs_voice_ids()[gender]
    meta.current_stage = StageEnum.voiceover
    session_svc.update_metadata(session_id, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})

    return await _generate_voiceover(session_id, ws)


async def _handle_voiceover_regenerate(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    return await _generate_voiceover(session_id, ws)


async def _generate_voiceover(session_id: str, ws: ConnectionManager) -> dict:
    meta = session_svc.get_session(session_id)
    script = session_svc.load_json_asset(session_id, "script_approved.json")
    text = script["full_text"]
    gender = meta.settings.voice_gender or "male"
    from config import get_elevenlabs_voice_ids
    voice_id = get_elevenlabs_voice_ids()[gender]
    if meta.settings.voice_id != voice_id:
        meta.settings.voice_id = voice_id
        session_svc.update_metadata(session_id, settings=meta.settings)

    current_v = meta.approval_state.voiceover.iterations
    new_v = current_v + 1
    pill = _pill_id()
    session_svc.append_chat_message(session_id, _status_pill(pill, f"Generating voiceover (v{new_v})…", "voiceover"))
    await ws.send_status(session_id, f"Generating voiceover (v{new_v})…", "voiceover", pill_id=pill)

    try:
        audio_bytes = await elevenlabs_svc.generate_voiceover(text, gender, voice_id)
    except Exception as exc:
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(str(exc), "voiceover"))
        await ws.send_error(session_id, str(exc), "voiceover", pill_id=pill)
        return {"status": "error", "message": str(exc)}

    tts_runtime = elevenlabs_svc.get_tts_runtime_config(gender, voice_id)
    session_svc.save_asset(session_id, f"voiceover_v{new_v}.mp3", audio_bytes)
    session_svc.save_json_asset(session_id, f"voiceover_v{new_v}.meta.json", {
        "gender": gender,
        "voice_id": voice_id,
        "model_id": tts_runtime["model_id"],
        "language_code": tts_runtime["language_code"],
        "voice_settings": tts_runtime["voice_settings"],
        "output_format": tts_runtime["output_format"],
        "audio_tempo": tts_runtime["audio_tempo"],
        "source": "backend/.env",
    })
    meta.approval_state.voiceover.iterations = new_v
    session_svc.update_metadata(session_id, approval_state=meta.approval_state)

    session_svc.resolve_status_pill(session_id, pill)
    card = _asset_card("voiceover", new_v, {
        "audio_path": f"voiceover_v{new_v}.mp3",
        "gender": gender,
        "voice_id": voice_id,
        "model_id": tts_runtime["model_id"],
        "language_code": tts_runtime["language_code"],
        "tts_speed": tts_runtime["voice_settings"].get("speed"),
        "audio_tempo": tts_runtime["audio_tempo"],
    })
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "voiceover", pill_id=pill, data={"version": new_v})
    return {"status": "ok"}


async def _handle_voiceover_approve(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    meta = session_svc.get_session(session_id)
    v = meta.approval_state.voiceover.iterations
    session_svc.symlink_approved(session_id, f"voiceover_v{v}.mp3", "voiceover_approved.mp3")
    meta.approval_state.voiceover.approved = True
    meta.approval_state.voiceover.approved_version = v
    if "voiceover" not in meta.completed_stages:
        meta.completed_stages.append("voiceover")
    session_svc.update_metadata(session_id, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})

    # Run forced alignment automatically (no approval gate)
    await _run_alignment(session_id, ws)
    return {"status": "ok"}


async def _run_alignment(session_id: str, ws: ConnectionManager) -> None:
    pill = _pill_id()
    session_svc.append_chat_message(session_id, _status_pill(pill, "Running forced alignment…", "alignment"))
    await ws.send_status(session_id, "Running forced alignment…", "alignment", pill_id=pill)

    script = session_svc.load_json_asset(session_id, "script_approved.json")
    audio_bytes = session_svc.get_asset_path(session_id, "voiceover_approved.mp3").read_bytes()

    try:
        alignment = await elevenlabs_svc.forced_alignment(audio_bytes, script["full_text"])
    except Exception as exc:
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(
            f"Forced alignment failed: {exc}. This usually means the audio quality is unusual. "
            "Try regenerating the voiceover.",
            "alignment",
        ))
        await ws.send_error(session_id, str(exc), "alignment", pill_id=pill)
        return

    session_svc.save_json_asset(session_id, "alignment.json", alignment)
    meta = session_svc.get_session(session_id)
    if "alignment" not in meta.completed_stages:
        meta.completed_stages.append("alignment")
    meta.current_stage = StageEnum.storyboard
    session_svc.update_metadata(session_id, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})

    session_svc.resolve_status_pill(session_id, pill)
    await ws.send_asset_ready(session_id, "alignment", pill_id=pill)
    await _generate_storyboard(session_id, ws)


# ── Storyboard ───────────────────────────────────────────────────────────────

async def _handle_storyboard_retry(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    return await _generate_storyboard(session_id, ws)


async def _generate_storyboard(session_id: str, ws: ConnectionManager) -> dict:
    script = session_svc.load_json_asset(session_id, "script_approved.json")
    alignment = session_svc.load_json_asset(session_id, "alignment.json")
    brief = session_svc.load_json_asset(session_id, "topic_brief_approved.json")

    pill = _pill_id()
    session_svc.resolve_error_cards(session_id, "storyboard")
    session_svc.resolve_status_pills(session_id, "storyboard")
    session_svc.append_chat_message(session_id, _status_pill(pill, "Generating storyboard…", "storyboard"))
    await ws.send_status(session_id, "Generating storyboard…", "storyboard", pill_id=pill)

    async def progress(message: str) -> None:
        session_svc.update_status_pill_message(session_id, pill, message)
        await ws.send_status(session_id, message, "storyboard", pill_id=pill)

    try:
        storyboard = await openai_svc.generate_storyboard(script, alignment, brief, on_status=progress)
    except Exception as exc:
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(str(exc), "storyboard"))
        await ws.send_error(session_id, str(exc), "storyboard", pill_id=pill)
        return {"status": "error", "message": str(exc)}

    if storyboard.get("validation_error"):
        message = (
            f"Storyboard generation failed validation after 3 attempts: "
            f"{storyboard['validation_error']}. Please describe a fix."
        )
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(
            message,
            "storyboard",
        ))
        await ws.send_error(session_id, message, "storyboard", pill_id=pill)
        return {"status": "error", "message": message}

    meta = session_svc.get_session(session_id)
    version = meta.approval_state.storyboard.iterations + 1
    session_svc.save_json_asset(session_id, f"storyboard_v{version}.json", storyboard)
    meta.approval_state.storyboard.iterations = version
    session_svc.update_metadata(session_id, approval_state=meta.approval_state)

    session_svc.resolve_status_pill(session_id, pill)
    card = _asset_card("storyboard", version, storyboard)
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "storyboard", pill_id=pill, data=storyboard)
    return {"status": "ok", "data": storyboard}


async def _handle_storyboard_change(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    feedback = payload.get("feedback", "")
    meta = session_svc.get_session(session_id)
    v = meta.approval_state.storyboard.iterations
    if v <= 0 or not session_svc.asset_exists(session_id, f"storyboard_v{v}.json"):
        return await _generate_storyboard(session_id, ws)
    current = session_svc.load_json_asset(session_id, f"storyboard_v{v}.json")
    alignment = session_svc.load_json_asset(session_id, "alignment.json")

    pill = _pill_id()
    session_svc.resolve_error_cards(session_id, "storyboard")
    session_svc.resolve_status_pills(session_id, "storyboard")
    session_svc.append_chat_message(session_id, _status_pill(pill, "Rewriting storyboard…", "storyboard"))
    await ws.send_status(session_id, "Rewriting storyboard…", "storyboard", pill_id=pill)

    async def progress(message: str) -> None:
        session_svc.update_status_pill_message(session_id, pill, message)
        await ws.send_status(session_id, message, "storyboard", pill_id=pill)

    try:
        storyboard = await openai_svc.rewrite_storyboard(current, feedback, alignment, on_status=progress)
    except Exception as exc:
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(str(exc), "storyboard"))
        await ws.send_error(session_id, str(exc), "storyboard", pill_id=pill)
        return {"status": "error", "message": str(exc)}
    if storyboard.get("validation_error"):
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(storyboard["validation_error"], "storyboard"))
        await ws.send_error(session_id, storyboard["validation_error"], "storyboard", pill_id=pill)
        return {"status": "error", "message": storyboard["validation_error"]}

    new_v = v + 1
    session_svc.save_json_asset(session_id, f"storyboard_v{new_v}.json", storyboard)
    meta.approval_state.storyboard.iterations = new_v
    session_svc.update_metadata(session_id, approval_state=meta.approval_state)

    session_svc.resolve_status_pill(session_id, pill)
    card = _asset_card("storyboard", new_v, storyboard)
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "storyboard", pill_id=pill, data=storyboard)
    return {"status": "ok", "data": storyboard}


async def _handle_storyboard_approve(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    meta = session_svc.get_session(session_id)
    v = meta.approval_state.storyboard.iterations
    session_svc.symlink_approved(session_id, f"storyboard_v{v}.json", "storyboard_approved.json")
    storyboard = session_svc.load_json_asset(session_id, "storyboard_approved.json")

    # Initialize image and video approval slots
    image_count = storyboard["image_count"]
    video_count = storyboard["total_scenes"]
    meta.approval_state.storyboard.approved = True
    meta.approval_state.storyboard.approved_version = v
    meta.approval_state.images = [
        ImageApproval(slot=f"img_{i:02d}") for i in range(1, image_count + 1)
    ]
    meta.approval_state.videos = [
        VideoApproval(clip_index=i) for i in range(1, video_count + 1)
    ]
    if "storyboard" not in meta.completed_stages:
        meta.completed_stages.append("storyboard")
    meta.current_stage = StageEnum.clarifying_questions
    session_svc.update_metadata(session_id, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})

    # Generate clarifying questions
    await _generate_clarifying_questions(session_id, ws)
    return {"status": "ok"}


# ── Clarifying Questions ─────────────────────────────────────────────────────

async def _generate_clarifying_questions(session_id: str, ws: ConnectionManager) -> None:
    storyboard = session_svc.load_json_asset(session_id, "storyboard_approved.json")
    photo_bytes, photo_mime = _get_photo_bytes(session_id)

    pill = _pill_id()
    session_svc.append_chat_message(session_id, _status_pill(pill, "Generating visual style questions…", "clarifying_questions"))
    await ws.send_status(session_id, "Generating visual style questions…", "clarifying_questions", pill_id=pill)

    try:
        questions = await openai_svc.generate_clarifying_questions(storyboard, photo_bytes, photo_mime)
    except Exception as exc:
        session_svc.append_chat_message(session_id, _error_card(str(exc), "clarifying_questions"))
        return

    session_svc.save_json_asset(session_id, "clarifying_questions.json", questions)
    session_svc.resolve_status_pill(session_id, pill)
    card = _asset_card("clarifying_questions", 1, questions)
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "clarifying_questions", pill_id=pill, data=questions)


async def _handle_clarifying_answer(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    """
    payload: {"answers": {"q1": "1990s warm Kodachrome", "q2": "Warm earth tones", ...}}
    Once all questions are answered, starts image generation.
    """
    answers = payload.get("answers", {})
    session_svc.save_json_asset(session_id, "clarifying_answers.json", answers)
    session_svc.approve_last_asset_card(session_id, "clarifying_questions")

    meta = session_svc.get_session(session_id)
    if "clarifying_questions" not in meta.completed_stages:
        meta.completed_stages.append("clarifying_questions")
    meta.current_stage = StageEnum.image_generation
    meta.current_substage = CurrentSubstage(type=SubstageType.image, index=1, iteration=1)
    session_svc.update_metadata(session_id, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})

    # Start first image generation automatically
    await _generate_image(session_id, image_index=1, ws=ws)
    return {"status": "ok"}


# ── Image Generation ─────────────────────────────────────────────────────────

async def _generate_image(session_id: str, image_index: int, ws: ConnectionManager, feedback: Optional[str] = None) -> None:
    meta = session_svc.get_session(session_id)
    storyboard = session_svc.load_json_asset(session_id, "storyboard_approved.json")
    brief = session_svc.load_json_asset(session_id, "topic_brief_approved.json")
    answers = session_svc.load_json_asset(session_id, "clarifying_answers.json")
    photo_bytes, photo_mime = _get_photo_bytes(session_id)

    slot = f"img_{image_index:02d}"
    image_approval = next((a for a in meta.approval_state.images if a.slot == slot), None)
    if not image_approval:
        return

    current_v = image_approval.iterations
    new_v = current_v + 1 if feedback else 1
    is_regen = feedback is not None and current_v > 0

    pill = _pill_id()
    msg = f"Generating image {image_index} of {storyboard['image_count']}…"
    if is_regen:
        msg = f"Rewriting image prompt with your feedback… (Image {image_index})"
    session_svc.resolve_error_cards(session_id)
    session_svc.append_chat_message(session_id, _status_pill(pill, msg, "image_generation", image_index))
    await ws.send_status(session_id, msg, "image_generation", image_index, pill_id=pill)

    scene, frame_context = _image_scene_context(storyboard, slot, image_index)

    try:
        if is_regen:
            await ws.send_status(session_id, f"Rewriting image prompt with your feedback…", "image_generation", image_index)
            prev_img_slot = f"img_{image_index - 1:02d}" if image_index > 1 else None
            prev_image_bytes = _get_image_bytes(session_id, prev_img_slot) if prev_img_slot else b""
            rejected_bytes = _get_latest_image_bytes(session_id, slot, current_v)

            prev_prompt_path = session_svc.get_asset_path(session_id, f"images/{slot}_prompt_v{current_v}.txt")
            prev_prompt = prev_prompt_path.read_text() if prev_prompt_path.exists() else ""

            if image_index == 1:
                # For img_01 regen: uploaded_photo + rejected (no prev chain)
                prompt = await openai_svc.write_image_prompt_regen(
                    photo_bytes, b"", rejected_bytes, prev_prompt, feedback, scene, slot, answers, frame_context, photo_mime
                )
                ref_images = [photo_bytes, rejected_bytes]
                ref_mimes = [photo_mime, "image/png"]
            else:
                prompt = await openai_svc.write_image_prompt_regen(
                    photo_bytes, prev_image_bytes, rejected_bytes, prev_prompt, feedback, scene, slot, answers, frame_context, photo_mime
                )
                ref_images = [photo_bytes, prev_image_bytes, rejected_bytes]
                ref_mimes = [photo_mime, "image/png", "image/png"]
        elif image_index == 1:
            await ws.send_status(session_id, f"Writing image prompt for scene 1…", "image_generation", 1)
            prompt = await openai_svc.write_image_prompt_1(
                photo_bytes, scene, answers, brief.get("person_name", ""), frame_context, photo_mime
            )
            ref_images = [photo_bytes]
            ref_mimes = [photo_mime]
        else:
            await ws.send_status(session_id, f"Writing image prompt for image {image_index}…", "image_generation", image_index)
            prev_slot = f"img_{image_index - 1:02d}"
            prev_image_bytes = _get_image_bytes(session_id, prev_slot)
            total = storyboard["image_count"]
            prompt = await openai_svc.write_image_prompt_chain(
                photo_bytes, prev_image_bytes, scene, slot, answers, image_index, total, frame_context, photo_mime
            )
            ref_images = [photo_bytes, prev_image_bytes]
            ref_mimes = [photo_mime, "image/png"]

        await ws.send_status(session_id, f"Generating image {image_index} of {storyboard['image_count']}…", "image_generation", image_index)
        image_bytes = await gemini_svc.generate_image(prompt, ref_images, ref_mimes)

    except Exception as exc:
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(str(exc), "image_generation", image_index))
        await ws.send_error(session_id, str(exc), "image_generation", image_index)
        return

    # Save prompt and image
    session_svc.save_text_asset(session_id, f"images/{slot}_prompt_v{new_v}.txt", prompt)
    session_svc.save_asset(session_id, f"images/{slot}_v{new_v}.png", image_bytes)

    # Update approval state
    image_approval.iterations = new_v
    session_svc.update_metadata(session_id, approval_state=meta.approval_state)

    card = _asset_card("image", new_v, {
        "slot": slot,
        "image_path": f"images/{slot}_v{new_v}.png",
        "image_index": image_index,
        "total_images": storyboard["image_count"],
    })
    session_svc.resolve_status_pill(session_id, pill)
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "image_generation", pill_id=pill, data={
        "slot": slot, "version": new_v, "image_index": image_index
    })


async def _handle_image_change(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    image_index = payload.get("image_index", 1)
    feedback = payload.get("feedback", "")
    meta = session_svc.get_session(session_id)
    target_image = meta.approval_state.images[image_index - 1]
    target_image.approved = False
    target_image.approved_version = None

    # A changed image invalidates every downstream generated frame and any clip
    # that uses this image as its start/end frame.
    for later_image in meta.approval_state.images[image_index:]:
        later_image.approved = False
        later_image.iterations = 0
        later_image.approved_version = None
    first_affected_clip_idx = max(image_index - 2, 0)
    for video in meta.approval_state.videos[first_affected_clip_idx:]:
        video.approved = False
        video.iterations = 0
        video.approved_version = None
        video.veo_model = None
        video.prompt_iterations = 0
        video.prompt_approved_version = None

    meta.current_stage = StageEnum.image_generation
    meta.current_substage = CurrentSubstage(
        type=SubstageType.image,
        index=image_index,
        iteration=meta.approval_state.images[image_index - 1].iterations + 1,
    )
    session_svc.update_metadata(
        session_id,
        approval_state=meta.approval_state,
        current_stage=meta.current_stage,
        current_substage=meta.current_substage,
    )
    await _generate_image(session_id, image_index, ws, feedback=feedback)
    return {"status": "ok"}


async def _handle_image_approve(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    image_index = payload.get("image_index", 1)
    meta = session_svc.get_session(session_id)
    storyboard = session_svc.load_json_asset(session_id, "storyboard_approved.json")
    total_images = storyboard["image_count"]
    total_scenes = storyboard["total_scenes"]

    slot = f"img_{image_index:02d}"
    image_approval = next(a for a in meta.approval_state.images if a.slot == slot)
    v = image_approval.iterations
    session_svc.symlink_approved(session_id, f"images/{slot}_v{v}.png", f"images/{slot}_approved.png")
    image_approval.approved = True
    image_approval.approved_version = v
    session_svc.update_metadata(session_id, approval_state=meta.approval_state)

    # Determine next step: video if we have start+end for a clip, else next image
    # Interleave: img1 -> img2 -> clip1 -> img3 -> clip2 -> img4 -> clip3 -> ...
    if image_index >= 2:
        # Check if video for clip (image_index - 1) is ready to generate
        clip_index = image_index - 1
        prev_slot = f"img_{image_index - 1:02d}"
        if (session_svc.asset_exists(session_id, f"images/{prev_slot}_approved.png")
                and not meta.approval_state.videos[clip_index - 1].approved
                and meta.approval_state.videos[clip_index - 1].iterations == 0):
            # Generate video prompt for this clip
            meta.current_stage = StageEnum.video_generation
            meta.current_substage = CurrentSubstage(type=SubstageType.video_prompt, index=clip_index, iteration=1)
            session_svc.update_metadata(session_id, current_stage=meta.current_stage, current_substage=meta.current_substage)
            await _generate_video_prompt(session_id, clip_index, ws)
            return {"status": "ok"}

    # Next image
    next_index = image_index + 1
    if next_index <= total_images:
        meta.current_substage = CurrentSubstage(type=SubstageType.image, index=next_index, iteration=1)
        session_svc.update_metadata(session_id, current_substage=meta.current_substage)
        await _generate_image(session_id, next_index, ws)
    else:
        # All images done, check for any pending videos
        await _check_all_complete(session_id, ws)
    return {"status": "ok"}


# ── Video Generation ─────────────────────────────────────────────────────────

async def _generate_video_prompt(session_id: str, clip_index: int, ws: ConnectionManager) -> None:
    storyboard = session_svc.load_json_asset(session_id, "storyboard_approved.json")
    scene = storyboard["scenes"][clip_index - 1]
    start_slot = scene["image_slot_start"]
    end_slot = scene["image_slot_end"]

    start_bytes = _get_image_bytes(session_id, start_slot)
    end_bytes = _get_image_bytes(session_id, end_slot)

    pill = _pill_id()
    session_svc.resolve_error_cards(session_id)
    session_svc.append_chat_message(session_id, _status_pill(pill, f"Writing video prompt for clip {clip_index}…", "video_generation", clip_index))
    await ws.send_status(session_id, f"Writing video prompt for clip {clip_index}…", "video_generation", clip_index, pill_id=pill)

    try:
        prompt = await openai_svc.write_video_prompt(start_bytes, end_bytes, scene)
    except Exception as exc:
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(str(exc), "video_generation", clip_index))
        return

    version = 1
    meta = session_svc.get_session(session_id)
    video_approval = meta.approval_state.videos[clip_index - 1]
    video_approval.prompt_iterations = version

    session_svc.save_text_asset(session_id, f"video_prompts/clip_{clip_index:02d}_prompt_v{version}.txt", prompt)
    session_svc.update_metadata(session_id, approval_state=meta.approval_state)

    card = _asset_card("video_prompt", version, {
        "clip_index": clip_index,
        "prompt": prompt,
        "start_image_path": f"images/{start_slot}_approved.png",
        "end_image_path": f"images/{end_slot}_approved.png",
        "duration_seconds": scene["duration_seconds"],
    })
    session_svc.resolve_status_pill(session_id, pill)
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "video_prompt", pill_id=pill, data={"clip_index": clip_index, "prompt": prompt})


async def _handle_video_prompt_change(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    clip_index = payload.get("clip_index", 1)
    feedback = payload.get("feedback", "")
    meta = session_svc.get_session(session_id)
    storyboard = session_svc.load_json_asset(session_id, "storyboard_approved.json")
    scene = storyboard["scenes"][clip_index - 1]

    video_approval = meta.approval_state.videos[clip_index - 1]
    current_v = video_approval.prompt_iterations
    start_bytes = _get_image_bytes(session_id, scene["image_slot_start"])
    end_bytes = _get_image_bytes(session_id, scene["image_slot_end"])

    prev_prompt_path = session_svc.get_asset_path(session_id, f"video_prompts/clip_{clip_index:02d}_prompt_v{current_v}.txt")
    prev_prompt = prev_prompt_path.read_text() if prev_prompt_path.exists() else ""

    pill = _pill_id()
    session_svc.resolve_error_cards(session_id)
    session_svc.append_chat_message(session_id, _status_pill(pill, f"Rewriting video prompt for clip {clip_index}…", "video_generation", clip_index))
    await ws.send_status(session_id, f"Rewriting video prompt for clip {clip_index}…", "video_generation", clip_index, pill_id=pill)

    try:
        prompt = await openai_svc.rewrite_video_prompt(start_bytes, end_bytes, prev_prompt, feedback, scene)
    except Exception as exc:
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(str(exc), "video_generation", clip_index))
        return {"status": "error", "message": str(exc)}

    new_v = current_v + 1
    session_svc.save_text_asset(session_id, f"video_prompts/clip_{clip_index:02d}_prompt_v{new_v}.txt", prompt)
    video_approval.prompt_iterations = new_v
    session_svc.update_metadata(session_id, approval_state=meta.approval_state)

    card = _asset_card("video_prompt", new_v, {
        "clip_index": clip_index,
        "prompt": prompt,
        "start_image_path": f"images/{scene['image_slot_start']}_approved.png",
        "end_image_path": f"images/{scene['image_slot_end']}_approved.png",
        "duration_seconds": scene["duration_seconds"],
    })
    session_svc.resolve_status_pill(session_id, pill)
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "video_prompt", pill_id=pill, data={"clip_index": clip_index, "prompt": prompt})
    return {"status": "ok"}


async def _handle_video_prompt_approve(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    clip_index = payload.get("clip_index", 1)
    veo_model = payload.get("veo_model", "fast")
    meta = session_svc.get_session(session_id)
    video_approval = meta.approval_state.videos[clip_index - 1]

    v = video_approval.prompt_iterations
    session_svc.symlink_approved(
        session_id,
        f"video_prompts/clip_{clip_index:02d}_prompt_v{v}.txt",
        f"video_prompts/clip_{clip_index:02d}_prompt_approved.txt",
    )
    video_approval.veo_model = veo_model
    session_svc.update_metadata(session_id, approval_state=meta.approval_state)
    session_svc.approve_last_asset_card(session_id, "video_prompt")

    _fire_bg(_run_veo(session_id, clip_index, ws))
    return {"status": "ok"}


async def _run_veo(session_id: str, clip_index: int, ws: ConnectionManager) -> None:
    meta = session_svc.get_session(session_id)
    storyboard = session_svc.load_json_asset(session_id, "storyboard_approved.json")
    scene = storyboard["scenes"][clip_index - 1]
    video_approval = meta.approval_state.videos[clip_index - 1]

    prompt_path = session_svc.get_asset_path(session_id, f"video_prompts/clip_{clip_index:02d}_prompt_approved.txt")
    prompt = prompt_path.read_text()
    start_bytes = _get_image_bytes(session_id, scene["image_slot_start"])
    end_bytes = _get_image_bytes(session_id, scene["image_slot_end"])
    model = video_approval.veo_model or "fast"
    new_v = video_approval.iterations + 1

    pill = _pill_id()
    session_svc.resolve_error_cards(session_id)
    session_svc.append_chat_message(session_id, _status_pill(
        pill, f"Submitting clip {clip_index} to Veo 3.1 {'Fast' if model == 'fast' else 'Standard'}…",
        "video_generation", clip_index,
    ))
    await ws.send_status(session_id, f"Submitting clip {clip_index} to Veo 3.1…", "video_generation", clip_index, pill_id=pill)

    async def status_cb(msg: str):
        await ws.send_status(session_id, msg, "video_generation", clip_index)

    try:
        video_bytes = await gemini_svc.run_video_job(
            prompt=prompt,
            start_frame_bytes=start_bytes,
            end_frame_bytes=end_bytes,
            duration_seconds=scene["duration_seconds"],
            model_variant=model,
            status_callback=status_cb,
        )
    except gemini_svc.VeoTimeoutError as exc:
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(str(exc), "video_generation", clip_index))
        await ws.send_error(session_id, str(exc), "video_generation", clip_index)
        return
    except gemini_svc.VeoContentPolicyError as exc:
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(str(exc), "video_generation", clip_index))
        await ws.send_error(session_id, str(exc), "video_generation", clip_index)
        return
    except Exception as exc:
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(str(exc), "video_generation", clip_index))
        await ws.send_error(session_id, str(exc), "video_generation", clip_index)
        return

    session_svc.save_asset(session_id, f"videos/clip_{clip_index:02d}_v{new_v}.mp4", video_bytes)
    video_approval.iterations = new_v
    session_svc.update_metadata(session_id, approval_state=meta.approval_state)

    session_svc.resolve_status_pill(session_id, pill)
    card = _asset_card("video", new_v, {
        "clip_index": clip_index,
        "video_path": f"videos/clip_{clip_index:02d}_v{new_v}.mp4",
        "veo_model": model,
        "duration_seconds": scene["duration_seconds"],
    })
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "video_generation", pill_id=pill, data={
        "clip_index": clip_index, "version": new_v
    })


async def _handle_video_change(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    """User rejected a video — rewrite prompt, show for re-approval."""
    clip_index = payload.get("clip_index", 1)
    feedback = payload.get("feedback", "")
    return await _handle_video_prompt_change(session_id, {
        "clip_index": clip_index,
        "feedback": feedback,
    }, ws)


async def _handle_video_approve(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    clip_index = payload.get("clip_index", 1)
    meta = session_svc.get_session(session_id)
    video_approval = meta.approval_state.videos[clip_index - 1]
    v = video_approval.iterations

    session_svc.symlink_approved(
        session_id,
        f"videos/clip_{clip_index:02d}_v{v}.mp4",
        f"videos/clip_{clip_index:02d}_approved.mp4",
    )
    video_approval.approved = True
    video_approval.approved_version = v

    storyboard = session_svc.load_json_asset(session_id, "storyboard_approved.json")
    total_images = storyboard["image_count"]
    next_image_index = clip_index + 2  # clip N ends at img_{N+1}, so next is img_{N+2}

    session_svc.update_metadata(session_id, approval_state=meta.approval_state)

    if next_image_index <= total_images:
        meta.current_stage = StageEnum.image_generation
        meta.current_substage = CurrentSubstage(type=SubstageType.image, index=next_image_index, iteration=1)
        session_svc.update_metadata(session_id, current_stage=meta.current_stage, current_substage=meta.current_substage)
        await _generate_image(session_id, next_image_index, ws)
    else:
        await _check_all_complete(session_id, ws)

    return {"status": "ok"}


async def _check_all_complete(session_id: str, ws: ConnectionManager) -> None:
    """Check if all images and videos are approved; if so, prompt for assembly."""
    meta = session_svc.get_session(session_id)
    all_images_done = all(a.approved for a in meta.approval_state.images)
    all_videos_done = all(v.approved for v in meta.approval_state.videos)

    if all_images_done and all_videos_done:
        meta.current_stage = StageEnum.assembly
        session_svc.update_metadata(session_id, current_stage=meta.current_stage)
        question = _question(
            "All images and videos approved! Ready to assemble the final reel?",
            widget={"type": "buttons", "options": ["Assemble"]},
        )
        session_svc.append_chat_message(session_id, question)
        await ws.send_status(session_id, "All assets approved. Ready for assembly.", "assembly")


# ── Assembly ─────────────────────────────────────────────────────────────────

async def _handle_assembly_start(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    session_svc.resolve_error_cards(session_id, "assembly")
    session_svc.resolve_status_pills(session_id, "assembly")
    meta = session_svc.get_session(session_id)
    current_version = meta.approval_state.final_reel.get("version", 0) if meta.approval_state.final_reel else 0
    new_version = current_version + 1

    async def status_cb(msg: str):
        session_svc.append_chat_message(session_id, _status_pill(_pill_id(), msg, "assembly"))
        await ws.send_status(session_id, msg, "assembly")

    try:
        final_path = await ffmpeg_svc.run_assembly(session_id, new_version, status_cb)
    except Exception as exc:
        session_svc.resolve_status_pills(session_id, "assembly")
        session_svc.append_chat_message(session_id, _error_card(str(exc), "assembly"))
        await ws.send_error(session_id, str(exc), "assembly")
        return {"status": "error", "message": str(exc)}

    session_svc.resolve_status_pills(session_id, "assembly")
    meta = session_svc.get_session(session_id)
    meta.approval_state.final_reel = {"assembled": True, "version": new_version}
    meta.current_stage = StageEnum.final_review
    if "assembly" not in meta.completed_stages:
        meta.completed_stages.append("assembly")
    session_svc.update_metadata(session_id, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})

    size_mb = final_path.stat().st_size / (1024 * 1024)
    card = _asset_card("final_reel", new_version, {
        "reel_path": f"final/reel_v{new_version}.mp4",
        "size_mb": round(size_mb, 1),
        "version": new_version,
    })
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "assembly", data={"version": new_version, "size_mb": size_mb})
    return {"status": "ok", "version": new_version}


# ── Redo Clip ────────────────────────────────────────────────────────────────

async def _handle_redo_clip(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    clip_index = payload.get("clip_index", 1)
    meta = session_svc.get_session(session_id)

    # Reset this clip's approval state
    video_approval = meta.approval_state.videos[clip_index - 1]
    video_approval.approved = False
    video_approval.approved_version = None
    video_approval.prompt_iterations = 0

    meta.current_stage = StageEnum.video_generation
    meta.current_substage = CurrentSubstage(type=SubstageType.video_prompt, index=clip_index, iteration=1)
    meta.approval_state.final_reel = None  # reel is now stale
    session_svc.update_metadata(session_id, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})

    # Generate new video prompt
    await _generate_video_prompt(session_id, clip_index, ws)
    return {"status": "ok"}
