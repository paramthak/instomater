"""OpenAI service — script, audio-tag, storyboard, image and video prompts.

All LLM calls go through Structured Outputs with explicit JSON Schemas where
applicable. Reasoning models (GPT-5.x) accept ``reasoning_effort``. Python in
this module does NOT validate or rewrite model output — that's the prompt's
job. Python only fills deterministic structural fields (scene_id, image
slot names, totals, start/end times derived from durations).
"""
from __future__ import annotations

import base64
import json
from typing import Awaitable, Callable, Optional

from openai import AsyncOpenAI

from config import (
    OPENAI_API_KEY,
    OPENAI_MODEL_SCRIPT,
    OPENAI_MODEL_STORYBOARD,
    OPENAI_MODEL_IMAGE_PROMPT,
    OPENAI_MODEL_VIDEO_PROMPT,
    SCRIPT_REASONING_EFFORT,
    STORYBOARD_REASONING_EFFORT,
    IMAGE_PROMPT_REASONING_EFFORT,
    VIDEO_PROMPT_REASONING_EFFORT,
)
from pipeline.prompts import (
    SCRIPT_WRITER_SYSTEM,
    SCRIPT_REWRITE_SYSTEM,
    SCRIPT_OUTPUT_SCHEMA,
    STORYBOARD_WRITER_SYSTEM,
    STORYBOARD_REWRITE_SYSTEM,
    STORYBOARD_OUTPUT_SCHEMA,
    IMAGE_PROMPT_SYSTEM,
    IMAGE_PROMPT_REGEN_SYSTEM,
    IMAGE_PROMPT_QA_CORRECTION_SYSTEM,
    VIDEO_PROMPT_SYSTEM,
    VIDEO_PROMPT_REGEN_SYSTEM,
)
from services import cost_svc

# Default model for any caller that doesn't pass one explicitly.
OPENAI_MODEL = OPENAI_MODEL_STORYBOARD

_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Veo 3.1 hard constraint — only 4, 6, 8 second clip durations.
VALID_DURATIONS = {4, 6, 8}
_DEFAULT_TRANSITION_SECONDS = 0.45


# ── helpers ──────────────────────────────────────────────────────────────────


def _b64_image(image_bytes: bytes, media_type: str = "image/png") -> dict:
    encoded = base64.standard_b64encode(image_bytes).decode()
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{media_type};base64,{encoded}"},
    }


def _alignment_duration(alignment: dict | None) -> float | None:
    if not isinstance(alignment, dict):
        return None
    words = alignment.get("words") or []
    if not words:
        return None
    return max(float(word.get("end") or 0) for word in words)


# ── core OpenAI wrappers ────────────────────────────────────────────────────


async def _chat_json(
    system: str,
    user_content,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    json_schema: dict | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    cost_context: Optional[dict] = None,
) -> dict:
    """Send a chat completion and return the parsed JSON payload.

    GPT-5 reasoning models accept ``reasoning_effort`` (none|low|medium|high|xhigh)
    and ignore ``temperature``. When ``json_schema`` is supplied, Structured
    Outputs are used (schema adherence is API-enforced); otherwise plain JSON
    object mode is requested.
    """
    if isinstance(user_content, str):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
    else:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
    resolved_model = model or OPENAI_MODEL
    kwargs: dict = {"model": resolved_model, "messages": messages}
    if json_schema is not None:
        kwargs["response_format"] = {"type": "json_schema", "json_schema": json_schema}
    else:
        kwargs["response_format"] = {"type": "json_object"}
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    elif temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_completion_tokens"] = max_tokens
    response = await _client.chat.completions.create(**kwargs)
    if cost_context and cost_context.get("session_id"):
        cost_svc.log_openai_chat(
            cost_context["session_id"],
            model=resolved_model,
            stage=cost_context.get("stage", "unknown"),
            asset_type=cost_context.get("asset_type", "unknown"),
            asset_id=cost_context.get("asset_id", "unknown"),
            version=cost_context.get("version"),
            usage=getattr(response, "usage", None),
        )
    return json.loads(response.choices[0].message.content)


async def _chat_text(
    system: str,
    user_content,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    temperature: float | None = None,
    cost_context: Optional[dict] = None,
) -> str:
    if isinstance(user_content, str):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
    else:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
    resolved_model = model or OPENAI_MODEL
    kwargs: dict = {"model": resolved_model, "messages": messages}
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    elif temperature is not None:
        kwargs["temperature"] = temperature
    response = await _client.chat.completions.create(**kwargs)
    if cost_context and cost_context.get("session_id"):
        cost_svc.log_openai_chat(
            cost_context["session_id"],
            model=resolved_model,
            stage=cost_context.get("stage", "unknown"),
            asset_type=cost_context.get("asset_type", "unknown"),
            asset_id=cost_context.get("asset_id", "unknown"),
            version=cost_context.get("version"),
            usage=getattr(response, "usage", None),
        )
    return response.choices[0].message.content.strip()


# ── Script ───────────────────────────────────────────────────────────────────


async def generate_script(
    script_prompt: str,
    *,
    session_id: Optional[str] = None,
    version: Optional[int] = None,
) -> dict:
    """Generate a clean spoken-text script.

    Returns ``{"full_text": str}`` — single field, no annotations, no audio
    tags, no markdown. Word count and duration are derivable downstream.
    """
    cost_context = (
        {"session_id": session_id, "stage": "script", "asset_type": "script",
         "asset_id": "script", "version": version}
        if session_id else None
    )
    user_text = f"PERSON / PROMPT\n{str(script_prompt or '').strip()}\n\nWrite the script now."
    payload = await _chat_json(
        SCRIPT_WRITER_SYSTEM,
        user_text,
        model=OPENAI_MODEL_SCRIPT,
        reasoning_effort=SCRIPT_REASONING_EFFORT,
        json_schema=SCRIPT_OUTPUT_SCHEMA,
        cost_context=cost_context,
    )
    full_text = str(payload.get("full_text") or "").strip()
    if not full_text:
        raise RuntimeError("Script writer returned empty full_text.")
    return {"full_text": full_text}


async def rewrite_script(
    current: dict,
    feedback: str,
    script_history: list[dict] | None = None,
    *,
    session_id: Optional[str] = None,
    version: Optional[int] = None,
) -> dict:
    history_block = (
        f"PRIOR ITERATIONS (oldest to newest)\n{json.dumps(script_history, indent=2)}\n\n"
        if script_history else ""
    )
    user_text = (
        f"{history_block}"
        f"CURRENT SCRIPT\n{json.dumps(current, indent=2)}\n\n"
        f"USER FEEDBACK\n\"{feedback}\""
    )
    cost_context = (
        {"session_id": session_id, "stage": "script", "asset_type": "script",
         "asset_id": "script", "version": version}
        if session_id else None
    )
    payload = await _chat_json(
        SCRIPT_REWRITE_SYSTEM,
        user_text,
        model=OPENAI_MODEL_SCRIPT,
        reasoning_effort=SCRIPT_REASONING_EFFORT,
        json_schema=SCRIPT_OUTPUT_SCHEMA,
        cost_context=cost_context,
    )
    full_text = str(payload.get("full_text") or "").strip()
    if not full_text:
        raise RuntimeError("Script rewrite returned empty full_text.")
    return {"full_text": full_text}


def normalize_manual_script(text: str) -> dict:
    """Inline-edit path: user pasted edited script text directly. Trust it."""
    return {"full_text": str(text or "").strip()}


# ── Storyboard ───────────────────────────────────────────────────────────────


def _storyboard_user_input(script: dict, alignment: dict) -> str:
    audio_duration = _alignment_duration(alignment)
    duration_str = f"{audio_duration:.2f}" if audio_duration is not None else "unknown"
    return (
        f"audio_duration_seconds: {duration_str}\n\n"
        f"script.full_text:\n{script.get('full_text', '')}\n\n"
        f"alignment.words:\n{json.dumps(alignment.get('words') or [], indent=2)}\n\n"
        "Return the storyboard JSON conforming to the supplied schema."
    )


def _normalize_storyboard(data: dict) -> dict:
    """Fill deterministic structural fields after the LLM returns.

    Python only sets things derivable from the data Python owns — scene
    indices, image slot names, totals, and start/end times computed from the
    LLM-chosen durations. It does NOT touch voiceover_text, durations,
    settings, or any creative content the model decided.
    """
    if not isinstance(data, dict):
        data = {}
    scenes = data.get("scenes") or []
    data["scenes"] = scenes
    data["total_scenes"] = len(scenes)
    data["total_clips"] = len(scenes)
    data["image_count"] = len(scenes) if scenes else 0
    data["total_images"] = data["image_count"]

    current_time = 0.0
    for idx, scene in enumerate(scenes):
        scene["scene_id"] = f"{idx + 1:02d}"
        scene["image_slot"] = f"img_{idx + 1:02d}"
        duration = float(scene.get("duration_seconds") or 0)
        scene["start_time"] = round(current_time, 2)
        current_time += duration
        scene["end_time"] = round(current_time, 2)

    data["total_duration_seconds"] = round(current_time, 2)
    return data


def _validate_storyboard(data: dict) -> tuple[bool, str]:
    """Minimal structural sanity. Schema enforces field-level rules; this
    catches only the few cases where the schema cannot."""
    scenes = data.get("scenes") or []
    if not scenes:
        return False, "No scenes in storyboard"
    for scene in scenes:
        if scene.get("duration_seconds") not in VALID_DURATIONS:
            return False, f"Scene {scene.get('scene_id')} duration {scene.get('duration_seconds')} not in {VALID_DURATIONS}"
        if not (scene.get("voiceover_text") or "").strip():
            return False, f"Scene {scene.get('scene_id')} has empty voiceover_text"
    return True, "OK"


async def generate_storyboard(
    script: dict,
    alignment: dict,
    on_status: Callable[[str], Awaitable[None]] | None = None,
    *,
    session_id: Optional[str] = None,
    version: Optional[int] = None,
) -> dict:
    """Generate the storyboard in a single GPT-5.4 high-reasoning call.

    Structured Outputs enforces field-level rules (durations 4/6/8, shot type
    enums, setting category enum). The prompt enforces semantic rules
    (timing math, word coverage, setting variety, within-clip frame
    similarity). No retry loop — if validation fails the user sees the error
    and can request a rewrite explicitly.
    """
    if on_status:
        await on_status("Generating storyboard…")
    cost_context = (
        {"session_id": session_id, "stage": "storyboard", "asset_type": "storyboard",
         "asset_id": "storyboard", "version": version}
        if session_id else None
    )
    payload = await _chat_json(
        STORYBOARD_WRITER_SYSTEM,
        _storyboard_user_input(script, alignment),
        model=OPENAI_MODEL_STORYBOARD,
        reasoning_effort=STORYBOARD_REASONING_EFFORT,
        json_schema=STORYBOARD_OUTPUT_SCHEMA,
        cost_context=cost_context,
    )
    data = _normalize_storyboard(payload)
    valid, err = _validate_storyboard(data)
    if not valid:
        data["validation_error"] = err
    return data


async def rewrite_storyboard(
    current: dict,
    feedback: str,
    alignment: dict,
    script: dict | None = None,
    on_status: Callable[[str], Awaitable[None]] | None = None,
    *,
    session_id: Optional[str] = None,
    version: Optional[int] = None,
) -> dict:
    if on_status:
        await on_status("Rewriting storyboard…")
    audio_duration = _alignment_duration(alignment)
    duration_str = f"{audio_duration:.2f}" if audio_duration is not None else "unknown"
    user_text = (
        (f"audio_duration_seconds: {duration_str}\n\n")
        + (f"script.full_text:\n{script.get('full_text', '')}\n\n" if isinstance(script, dict) else "")
        + (f"alignment.words:\n{json.dumps(alignment.get('words') or [], indent=2)}\n\n")
        + (f"CURRENT STORYBOARD\n{json.dumps(current, indent=2)}\n\n")
        + (f"USER FEEDBACK\n\"{feedback}\"\n\n")
        + ("Return the full revised storyboard JSON conforming to the schema.")
    )
    cost_context = (
        {"session_id": session_id, "stage": "storyboard", "asset_type": "storyboard",
         "asset_id": "storyboard", "version": version}
        if session_id else None
    )
    payload = await _chat_json(
        STORYBOARD_REWRITE_SYSTEM,
        user_text,
        model=OPENAI_MODEL_STORYBOARD,
        reasoning_effort=STORYBOARD_REASONING_EFFORT,
        json_schema=STORYBOARD_OUTPUT_SCHEMA,
        cost_context=cost_context,
    )
    data = _normalize_storyboard(payload)
    valid, err = _validate_storyboard(data)
    if not valid:
        data["validation_error"] = err
    return data


# ── Image prompts ────────────────────────────────────────────────────────────


async def write_image_prompt(
    photo_bytes: bytes,
    scene: dict,
    person_name: str,
    photo_media_type: str = "image/jpeg",
    *,
    session_id: Optional[str] = None,
    asset_id: str = "image",
    version: Optional[int] = None,
) -> str:
    """Single-image-per-clip prompt writer. Replaces write_image_prompt_1 and
    write_image_prompt_chain. No previous-frame chaining; identity comes only
    from the canonical photo + the storyboard's face_reference_mode."""
    cost_context = (
        {"session_id": session_id, "stage": "image_generation", "asset_type": "image_prompt",
         "asset_id": asset_id, "version": version}
        if session_id else None
    )
    content = [
        {
            "type": "text",
            "text": (
                f"canonical_reference_photo: (attached as Image 1)\n"
                f"storyboard_scene: {json.dumps(scene, indent=2)}\n"
                f"person_name: {person_name}\n"
            ),
        },
        _b64_image(photo_bytes, photo_media_type),
    ]
    return await _chat_text(
        IMAGE_PROMPT_SYSTEM,
        content,
        model=OPENAI_MODEL_IMAGE_PROMPT,
        reasoning_effort=IMAGE_PROMPT_REASONING_EFFORT,
        cost_context=cost_context,
    )


async def write_image_prompt_regen(
    photo_bytes: bytes,
    rejected_bytes: bytes,
    prev_prompt: str,
    feedback: str,
    scene: dict,
    slot: str,
    photo_media_type: str = "image/jpeg",
    *,
    session_id: Optional[str] = None,
    version: Optional[int] = None,
) -> str:
    cost_context = (
        {"session_id": session_id, "stage": "image_generation", "asset_type": "image_prompt",
         "asset_id": slot, "version": version}
        if session_id else None
    )
    content = [
        {
            "type": "text",
            "text": (
                f"canonical_reference_photo: (Image 1 attached)\n"
                f"rejected_iteration: (Image 2 attached)\n"
                f"previous_prompt: {prev_prompt}\n"
                f"user_feedback: \"{feedback}\"\n"
                f"image_slot: {slot}\n"
                f"storyboard_scene: {json.dumps(scene, indent=2)}\n"
            ),
        },
        _b64_image(photo_bytes, photo_media_type),
        _b64_image(rejected_bytes, "image/png"),
    ]
    return await _chat_text(
        IMAGE_PROMPT_REGEN_SYSTEM, content,
        model=OPENAI_MODEL_IMAGE_PROMPT,
        reasoning_effort=IMAGE_PROMPT_REASONING_EFFORT,
        cost_context=cost_context,
    )


async def write_image_prompt_qa_correction(
    original_prompt: str,
    qa_feedback: str,
    scene: dict,
    *,
    session_id: Optional[str] = None,
    asset_id: str = "image",
    version: Optional[int] = None,
) -> str:
    """Append a QA correction directive to an existing image prompt."""
    cost_context = (
        {"session_id": session_id, "stage": "image_generation", "asset_type": "image_prompt",
         "asset_id": asset_id, "version": version}
        if session_id else None
    )
    mode = scene.get("face_reference_mode", "match_age")
    target_age = scene.get("face_reference_target_age")
    camera_angle = (scene.get("image_description") or {}).get("camera_angle", "front-3/4")
    user_text = (
        f"ORIGINAL PROMPT:\n{original_prompt}\n\n"
        f"QA FEEDBACK:\n{qa_feedback}\n\n"
        f"face_reference_mode: {mode}\n"
        f"face_reference_target_age: {target_age}\n"
        f"camera_angle: {camera_angle}\n\n"
        "Return the full corrected prompt with the CORRECTION block appended."
    )
    return await _chat_text(
        IMAGE_PROMPT_QA_CORRECTION_SYSTEM, user_text,
        model=OPENAI_MODEL_IMAGE_PROMPT,
        reasoning_effort=IMAGE_PROMPT_REASONING_EFFORT,
        cost_context=cost_context,
    )


# ── Video prompts ────────────────────────────────────────────────────────────


async def write_video_prompt(
    start_frame_bytes: bytes,
    scene: dict,
    *,
    session_id: Optional[str] = None,
    clip_index: Optional[int] = None,
    version: Optional[int] = None,
) -> str:
    cost_context = (
        {"session_id": session_id, "stage": "video_generation", "asset_type": "video_prompt",
         "asset_id": f"clip_{clip_index:02d}" if clip_index else "video_prompt",
         "version": version}
        if session_id else None
    )
    content = [
        {
            "type": "text",
            "text": (
                f"start_frame: (Image 1 attached — the SOLE anchor frame for this clip)\n"
                f"storyboard_scene: {json.dumps(scene, indent=2)}\n"
                f"duration_seconds: {scene.get('duration_seconds')}"
            ),
        },
        _b64_image(start_frame_bytes, "image/png"),
    ]
    return await _chat_text(
        VIDEO_PROMPT_SYSTEM, content,
        model=OPENAI_MODEL_VIDEO_PROMPT,
        reasoning_effort=VIDEO_PROMPT_REASONING_EFFORT,
        cost_context=cost_context,
    )


async def rewrite_video_prompt(
    start_frame_bytes: bytes,
    prev_prompt: str,
    feedback: str,
    scene: dict,
    *,
    session_id: Optional[str] = None,
    clip_index: Optional[int] = None,
    version: Optional[int] = None,
) -> str:
    cost_context = (
        {"session_id": session_id, "stage": "video_generation", "asset_type": "video_prompt",
         "asset_id": f"clip_{clip_index:02d}" if clip_index else "video_prompt",
         "version": version}
        if session_id else None
    )
    content = [
        {
            "type": "text",
            "text": (
                f"start_frame: (Image 1 attached)\n"
                f"previous_prompt: {prev_prompt}\n"
                f"user_feedback: \"{feedback}\"\n"
                f"storyboard_scene: {json.dumps(scene, indent=2)}\n"
                f"duration_seconds: {scene.get('duration_seconds')}"
            ),
        },
        _b64_image(start_frame_bytes, "image/png"),
    ]
    return await _chat_text(
        VIDEO_PROMPT_REGEN_SYSTEM, content,
        model=OPENAI_MODEL_VIDEO_PROMPT,
        reasoning_effort=VIDEO_PROMPT_REASONING_EFFORT,
        cost_context=cost_context,
    )
