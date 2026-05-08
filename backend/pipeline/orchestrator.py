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
from services import cost_svc
from services import audio_tag_svc
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
    StageEnum.script,
    StageEnum.photo_upload,
    StageEnum.voiceover,
    StageEnum.alignment,
    StageEnum.storyboard,
    StageEnum.image_generation,
    StageEnum.video_generation,
    StageEnum.assembly,
    StageEnum.final_review,
]


def _stage_index(stage: StageEnum) -> int:
    return _STAGE_ORDER.index(stage)


def assert_stage_reachable(session_id: str, required_stage: StageEnum) -> None:
    meta = session_svc.get_session(session_id)
    if meta.assembly_locked:
        raise StageGateError("This session is locked because Avengers Assemble was clicked.")
    current_idx = _stage_index(meta.current_stage)
    required_idx = _stage_index(required_stage)
    if required_idx > current_idx + 1:
        raise StageGateError(
            f"Cannot run {required_stage.value}: session is at {meta.current_stage.value}"
        )


def _active_version(stage_approval) -> int:
    return int(stage_approval.approved_version or stage_approval.iterations or 1)


def _remove_completed_after(meta, stage: str) -> None:
    order = [s.value for s in _STAGE_ORDER]
    if stage not in order:
        return
    cutoff = order.index(stage)
    meta.completed_stages = [
        completed for completed in meta.completed_stages
        if completed in order and order.index(completed) <= cutoff
    ]


def _clear_final(meta) -> None:
    meta.approval_state.final_reel = None


def _clip_for_image_index(image_index: int) -> int:
    """Single image per clip: image N maps directly to clip N (1-based)."""
    return int(image_index)


def _invalidate_after_script(meta) -> None:
    meta.approval_state.voiceover.approved = False
    meta.approval_state.voiceover.approved_version = None
    meta.approval_state.storyboard.approved = False
    meta.approval_state.storyboard.approved_version = None
    meta.approval_state.images = []
    meta.approval_state.videos = []
    _clear_final(meta)


def _invalidate_after_voiceover(meta) -> None:
    meta.approval_state.storyboard.approved = False
    meta.approval_state.storyboard.approved_version = None
    meta.approval_state.images = []
    meta.approval_state.videos = []
    _clear_final(meta)


def _invalidate_after_storyboard(meta) -> None:
    meta.approval_state.images = []
    meta.approval_state.videos = []
    _clear_final(meta)


def _invalidate_clip(meta, clip_index: int) -> None:
    idx = int(clip_index) - 1
    if 0 <= idx < len(meta.approval_state.videos):
        video = meta.approval_state.videos[idx]
        video.approved = False
        video.approved_version = None
        video.iterations = 0
        video.veo_model = None
        video.prompt_approved_version = None
    _clear_final(meta)


def _asset_cost_for_card(session_id: str, asset_type: str, asset_id: str, version: int) -> dict:
    return cost_svc.asset_cost(
        session_id,
        asset_type=asset_type,
        asset_id=asset_id,
        version=version,
    )["summary"]


def _asset_card_statuses(session_id: str, subtype: str) -> dict[int, str]:
    history = session_svc.get_chat_history(session_id)
    statuses: dict[int, str] = {}
    for msg in history.messages:
        if msg.get("msg_type") == "asset_card" and msg.get("subtype") == subtype:
            try:
                statuses[int(msg.get("iteration", 0))] = str(msg.get("status") or "")
            except (TypeError, ValueError):
                continue
    return statuses


def _script_iteration_chain(session_id: str, through_version: int | None = None) -> list[dict]:
    meta = session_svc.get_session(session_id)
    latest = through_version or meta.approval_state.script.iterations
    statuses = _asset_card_statuses(session_id, "script")
    chain: list[dict] = []
    for version in range(1, latest + 1):
        path = f"script_v{version}.json"
        if not session_svc.asset_exists(session_id, path):
            continue
        script = session_svc.load_json_asset(session_id, path)
        rewrite_context = script.get("rewrite_context") if isinstance(script, dict) else None
        chain.append({
            "version": version,
            "status": statuses.get(version),
            "rewrite_context": rewrite_context,
            "script": script,
        })
    return chain


async def _continue_autopilot(session_id: str, ws: ConnectionManager) -> None:
    meta = session_svc.get_session(session_id)
    if meta.assembly_locked or not meta.settings.autopilot_enabled:
        return

    if (
        meta.current_stage == StageEnum.script
        and meta.approval_state.script.iterations
        and meta.approval_state.script.approved_version != meta.approval_state.script.iterations
    ):
        await _handle_script_approve(session_id, {}, ws)
        return
    if meta.current_stage == StageEnum.voiceover:
        if (
            meta.approval_state.voiceover.iterations
            and meta.approval_state.voiceover.approved_version != meta.approval_state.voiceover.iterations
        ):
            await _handle_voiceover_approve(session_id, {}, ws)
            return
        if meta.approval_state.script.approved and not meta.approval_state.voiceover.iterations:
            await _handle_voice_select(session_id, {"gender": "female", "speed": meta.settings.voice_speed or 1.2}, ws)
            return
    if (
        meta.current_stage == StageEnum.storyboard
        and meta.approval_state.storyboard.iterations
        and meta.approval_state.storyboard.approved_version != meta.approval_state.storyboard.iterations
    ):
        await _handle_storyboard_approve(session_id, {}, ws)
        return
    if meta.current_stage == StageEnum.image_generation:
        for idx, image in enumerate(meta.approval_state.images, start=1):
            if image.iterations and image.approved_version != image.iterations:
                await _handle_image_approve(session_id, {"image_index": idx}, ws)
                return
    if meta.current_stage == StageEnum.video_generation:
        for video in meta.approval_state.videos:
            if video.prompt_iterations and video.prompt_approved_version != video.prompt_iterations:
                await _handle_video_prompt_approve(session_id, {"clip_index": video.clip_index, "veo_model": video.veo_model or "fast"}, ws)
                return
            if video.iterations and video.approved_version != video.iterations:
                await _handle_video_approve(session_id, {"clip_index": video.clip_index}, ws)
                return
    if meta.current_stage == StageEnum.assembly:
        all_images_done = all(a.approved for a in meta.approval_state.images)
        all_videos_done = all(v.approved for v in meta.approval_state.videos)
        if all_images_done and all_videos_done:
            await _handle_assembly_start(session_id, {}, ws)


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


def _approved_image_asset_path(meta, slot: str) -> str:
    image = next((item for item in meta.approval_state.images if item.slot == slot), None)
    if image and image.approved_version:
        return f"images/{slot}_v{image.approved_version}.png"
    return f"images/{slot}_approved.png"


def _image_qa_failed(audit: dict) -> bool:
    """Single image-per-clip world: gate on the consolidated `approved` flag.

    The audit service now performs all sub-checks (identity per
    face_reference_mode, era consistency, no on-display text, camera angle,
    photoreal). If the audit was unavailable the fallback denies, surfacing
    the failure to the user. Treat a missing/empty audit as failed too.
    """
    if not audit:
        return True
    if audit.get("audit_error"):
        return True
    return audit.get("approved") is not True


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
        "script.retry": _handle_script_retry,
        "script.change": _handle_script_change,
        "script.edit": _handle_script_edit,
        "script.approve": _handle_script_approve,
        "script.restore": _handle_script_restore,
        "voiceover.select_voice": _handle_voice_select,
        "voiceover.regenerate": _handle_voiceover_regenerate,
        "voiceover.approve": _handle_voiceover_approve,
        "voiceover.restore": _handle_voiceover_restore,
        "storyboard.retry": _handle_storyboard_retry,
        "storyboard.change": _handle_storyboard_change,
        "storyboard.approve": _handle_storyboard_approve,
        "storyboard.restore": _handle_storyboard_restore,
        "image_generation.retry": _handle_image_change,
        "image_generation.change": _handle_image_change,
        "image_generation.approve": _handle_image_approve,
        "image_generation.restore": _handle_image_restore,
        "video_generation.prompt_change": _handle_video_prompt_change,
        "video_generation.prompt_approve": _handle_video_prompt_approve,
        "video_generation.prompt_restore": _handle_video_prompt_restore,
        "video_generation.retry": _handle_video_prompt_approve,
        "video_generation.change": _handle_video_change,
        "video_generation.approve": _handle_video_approve,
        "video_generation.restore": _handle_video_restore,
        "assembly.start": _handle_assembly_start,
        "assembly.retry": _handle_assembly_start,
        "redo_clip.start": _handle_redo_clip,
        "settings.autopilot": _handle_autopilot_toggle,
    }

    handler = handlers.get(key)
    if not handler:
        raise ValueError(f"Unknown action key: {key}")

    # Stage gate: enforce sequential ordering
    stage_to_enum = {
        "script": StageEnum.script,
        "photo_upload": StageEnum.photo_upload,
        "voiceover": StageEnum.voiceover,
        "alignment": StageEnum.alignment,
        "storyboard": StageEnum.storyboard,
        "image_generation": StageEnum.image_generation,
        "video_generation": StageEnum.video_generation,
        "assembly": StageEnum.assembly,
        "redo_clip": StageEnum.video_generation,
    }
    required = stage_to_enum.get(stage)
    if required:
        assert_stage_reachable(session_id, required)

    return await handler(session_id, payload, ws)


async def _handle_autopilot_toggle(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    meta = session_svc.get_session(session_id)
    if meta.assembly_locked:
        return {"status": "error", "message": "This session is locked after Avengers Assemble."}
    meta.settings.autopilot_enabled = bool(payload.get("enabled", False))
    if payload.get("voice_speed") is not None:
        meta.settings.voice_speed = float(payload.get("voice_speed") or 1.2)
    session_svc.update_metadata(session_id, settings=meta.settings)
    await ws.send_status(
        session_id,
        "Autopilot on." if meta.settings.autopilot_enabled else "Autopilot off.",
        meta.current_stage.value,
    )
    if meta.settings.autopilot_enabled:
        await _continue_autopilot(session_id, ws)
    return {"status": "ok", "enabled": meta.settings.autopilot_enabled}


# ── Script ───────────────────────────────────────────────────────────────────

async def _handle_script_retry(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    return await start_script_generation(session_id, ws)


def _load_script_prompt(session_id: str) -> str:
    path = session_svc.get_asset_path(session_id, "script_prompt.txt")
    if path.exists():
        return path.read_text().strip()
    return session_svc.get_session(session_id).person_name.strip()


async def start_script_generation(session_id: str, ws: ConnectionManager) -> dict:
    """Generate the first script directly from the user's initial input."""
    script_prompt = _load_script_prompt(session_id)
    meta = session_svc.get_session(session_id)
    version = meta.approval_state.script.iterations + 1
    pill = _pill_id()
    session_svc.resolve_error_cards(session_id, "script")
    session_svc.resolve_status_pills(session_id, "script")
    session_svc.append_chat_message(session_id, _status_pill(pill, "Generating script…", "script"))
    await ws.send_status(session_id, "Generating script…", "script", pill_id=pill)

    try:
        script = await openai_svc.generate_script(script_prompt, session_id=session_id, version=version)
    except Exception as exc:
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(str(exc), "script"))
        await ws.send_error(session_id, str(exc), "script", pill_id=pill)
        return {"status": "error", "message": str(exc)}

    session_svc.mark_pending_asset_cards_previous_from(session_id, {"script"})
    session_svc.save_json_asset(session_id, f"script_v{version}.json", script)
    meta.approval_state.script.iterations = version
    meta.approval_state.script.approved = False
    meta.approval_state.script.approved_version = None
    meta.current_stage = StageEnum.script
    session_svc.update_metadata(session_id, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})

    session_svc.resolve_status_pill(session_id, pill)
    card_data = {**script, "cost_summary": _asset_cost_for_card(session_id, "script", "script", version)}
    card = _asset_card("script", version, card_data)
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "script", pill_id=pill, data=script)
    await _continue_autopilot(session_id, ws)
    return {"status": "ok", "data": script}


async def _handle_script_change(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    feedback = payload.get("feedback", "")
    meta = session_svc.get_session(session_id)
    v = int(payload.get("version") or payload.get("iteration") or _active_version(meta.approval_state.script))
    current = session_svc.load_json_asset(session_id, f"script_v{v}.json")
    script_history = _script_iteration_chain(session_id)

    pill = _pill_id()
    session_svc.resolve_error_cards(session_id, "script")
    session_svc.resolve_status_pills(session_id, "script")
    session_svc.append_chat_message(session_id, _status_pill(pill, "Rewriting script…", "script"))
    await ws.send_status(session_id, "Rewriting script…", "script", pill_id=pill)

    try:
        script = await openai_svc.rewrite_script(
            current,
            feedback,
            script_history,
            session_id=session_id,
            version=meta.approval_state.script.iterations + 1,
        )
    except Exception as exc:
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(str(exc), "script"))
        await ws.send_error(session_id, str(exc), "script", pill_id=pill)
        return {"status": "error", "message": str(exc)}

    new_v = meta.approval_state.script.iterations + 1
    script["rewrite_context"] = {
        "from_version": v,
        "feedback": feedback,
    }
    session_svc.mark_pending_asset_cards_previous_from(session_id, {"script"})
    session_svc.save_json_asset(session_id, f"script_v{new_v}.json", script)
    meta.approval_state.script.iterations = new_v
    meta.current_stage = StageEnum.script
    session_svc.update_metadata(session_id, approval_state=meta.approval_state, current_stage=meta.current_stage)

    session_svc.resolve_status_pill(session_id, pill)
    card_data = {**script, "cost_summary": _asset_cost_for_card(session_id, "script", "script", new_v)}
    card = _asset_card("script", new_v, card_data)
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "script", pill_id=pill, data=script)
    await _continue_autopilot(session_id, ws)
    return {"status": "ok", "data": script}


async def _handle_script_edit(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    edited_text = str(payload.get("script") or payload.get("full_text") or "").strip()
    if not edited_text:
        return {"status": "error", "message": "script is required"}

    meta = session_svc.get_session(session_id)
    source_v = int(payload.get("version") or payload.get("iteration") or _active_version(meta.approval_state.script))
    if not session_svc.asset_exists(session_id, f"script_v{source_v}.json"):
        return {"status": "error", "message": f"script_v{source_v}.json not found"}

    new_v = meta.approval_state.script.iterations + 1
    script = openai_svc.normalize_manual_script(edited_text)
    if not script["full_text"].strip():
        return {"status": "error", "message": "script must include spoken text"}
    script["rewrite_context"] = {
        "from_version": source_v,
        "mode": "inline_edit",
    }

    session_svc.resolve_error_cards(session_id, "script")
    session_svc.resolve_status_pills(session_id, "script")
    session_svc.mark_pending_asset_cards_previous_from(session_id, {"script"})
    session_svc.save_json_asset(session_id, f"script_v{new_v}.json", script)
    meta.approval_state.script.iterations = new_v
    meta.current_stage = StageEnum.script
    session_svc.update_metadata(session_id, approval_state=meta.approval_state, current_stage=meta.current_stage)

    card = _asset_card("script", new_v, script)
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "script", data=script)
    return {"status": "ok", "data": script}


async def _ensure_audio_tags(session_id: str) -> str:
    """Inject ElevenLabs v3 audio tags into the approved clean script.

    Runs automatically (no user gate) after every script approval / restore /
    edit. Saves ``script_tagged_v{n}.txt`` and symlinks
    ``script_tagged_approved.txt``. The CLEAN script remains the source of
    truth for forced alignment and storyboard; only TTS reads the tagged
    version.
    """
    meta = session_svc.get_session(session_id)
    script_v = meta.approval_state.script.approved_version or meta.approval_state.script.iterations
    if not script_v:
        return ""
    tagged_v_path = f"script_tagged_v{script_v}.txt"
    if not session_svc.asset_exists(session_id, tagged_v_path):
        script = session_svc.load_json_asset(session_id, f"script_v{script_v}.json")
        clean_text = str(script.get("full_text") or "").strip()
        if not clean_text:
            return ""
        tagged = await audio_tag_svc.inject_audio_tags(
            clean_text, session_id=session_id, version=script_v,
        )
        session_svc.save_text_asset(session_id, tagged_v_path, tagged)
    session_svc.symlink_approved(session_id, tagged_v_path, "script_tagged_approved.txt")
    return session_svc.get_asset_path(session_id, "script_tagged_approved.txt").read_text()


async def continue_after_photo_confirm(session_id: str, ws: ConnectionManager, status_prefix: str = "Photo confirmed.") -> dict:
    meta = session_svc.get_session(session_id)
    if "photo_upload" not in meta.completed_stages:
        meta.completed_stages.append("photo_upload")
    meta.current_stage = StageEnum.voiceover
    session_svc.update_metadata(session_id, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})

    if meta.settings.autopilot_enabled:
        return await _handle_voice_select(session_id, {"gender": "female", "speed": meta.settings.voice_speed or 1.2}, ws)

    # If voiceover has already been initiated (e.g. user inline-edited the
    # script after voiceover was generated), don't re-ask for a voice — the
    # existing voiceover card stays in the chat and the script edit only
    # invalidates downstream assets.
    if meta.approval_state.voiceover.iterations > 0:
        return {"status": "ok"}

    session_svc.append_chat_message(session_id, _question(
        "Choose a voice or paste an ElevenLabs voice ID.",
        widget={"type": "voice_select", "options": ["Female", "Male", "Custom voice ID"]},
    ))
    await ws.send_status(session_id, f"{status_prefix} Choose a voice.", "voiceover")
    return {"status": "ok"}


async def _handle_script_approve(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    meta = session_svc.get_session(session_id)
    v = meta.approval_state.script.iterations
    previous_v = meta.approval_state.script.approved_version
    script = session_svc.load_json_asset(session_id, f"script_v{v}.json")
    session_svc.symlink_approved(session_id, f"script_v{v}.json", "script_approved.json")
    meta.approval_state.script.approved = True
    meta.approval_state.script.approved_version = v
    if previous_v and previous_v != v:
        _invalidate_after_script(meta)
    if "script" not in meta.completed_stages:
        meta.completed_stages.append("script")
    if meta.photo_ext and "photo_upload" in meta.completed_stages:
        meta.current_stage = StageEnum.voiceover
    else:
        meta.current_stage = StageEnum.photo_upload
    session_svc.update_metadata(session_id, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})
    session_svc.mark_asset_card_approved(session_id, "script", v)

    # Auto-inject ElevenLabs v3 audio tags so the voiceover stage uses the
    # tagged variant. The clean script continues to flow to alignment + storyboard.
    await _ensure_audio_tags(session_id)

    if meta.current_stage == StageEnum.voiceover:
        await continue_after_photo_confirm(session_id, ws, "Script approved.")
    else:
        question = _question(
            "Upload a photo of the person — drag, paste, or click to select.",
            widget={"type": "photo_upload"},
        )
        session_svc.append_chat_message(session_id, question)
        await ws.send_status(session_id, "Script approved. Upload a photo.", "photo_upload")
    return {"status": "ok"}


async def _handle_script_restore(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    version = int(payload.get("version") or payload.get("iteration") or 1)
    if not session_svc.asset_exists(session_id, f"script_v{version}.json"):
        return {"status": "error", "message": f"script_v{version}.json not found"}
    meta = session_svc.get_session(session_id)
    session_svc.symlink_approved(session_id, f"script_v{version}.json", "script_approved.json")
    meta.approval_state.script.approved = True
    meta.approval_state.script.approved_version = version
    _invalidate_after_script(meta)
    _remove_completed_after(meta, "script")
    if "script" not in meta.completed_stages:
        meta.completed_stages.append("script")
    if meta.photo_ext:
        if "photo_upload" not in meta.completed_stages:
            meta.completed_stages.append("photo_upload")
        meta.current_stage = StageEnum.voiceover
    else:
        meta.current_stage = StageEnum.photo_upload
    session_svc.update_metadata(session_id, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})
    session_svc.mark_asset_card_approved(session_id, "script", version)
    await _ensure_audio_tags(session_id)
    if meta.current_stage == StageEnum.voiceover:
        await continue_after_photo_confirm(session_id, ws, "Script restored.")
    else:
        session_svc.append_chat_message(session_id, _question(
            "Upload a photo of the person — drag, paste, or click to select.",
            widget={"type": "photo_upload"},
        ))
        await ws.send_status(session_id, "Script restored. Upload a photo.", "photo_upload")
    return {"status": "ok"}


# ── Voiceover ────────────────────────────────────────────────────────────────

async def _handle_voice_select(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    gender = payload.get("gender", "male").lower()
    custom_voice_id = (payload.get("voice_id") or "").strip()
    if custom_voice_id:
        gender = "custom"
    elif gender not in ("male", "female"):
        gender = "female"
    speed = float(payload.get("speed") or 1.2)

    meta = session_svc.get_session(session_id)
    meta.settings.voice_gender = gender
    from config import get_elevenlabs_voice_ids
    meta.settings.voice_id = custom_voice_id or get_elevenlabs_voice_ids().get(gender, get_elevenlabs_voice_ids()["female"])
    meta.settings.voice_speed = max(0.7, min(1.2, speed))
    meta.current_stage = StageEnum.voiceover
    session_svc.update_metadata(session_id, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})

    return await _generate_voiceover(session_id, ws)


async def _handle_voiceover_regenerate(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    meta = session_svc.get_session(session_id)
    if payload.get("voice_id"):
        meta.settings.voice_id = str(payload.get("voice_id")).strip()
        meta.settings.voice_gender = "custom"
    if payload.get("gender"):
        gender = str(payload.get("gender")).lower()
        if gender in {"male", "female"}:
            from config import get_elevenlabs_voice_ids
            meta.settings.voice_gender = gender
            meta.settings.voice_id = get_elevenlabs_voice_ids()[gender]
    if payload.get("speed") is not None:
        meta.settings.voice_speed = max(0.7, min(1.2, float(payload.get("speed"))))
    session_svc.update_metadata(session_id, settings=meta.settings)
    return await _generate_voiceover(session_id, ws)


async def _generate_voiceover(session_id: str, ws: ConnectionManager) -> dict:
    meta = session_svc.get_session(session_id)
    # TTS receives the tagged script (ElevenLabs v3 audio tags applied).
    # The clean script stays on disk for alignment + storyboard.
    if not session_svc.asset_exists(session_id, "script_tagged_approved.txt"):
        await _ensure_audio_tags(session_id)
    tagged_path = session_svc.get_asset_path(session_id, "script_tagged_approved.txt")
    if tagged_path.exists():
        text = tagged_path.read_text().strip()
    else:
        # Defensive fallback — should never hit in a well-formed session.
        script = session_svc.load_json_asset(session_id, "script_approved.json")
        text = script["full_text"]
    gender = meta.settings.voice_gender or "female"
    from config import get_elevenlabs_voice_ids
    voice_id = meta.settings.voice_id or get_elevenlabs_voice_ids().get(gender, get_elevenlabs_voice_ids()["female"])
    speed = meta.settings.voice_speed or 1.2
    if meta.settings.voice_id != voice_id or meta.settings.voice_speed != speed:
        meta.settings.voice_id = voice_id
        meta.settings.voice_speed = speed
        session_svc.update_metadata(session_id, settings=meta.settings)

    current_v = meta.approval_state.voiceover.iterations
    new_v = current_v + 1
    pill = _pill_id()
    session_svc.append_chat_message(session_id, _status_pill(pill, f"Generating voiceover (v{new_v})…", "voiceover"))
    await ws.send_status(session_id, f"Generating voiceover (v{new_v})…", "voiceover", pill_id=pill)

    try:
        audio_bytes = await elevenlabs_svc.generate_voiceover(
            text,
            gender,
            voice_id,
            speed=speed,
            cost_context={
                "session_id": session_id,
                "stage": "voiceover",
                "asset_type": "voiceover",
                "asset_id": "voiceover",
                "version": new_v,
            },
        )
    except Exception as exc:
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(str(exc), "voiceover"))
        await ws.send_error(session_id, str(exc), "voiceover", pill_id=pill)
        return {"status": "error", "message": str(exc)}

    tts_runtime = elevenlabs_svc.get_tts_runtime_config(gender, voice_id, speed)
    session_svc.save_asset(session_id, f"voiceover_v{new_v}.mp3", audio_bytes)
    session_svc.save_json_asset(session_id, f"voiceover_v{new_v}.meta.json", {
        "gender": gender,
        "voice_id": voice_id,
        "model_id": tts_runtime["model_id"],
        "language_code": tts_runtime["language_code"],
        "voice_settings": tts_runtime["voice_settings"],
        "output_format": tts_runtime["output_format"],
        "cost_summary": _asset_cost_for_card(session_id, "voiceover", "voiceover", new_v),
        "source": "backend/.env",
    })
    meta.approval_state.voiceover.iterations = new_v
    meta.current_stage = StageEnum.voiceover
    session_svc.update_metadata(session_id, approval_state=meta.approval_state, current_stage=meta.current_stage)

    session_svc.resolve_status_pill(session_id, pill)
    card = _asset_card("voiceover", new_v, {
        "audio_path": f"voiceover_v{new_v}.mp3",
        "gender": gender,
        "voice_id": voice_id,
        "model_id": tts_runtime["model_id"],
        "language_code": tts_runtime["language_code"],
        "tts_speed": tts_runtime["voice_settings"].get("speed"),
        "cost_summary": _asset_cost_for_card(session_id, "voiceover", "voiceover", new_v),
    })
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "voiceover", pill_id=pill, data={"version": new_v})
    await _continue_autopilot(session_id, ws)
    return {"status": "ok"}


async def _handle_voiceover_approve(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    meta = session_svc.get_session(session_id)
    v = meta.approval_state.voiceover.iterations
    previous_v = meta.approval_state.voiceover.approved_version
    session_svc.symlink_approved(session_id, f"voiceover_v{v}.mp3", "voiceover_approved.mp3")
    meta.approval_state.voiceover.approved = True
    meta.approval_state.voiceover.approved_version = v
    if previous_v and previous_v != v:
        _invalidate_after_voiceover(meta)
    if "voiceover" not in meta.completed_stages:
        meta.completed_stages.append("voiceover")
    session_svc.update_metadata(session_id, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})
    session_svc.mark_asset_card_approved(session_id, "voiceover", v)

    # Run forced alignment automatically (no approval gate)
    await _run_alignment(session_id, ws)
    return {"status": "ok"}


async def _handle_voiceover_restore(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    version = int(payload.get("version") or payload.get("iteration") or 1)
    if not session_svc.asset_exists(session_id, f"voiceover_v{version}.mp3"):
        return {"status": "error", "message": f"voiceover_v{version}.mp3 not found"}
    meta = session_svc.get_session(session_id)
    session_svc.symlink_approved(session_id, f"voiceover_v{version}.mp3", "voiceover_approved.mp3")
    meta.approval_state.voiceover.approved = True
    meta.approval_state.voiceover.approved_version = version
    _invalidate_after_voiceover(meta)
    _remove_completed_after(meta, "voiceover")
    if "voiceover" not in meta.completed_stages:
        meta.completed_stages.append("voiceover")
    meta.current_stage = StageEnum.alignment
    session_svc.update_metadata(session_id, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})
    session_svc.mark_asset_card_approved(session_id, "voiceover", version)
    await _run_alignment(session_id, ws)
    return {"status": "ok"}


async def _run_alignment(session_id: str, ws: ConnectionManager) -> None:
    pill = _pill_id()
    session_svc.append_chat_message(session_id, _status_pill(pill, "Running forced alignment…", "alignment"))
    await ws.send_status(session_id, "Running forced alignment…", "alignment", pill_id=pill)

    script = session_svc.load_json_asset(session_id, "script_approved.json")
    audio_bytes = session_svc.get_asset_path(session_id, "voiceover_approved.mp3").read_bytes()

    try:
        meta = session_svc.get_session(session_id)
        alignment = await elevenlabs_svc.forced_alignment(
            audio_bytes,
            script["full_text"],
            cost_context={
                "session_id": session_id,
                "stage": "alignment",
                "asset_type": "alignment",
                "asset_id": "alignment",
                "version": meta.approval_state.voiceover.approved_version,
            },
        )
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
    meta = session_svc.get_session(session_id)
    version = meta.approval_state.storyboard.iterations + 1

    pill = _pill_id()
    session_svc.resolve_error_cards(session_id, "storyboard")
    session_svc.resolve_status_pills(session_id, "storyboard")
    session_svc.append_chat_message(session_id, _status_pill(pill, "Generating storyboard…", "storyboard"))
    await ws.send_status(session_id, "Generating storyboard…", "storyboard", pill_id=pill)

    async def progress(message: str) -> None:
        session_svc.update_status_pill_message(session_id, pill, message)
        await ws.send_status(session_id, message, "storyboard", pill_id=pill)

    try:
        storyboard = await openai_svc.generate_storyboard(
            script, alignment, on_status=progress, session_id=session_id, version=version
        )
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

    session_svc.save_json_asset(session_id, f"storyboard_v{version}.json", storyboard)
    meta.approval_state.storyboard.iterations = version
    meta.current_stage = StageEnum.storyboard
    session_svc.update_metadata(session_id, approval_state=meta.approval_state, current_stage=meta.current_stage)

    session_svc.resolve_status_pill(session_id, pill)
    card_data = {**storyboard, "cost_summary": _asset_cost_for_card(session_id, "storyboard", "storyboard", version)}
    card = _asset_card("storyboard", version, card_data)
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "storyboard", pill_id=pill, data=storyboard)
    await _continue_autopilot(session_id, ws)
    return {"status": "ok", "data": storyboard}


async def _handle_storyboard_change(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    feedback = payload.get("feedback", "")
    meta = session_svc.get_session(session_id)
    v = int(payload.get("version") or payload.get("iteration") or _active_version(meta.approval_state.storyboard))
    if v <= 0 or not session_svc.asset_exists(session_id, f"storyboard_v{v}.json"):
        return await _generate_storyboard(session_id, ws)
    current = session_svc.load_json_asset(session_id, f"storyboard_v{v}.json")
    alignment = session_svc.load_json_asset(session_id, "alignment.json")
    script = (
        session_svc.load_json_asset(session_id, "script_approved.json")
        if session_svc.asset_exists(session_id, "script_approved.json")
        else None
    )

    pill = _pill_id()
    session_svc.resolve_error_cards(session_id, "storyboard")
    session_svc.resolve_status_pills(session_id, "storyboard")
    session_svc.append_chat_message(session_id, _status_pill(pill, "Rewriting storyboard…", "storyboard"))
    await ws.send_status(session_id, "Rewriting storyboard…", "storyboard", pill_id=pill)

    async def progress(message: str) -> None:
        session_svc.update_status_pill_message(session_id, pill, message)
        await ws.send_status(session_id, message, "storyboard", pill_id=pill)

    try:
        storyboard = await openai_svc.rewrite_storyboard(
            current,
            feedback,
            alignment,
            script,
            on_status=progress,
            session_id=session_id,
            version=meta.approval_state.storyboard.iterations + 1,
        )
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

    new_v = meta.approval_state.storyboard.iterations + 1
    session_svc.save_json_asset(session_id, f"storyboard_v{new_v}.json", storyboard)
    meta.approval_state.storyboard.iterations = new_v
    meta.current_stage = StageEnum.storyboard
    session_svc.update_metadata(session_id, approval_state=meta.approval_state, current_stage=meta.current_stage)

    session_svc.resolve_status_pill(session_id, pill)
    card_data = {**storyboard, "cost_summary": _asset_cost_for_card(session_id, "storyboard", "storyboard", new_v)}
    card = _asset_card("storyboard", new_v, card_data)
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "storyboard", pill_id=pill, data=storyboard)
    await _continue_autopilot(session_id, ws)
    return {"status": "ok", "data": storyboard}


async def _handle_storyboard_approve(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    meta = session_svc.get_session(session_id)
    v = meta.approval_state.storyboard.iterations
    previous_v = meta.approval_state.storyboard.approved_version
    session_svc.symlink_approved(session_id, f"storyboard_v{v}.json", "storyboard_approved.json")
    storyboard = session_svc.load_json_asset(session_id, "storyboard_approved.json")

    # Initialize image and video approval slots
    image_count = storyboard["image_count"]
    video_count = storyboard["total_scenes"]
    meta.approval_state.storyboard.approved = True
    meta.approval_state.storyboard.approved_version = v
    if previous_v and previous_v != v:
        _invalidate_after_storyboard(meta)
    meta.approval_state.images = [
        ImageApproval(slot=f"img_{i:02d}") for i in range(1, image_count + 1)
    ]
    meta.approval_state.videos = [
        VideoApproval(clip_index=i) for i in range(1, video_count + 1)
    ]
    if "storyboard" not in meta.completed_stages:
        meta.completed_stages.append("storyboard")
    meta.current_stage = StageEnum.image_generation
    meta.current_substage = CurrentSubstage(type=SubstageType.image, index=1, iteration=1)
    session_svc.update_metadata(session_id, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})
    session_svc.mark_asset_card_approved(session_id, "storyboard", v)

    await _generate_image(session_id, image_index=1, ws=ws)
    return {"status": "ok"}


async def _handle_storyboard_restore(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    version = int(payload.get("version") or payload.get("iteration") or 1)
    if not session_svc.asset_exists(session_id, f"storyboard_v{version}.json"):
        return {"status": "error", "message": f"storyboard_v{version}.json not found"}
    meta = session_svc.get_session(session_id)
    session_svc.symlink_approved(session_id, f"storyboard_v{version}.json", "storyboard_approved.json")
    storyboard = session_svc.load_json_asset(session_id, "storyboard_approved.json")
    meta.approval_state.storyboard.approved = True
    meta.approval_state.storyboard.approved_version = version
    meta.approval_state.images = [
        ImageApproval(slot=f"img_{i:02d}") for i in range(1, storyboard["image_count"] + 1)
    ]
    meta.approval_state.videos = [
        VideoApproval(clip_index=i) for i in range(1, storyboard["total_scenes"] + 1)
    ]
    _clear_final(meta)
    _remove_completed_after(meta, "storyboard")
    if "storyboard" not in meta.completed_stages:
        meta.completed_stages.append("storyboard")
    meta.current_stage = StageEnum.image_generation
    meta.current_substage = CurrentSubstage(type=SubstageType.image, index=1, iteration=1)
    session_svc.update_metadata(session_id, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})
    session_svc.mark_asset_card_approved(session_id, "storyboard", version)
    await _generate_image(session_id, image_index=1, ws=ws)
    return {"status": "ok"}


# ── Image Generation ─────────────────────────────────────────────────────────

async def _generate_image(session_id: str, image_index: int, ws: ConnectionManager, feedback: Optional[str] = None) -> None:
    """Generate the single image for clip `image_index` (1-based).

    Single-image-per-clip architecture: each scene owns exactly one image
    (the start frame for Veo). No chain, no end-frame, no frame roles.
    """
    meta = session_svc.get_session(session_id)
    storyboard = session_svc.load_json_asset(session_id, "storyboard_approved.json")
    subject_name = meta.person_name
    photo_bytes, photo_mime = _get_photo_bytes(session_id)

    slot = f"img_{image_index:02d}"
    image_approval = next((a for a in meta.approval_state.images if a.slot == slot), None)
    if not image_approval:
        return

    current_v = image_approval.iterations
    new_v = current_v + 1
    # Regen path only when there is actual user feedback. Empty-string feedback
    # (from a no-arg "retry") must take the fresh path so we don't anchor on
    # the rejected pose — that biases every retry toward the same wrong frame.
    is_regen = bool(feedback and feedback.strip()) and current_v > 0
    total_images = len(storyboard["scenes"])
    scene = storyboard["scenes"][image_index - 1]

    pill = _pill_id()
    msg = f"Generating image {image_index} of {total_images}…"
    if is_regen:
        msg = f"Rewriting image prompt with your feedback… (Image {image_index})"
    session_svc.resolve_error_cards(session_id)
    session_svc.append_chat_message(session_id, _status_pill(pill, msg, "image_generation", image_index))
    await ws.send_status(session_id, msg, "image_generation", image_index, pill_id=pill)

    try:
        if is_regen:
            await ws.send_status(session_id, "Rewriting image prompt with your feedback…", "image_generation", image_index)
            rejected_bytes = _get_latest_image_bytes(session_id, slot, current_v)
            prev_prompt_path = session_svc.get_asset_path(session_id, f"images/{slot}_prompt_v{current_v}.txt")
            prev_prompt = prev_prompt_path.read_text() if prev_prompt_path.exists() else ""

            prompt = await openai_svc.write_image_prompt_regen(
                photo_bytes=photo_bytes,
                rejected_bytes=rejected_bytes,
                prev_prompt=prev_prompt,
                feedback=feedback,
                scene=scene,
                slot=slot,
                photo_media_type=photo_mime,
                session_id=session_id,
                version=new_v,
            )
            # Edit-base reference set: canonical photo + rejected image.
            ref_images = [photo_bytes, rejected_bytes]
            ref_mimes = [photo_mime, "image/png"]
        else:
            await ws.send_status(session_id, f"Writing image prompt for image {image_index}…", "image_generation", image_index)
            prompt = await openai_svc.write_image_prompt(
                photo_bytes=photo_bytes,
                scene=scene,
                person_name=subject_name,
                photo_media_type=photo_mime,
                session_id=session_id,
                asset_id=slot,
                version=new_v,
            )
            ref_images = [photo_bytes]
            ref_mimes = [photo_mime]

        await ws.send_status(session_id, f"Generating image {image_index} of {total_images}…", "image_generation", image_index)
        image_bytes = b""
        qa_summary: dict = {}
        qa_failed = False
        generation_prompt = prompt
        generation_refs = list(ref_images)
        generation_mimes = list(ref_mimes)

        # Hard cap: 1 QA-driven retry. After that surface to the user.
        for qa_attempt in range(2):
            image_bytes = await gemini_svc.generate_image(
                generation_prompt,
                generation_refs,
                generation_mimes,
                cost_context={
                    "session_id": session_id,
                    "stage": "image_generation",
                    "asset_type": "image",
                    "asset_id": slot,
                    "version": new_v,
                },
            )
            qa_summary = await gemini_svc.audit_generated_image(
                reference_photo=photo_bytes,
                generated_image=image_bytes,
                scene=scene,
                prompt=generation_prompt,
                reference_mime_type=photo_mime,
                cost_context={
                    "session_id": session_id,
                    "stage": "image_generation",
                    "asset_type": "image_qa",
                    "asset_id": slot,
                    "version": new_v,
                },
            )
            if not _image_qa_failed(qa_summary):
                prompt = generation_prompt
                break
            if qa_attempt == 0:
                qa_feedback = qa_summary.get("recommended_feedback") or "; ".join(qa_summary.get("issues") or [])
                generation_prompt = await openai_svc.write_image_prompt_qa_correction(
                    original_prompt=prompt,
                    qa_feedback=qa_feedback,
                    scene=scene,
                    session_id=session_id,
                    asset_id=slot,
                    version=new_v,
                )
            else:
                prompt = generation_prompt
                qa_failed = True

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
        "total_images": total_images,
        "qa_summary": qa_summary,
        "cost_summary": _asset_cost_for_card(session_id, "image", slot, new_v),
    })
    session_svc.resolve_status_pill(session_id, pill)
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "image_generation", pill_id=pill, data={
        "slot": slot, "version": new_v, "image_index": image_index
    })
    if qa_failed:
        message = (
            f"Image {image_index} was generated but failed identity/scene QA after an auto-retry. "
            "Review it manually or regenerate with feedback before approving."
        )
        session_svc.append_chat_message(session_id, _error_card(message, "image_generation", image_index))
        await ws.send_error(session_id, message, "image_generation", image_index)
        return
    await _continue_autopilot(session_id, ws)


async def _handle_image_change(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    image_index = payload.get("image_index", 1)
    feedback = payload.get("feedback", "")
    meta = session_svc.get_session(session_id)

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
    total_scenes = storyboard["total_scenes"]

    slot = f"img_{image_index:02d}"
    image_approval = next(a for a in meta.approval_state.images if a.slot == slot)
    v = image_approval.iterations
    previous_v = image_approval.approved_version
    session_svc.symlink_approved(session_id, f"images/{slot}_v{v}.png", f"images/{slot}_approved.png")
    image_approval.approved = True
    image_approval.approved_version = v
    clip_index = _clip_for_image_index(image_index)
    if previous_v and previous_v != v:
        _invalidate_clip(meta, clip_index)
        session_svc.mark_asset_cards_previous_from(
            session_id,
            {"video_prompt", "video"},
            {"clip_index": clip_index},
        )
    session_svc.update_metadata(session_id, approval_state=meta.approval_state)
    session_svc.mark_asset_card_approved(session_id, "image", v, {"slot": slot})

    # Single image per clip: as soon as image N is approved, write the video
    # prompt for clip N.
    if 1 <= clip_index <= total_scenes and not meta.approval_state.videos[clip_index - 1].approved:
        meta.current_stage = StageEnum.video_generation
        meta.current_substage = CurrentSubstage(type=SubstageType.video_prompt, index=clip_index, iteration=1)
        session_svc.update_metadata(session_id, current_stage=meta.current_stage, current_substage=meta.current_substage)
        await _generate_video_prompt(session_id, clip_index, ws)
        return {"status": "ok"}

    await _check_all_complete(session_id, ws)
    return {"status": "ok"}


async def _handle_image_restore(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    image_index = int(payload.get("image_index", 1))
    version = int(payload.get("version") or payload.get("iteration") or 1)
    slot = f"img_{image_index:02d}"
    if not session_svc.asset_exists(session_id, f"images/{slot}_v{version}.png"):
        return {"status": "error", "message": f"{slot}_v{version}.png not found"}
    meta = session_svc.get_session(session_id)
    image_approval = next(a for a in meta.approval_state.images if a.slot == slot)
    session_svc.symlink_approved(session_id, f"images/{slot}_v{version}.png", f"images/{slot}_approved.png")
    image_approval.approved = True
    image_approval.approved_version = version
    clip_index = _clip_for_image_index(image_index)
    _invalidate_clip(meta, clip_index)
    session_svc.mark_asset_cards_previous_from(
        session_id,
        {"video_prompt", "video"},
        {"clip_index": clip_index},
    )
    meta.current_stage = StageEnum.image_generation
    session_svc.update_metadata(session_id, approval_state=meta.approval_state, current_stage=meta.current_stage)
    session_svc.mark_asset_card_approved(session_id, "image", version, {"slot": slot})

    if (
        session_svc.asset_exists(session_id, f"images/{slot}_approved.png")
        and 1 <= clip_index <= len(meta.approval_state.videos)
    ):
        meta.current_stage = StageEnum.video_generation
        meta.current_substage = CurrentSubstage(type=SubstageType.video_prompt, index=clip_index, iteration=1)
        session_svc.update_metadata(session_id, current_stage=meta.current_stage, current_substage=meta.current_substage)
        await _generate_video_prompt(session_id, clip_index, ws)
    else:
        await _check_all_complete(session_id, ws)
    return {"status": "ok"}


# ── Video Generation ─────────────────────────────────────────────────────────

async def _generate_video_prompt(session_id: str, clip_index: int, ws: ConnectionManager) -> None:
    storyboard = session_svc.load_json_asset(session_id, "storyboard_approved.json")
    scene = storyboard["scenes"][clip_index - 1]
    slot = scene["image_slot"]

    start_bytes = _get_image_bytes(session_id, slot)

    pill = _pill_id()
    session_svc.resolve_error_cards(session_id)
    session_svc.append_chat_message(session_id, _status_pill(pill, f"Writing video prompt for clip {clip_index}…", "video_generation", clip_index))
    await ws.send_status(session_id, f"Writing video prompt for clip {clip_index}…", "video_generation", clip_index, pill_id=pill)

    meta = session_svc.get_session(session_id)
    video_approval = meta.approval_state.videos[clip_index - 1]
    version = video_approval.prompt_iterations + 1

    try:
        prompt = await openai_svc.write_video_prompt(
            start_bytes,
            scene,
            session_id=session_id,
            clip_index=clip_index,
            version=version,
        )
    except Exception as exc:
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(str(exc), "video_generation", clip_index))
        return

    video_approval.prompt_iterations = version
    video_approval.prompt_approved_version = None
    video_approval.iterations = 0
    video_approval.approved = False
    video_approval.approved_version = None
    meta.current_substage = CurrentSubstage(type=SubstageType.video_prompt, index=clip_index, iteration=version)

    session_svc.save_text_asset(session_id, f"video_prompts/clip_{clip_index:02d}_prompt_v{version}.txt", prompt)
    session_svc.update_metadata(session_id, approval_state=meta.approval_state, current_substage=meta.current_substage)

    card = _asset_card("video_prompt", version, {
        "clip_index": clip_index,
        "prompt": prompt,
        "start_image_path": _approved_image_asset_path(meta, slot),
        "duration_seconds": scene["duration_seconds"],
        "cost_summary": _asset_cost_for_card(session_id, "video_prompt", f"clip_{clip_index:02d}", version),
    })
    session_svc.resolve_status_pill(session_id, pill)
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "video_prompt", pill_id=pill, data={"clip_index": clip_index, "prompt": prompt})
    await _continue_autopilot(session_id, ws)


async def _handle_video_prompt_change(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    clip_index = payload.get("clip_index", 1)
    feedback = payload.get("feedback", "")
    meta = session_svc.get_session(session_id)
    storyboard = session_svc.load_json_asset(session_id, "storyboard_approved.json")
    scene = storyboard["scenes"][clip_index - 1]

    video_approval = meta.approval_state.videos[clip_index - 1]
    current_v = int(payload.get("version") or payload.get("iteration") or video_approval.prompt_approved_version or video_approval.prompt_iterations)
    slot = scene["image_slot"]
    start_bytes = _get_image_bytes(session_id, slot)

    prev_prompt_path = session_svc.get_asset_path(session_id, f"video_prompts/clip_{clip_index:02d}_prompt_v{current_v}.txt")
    prev_prompt = prev_prompt_path.read_text() if prev_prompt_path.exists() else ""

    pill = _pill_id()
    session_svc.resolve_error_cards(session_id)
    session_svc.append_chat_message(session_id, _status_pill(pill, f"Rewriting video prompt for clip {clip_index}…", "video_generation", clip_index))
    await ws.send_status(session_id, f"Rewriting video prompt for clip {clip_index}…", "video_generation", clip_index, pill_id=pill)

    new_v = video_approval.prompt_iterations + 1
    try:
        prompt = await openai_svc.rewrite_video_prompt(
            start_bytes,
            prev_prompt,
            feedback,
            scene,
            session_id=session_id,
            clip_index=clip_index,
            version=new_v,
        )
    except Exception as exc:
        session_svc.resolve_status_pill(session_id, pill)
        session_svc.append_chat_message(session_id, _error_card(str(exc), "video_generation", clip_index))
        return {"status": "error", "message": str(exc)}

    session_svc.save_text_asset(session_id, f"video_prompts/clip_{clip_index:02d}_prompt_v{new_v}.txt", prompt)
    video_approval.prompt_iterations = new_v
    meta.current_substage = CurrentSubstage(type=SubstageType.video_prompt, index=clip_index, iteration=new_v)
    session_svc.update_metadata(session_id, approval_state=meta.approval_state, current_substage=meta.current_substage)

    card = _asset_card("video_prompt", new_v, {
        "clip_index": clip_index,
        "prompt": prompt,
        "start_image_path": _approved_image_asset_path(meta, slot),
        "duration_seconds": scene["duration_seconds"],
        "cost_summary": _asset_cost_for_card(session_id, "video_prompt", f"clip_{clip_index:02d}", new_v),
    })
    session_svc.resolve_status_pill(session_id, pill)
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "video_prompt", pill_id=pill, data={"clip_index": clip_index, "prompt": prompt})
    await _continue_autopilot(session_id, ws)
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
    video_approval.prompt_approved_version = v
    video_approval.approved = False
    video_approval.approved_version = None
    video_approval.iterations = 0
    _clear_final(meta)
    session_svc.update_metadata(session_id, approval_state=meta.approval_state)
    session_svc.mark_asset_card_approved(session_id, "video_prompt", v, {"clip_index": clip_index})

    _fire_bg(_run_veo(session_id, clip_index, ws))
    return {"status": "ok"}


async def _handle_video_prompt_restore(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    clip_index = int(payload.get("clip_index", 1))
    version = int(payload.get("version") or payload.get("iteration") or 1)
    if not session_svc.asset_exists(session_id, f"video_prompts/clip_{clip_index:02d}_prompt_v{version}.txt"):
        return {"status": "error", "message": f"clip_{clip_index:02d}_prompt_v{version}.txt not found"}
    meta = session_svc.get_session(session_id)
    video_approval = meta.approval_state.videos[clip_index - 1]
    session_svc.symlink_approved(
        session_id,
        f"video_prompts/clip_{clip_index:02d}_prompt_v{version}.txt",
        f"video_prompts/clip_{clip_index:02d}_prompt_approved.txt",
    )
    video_approval.prompt_approved_version = version
    video_approval.iterations = 0
    video_approval.approved = False
    video_approval.approved_version = None
    _clear_final(meta)
    meta.current_stage = StageEnum.video_generation
    meta.current_substage = CurrentSubstage(type=SubstageType.video_prompt, index=clip_index, iteration=version)
    session_svc.update_metadata(session_id, approval_state=meta.approval_state, current_stage=meta.current_stage, current_substage=meta.current_substage)
    session_svc.mark_asset_card_approved(session_id, "video_prompt", version, {"clip_index": clip_index})
    _fire_bg(_run_veo(session_id, clip_index, ws))
    return {"status": "ok"}


async def _run_veo(session_id: str, clip_index: int, ws: ConnectionManager) -> None:
    meta = session_svc.get_session(session_id)
    storyboard = session_svc.load_json_asset(session_id, "storyboard_approved.json")
    scene = storyboard["scenes"][clip_index - 1]
    video_approval = meta.approval_state.videos[clip_index - 1]

    prompt_path = session_svc.get_asset_path(session_id, f"video_prompts/clip_{clip_index:02d}_prompt_approved.txt")
    prompt = prompt_path.read_text()
    start_bytes = _get_image_bytes(session_id, scene["image_slot"])
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
            duration_seconds=scene["duration_seconds"],
            model_variant=model,
            status_callback=status_cb,
            cost_context={
                "session_id": session_id,
                "stage": "video_generation",
                "asset_type": "video",
                "asset_id": f"clip_{clip_index:02d}",
                "version": new_v,
            },
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
    video_approval.approved = False
    video_approval.approved_version = None
    session_svc.update_metadata(session_id, approval_state=meta.approval_state)

    session_svc.resolve_status_pill(session_id, pill)
    card = _asset_card("video", new_v, {
        "clip_index": clip_index,
        "video_path": f"videos/clip_{clip_index:02d}_v{new_v}.mp4",
        "veo_model": model,
        "duration_seconds": scene["duration_seconds"],
        "cost_summary": _asset_cost_for_card(session_id, "video", f"clip_{clip_index:02d}", new_v),
    })
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "video_generation", pill_id=pill, data={
        "clip_index": clip_index, "version": new_v
    })
    await _continue_autopilot(session_id, ws)


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
    previous_v = video_approval.approved_version

    session_svc.symlink_approved(
        session_id,
        f"videos/clip_{clip_index:02d}_v{v}.mp4",
        f"videos/clip_{clip_index:02d}_approved.mp4",
    )
    video_approval.approved = True
    video_approval.approved_version = v
    if previous_v and previous_v != v:
        _clear_final(meta)
    session_svc.mark_asset_card_approved(session_id, "video", v, {"clip_index": clip_index})

    storyboard = session_svc.load_json_asset(session_id, "storyboard_approved.json")
    total_images = len(storyboard["scenes"])
    next_image_index = clip_index + 1

    session_svc.update_metadata(session_id, approval_state=meta.approval_state)

    if next_image_index <= total_images:
        meta.current_stage = StageEnum.image_generation
        meta.current_substage = CurrentSubstage(type=SubstageType.image, index=next_image_index, iteration=1)
        session_svc.update_metadata(session_id, current_stage=meta.current_stage, current_substage=meta.current_substage)
        await _generate_image(session_id, next_image_index, ws)
    else:
        await _check_all_complete(session_id, ws)

    return {"status": "ok"}


async def _handle_video_restore(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    clip_index = int(payload.get("clip_index", 1))
    version = int(payload.get("version") or payload.get("iteration") or 1)
    if not session_svc.asset_exists(session_id, f"videos/clip_{clip_index:02d}_v{version}.mp4"):
        return {"status": "error", "message": f"clip_{clip_index:02d}_v{version}.mp4 not found"}
    meta = session_svc.get_session(session_id)
    video_approval = meta.approval_state.videos[clip_index - 1]
    session_svc.symlink_approved(
        session_id,
        f"videos/clip_{clip_index:02d}_v{version}.mp4",
        f"videos/clip_{clip_index:02d}_approved.mp4",
    )
    video_approval.approved = True
    video_approval.approved_version = version
    _clear_final(meta)
    meta.current_stage = StageEnum.video_generation
    session_svc.update_metadata(session_id, approval_state=meta.approval_state, current_stage=meta.current_stage)
    session_svc.mark_asset_card_approved(session_id, "video", version, {"clip_index": clip_index})
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
        if meta.settings.autopilot_enabled:
            await _handle_assembly_start(session_id, {}, ws)
            return
        question = _question(
            "All images and videos approved! Ready to assemble the final reel?",
            widget={"type": "buttons", "options": ["Avengers Assemble"]},
        )
        session_svc.append_chat_message(session_id, question)
        await ws.send_status(session_id, "All assets approved. Ready for assembly.", "assembly")


# ── Assembly ─────────────────────────────────────────────────────────────────

async def _handle_assembly_start(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    session_svc.resolve_error_cards(session_id, "assembly")
    session_svc.resolve_status_pills(session_id, "assembly")
    meta = session_svc.get_session(session_id)
    if meta.assembly_locked:
        return {"status": "error", "message": "This session is already locked after Avengers Assemble."}
    meta.assembly_locked = True
    meta.current_stage = StageEnum.assembly
    session_svc.update_metadata(session_id, assembly_locked=True, current_stage=meta.current_stage)
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
        "cost_summary": cost_svc.get_ledger(session_id)["summary"],
    })
    session_svc.append_chat_message(session_id, card)
    await ws.send_asset_ready(session_id, "assembly", data={"version": new_version, "size_mb": size_mb})
    return {"status": "ok", "version": new_version}


# ── Redo Clip ────────────────────────────────────────────────────────────────

async def _handle_redo_clip(session_id: str, payload: dict, ws: ConnectionManager) -> dict:
    clip_index = payload.get("clip_index", 1)
    meta = session_svc.get_session(session_id)
    if meta.assembly_locked:
        return {"status": "error", "message": "This session is locked after Avengers Assemble."}

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
