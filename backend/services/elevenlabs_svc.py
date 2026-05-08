from __future__ import annotations

import asyncio

import httpx

from config import (
    get_elevenlabs_api_key,
    get_elevenlabs_tts_language_code,
    get_elevenlabs_tts_model,
    get_elevenlabs_tts_output_format,
    get_elevenlabs_tts_voice_settings,
    get_elevenlabs_voice_ids,
)
from services import cost_svc

_BASE = "https://api.elevenlabs.io/v1"

# Retry caps — explicit range(), no while True, no recursion
_TTS_MAX_RETRIES = 3
_ALIGNMENT_MAX_RETRIES = 2
_RATE_LIMIT_BACKOFF_SEC = 30  # multiply by (attempt + 1) for exponential
_NON_RETRYABLE_STATUS = {400, 401, 403, 404, 422}


def _headers() -> dict[str, str]:
    return {"xi-api-key": get_elevenlabs_api_key()}


def get_tts_runtime_config(gender: str, voice_id: str | None = None, speed: float | None = None) -> dict:
    voice_ids = get_elevenlabs_voice_ids()
    resolved_voice_id = voice_id or voice_ids.get(gender, voice_ids["female"])
    model_id = get_elevenlabs_tts_model()
    language_code = get_elevenlabs_tts_language_code()
    voice_settings = get_elevenlabs_tts_voice_settings()
    if speed is not None:
        voice_settings["speed"] = max(0.7, min(1.2, float(speed)))
    if model_id == "eleven_v3":
        voice_settings = {
            key: value
            for key, value in voice_settings.items()
            if key != "use_speaker_boost"
        }
    return {
        "voice_id": resolved_voice_id,
        "model_id": model_id,
        "language_code": language_code or None,
        "voice_settings": voice_settings,
        "output_format": get_elevenlabs_tts_output_format(),
    }


async def generate_voiceover(
    text: str,
    gender: str,
    voice_id: str | None = None,
    speed: float | None = None,
    cost_context: dict | None = None,
) -> bytes:
    """
    Call ElevenLabs TTS. Returns MP3 bytes.
    Retries up to 3× on 429 with 30/60/90s backoff. After 3 failures raises.
    Caller is responsible for injecting audio tags (see audio_tag_svc).
    """
    runtime = get_tts_runtime_config(gender, voice_id, speed)
    resolved_voice_id = runtime["voice_id"]
    model_id = runtime["model_id"]
    url = f"{_BASE}/text-to-speech/{resolved_voice_id}"
    payload = {
        "text": str(text or "").strip(),
        "model_id": model_id,
        "voice_settings": runtime["voice_settings"],
    }
    # Don't send language_code when the text contains an accent tag — ElevenLabs
    # uses language_code to lock the dialect, which overrides inline accent tags.
    has_accent_tag = "[strong indian english accent]" in text.lower()
    if runtime["language_code"] and not has_accent_tag:
        payload["language_code"] = runtime["language_code"]
    params = {"output_format": runtime["output_format"]}

    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=120) as client:
        for attempt in range(_TTS_MAX_RETRIES):
            try:
                resp = await client.post(url, json=payload, headers=_headers(), params=params)
                if resp.status_code == 429:
                    wait = _RATE_LIMIT_BACKOFF_SEC * (attempt + 1)
                    last_exc = RuntimeError(f"ElevenLabs rate-limited (attempt {attempt + 1})")
                    if attempt < _TTS_MAX_RETRIES - 1:
                        await asyncio.sleep(wait)
                        continue
                    break
                resp.raise_for_status()
                if cost_context and cost_context.get("session_id"):
                    cost_svc.log_elevenlabs_tts(
                        cost_context["session_id"],
                        model=model_id,
                        stage=cost_context.get("stage", "voiceover"),
                        asset_type=cost_context.get("asset_type", "voiceover"),
                        asset_id=cost_context.get("asset_id", "voiceover"),
                        version=cost_context.get("version"),
                        characters=len(payload["text"]),
                    )
                return resp.content
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:500]
                last_exc = RuntimeError(
                    f"ElevenLabs returned HTTP {exc.response.status_code}: {detail}"
                )
                if exc.response.status_code in _NON_RETRYABLE_STATUS:
                    break
                if attempt < _TTS_MAX_RETRIES - 1:
                    await asyncio.sleep(_RATE_LIMIT_BACKOFF_SEC)
                    continue
                break

    raise RuntimeError(
        f"ElevenLabs TTS failed after {_TTS_MAX_RETRIES} attempts: {last_exc}"
    )


async def forced_alignment(audio_bytes: bytes, text: str, cost_context: dict | None = None) -> dict:
    """
    Call ElevenLabs Forced Alignment. Returns word-level timestamp dict.
    Retries up to 2× on failure.
    """
    url = f"{_BASE}/forced-alignment"
    last_exc: Exception | None = None

    async with httpx.AsyncClient(timeout=120) as client:
        for attempt in range(_ALIGNMENT_MAX_RETRIES):
            try:
                resp = await client.post(
                    url,
                    headers=_headers(),
                    files={
                        "file": ("voiceover.mp3", audio_bytes, "audio/mpeg"),
                        "text": (None, text),
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                if cost_context and cost_context.get("session_id"):
                    words = data.get("words") or []
                    audio_seconds = max((float(word.get("end", 0) or 0) for word in words), default=0.0)
                    cost_svc.log_elevenlabs_alignment(
                        cost_context["session_id"],
                        model="forced-alignment",
                        stage=cost_context.get("stage", "alignment"),
                        asset_type=cost_context.get("asset_type", "alignment"),
                        asset_id=cost_context.get("asset_id", "alignment"),
                        version=cost_context.get("version"),
                        audio_seconds=audio_seconds,
                    )
                return data
            except Exception as exc:
                last_exc = exc
                if attempt < _ALIGNMENT_MAX_RETRIES - 1:
                    await asyncio.sleep(5)
                    continue
                break

    raise RuntimeError(
        f"ElevenLabs forced alignment failed after {_ALIGNMENT_MAX_RETRIES} attempts: {last_exc}"
    )
