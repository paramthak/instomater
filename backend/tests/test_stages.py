"""Tests for stage gate enforcement and pipeline flow."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import (
    TOPIC_BRIEF_FIXTURE,
    SCRIPT_FIXTURE,
    ALIGNMENT_FIXTURE,
    STORYBOARD_FIXTURE,
    CLARIFYING_QUESTIONS_FIXTURE,
    make_test_image,
)


def _create_session(client, name="StageTest"):
    r = client.post("/sessions", json={"name": name})
    return r.json()["session_id"]


def test_stage_gate_enforced(client):
    """Calling image_generation action when at topic_brief stage should return 409."""
    sid = _create_session(client)
    resp = client.post(f"/sessions/{sid}/action", json={
        "action": "approve",
        "stage": "image_generation",
        "payload": {"image_index": 1},
    })
    assert resp.status_code == 409
    detail = resp.json()["detail"].lower()
    assert "cannot run" in detail or "image_generation" in detail


def test_topic_brief_approve_seeds_data(client):
    """Directly seeding topic brief data + approve should advance stage."""
    from services import session_svc
    from models.session import StageApproval

    sid = _create_session(client, "TopicBriefApproveTest")

    # Manually seed v1 data and iteration count (bypasses async generation)
    session_svc.save_json_asset(sid, "topic_brief_v1.json", TOPIC_BRIEF_FIXTURE)
    meta = session_svc.get_session(sid)
    meta.approval_state.topic_brief.iterations = 1
    session_svc.update_metadata(sid, approval_state=meta.approval_state)

    resp = client.post(f"/sessions/{sid}/action", json={
        "action": "approve",
        "stage": "topic_brief",
        "payload": {},
    })
    assert resp.status_code == 200

    meta_after = client.get(f"/sessions/{sid}").json()["metadata"]
    assert meta_after["approval_state"]["topic_brief"]["approved"] is True
    assert meta_after["current_stage"] == "photo_upload"


@patch("services.openai_svc._client")
def test_storyboard_duration_validation_error_surfaces(mock_openai, client):
    """If storyboard returns invalid durations 3 times, error flag is set."""
    bad_storyboard = {
        **STORYBOARD_FIXTURE,
        "scenes": [{**STORYBOARD_FIXTURE["scenes"][0], "duration_seconds": 5}],  # 5 is invalid
    }
    mock_openai.chat.completions.create = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(bad_storyboard)))]
    ))

    from services import openai_svc
    import asyncio

    async def run():
        result = await openai_svc.generate_storyboard(SCRIPT_FIXTURE, ALIGNMENT_FIXTURE, TOPIC_BRIEF_FIXTURE)
        return result

    result = asyncio.get_event_loop().run_until_complete(run())
    # After 3 failed attempts, should have validation_error key
    assert "validation_error" in result


def test_veo_polling_timeout():
    """Veo polling loop must stop at VEO_MAX_POLLS, never loop infinitely."""
    import asyncio
    from unittest.mock import AsyncMock
    from services.gemini_svc import run_video_job, VeoTimeoutError

    poll_count = 0

    async def mock_submit(*args, **kwargs):
        return "test-operation"

    async def mock_poll(op_name):
        nonlocal poll_count
        poll_count += 1
        return "RUNNING", None  # Always running — should hit timeout

    async def run():
        with patch("services.gemini_svc.submit_video_job", new=mock_submit), \
             patch("services.gemini_svc.poll_video_job", new=mock_poll), \
             patch("asyncio.sleep", new=AsyncMock()):
            from config import VEO_MAX_POLLS
            # Override max polls to 3 for fast test
            with patch("services.gemini_svc.VEO_MAX_POLLS", 3):
                import importlib
                import services.gemini_svc as gsvc
                gsvc.VEO_MAX_POLLS = 3
                try:
                    await run_video_job(
                        "test prompt",
                        b"start",
                        b"end",
                        4,
                        "fast",
                    )
                    return False  # Should not reach here
                except VeoTimeoutError:
                    return True

    result = asyncio.get_event_loop().run_until_complete(run())
    assert result, "VeoTimeoutError should have been raised"
    assert poll_count == 3, f"Expected exactly 3 polls, got {poll_count}"


def test_assembly_preflight_missing_clip(client, tmp_path):
    """Preflight check should fail if an expected clip is missing."""
    import asyncio
    from services import ffmpeg_svc, session_svc

    sid = _create_session(client, "AssemblyTest")

    # Save a storyboard with 2 scenes
    session_svc.save_json_asset(sid, "storyboard_approved.json", STORYBOARD_FIXTURE)

    # Don't create any clips — preflight should fail
    async def run():
        try:
            await ffmpeg_svc.preflight_check(sid)
            return False
        except ffmpeg_svc.FFmpegError:
            return True

    result = asyncio.get_event_loop().run_until_complete(run())
    assert result, "FFmpegError should have been raised for missing clips"


# Required for patch on async mock
from unittest.mock import MagicMock
