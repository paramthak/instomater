"""Tests for individual service functions — all API calls mocked."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
import httpx

from tests.conftest import (
    ALIGNMENT_FIXTURE,
    RICH_ALIGNMENT_FIXTURE,
    RICH_STORYBOARD_FIXTURE,
    SCRIPT_FIXTURE,
    SCRIPT_PROMPT_FIXTURE,
    SCRIPT_RESPONSE_TEXT,
    STORYBOARD_FIXTURE,
)


# ── OpenAI service ────────────────────────────────────────────────────────────

@patch("services.openai_svc._client")
def test_openai_script_picks_hook(mock_openai):
    from services import openai_svc
    mock_openai.chat.completions.create = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content=SCRIPT_RESPONSE_TEXT))]
    ))
    result = asyncio.get_event_loop().run_until_complete(
        openai_svc.generate_script(SCRIPT_PROMPT_FIXTURE)
    )
    user_message = mock_openai.chat.completions.create.call_args.kwargs["messages"][-1]["content"]
    assert "SCRIPT INPUT" in user_message
    assert SCRIPT_PROMPT_FIXTURE in user_message
    assert result["full_text"] == SCRIPT_FIXTURE["full_text"]
    assert result["display_text"] == SCRIPT_FIXTURE["full_text"]
    assert "structure" not in result
    assert "self_check" not in result


@patch("services.openai_svc._client")
def test_openai_script_keeps_plain_script_contract(mock_openai):
    from services import openai_svc

    script_text = (
        "There's a kid from Mumbai with one borrowed suitcase.\n"
        "His parents counted rent before blessing the flight.\n"
        "He landed abroad with eighty dollars and one phone number.\n"
        "But here's where it gets crazy.\n"
        "The lab didn't just teach code. It taught him how serious rooms argue.\n"
        "Test Person carried that lesson into every room after.\n"
        "That suitcase still closes the first loop."
    )
    mock_openai.chat.completions.create = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content=f"{script_text}\n\n**Word count: 67 words / ~40s**"))]
    ))

    result = asyncio.get_event_loop().run_until_complete(
        openai_svc.generate_script(SCRIPT_PROMPT_FIXTURE)
    )
    assert "validation_error" not in result
    assert result["estimated_word_count"] == 67
    assert result["estimated_duration_seconds"] == 40
    assert result["full_text"] == " ".join(script_text.split())


@patch("services.openai_svc._client")
def test_openai_script_does_not_retry_or_fallback(mock_openai):
    from services import openai_svc

    bad_script = "Too short.\nStill short.\nMissing the actual story.\nNot enough.\n\n**Word count: 9 words / ~40s**"
    mock_openai.chat.completions.create = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content=bad_script))]
    ))

    result = asyncio.get_event_loop().run_until_complete(
        openai_svc.generate_script(SCRIPT_PROMPT_FIXTURE)
    )
    assert "validation_error" not in result
    assert "fallback_reason" not in result
    assert result["estimated_word_count"] == 9
    assert mock_openai.chat.completions.create.call_count == 1


@patch("services.openai_svc._client")
def test_openai_script_quota_error_does_not_fallback(mock_openai):
    from services import openai_svc

    mock_openai.chat.completions.create = AsyncMock(
        side_effect=RuntimeError("insufficient_quota")
    )

    with pytest.raises(RuntimeError, match="insufficient_quota"):
        asyncio.get_event_loop().run_until_complete(
            openai_svc.generate_script(SCRIPT_PROMPT_FIXTURE)
        )

    assert mock_openai.chat.completions.create.call_count == 1


@patch("services.openai_svc._client")
def test_openai_script_rejects_missing_name_response(mock_openai):
    from services import openai_svc

    mock_openai.chat.completions.create = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(
            content="Please provide a name for the script. I need a person's name to generate the Instagram Reels script."
        ))]
    ))

    with pytest.raises(RuntimeError, match="already provided"):
        asyncio.get_event_loop().run_until_complete(
            openai_svc.generate_script(SCRIPT_PROMPT_FIXTURE)
        )

    assert mock_openai.chat.completions.create.call_count == 1


def test_script_system_prompt_carries_quality_guidance():
    from services import openai_svc

    prompt = openai_svc._script_system_for(SCRIPT_PROMPT_FIXTURE)
    assert "Leap Scholar" in prompt
    assert SCRIPT_PROMPT_FIXTURE in prompt
    assert "Section 1" in prompt
    assert "The clean script" in prompt
    assert "STEP 10" in prompt


def test_manual_script_normalization_counts_spoken_words_only():
    from services import openai_svc

    result = openai_svc.normalize_manual_script(
        "First line lands here.\n"
        "Second line follows.\n\n"
        "**Word count: 999 words / ~80s**"
    )

    assert result["full_text"] == "First line lands here. Second line follows."
    assert result["estimated_word_count"] == 7
    assert result["estimated_duration_seconds"] == 3
    assert result["display_text"] == "First line lands here.\nSecond line follows."


def test_script_normalization_strips_leaked_beat_breakdown():
    from services import openai_svc

    result = openai_svc.normalize_manual_script(
        "This is the actual script.\n"
        "It should be the only spoken text.\n\n"
        "---\n\n"
        "Beat breakdown:\n"
        "| Beat | Lines |\n"
        "|---|---|"
    )

    assert result["full_text"] == "This is the actual script. It should be the only spoken text."
    assert "Beat breakdown" not in result["display_text"]


@patch("services.openai_svc._client")
def test_rewrite_script_receives_script_history(mock_openai):
    from services import openai_svc
    from tests.conftest import SCRIPT_FIXTURE as sf

    mock_openai.chat.completions.create = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content=SCRIPT_RESPONSE_TEXT))]
    ))

    script_history = [
        {"version": 1, "status": "previous", "script": sf},
        {
            "version": 2,
            "status": "pending_approval",
            "rewrite_context": {"from_version": 1, "feedback": "less generic"},
            "script": sf,
        },
    ]

    result = asyncio.get_event_loop().run_until_complete(
        openai_svc.rewrite_script(sf, "make the career unlock clearer", script_history)
    )

    user_message = mock_openai.chat.completions.create.call_args.kwargs["messages"][-1]["content"]
    assert "SCRIPT ITERATION CHAIN" in user_message
    assert "less generic" in user_message
    assert "Test Person" in user_message
    assert result["full_text"] == SCRIPT_FIXTURE["full_text"]
    assert "Mumbai" in user_message
    assert "validation_error" not in result


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
            openai_svc.generate_storyboard(SCRIPT_FIXTURE, RICH_ALIGNMENT_FIXTURE)
        )
    # Should succeed on first attempt
    assert "validation_error" not in result
    assert result["scenes"][0]["subject_life_stage"]
    assert result["scenes"][0]["age_continuity_note"]


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
        openai_svc.generate_storyboard(SCRIPT_FIXTURE, RICH_ALIGNMENT_FIXTURE)
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
        openai_svc.generate_storyboard(SCRIPT_FIXTURE, RICH_ALIGNMENT_FIXTURE)
    )
    assert "validation_error" not in result
    assert result["total_duration_seconds"] == scene_total


@patch("services.openai_svc._client")
def test_storyboard_derives_empty_voiceover_scene_from_alignment(mock_openai):
    """voiceover_text is deterministic alignment data, not fragile model copy."""
    storyboard = json.loads(json.dumps(RICH_STORYBOARD_FIXTURE))
    storyboard["scenes"][-1]["voiceover_text"] = ""
    storyboard["scenes"][-1]["voiceover_words"] = ""
    mock_openai.chat.completions.create = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(storyboard)))]
    ))
    from services import openai_svc

    result = asyncio.get_event_loop().run_until_complete(
        openai_svc.generate_storyboard(SCRIPT_FIXTURE, RICH_ALIGNMENT_FIXTURE)
    )
    assert "validation_error" not in result
    assert result["scenes"][-1]["voiceover_text"]
    assert " ".join(scene["voiceover_text"] for scene in result["scenes"]) == " ".join(
        word["text"] for word in RICH_ALIGNMENT_FIXTURE["words"]
    )


# ── Gemini image QA ──────────────────────────────────────────────────────────


def test_image_qa_fallback_denies_on_audit_error():
    """When Gemini audit raises, audit_generated_image must DENY (approved=False).
    The legacy behavior of auto-passing on error has been reversed — a missing
    audit must surface to the user, not silently approve."""
    from services import gemini_svc
    from tests.conftest import STORYBOARD_FIXTURE

    scene = STORYBOARD_FIXTURE["scenes"][0]  # match_age scene

    def _boom(*args, **kwargs):
        raise RuntimeError("Gemini unavailable")

    with patch("services.gemini_svc.asyncio.to_thread", side_effect=_boom):
        result = asyncio.get_event_loop().run_until_complete(
            gemini_svc.audit_generated_image(
                reference_photo=b"ref",
                generated_image=b"gen",
                scene=scene,
                prompt="test prompt",
            )
        )

    assert result["approved"] is False
    assert result["identity_match"] is False
    assert result["era_consistent"] is False
    assert result["no_text_on_displays"] is False
    assert result["camera_angle_matches"] is False
    assert result["looks_photoreal_not_ai"] is False
    assert "audit_error" in result


def test_image_qa_skip_face_ref_forces_identity_match_true():
    """For scenes with face_reference_mode=skip_face_ref, the identity check is
    not applicable — when a real audit succeeds it must report identity_match=True
    regardless of what the model returned."""
    from services import gemini_svc
    from tests.conftest import STORYBOARD_FIXTURE

    skip_scene = next(
        s for s in STORYBOARD_FIXTURE["scenes"]
        if s["face_reference_mode"] == "skip_face_ref"
    )

    fake_resp = MagicMock()
    fake_resp.text = json.dumps({
        "approved": True,
        "identity_match": False,  # model says false; service must override to True
        "identity_score": 0.0,
        "scene_match": True,
        "setting_match": True,
        "era_consistent": True,
        "no_text_on_displays": True,
        "camera_angle_matches": True,
        "looks_photoreal_not_ai": True,
        "issues": [],
        "recommended_feedback": "",
    })
    fake_resp.candidates = []
    fake_resp.usage_metadata = None

    async def _to_thread(fn, *args, **kwargs):
        return fake_resp

    with patch("services.gemini_svc.asyncio.to_thread", side_effect=_to_thread):
        result = asyncio.get_event_loop().run_until_complete(
            gemini_svc.audit_generated_image(
                reference_photo=b"ref",
                generated_image=b"gen",
                scene=skip_scene,
                prompt="test prompt",
            )
        )

    assert result["identity_match"] is True
    assert result["identity_score"] == 1.0
    assert result["approved"] is True


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
        "image_count": 3,
        "scenes": [
            {**STORYBOARD_FIXTURE["scenes"][0], "duration_seconds": 4, "scene_id": "01",
             "transition_out": {"type": "dissolve", "duration_seconds": 0.3},
             "transition_duration_seconds": 0.3,
             "image_slot": "img_01"},
            {**STORYBOARD_FIXTURE["scenes"][0], "duration_seconds": 4, "scene_id": "02",
             "transition_out": {"type": "fade", "duration_seconds": 0.4},
             "transition_duration_seconds": 0.4,
             "image_slot": "img_02"},
            {**STORYBOARD_FIXTURE["scenes"][0], "duration_seconds": 6, "scene_id": "03",
             "transition_out": {"type": "fade", "duration_seconds": 0.3},
             "transition_duration_seconds": 0.3,
             "image_slot": "img_03"},
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


def test_assembly_timing_rejects_short_visual_plan(client):
    from services import ffmpeg_svc, session_svc

    sid = client.post("/sessions", json={"name": "Timing Test"}).json()["session_id"]
    storyboard = json.loads(json.dumps(RICH_STORYBOARD_FIXTURE))
    session_svc.save_json_asset(sid, "storyboard_approved.json", storyboard)
    session_svc.save_asset(sid, "voiceover_approved.mp3", b"fake")

    async def fake_probe(path, log_path):
        return 66.16

    with patch("services.ffmpeg_svc._probe_duration", new=AsyncMock(side_effect=fake_probe)):
        with pytest.raises(ffmpeg_svc.FFmpegError, match="Storyboard timing mismatch"):
            asyncio.get_event_loop().run_until_complete(
                ffmpeg_svc.validate_assembly_timing(sid)
            )


def test_cost_svc_summarizes_provider_attempts(client):
    from services import cost_svc, session_svc

    sid = client.post("/sessions", json={"name": "Cost Test"}).json()["session_id"]
    cost_svc.log_veo(
        sid,
        model_variant="fast",
        stage="video_generation",
        asset_type="video",
        asset_id="clip_01",
        version=1,
        duration_seconds=4,
        sample_count=2,
    )
    cost_svc.log_elevenlabs_tts(
        sid,
        model="eleven_v3",
        stage="voiceover",
        asset_type="voiceover",
        asset_id="voiceover",
        version=1,
        characters=1000,
    )

    ledger = cost_svc.get_ledger(sid)
    assert ledger["summary"]["entry_count"] == 2
    assert ledger["summary"]["by_provider"]["veo"] == 0.8
    assert ledger["summary"]["by_provider"]["elevenlabs"] == 0.1


def test_ffmpeg_normalize_has_no_hidden_trim_or_interpolation():
    import inspect
    from services import ffmpeg_svc

    normalize_source = inspect.getsource(ffmpeg_svc.normalize_clips)
    concat_source = inspect.getsource(ffmpeg_svc.concat_with_transitions)
    assert "minterpolate" not in normalize_source
    assert "trim=start" not in normalize_source
    assert "transition={transition}" in concat_source
