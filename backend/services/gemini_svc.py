from __future__ import annotations

import asyncio
import io
import os
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

_image_client = genai.Client(api_key=GEMINI_API_KEY)
_veo_client: genai.Client | None = None

_IMAGE_AUTO_RETRY = 2  # 1 auto-retry on transient errors (range stops after 2 attempts)
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


def _make_image_part(image_bytes: bytes, mime_type: str = "image/png") -> gtypes.Part:
    return gtypes.Part.from_bytes(data=image_bytes, mime_type=mime_type)


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


async def generate_image(
    prompt: str,
    reference_images: list[bytes],
    reference_mime_types: Optional[list[str]] = None,
) -> bytes:
    """
    Generate an image with Nano Banana Pro.
    reference_images: 1, 2, or 3 bytes objects (uploaded photo first, then chain image, then rejected)
    Returns PNG bytes.
    Auto-retries once on transient 5xx / network errors.
    """
    if reference_mime_types is None:
        reference_mime_types = ["image/jpeg"] + ["image/png"] * (len(reference_images) - 1)

    identity_lock = (
        "CRITICAL IDENTITY LOCK: The first attached image is the canonical face reference. "
        "Match that person's facial structure, eyes, nose, mouth, skin tone, hairline, and hair density. "
        "Do not invent a different face, a different hairline, extra hair, or a different age. "
        "If the scene is set earlier or later in life, age only subtly; identity from Image 1 wins over era styling."
    )

    parts = []
    for img_bytes, mime in zip(reference_images, reference_mime_types):
        parts.append(_make_image_part(img_bytes, mime))
    parts.append(gtypes.Part.from_text(text=f"{identity_lock}\n\n{prompt}"))

    last_exc: Exception | None = None
    for attempt in range(_IMAGE_AUTO_RETRY):
        try:
            response = await asyncio.to_thread(
                _image_client.models.generate_content,
                model=GEMINI_IMAGE_MODEL,
                contents=gtypes.Content(parts=parts),
                config=gtypes.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                    image_config=gtypes.ImageConfig(aspect_ratio="9:16"),
                ),
            )
            # Extract image bytes from response
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                    return part.inline_data.data
            raise RuntimeError("Gemini returned no image in response")
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
    end_frame_bytes: bytes,
    duration_seconds: int,
    model_variant: str = "fast",
    rejected_video_bytes: Optional[bytes] = None,
) -> str:
    """
    Submit a Veo 3.1 job. Returns the operation name for polling.
    model_variant: "fast" | "standard"
    """
    model = VEO_FAST_MODEL if model_variant == "fast" else VEO_STANDARD_MODEL

    start_image = gtypes.Image(image_bytes=start_frame_bytes, mime_type="image/png")
    end_image = gtypes.Image(image_bytes=end_frame_bytes, mime_type="image/png")

    config = gtypes.GenerateVideosConfig(
        aspect_ratio="9:16",
        duration_seconds=duration_seconds,
        generate_audio=False,
        last_frame=end_image,
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

                api_key = getattr(_image_client._api_client, "api_key", None)
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
    end_frame_bytes: bytes,
    duration_seconds: int,
    model_variant: str = "fast",
    status_callback=None,
) -> bytes:
    """
    Submit + poll until done. Hard cap: VEO_MAX_POLLS iterations.
    status_callback: async callable(message: str) for real-time updates.
    Raises VeoTimeoutError if cap exceeded.
    """
    operation_name = await submit_video_job(
        prompt, start_frame_bytes, end_frame_bytes, duration_seconds, model_variant
    )

    elapsed = 0
    for attempt in range(VEO_MAX_POLLS):  # hard cap — no while True
        await asyncio.sleep(VEO_POLL_INTERVAL_SEC)
        elapsed += VEO_POLL_INTERVAL_SEC

        if status_callback:
            await status_callback(f"Polling Veo job — {elapsed} seconds elapsed…")

        status, video_bytes = await poll_video_job(operation_name)
        if status == "SUCCEEDED":
            return video_bytes
        # RUNNING: continue loop

    raise VeoTimeoutError(
        f"Veo job timed out after {VEO_MAX_POLLS * VEO_POLL_INTERVAL_SEC} seconds ({VEO_MAX_POLLS} polls)"
    )
