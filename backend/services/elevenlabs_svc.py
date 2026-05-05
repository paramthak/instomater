from __future__ import annotations

import asyncio
import os
import re
import tempfile
from pathlib import Path

import httpx

from config import (
    get_elevenlabs_api_key,
    get_elevenlabs_audio_tempo,
    get_elevenlabs_tts_language_code,
    get_elevenlabs_tts_model,
    get_elevenlabs_tts_output_format,
    get_elevenlabs_tts_voice_settings,
    get_elevenlabs_voice_ids,
)

_BASE = "https://api.elevenlabs.io/v1"
_FFMPEG_FULL_BIN = Path("/opt/homebrew/opt/ffmpeg-full/bin")

# Retry caps — explicit range(), no while True, no recursion
_TTS_MAX_RETRIES = 3
_ALIGNMENT_MAX_RETRIES = 2
_RATE_LIMIT_BACKOFF_SEC = 30  # multiply by (attempt + 1) for exponential
_NON_RETRYABLE_STATUS = {400, 401, 403, 404, 422}
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")
_AUDIO_TAG_RE = re.compile(r"\[[^\]]+\]")
_START_TAG = "[strong Indian English accent] [fast] [confident]"


def _headers() -> dict[str, str]:
    return {"xi-api-key": get_elevenlabs_api_key()}


def _tool_path(env_name: str, binary: str) -> str:
    override = os.getenv(env_name)
    if override:
        return override
    full_binary = _FFMPEG_FULL_BIN / binary
    if full_binary.exists():
        return str(full_binary)
    return binary


def _atempo_chain(factor: float) -> str:
    parts: list[str] = []
    remaining = factor
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    parts.append(f"atempo={remaining:.4f}")
    return ",".join(parts)


async def _apply_audio_tempo(audio_bytes: bytes, factor: float) -> bytes:
    if factor <= 1.01:
        return audio_bytes

    ffmpeg = _tool_path("FFMPEG_BIN", "ffmpeg")
    with tempfile.TemporaryDirectory(prefix="instomater-tts-") as tmp:
        src = Path(tmp) / "voiceover_in.mp3"
        dst = Path(tmp) / "voiceover_out.mp3"
        src.write_bytes(audio_bytes)
        proc = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-y",
            "-i",
            str(src),
            "-filter:a",
            _atempo_chain(factor),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(dst),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                "Voiceover tempo adjustment failed: "
                f"{stderr.decode(errors='replace')[-1200:]}"
            )
        return dst.read_bytes()


def _direct_for_eleven_v3(text: str, model_id: str | None = None) -> str:
    """
    Add light v3 audio direction without changing the script words.
    Eleven v3 responds strongly to inline tags; alignment still uses the original text.
    """
    cleaned = "\n".join(line.strip() for line in text.strip().splitlines() if line.strip())
    model = model_id or get_elevenlabs_tts_model()
    if not cleaned or model != "eleven_v3" or _AUDIO_TAG_RE.search(cleaned):
        return cleaned

    sentences = [part.strip() for part in _SENTENCE_BOUNDARY_RE.split(cleaned) if part.strip()]
    if len(sentences) < 3:
        return f"{_START_TAG} {cleaned}"

    midpoint = max(1, len(sentences) // 2)
    directed: list[str] = []
    for idx, sentence in enumerate(sentences):
        if idx == 0:
            directed.append(f"{_START_TAG} {sentence}")
        elif idx == 1:
            directed.append(f"[fast] [engaged] {sentence}")
        elif idx == midpoint:
            directed.append(f"[quick pace] [focused] {sentence}")
        elif idx == len(sentences) - 1:
            directed.append(f"[warm] {sentence}")
        else:
            directed.append(sentence)
    return "\n".join(directed)


def get_tts_runtime_config(gender: str, voice_id: str | None = None) -> dict:
    voice_ids = get_elevenlabs_voice_ids()
    resolved_voice_id = voice_id or voice_ids[gender]
    model_id = get_elevenlabs_tts_model()
    language_code = get_elevenlabs_tts_language_code()
    voice_settings = get_elevenlabs_tts_voice_settings()
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
        "audio_tempo": get_elevenlabs_audio_tempo(),
    }


async def generate_voiceover(text: str, gender: str, voice_id: str | None = None) -> bytes:
    """
    Call ElevenLabs TTS. Returns MP3 bytes.
    Retries up to 3× on 429 with 30/60/90s backoff. After 3 failures raises.
    """
    runtime = get_tts_runtime_config(gender, voice_id)
    resolved_voice_id = runtime["voice_id"]
    model_id = runtime["model_id"]
    url = f"{_BASE}/text-to-speech/{resolved_voice_id}"
    payload = {
        "text": _direct_for_eleven_v3(text, model_id),
        "model_id": model_id,
        "voice_settings": runtime["voice_settings"],
    }
    if runtime["language_code"]:
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
                return await _apply_audio_tempo(resp.content, runtime["audio_tempo"])
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


async def forced_alignment(audio_bytes: bytes, text: str) -> dict:
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
                return resp.json()
            except Exception as exc:
                last_exc = exc
                if attempt < _ALIGNMENT_MAX_RETRIES - 1:
                    await asyncio.sleep(5)
                    continue
                break

    raise RuntimeError(
        f"ElevenLabs forced alignment failed after {_ALIGNMENT_MAX_RETRIES} attempts: {last_exc}"
    )
