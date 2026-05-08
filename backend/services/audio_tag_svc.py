"""ElevenLabs v3 audio-tag injection stage.

Sits between script approval and TTS. Reads a clean spoken script, returns the
same words with sparse inline audio tags (``[confident]``, ``[softly]``,
``[sighs]``, etc.) inserted at narrative beats. The clean script continues to
flow to forced alignment and storyboard; only TTS receives the tagged version.
"""
from __future__ import annotations

from typing import Optional

from config import OPENAI_MODEL_AUDIO_TAGS, AUDIO_TAGS_REASONING_EFFORT
from pipeline.prompts import AUDIO_TAG_INJECTOR_SYSTEM, AUDIO_TAG_OUTPUT_SCHEMA
from services import openai_svc


async def inject_audio_tags(
    clean_script: str,
    *,
    session_id: Optional[str] = None,
    version: Optional[int] = None,
) -> str:
    """Return the clean script with ElevenLabs v3 audio tags woven in.

    Word content is preserved verbatim; tags are inserted at sentence/clause
    boundaries to give the TTS engine emotional and pacing variation.
    """
    text = str(clean_script or "").strip()
    if not text:
        return ""

    cost_context = (
        {
            "session_id": session_id,
            "stage": "audio_tags",
            "asset_type": "audio_tags",
            "asset_id": "audio_tags",
            "version": version,
        }
        if session_id
        else None
    )

    payload = await openai_svc._chat_json(
        AUDIO_TAG_INJECTOR_SYSTEM,
        f"CLEAN SCRIPT\n{text}\n\nReturn the tagged_script JSON.",
        model=OPENAI_MODEL_AUDIO_TAGS,
        reasoning_effort=AUDIO_TAGS_REASONING_EFFORT,
        json_schema=AUDIO_TAG_OUTPUT_SCHEMA,
        cost_context=cost_context,
    )

    tagged = str(payload.get("tagged_script") or "").strip()
    return tagged or text
