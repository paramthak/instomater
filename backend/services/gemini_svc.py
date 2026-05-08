from __future__ import annotations

import asyncio
import io
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types as gtypes
from google.oauth2.service_account import Credentials

from config import (
    BASE_DIR,
    GEMINI_API_KEY,
    GEMINI_IMAGE_MODEL,
    GEMINI_TEXT_MODEL,
    GCP_LOCATION,
    GCP_PROJECT_ID,
    GCP_SERVICE_ACCOUNT_PATH,
    VEO_STANDARD_MODEL,
    VEO_FAST_MODEL,
    VEO_RESOLUTION,
    VEO_SAMPLE_COUNT,
    VEO_POLL_INTERVAL_SEC,
    VEO_MAX_POLLS,
)
from services import cost_svc

_gemini_client: genai.Client | None = None  # Gemini API — used for text/QA only
_veo_client: genai.Client | None = None      # Vertex AI — used for Imagen image gen + Veo video gen

_IMAGE_AUTO_RETRY = 2  # 1 auto-retry on transient errors (range stops after 2 attempts)
_IMAGE_QA_FALLBACK = {
    "approved": False,
    "identity_match": False,
    "identity_score": 0.0,
    "scene_match": False,
    "setting_match": False,
    "era_consistent": False,
    "no_text_on_displays": False,
    "camera_angle_matches": False,
    "looks_photoreal_not_ai": False,
    "issues": ["audit_unavailable"],
    "recommended_feedback": "Automated review unavailable; please inspect manually.",
}
_FFMPEG_FULL_BIN = Path("/opt/homebrew/opt/ffmpeg-full/bin")
_MOTION_SAMPLE_FPS = 6
_MOTION_SAMPLE_WIDTH = 96
_MOTION_SAMPLE_HEIGHT = 170
_MOTION_DEPARTURE_MAE = 10.0
_FROZEN_PAIR_MAE = 1.5


class VeoTimeoutError(Exception):
    pass


class VeoContentPolicyError(Exception):
    pass


def _service_account_path() -> Path:
    path = Path(GCP_SERVICE_ACCOUNT_PATH).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def _get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client
    _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def _make_image_part(image_bytes: bytes, mime_type: str = "image/png") -> gtypes.Part:
    return gtypes.Part.from_bytes(data=image_bytes, mime_type=mime_type)


def _get_veo_client() -> genai.Client:
    global _veo_client
    if _veo_client is not None:
        return _veo_client

    if not GCP_PROJECT_ID:
        raise RuntimeError(
            "Missing GCP_PROJECT_ID for Vertex AI Veo. Add it to backend/.env."
        )

    kwargs = {
        "vertexai": True,
        "project": GCP_PROJECT_ID,
        "location": GCP_LOCATION,
        "http_options": gtypes.HttpOptions(api_version="v1"),
    }

    if GCP_SERVICE_ACCOUNT_PATH:
        key_path = _service_account_path()
        if not key_path.exists():
            raise RuntimeError(
                f"GCP_SERVICE_ACCOUNT_PATH does not exist: {key_path}"
            )
        kwargs["credentials"] = Credentials.from_service_account_file(
            str(key_path),
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )

    _veo_client = genai.Client(**kwargs)
    return _veo_client


def _tool_path(env_name: str, binary: str) -> str:
    override = os.getenv(env_name)
    if override:
        return override
    full_binary = _FFMPEG_FULL_BIN / binary
    if full_binary.exists():
        return str(full_binary)
    return binary


_FFMPEG = _tool_path("FFMPEG_BIN", "ffmpeg")


def _mean_absolute_error(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / max(len(a), 1)


async def _decode_motion_frames(video_bytes: bytes) -> list[bytes]:
    frame_size = _MOTION_SAMPLE_WIDTH * _MOTION_SAMPLE_HEIGHT
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(video_bytes)
            tmp_path = Path(tmp.name)

        proc = await asyncio.create_subprocess_exec(
            _FFMPEG,
            "-v", "error",
            "-i", str(tmp_path),
            "-vf",
            (
                f"fps={_MOTION_SAMPLE_FPS},"
                f"scale={_MOTION_SAMPLE_WIDTH}:{_MOTION_SAMPLE_HEIGHT}:force_original_aspect_ratio=decrease,"
                f"pad={_MOTION_SAMPLE_WIDTH}:{_MOTION_SAMPLE_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
                "format=gray"
            ),
            "-f", "rawvideo",
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await proc.communicate()
        if proc.returncode != 0:
            return []

        frames = [stdout[i:i + frame_size] for i in range(0, len(stdout), frame_size)]
        return [frame for frame in frames if len(frame) == frame_size]
    finally:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)


async def _motion_quality_score(video_bytes: bytes) -> float:
    """Lower is better: penalize held start/end frames and low frame-to-frame motion."""
    frames = await _decode_motion_frames(video_bytes)
    if len(frames) < 3:
        return 999.0

    start_hold = (len(frames) - 1) / _MOTION_SAMPLE_FPS
    first = frames[0]
    for idx, frame in enumerate(frames[1:], start=1):
        if _mean_absolute_error(first, frame) >= _MOTION_DEPARTURE_MAE:
            start_hold = idx / _MOTION_SAMPLE_FPS
            break

    end_hold = (len(frames) - 1) / _MOTION_SAMPLE_FPS
    last = frames[-1]
    for idx in range(len(frames) - 2, -1, -1):
        if _mean_absolute_error(last, frames[idx]) >= _MOTION_DEPARTURE_MAE:
            end_hold = (len(frames) - 1 - idx) / _MOTION_SAMPLE_FPS
            break

    frozen_pairs = 0
    for prev, cur in zip(frames, frames[1:]):
        if _mean_absolute_error(prev, cur) < _FROZEN_PAIR_MAE:
            frozen_pairs += 1
    frozen_ratio = frozen_pairs / max(len(frames) - 1, 1)

    return start_hold * 4.0 + end_hold * 2.0 + frozen_ratio * 3.0


async def _select_best_video_candidate(candidates: list[bytes]) -> bytes:
    if len(candidates) <= 1:
        return candidates[0]

    scored: list[tuple[float, int, bytes]] = []
    for idx, candidate in enumerate(candidates):
        try:
            score = await _motion_quality_score(candidate)
        except Exception:
            score = 999.0
        scored.append((score, idx, candidate))

    scored.sort(key=lambda item: (item[0], item[1]))
    return scored[0][2]


def _response_text(response) -> str:
    text = getattr(response, "text", None)
    if text:
        return text.strip()
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                return part_text.strip()
    return ""


def _parse_json_response(text: str) -> dict:
    if not text:
        return {}
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


async def audit_generated_image(
    *,
    reference_photo: bytes,
    generated_image: bytes,
    scene: dict,
    prompt: str,
    previous_image: Optional[bytes] = None,
    reference_mime_type: str = "image/jpeg",
    cost_context: Optional[dict] = None,
) -> dict:
    """
    Vision QA for generated frames. Returns DENY fallback if the audit fails —
    a missing audit must surface to the user, not auto-pass. Identity is only
    checked when the scene's face_reference_mode == "match_age"; for
    age-regressed or face-skipped scenes, identity_match is forced True so it
    doesn't gate approval on a check that doesn't apply.
    """
    face_mode = scene.get("face_reference_mode", "match_age")
    target_age = scene.get("face_reference_target_age")
    check_identity = face_mode == "match_age"

    parts = [
        _make_image_part(reference_photo, reference_mime_type),
        _make_image_part(generated_image, "image/png"),
    ]
    if previous_image:
        parts.append(_make_image_part(previous_image, "image/png"))

    identity_clause = (
        "identity_match true ONLY if the face in Image 2 is unmistakably the same person as Image 1 — "
        "matching at minimum: glasses present/absent, beard present/absent, hairline (full/receding/bald), "
        "moustache, distinctive eye shape. If ANY of those flip vs Image 1, identity_match is false."
        if check_identity
        else f"This scene uses face_reference_mode='{face_mode}' (target age {target_age}). "
             "Do NOT compare facial age with Image 1 — the subject is intentionally rendered at a different age. "
             "Identity check is skipped; return identity_match=true."
    )

    parts.append(gtypes.Part.from_text(text=(
        "Audit this generated still frame for an Instagram reel. Image 1 is the canonical "
        "reference photo. Image 2 is the generated frame. Image 3, if present, is the previous approved frame.\n\n"
        f"STORYBOARD SCENE JSON:\n{json.dumps(scene, indent=2)}\n\n"
        f"IMAGE PROMPT (truncated):\n{prompt[:3000]}\n\n"
        f"IDENTITY POLICY: {identity_clause}\n\n"
        "Return strict JSON ONLY with these fields:\n"
        "  approved (bool) — true ONLY if every other field is true\n"
        "  identity_match (bool)\n"
        "  identity_score (number 0..1)\n"
        "  scene_match (bool) — does the scene depict the storyboard's setting_category and location_anchor?\n"
        "  setting_match (bool) — same as scene_match in this single-image flow; keep for compat\n"
        "  era_consistent (bool) — false if any anachronisms vs era_year and era_constraints (modern phones in 1980s, LED billboards in 1990s, etc.)\n"
        "  no_text_on_displays (bool) — false if any readable text appears on a screen, monitor, sign, billboard, poster, or display surface\n"
        "  camera_angle_matches (bool) — false if the framing is not the storyboard's image_description.camera_angle\n"
        "  looks_photoreal_not_ai (bool) — false if the image has glossy AI skin, plastic textures, oversaturation, perfect symmetry, illustrative rendering, or other visible AI artifacts\n"
        "  issues (array of short strings)\n"
        "  recommended_feedback (string — directive sentences the regen prompt can apply)"
    )))

    try:
        response = await asyncio.to_thread(
            _get_gemini_client().models.generate_content,
            model=GEMINI_TEXT_MODEL,
            contents=gtypes.Content(parts=parts),
            config=gtypes.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
            ),
        )
        if cost_context and cost_context.get("session_id"):
            cost_svc.log_gemini_text(
                cost_context["session_id"],
                model=GEMINI_TEXT_MODEL,
                stage=cost_context.get("stage", "image_generation"),
                asset_type=cost_context.get("asset_type", "image_qa"),
                asset_id=cost_context.get("asset_id", "image"),
                version=cost_context.get("version"),
                usage_metadata=getattr(response, "usage_metadata", None),
            )
        data = {**_IMAGE_QA_FALLBACK, **_parse_json_response(_response_text(response))}
        if not check_identity:
            data["identity_match"] = True
            data["identity_score"] = 1.0
        else:
            data["identity_match"] = bool(data.get("identity_match"))
            data["identity_score"] = float(data.get("identity_score") or 0)
        for key in ("scene_match", "setting_match", "era_consistent",
                    "no_text_on_displays", "camera_angle_matches",
                    "looks_photoreal_not_ai"):
            data[key] = bool(data.get(key))
        data["approved"] = (
            data["identity_match"] and data["scene_match"] and data["era_consistent"]
            and data["no_text_on_displays"] and data["camera_angle_matches"]
            and data["looks_photoreal_not_ai"]
        )
        if not isinstance(data.get("issues"), list):
            data["issues"] = [str(data.get("issues"))]
        return data
    except Exception as exc:
        return {**_IMAGE_QA_FALLBACK, "audit_error": str(exc)}


async def generate_image(
    prompt: str,
    reference_images: list[bytes],
    reference_mime_types: Optional[list[str]] = None,
    cost_context: Optional[dict] = None,
) -> bytes:
    """
    Generate an identity-consistent image via Vertex AI gemini-2.5-flash-image.
    reference_images[0] is the uploaded reference photo (identity anchor).
    Additional reference images are passed only on regen attempts (rejected
    image as the "edit base"). No previous-frame chaining for new images —
    that caused cascade drift.
    Returns PNG/JPEG bytes. Auto-retries once on transient errors.

    The prompt itself carries the face_reference_mode directive (match_age /
    age_down_to / skip_face_ref) per IMAGE_PROMPT_SYSTEM. We do NOT prepend a
    hardcoded identity lock here — that previously contradicted age-regressed
    scenes by demanding the current-age face.
    """
    if reference_mime_types is None:
        reference_mime_types = ["image/jpeg"] + ["image/png"] * max(0, len(reference_images) - 1)

    parts = []
    for img_bytes, mime in zip(reference_images, reference_mime_types):
        parts.append(_make_image_part(img_bytes, mime))
    parts.append(gtypes.Part.from_text(text=prompt))

    last_exc: Exception | None = None
    for attempt in range(_IMAGE_AUTO_RETRY):
        try:
            response = await asyncio.to_thread(
                _get_veo_client().models.generate_content,
                model=GEMINI_IMAGE_MODEL,
                contents=[gtypes.Content(role="user", parts=parts)],
                config=gtypes.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                    image_config=gtypes.ImageConfig(
                        aspect_ratio="9:16",
                        person_generation="ALLOW_ADULT",
                    ),
                ),
            )
            if not response.candidates:
                pr = getattr(response, "prompt_feedback", None)
                raise RuntimeError(f"No candidates returned. prompt_feedback={pr}")
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                    if cost_context and cost_context.get("session_id"):
                        cost_svc.log_gemini_image(
                            cost_context["session_id"],
                            model=GEMINI_IMAGE_MODEL,
                            stage=cost_context.get("stage", "image_generation"),
                            asset_type=cost_context.get("asset_type", "image"),
                            asset_id=cost_context.get("asset_id", "image"),
                            version=cost_context.get("version"),
                            input_images=len(reference_images),
                            usage_metadata=getattr(response, "usage_metadata", None),
                        )
                    return part.inline_data.data
            raise RuntimeError("Model returned no image part in response")
        except Exception as exc:
            last_exc = exc
            err_str = str(exc).lower()
            if "content" in err_str and "policy" in err_str:
                raise RuntimeError(f"Image generation blocked by content policy: {exc}")
            if attempt < _IMAGE_AUTO_RETRY - 1:
                await asyncio.sleep(3)
                continue
            break

    raise RuntimeError(f"Image generation failed after {_IMAGE_AUTO_RETRY} attempts: {last_exc}")


async def submit_video_job(
    prompt: str,
    start_frame_bytes: bytes,
    duration_seconds: int,
    model_variant: str = "fast",
    rejected_video_bytes: Optional[bytes] = None,
) -> str:
    """
    Submit a Veo 3.1 job. Returns the operation name for polling.
    model_variant: "fast" | "standard"

    Single-frame mode: only a start image is supplied. Veo drives the motion
    from the prompt text alone — no last_frame anchor. This eliminates the
    teleport and identity-morph artifacts caused by mismatched start/end
    frames.
    """
    model = VEO_FAST_MODEL if model_variant == "fast" else VEO_STANDARD_MODEL

    start_image = gtypes.Image(image_bytes=start_frame_bytes, mime_type="image/png")

    config = gtypes.GenerateVideosConfig(
        aspect_ratio="9:16",
        duration_seconds=duration_seconds,
        generate_audio=False,
        number_of_videos=VEO_SAMPLE_COUNT,
        person_generation="allow_adult",
        resolution=VEO_RESOLUTION,
    )

    client = _get_veo_client()
    operation = await asyncio.to_thread(
        client.models.generate_videos,
        model=model,
        prompt=prompt,
        image=start_image,
        config=config,
    )
    return operation.name


async def poll_video_job(operation_name: str) -> tuple[str, Optional[bytes]]:
    """
    Poll a single time. Returns (status, video_bytes_or_None).
    status: "RUNNING" | "SUCCEEDED" | "FAILED"
    Caller is responsible for the polling loop with explicit iteration cap.
    """
    stub = gtypes.GenerateVideosOperation(name=operation_name)
    client = _get_veo_client()
    operation = await asyncio.to_thread(client.operations.get, stub)

    if not operation.done:
        return "RUNNING", None

    if operation.error:
        error_msg = getattr(operation.error, "message", str(operation.error))
        if "content" in error_msg.lower() and "policy" in error_msg.lower():
            raise VeoContentPolicyError(error_msg)
        raise RuntimeError(f"Veo job failed: {error_msg}")

    # Extract videos — API may return URI or inline bytes.
    result = operation.result
    if result and hasattr(result, "generated_videos") and result.generated_videos:
        candidates: list[bytes] = []
        for generated_video in result.generated_videos:
            if not hasattr(generated_video, "video") or not generated_video.video:
                continue
            v = generated_video.video
            if v.video_bytes:
                candidates.append(v.video_bytes)
                continue
            if v.uri:
                if v.uri.startswith("gs://"):
                    raise RuntimeError(
                        "Veo returned a Cloud Storage URI. Remove output_gcs_uri "
                        "or add GCS download support before using storage-backed outputs."
                    )
                import httpx

                api_key = getattr(_get_gemini_client()._api_client, "api_key", None)
                headers = {"X-Goog-Api-Key": api_key} if api_key else {}
                async with httpx.AsyncClient(timeout=120, follow_redirects=True) as http:
                    resp = await http.get(v.uri, headers=headers)
                    resp.raise_for_status()
                    candidates.append(resp.content)
        if candidates:
            return "SUCCEEDED", await _select_best_video_candidate(candidates)
    raise RuntimeError("Veo job succeeded but returned no video URI or bytes")


async def run_video_job(
    prompt: str,
    start_frame_bytes: bytes,
    duration_seconds: int,
    model_variant: str = "fast",
    status_callback=None,
    cost_context: Optional[dict] = None,
) -> bytes:
    """
    Submit + poll until done. Hard cap: VEO_MAX_POLLS iterations.
    status_callback: async callable(message: str) for real-time updates.
    Raises VeoTimeoutError if cap exceeded.
    """
    operation_name = await submit_video_job(
        prompt, start_frame_bytes, duration_seconds, model_variant
    )

    elapsed = 0
    for attempt in range(VEO_MAX_POLLS):  # hard cap — no while True
        await asyncio.sleep(VEO_POLL_INTERVAL_SEC)
        elapsed += VEO_POLL_INTERVAL_SEC

        if status_callback:
            await status_callback(f"Polling Veo job — {elapsed} seconds elapsed…")

        status, video_bytes = await poll_video_job(operation_name)
        if status == "SUCCEEDED":
            if cost_context and cost_context.get("session_id"):
                cost_svc.log_veo(
                    cost_context["session_id"],
                    model_variant=model_variant,
                    stage=cost_context.get("stage", "video_generation"),
                    asset_type=cost_context.get("asset_type", "video"),
                    asset_id=cost_context.get("asset_id", "video"),
                    version=cost_context.get("version"),
                    duration_seconds=duration_seconds,
                    sample_count=VEO_SAMPLE_COUNT,
                )
            return video_bytes
        # RUNNING: continue loop

    raise VeoTimeoutError(
        f"Veo job timed out after {VEO_MAX_POLLS * VEO_POLL_INTERVAL_SEC} seconds ({VEO_MAX_POLLS} polls)"
    )
