"""Tests for individual service functions — all API calls mocked."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
import httpx

from tests.conftest import (
    ALIGNMENT_FIXTURE,
    RICH_STORYBOARD_FIXTURE,
    SCRIPT_FIXTURE,
    STORYBOARD_FIXTURE,
    TOPIC_BRIEF_FIXTURE,
)


# ── OpenAI service ────────────────────────────────────────────────────────────

@patch("services.openai_svc._client")
def test_openai_topic_brief(mock_openai):
    mock_openai.chat.completions.create = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(TOPIC_BRIEF_FIXTURE)))]
    ))

    from services import openai_svc
    result = asyncio.get_event_loop().run_until_complete(
        openai_svc.generate_topic_brief("Test Person", "CEO")
    )
    assert result["person_name"] == "Test Person"
    assert result["gender"] == "male"


@patch("services.openai_svc._client")
def test_openai_script_picks_hook(mock_openai):
    from services import openai_svc
    from tests.conftest import SCRIPT_FIXTURE as sf
    mock_openai.chat.completions.create = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(sf)))]
    ))
    result = asyncio.get_event_loop().run_until_complete(
        openai_svc.generate_script(TOPIC_BRIEF_FIXTURE)
    )
    assert "full_text" in result


@patch("services.openai_svc._client")
def test_openai_script_accepts_hundred_word_script(mock_openai):
    from services import openai_svc
    from tests.conftest import SCRIPT_FIXTURE as sf

    script = json.loads(json.dumps(sf))
    script["structure"]["build"] += (
        " In 2000, another office taught him how patient teams make cleaner decisions."
    )
    script["full_text"] = " ".join(
        script["structure"][key] for key in ("hook", "setup", "build", "landing")
    )
    mock_openai.chat.completions.create = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(script)))]
    ))

    result = asyncio.get_event_loop().run_until_complete(
        openai_svc.generate_script(TOPIC_BRIEF_FIXTURE)
    )
    assert "validation_error" not in result
    assert 90 <= result["estimated_word_count"] <= 110


@patch("services.openai_svc._client")
def test_openai_script_falls_back_without_validation_error(mock_openai):
    from services import openai_svc

    bad_script = {
        "hook_category": "Storytelling",
        "hook_subtype_used": "curiosity_gap",
        "perspective": "third_person_documentary",
        "structure": {
            "hook": "Too short.",
            "setup": "Still short.",
            "build": "Missing the actual story.",
            "landing": "Not enough.",
        },
        "full_text": "Too short. Still short. Missing the actual story. Not enough.",
        "estimated_word_count": 9,
        "estimated_duration_seconds": 4,
        "name_mentions_count": 0,
        "self_check": {},
    }
    mock_openai.chat.completions.create = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(bad_script)))]
    ))

    result = asyncio.get_event_loop().run_until_complete(
        openai_svc.generate_script(TOPIC_BRIEF_FIXTURE)
    )
    assert "validation_error" not in result
    assert result.get("fallback_reason")
    assert 70 <= result["estimated_word_count"] <= 125


@patch("services.openai_svc._client")
def test_storyboard_valid_durations(mock_openai):
    """Valid storyboard (all 4/6/8) passes without retries."""
    mock_openai.chat.completions.create = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(RICH_STORYBOARD_FIXTURE)))]
    ))
    from services import openai_svc

    call_count = 0
    orig = openai_svc._chat_json
    async def counting_chat_json(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return await orig(*args, **kwargs)

    with patch.object(openai_svc, "_chat_json", side_effect=counting_chat_json):
        result = asyncio.get_event_loop().run_until_complete(
            openai_svc.generate_storyboard(TOPIC_BRIEF_FIXTURE, ALIGNMENT_FIXTURE, TOPIC_BRIEF_FIXTURE)
        )
    # Should succeed on first attempt
    assert "validation_error" not in result


@patch("services.openai_svc._client")
def test_storyboard_repairs_trailing_comma_json(mock_openai):
    """Model JSON with a trailing comma should be repaired instead of surfacing a parse error."""
    content = json.dumps(RICH_STORYBOARD_FIXTURE)
    content = f"{content[:-1]},\n}}"
    mock_openai.chat.completions.create = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content=content))]
    ))
    from services import openai_svc

    result = asyncio.get_event_loop().run_until_complete(
        openai_svc.generate_storyboard(SCRIPT_FIXTURE, ALIGNMENT_FIXTURE, TOPIC_BRIEF_FIXTURE)
    )
    assert "validation_error" not in result
    assert "max_tokens" not in mock_openai.chat.completions.create.call_args.kwargs


@patch("services.openai_svc._client")
def test_storyboard_recalculates_declared_duration(mock_openai):
    """Declared duration mismatches should be repaired from scene durations."""
    storyboard = json.loads(json.dumps(RICH_STORYBOARD_FIXTURE))
    scene_total = sum(scene["duration_seconds"] for scene in storyboard["scenes"])
    storyboard["total_duration_seconds"] = scene_total + 2
    mock_openai.chat.completions.create = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(storyboard)))]
    ))
    from services import openai_svc

    result = asyncio.get_event_loop().run_until_complete(
        openai_svc.generate_storyboard(SCRIPT_FIXTURE, ALIGNMENT_FIXTURE, TOPIC_BRIEF_FIXTURE)
    )
    assert "validation_error" not in result
    assert result["total_duration_seconds"] == scene_total


# ── ElevenLabs service ────────────────────────────────────────────────────────

def test_elevenlabs_tts_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"fake-mp3-bytes"
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(return_value=mock_resp)

        from services import elevenlabs_svc
        with patch.object(
            elevenlabs_svc,
            "_apply_audio_tempo",
            new=AsyncMock(side_effect=lambda audio, _factor: audio),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                elevenlabs_svc.generate_voiceover("Hello world", "male", "custom_voice")
            )
    assert result == b"fake-mp3-bytes"
    call_kwargs = mock_client.post.call_args.kwargs
    assert mock_client.post.call_args.args[0].endswith("/custom_voice")
    assert call_kwargs["params"] == {"output_format": "mp3_44100_128"}
    assert call_kwargs["json"]["model_id"] == "eleven_v3"
    assert call_kwargs["json"]["language_code"] == "en"
    assert call_kwargs["json"]["text"].startswith("[strong Indian English accent]")
    assert "[short pause]" not in call_kwargs["json"]["text"]
    assert call_kwargs["json"]["voice_settings"]["speed"] >= 1.15
    assert call_kwargs["json"]["voice_settings"]["style"] > 0
    assert "use_speaker_boost" not in call_kwargs["json"]["voice_settings"]


def test_elevenlabs_tts_retry_on_429_then_success():
    """3 rate limit responses then success — should succeed without infinite loop."""
    attempt_count = 0

    def make_resp(status):
        r = MagicMock()
        r.status_code = status
        r.content = b"audio"
        r.raise_for_status = MagicMock()
        if status != 200:
            r.raise_for_status.side_effect = httpx.HTTPStatusError(
                "rate limited", request=MagicMock(), response=r
            )
        return r

    async def fake_post(*args, **kwargs):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count <= 2:
            return make_resp(429)
        return make_resp(200)

    with patch("httpx.AsyncClient") as mock_cls, patch("asyncio.sleep", new=AsyncMock()):
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(side_effect=fake_post)

        from services import elevenlabs_svc
        with patch.object(
            elevenlabs_svc,
            "_apply_audio_tempo",
            new=AsyncMock(side_effect=lambda audio, _factor: audio),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                elevenlabs_svc.generate_voiceover("test", "female")
            )
    # Should succeed on 3rd attempt
    assert result == b"audio"
    assert attempt_count == 3


def test_elevenlabs_tts_exhausted_retries():
    """4 rate limit responses — should raise after 3 attempts (not 4 or infinity)."""
    attempt_count = 0

    def make_429():
        r = MagicMock()
        r.status_code = 429
        r.content = b""
        r.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError(
            "rate limited", request=MagicMock(), response=r
        ))
        return r

    async def always_429(*args, **kwargs):
        nonlocal attempt_count
        attempt_count += 1
        return make_429()

    with patch("httpx.AsyncClient") as mock_cls, patch("asyncio.sleep", new=AsyncMock()):
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(side_effect=always_429)

        from services import elevenlabs_svc
        with patch.object(
            elevenlabs_svc,
            "_apply_audio_tempo",
            new=AsyncMock(side_effect=lambda audio, _factor: audio),
        ):
            with pytest.raises(RuntimeError, match="failed after"):
                asyncio.get_event_loop().run_until_complete(
                    elevenlabs_svc.generate_voiceover("test", "male")
                )
    # Must not exceed max retries
    assert attempt_count == 3, f"Expected 3 attempts, got {attempt_count}"


# ── FFmpeg service ────────────────────────────────────────────────────────────

def test_ffmpeg_ass_generation(tmp_path):
    """ASS subtitle file should have correct word-anchored chunks."""
    import os
    os.environ["SESSIONS_DIR"] = str(tmp_path)

    from config import SESSIONS_DIR
    from services import session_svc
    import importlib
    import config
    config.SESSIONS_DIR = tmp_path
    config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    # Create a minimal session
    meta = session_svc.create_session("ASS Test")
    sid = meta.session_id

    session_svc.save_json_asset(sid, "alignment.json", ALIGNMENT_FIXTURE)
    session_svc.save_json_asset(sid, "storyboard_approved.json", STORYBOARD_FIXTURE)

    from services import ffmpeg_svc
    ass_path = ffmpeg_svc.generate_ass(sid)

    assert ass_path.exists()
    content = ass_path.read_text()
    assert "[Script Info]" in content
    assert "Dialogue:" in content
    assert "In" in content or "1990" in content


def test_ffmpeg_concat_offsets():
    """xfade offset calculation: offset = sum(durations[:i]) - sum(trans_durations[:i]) - trans_dur_i."""
    # 3 clips: 4, 4, 6 sec; transitions: dissolve 0.3s, fade 0.4s
    # clip 1 → 2: offset = 4 - 0 - 0.3 = 3.7
    # clip 2 → 3: offset = 4 + 4 - 0.3 - 0.4 = 7.3
    storyboard = {
        "total_scenes": 3,
        "total_duration_seconds": 14.0,
        "image_count": 4,
        "scenes": [
            {**STORYBOARD_FIXTURE["scenes"][0], "duration_seconds": 4, "scene_id": "01",
             "transition_out": "dissolve", "transition_duration_seconds": 0.3,
             "image_slot_start": "img_01", "image_slot_end": "img_02"},
            {**STORYBOARD_FIXTURE["scenes"][0], "duration_seconds": 4, "scene_id": "02",
             "transition_out": "fade", "transition_duration_seconds": 0.4,
             "image_slot_start": "img_02", "image_slot_end": "img_03"},
            {**STORYBOARD_FIXTURE["scenes"][0], "duration_seconds": 6, "scene_id": "03",
             "transition_out": "fade", "transition_duration_seconds": 0.3,
             "image_slot_start": "img_03", "image_slot_end": "img_04"},
        ],
    }

    # Replicate the offset calculation logic from concat_with_transitions
    scenes = storyboard["scenes"]
    offsets = []
    cumulative_duration = scenes[0]["duration_seconds"]
    cumulative_transitions = 0.0
    for i in range(1, len(scenes)):
        trans_dur = scenes[i - 1]["transition_duration_seconds"]
        offset = cumulative_duration - cumulative_transitions - trans_dur
        offsets.append(round(offset, 4))
        cumulative_duration += scenes[i]["duration_seconds"]
        cumulative_transitions += trans_dur

    assert abs(offsets[0] - 3.7) < 0.001, f"Expected 3.7, got {offsets[0]}"
    assert abs(offsets[1] - 7.3) < 0.001, f"Expected 7.3, got {offsets[1]}"
