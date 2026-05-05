"""
Test fixtures. All API calls are mocked — no real API keys needed.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image
import io

# Point sessions dir at a temp dir before importing app
@pytest.fixture(autouse=True, scope="session")
def setup_env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("sessions")
    os.environ["OPENAI_API_KEY"] = "test-openai-key"
    os.environ["GEMINI_API_KEY"] = "test-gemini-key"
    os.environ["ELEVENLABS_API_KEY"] = "test-elevenlabs-key"
    os.environ["ELEVENLABS_TTS_SPEED"] = "1.2"
    os.environ["ELEVENLABS_TTS_STYLE"] = "0.65"
    os.environ["ELEVENLABS_AUDIO_TEMPO"] = "1.2"
    os.environ["SESSIONS_DIR"] = str(tmp)
    yield
    # Cleanup handled by tmp_path_factory


@pytest.fixture(scope="session")
def app_client(setup_env):
    from main import app
    return TestClient(app)


@pytest.fixture
def client(app_client):
    return app_client


# ── Mock fixtures ──────────────────────────────────────────────────────────

TOPIC_BRIEF_FIXTURE = {
    "person_name": "Test Person",
    "person_slug": "test-person",
    "gender": "male",
    "origin_country": "India",
    "origin_city": "Mumbai",
    "current_role_or_legacy": "CEO of a major company",
    "key_life_milestones": [
        {"year": 1970, "event": "Born in Mumbai"},
        {"year": 1990, "event": "Moved abroad"},
    ],
    "narrative_arc_options": ["Arc 1", "Arc 2", "Arc 3"],
    "selected_narrative_arc": "Arc 1",
    "tone_suggestions": ["warm", "cool", "neutral"],
    "selected_tone": "warm",
    "factual_anchors_for_visuals": [
        "Small flat in Mumbai", "Airport departure", "First office", "Current HQ", "Home now"
    ],
    "estimated_target_duration_seconds": 38,
}

SCRIPT_FIXTURE = {
    "hook_category": "Storytelling",
    "hook_subtype_used": "curiosity_gap",
    "hook_formula_used": "curiosity_gap",
    "perspective": "third_person_documentary",
    "structure": {
        "hook": "In 1990, a Mumbai student packed one borrowed suitcase.",
        "setup": "One platform. Two shirts. A photocopied letter. His parents counted rent before blessing the flight.",
        "build": "He learned code under a ceiling fan. Slept beside textbooks. Skipped dinner when fees ran short. In 1994, Test Person landed abroad with eighty dollars and one phone number. Lab keys. Night buses. Cold coffee. He stayed because going home meant explaining the risk.",
        "landing": "That suitcase still closes the first loop. He checks windows before big decisions.",
    },
    "full_text": "In 1990, a Mumbai student packed one borrowed suitcase. One platform. Two shirts. A photocopied letter. His parents counted rent before blessing the flight. He learned code under a ceiling fan. Slept beside textbooks. Skipped dinner when fees ran short. In 1994, Test Person landed abroad with eighty dollars and one phone number. Lab keys. Night buses. Cold coffee. He stayed because going home meant explaining the risk. That suitcase still closes the first loop. He checks windows before big decisions.",
    "estimated_word_count": 81,
    "estimated_duration_seconds": 36.0,
    "name_mentions_count": 1,
    "self_check": {
        "hook_works_in_isolation": True,
        "hook_has_specific_anchor": True,
        "hook_avoids_banned_openings": True,
        "all_sentences_under_22_words": True,
        "fragment_count": 7,
        "specific_anchors_count": 8,
        "name_in_hook": False,
        "landing_closes_hook_loop": True,
        "landing_avoids_moral_or_cta": True,
        "word_count_in_range": True,
        "passes_breath_test": True,
        "fridge_line": "He stayed because going home meant explaining the risk.",
    },
}

ALIGNMENT_FIXTURE = {
    "characters": [{"text": "I", "start": 0.1, "end": 0.2}],
    "words": [
        {"text": "In", "start": 0.1, "end": 0.3},
        {"text": "1990", "start": 0.4, "end": 0.8},
        {"text": "a", "start": 0.9, "end": 1.0},
        {"text": "young", "start": 1.1, "end": 1.4},
        {"text": "person", "start": 1.5, "end": 1.9},
        {"text": "left.", "start": 2.0, "end": 2.4},
    ],
    "loss": 0.1,
}

STORYBOARD_FIXTURE = {
    "total_scenes": 2,
    "total_duration_seconds": 8.0,
    "image_count": 3,
    "scenes": [
        {
            "scene_id": "01",
            "start_time": 0.0,
            "end_time": 4.0,
            "duration_seconds": 4,
            "voiceover_words": "In 1990 a young person",
            "visual_description": "Airport scene",
            "image_slot_start": "img_01",
            "image_slot_end": "img_02",
            "transition_in": "fade",
            "transition_out": "dissolve",
            "transition_duration_seconds": 0.3,
        },
        {
            "scene_id": "02",
            "start_time": 4.0,
            "end_time": 8.0,
            "duration_seconds": 4,
            "voiceover_words": "left for a better life.",
            "visual_description": "New city scene",
            "image_slot_start": "img_02",
            "image_slot_end": "img_03",
            "transition_in": "dissolve",
            "transition_out": "fade",
            "transition_duration_seconds": 0.3,
        },
    ],
}


def _rich_scene(i: int, duration: int, shot: str, motion: str, part: str) -> dict:
    start = float(sum([4, 6, 4, 6, 4, 6, 8][: i - 1]))
    end = start + duration
    transition = "fadeblack" if i == 6 else "dissolve"
    return {
        "scene_id": f"{i:02d}",
        "script_part": part,
        "start_time": start,
        "end_time": end,
        "duration_seconds": duration,
        "voiceover_text": f"Voiceover beat {i}",
        "image_start": f"img_{i:02d}",
        "image_end": f"img_{i + 1:02d}",
        "shot_type": shot,
        "camera_motion": motion,
        "image_start_description": {
            "subject_and_pose": f"Subject pose {i}",
            "environment": "Era-specific room with lived-in details",
            "camera_framing": f"{shot} framing",
            "lighting": "Soft window light at 3200K",
            "color_palette": "Kodak Portra 400, ochre, cream, teal, charcoal",
        },
        "image_end_description": {
            "subject_and_pose": f"Subject later pose {i}",
            "environment": "Same world, visibly later physical beat",
            "camera_framing": f"{shot} reframed with changed depth",
            "lighting": "Soft window light shifted across the frame",
            "color_palette": "Kodak Portra 400, ochre, cream, teal, charcoal",
            "difference_from_start": "Subject posture, camera distance, and background depth have changed.",
        },
        "video_motion_prompt": {
            "start_state": f"Start state for scene {i}",
            "end_state": f"End state for scene {i}",
            "subject_motion": "Subtle weight shift and hand movement",
            "camera_motion_description": f"{motion} over the full clip",
            "atmosphere": "Dust in light, background figures drift softly",
        },
        "transition_out": {"type": transition, "duration_seconds": 0.5 if transition == "fadeblack" else 0.3},
        "visual_narration_check": "The visual adds material context rather than repeating the narration.",
    }


RICH_STORYBOARD_FIXTURE = {
    "total_clips": 7,
    "total_images": 8,
    "total_duration_seconds": 38.0,
    "visual_style": {
        "era": "1990s through early 2000s",
        "film_stock": "Kodak Portra 400",
        "dominant_palette": "warm ochre, dusty cream, muted teal, charcoal shadow",
        "lens_feel": "35mm film, 50mm equivalent, shallow depth of field",
    },
    "scenes": [
        _rich_scene(1, 4, "WS", "SLOW_PULL_BACK", "hook"),
        _rich_scene(2, 6, "CU", "SLOW_ZOOM_IN_STILL", "setup"),
        _rich_scene(3, 4, "MS", "SLOW_PUSH_IN", "build"),
        _rich_scene(4, 6, "WS", "SLOW_PAN", "build"),
        _rich_scene(5, 4, "CU", "HANDHELD_FLOAT", "build"),
        _rich_scene(6, 6, "MS", "SLOW_TILT_UP", "build"),
        _rich_scene(7, 8, "WS", "STATIC_LOCK", "landing"),
    ],
    "self_check": {
        "total_clips_valid": True,
        "total_duration_matches_voiceover": True,
        "no_mid_word_cuts": True,
        "all_durations_4_6_or_8": True,
        "shot_type_sequence": "WS CU MS WS CU MS WS",
        "no_same_shot_type_3_in_row": True,
        "camera_motion_sequence": "SLOW_PULL_BACK SLOW_ZOOM_IN_STILL SLOW_PUSH_IN SLOW_PAN HANDHELD_FLOAT SLOW_TILT_UP STATIC_LOCK",
        "no_two_push_ins_in_row": True,
        "final_clip_is_pull_back_or_static": True,
        "visual_style_consistent": True,
        "no_visual_repeats_narration": True,
        "first_and_last_image_echo": "Both use wide negative space with different emotional weight.",
    },
}

CLARIFYING_QUESTIONS_FIXTURE = {
    "questions": [
        {
            "id": "q1",
            "question_text": "What era?",
            "options": ["1990s", "Modern", "Custom: write your own"],
            "rationale": "Sets overall look",
        }
    ]
}


def make_test_image(width: int = 600, height: int = 800, fmt: str = "JPEG") -> bytes:
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()
