"""Tests for stage gate enforcement and pipeline flow."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import (
    SCRIPT_FIXTURE,
    ALIGNMENT_FIXTURE,
    STORYBOARD_FIXTURE,
    make_test_image,
)


def _create_session(client, name="StageTest"):
    r = client.post("/sessions", json={"name": name})
    return r.json()["session_id"]


def test_stage_gate_enforced(client):
    """Calling image_generation action when at the first stage should return 409."""
    sid = _create_session(client)
    resp = client.post(f"/sessions/{sid}/action", json={
        "action": "approve",
        "stage": "image_generation",
        "payload": {"image_index": 1},
    })
    assert resp.status_code == 409
    detail = resp.json()["detail"].lower()
    assert "cannot run" in detail or "image_generation" in detail


# test_regenerated_end_image_uses_versioned_frame_in_next_video_prompt removed:
# the chained start/end image flow no longer exists. Each clip now has a single
# anchor image, so there is no "regenerate end frame → invalidate next clip"
# semantic to assert against.


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
        result = await openai_svc.generate_storyboard(SCRIPT_FIXTURE, ALIGNMENT_FIXTURE)
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


def test_restore_script_promotes_single_active_version(client):
    from services import session_svc
    from models.session import StageEnum

    sid = _create_session(client, "RestoreScriptTest")
    session_svc.save_json_asset(sid, "script_v1.json", {
        "full_text": SCRIPT_FIXTURE["full_text"],
        "display_text": f'{SCRIPT_FIXTURE["full_text"]}\n\n**Word count: 81 words / ~40s**',
        "estimated_word_count": 81,
        "estimated_duration_seconds": 40,
    })
    script_v2 = {
        "full_text": "This second version should stop being active.",
        "display_text": "This second version should stop being active.\n\n**Word count: 7 words / ~40s**",
        "estimated_word_count": 7,
        "estimated_duration_seconds": 40,
    }
    session_svc.save_json_asset(sid, "script_v2.json", script_v2)

    meta = session_svc.get_session(sid)
    meta.current_stage = StageEnum.video_generation
    meta.approval_state.script.iterations = 2
    meta.approval_state.script.approved = True
    meta.approval_state.script.approved_version = 2
    meta.photo_ext = "jpg"
    meta.completed_stages = ["script", "photo_upload", "voiceover"]
    session_svc.update_metadata(sid, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})

    resp = client.post(f"/sessions/{sid}/action", json={
        "action": "restore",
        "stage": "script",
        "payload": {"version": 1},
    })
    assert resp.status_code == 200
    meta_after = client.get(f"/sessions/{sid}").json()["metadata"]
    assert meta_after["approval_state"]["script"]["approved_version"] == 1
    assert meta_after["approval_state"]["voiceover"]["approved"] is False
    assert meta_after["current_stage"] == "voiceover"


def test_change_accepted_script_keeps_downstream_until_new_approval(client):
    from services import session_svc
    from models.session import StageEnum

    sid = _create_session(client, "ChangeAcceptedScriptTest")
    session_svc.save_json_asset(sid, "script_v1.json", {
        "full_text": SCRIPT_FIXTURE["full_text"],
        "display_text": f'{SCRIPT_FIXTURE["full_text"]}\n\n**Word count: 81 words / ~40s**',
        "estimated_word_count": 81,
        "estimated_duration_seconds": 40,
    })
    meta = session_svc.get_session(sid)
    meta.current_stage = StageEnum.video_generation
    meta.approval_state.script.iterations = 1
    meta.approval_state.script.approved = True
    meta.approval_state.script.approved_version = 1
    meta.approval_state.voiceover.approved = True
    meta.approval_state.voiceover.approved_version = 1
    session_svc.update_metadata(sid, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})

    rewritten = {
        "full_text": "This new pending version is not active yet.",
        "display_text": "This new pending version is not active yet.\n\n**Word count: 8 words / ~40s**",
        "estimated_word_count": 8,
        "estimated_duration_seconds": 40,
    }

    with patch("services.openai_svc.rewrite_script", new=AsyncMock(return_value=rewritten)):
        resp = client.post(f"/sessions/{sid}/action", json={
            "action": "change",
            "stage": "script",
            "payload": {"feedback": "change it", "version": 1},
        })

    assert resp.status_code == 200
    meta_after = client.get(f"/sessions/{sid}").json()["metadata"]
    assert meta_after["approval_state"]["script"]["iterations"] == 2
    assert meta_after["approval_state"]["script"]["approved_version"] == 1
    assert meta_after["approval_state"]["voiceover"]["approved"] is True
    assert meta_after["current_stage"] == "script"


def test_inline_edit_script_creates_pending_version_without_invalidation(client):
    from services import session_svc
    from models.session import StageEnum

    sid = _create_session(client, "InlineEditScriptTest")
    session_svc.save_json_asset(sid, "script_v1.json", {
        "full_text": SCRIPT_FIXTURE["full_text"],
        "display_text": f'{SCRIPT_FIXTURE["full_text"]}\n\n**Word count: 81 words / ~40s**',
        "estimated_word_count": 81,
        "estimated_duration_seconds": 40,
    })
    session_svc.append_chat_message(sid, {
        "msg_type": "asset_card",
        "subtype": "script",
        "iteration": 1,
        "data": {
            "full_text": SCRIPT_FIXTURE["full_text"],
            "display_text": f'{SCRIPT_FIXTURE["full_text"]}\n\n**Word count: 81 words / ~40s**',
            "estimated_word_count": 81,
            "estimated_duration_seconds": 40,
        },
        "status": "approved",
        "timestamp": "2026-01-01T00:00:00Z",
    })
    meta = session_svc.get_session(sid)
    meta.current_stage = StageEnum.video_generation
    meta.approval_state.script.iterations = 1
    meta.approval_state.script.approved = True
    meta.approval_state.script.approved_version = 1
    meta.approval_state.voiceover.approved = True
    meta.approval_state.voiceover.approved_version = 1
    session_svc.update_metadata(sid, **{k: v for k, v in meta.model_dump().items() if k != "session_id"})

    resp = client.post(f"/sessions/{sid}/action", json={
        "action": "edit",
        "stage": "script",
        "payload": {
            "script": "This is the edited spoken script.\nIt should become version two.",
            "version": 1,
        },
    })

    assert resp.status_code == 200
    meta_after = client.get(f"/sessions/{sid}").json()["metadata"]
    assert meta_after["approval_state"]["script"]["iterations"] == 2
    assert meta_after["approval_state"]["script"]["approved_version"] == 1
    assert meta_after["approval_state"]["voiceover"]["approved"] is True
    assert meta_after["current_stage"] == "script"

    script_v2 = session_svc.load_json_asset(sid, "script_v2.json")
    assert script_v2["full_text"] == "This is the edited spoken script. It should become version two."
    assert script_v2["rewrite_context"] == {"from_version": 1, "mode": "inline_edit"}

    history = client.get(f"/sessions/{sid}").json()["chat_history"]
    latest_script_card = [msg for msg in history if msg.get("msg_type") == "asset_card" and msg.get("subtype") == "script"][-1]
    assert latest_script_card["iteration"] == 2
    assert latest_script_card["status"] == "pending_approval"


def test_assembly_lock_blocks_later_actions(client):
    from services import session_svc

    sid = _create_session(client, "LockedSession")
    session_svc.update_metadata(sid, assembly_locked=True)

    resp = client.post(f"/sessions/{sid}/action", json={
        "action": "change",
        "stage": "script",
        "payload": {"feedback": "change it"},
    })
    assert resp.status_code == 409
    assert "locked" in resp.json()["detail"].lower()


# Required for patch on async mock
from unittest.mock import MagicMock
