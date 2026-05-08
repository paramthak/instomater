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

SCRIPT_PROMPT_FIXTURE = "Test Person, CEO of a major company, moved abroad from Mumbai."

SCRIPT_FIXTURE = {
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
SCRIPT_RESPONSE_TEXT = f'{SCRIPT_FIXTURE["full_text"]}\n\n**Word count: 81 words / ~40s**'

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


def _make_alignment_fixture(text: str, duration: float) -> dict:
    words = text.split()
    step = duration / max(len(words), 1)
    aligned_words = []
    for idx, word in enumerate(words):
        start = round(idx * step, 3)
        end = round((idx + 1) * step, 3)
        aligned_words.append({"text": word, "start": start, "end": end})
    return {
        "characters": [],
        "words": aligned_words,
        "loss": 0.1,
    }


RICH_ALIGNMENT_FIXTURE = _make_alignment_fixture(SCRIPT_FIXTURE["full_text"], 36.0)

def _simple_scene(
    idx: int,
    duration: int,
    *,
    voiceover_text: str,
    setting_category: str,
    location_anchor: str,
    shot_type: str,
    camera_motion: str,
    face_reference_mode: str,
    face_reference_target_age: int | None,
    transition_type: str = "dissolve",
    transition_duration: float = 0.3,
    era_year: int = 1994,
    subject_life_stage: str = "young adult",
    age_continuity_note: str = "Subject appears in their early twenties; scene reads as 1990s era.",
) -> dict:
    return {
        "scene_id": f"{idx:02d}",
        "start_time": 0.0,            # filled by orchestrator/_normalize_storyboard
        "end_time": float(duration),  # filled by orchestrator/_normalize_storyboard
        "duration_seconds": duration,
        "voiceover_text": voiceover_text,
        "setting_category": setting_category,
        "location_anchor": location_anchor,
        "subject_life_stage": subject_life_stage,
        "age_continuity_note": age_continuity_note,
        "era_year": era_year,
        "visual_beat": f"Visual beat {idx} at {location_anchor}",
        "shot_type": shot_type,
        "camera_motion": camera_motion,
        "image_slot": f"img_{idx:02d}",
        "face_reference_mode": face_reference_mode,
        "face_reference_target_age": face_reference_target_age,
        "image_description": {
            "subject_and_pose": f"Subject pose {idx} mid-arc",
            "environment": f"{location_anchor} with era-accurate physical detail",
            "camera_framing": f"{shot_type} framing",
            "lighting": "Soft window light at 3200K",
            "color_palette": "Kodak Portra 400, ochre, cream, teal, charcoal",
            "era_constraints": (
                f"Era {era_year}: no smartphones, no LED billboards, no readable text on any "
                "screen, monitor, sign, or display surface."
            ),
            "camera_angle": "front-3/4",
            "no_text_displays": True,
            "realism_directive": (
                "photorealistic, documentary still, 35mm film grain, indistinguishable "
                "from a real archival photograph, no illustration, no CGI, no glossy AI sheen"
            ),
        },
        "motion_arc": {
            "camera_move": f"{camera_motion} over the full clip",
            "subject_action": "strides through the frame with deliberate weight",
            "traversal": "from frame-left to frame-right",
            "era_atmosphere": f"Dust and {era_year} ambient detail",
        },
        "transition_out": {"type": transition_type, "duration_seconds": transition_duration},
    }


# 3 scenes — one per face_reference_mode for branch coverage.
STORYBOARD_FIXTURE = {
    "total_scenes": 3,
    "total_clips": 3,
    "total_images": 3,
    "image_count": 3,
    "total_duration_seconds": 14.0,
    "visual_style": {
        "era": "1990s",
        "film_stock": "Kodak Portra 400",
        "dominant_palette": "warm ochre, dusty cream, muted teal, charcoal shadow",
        "lens_feel": "35mm film, 50mm equivalent, shallow depth of field",
    },
    "scenes": [
        _simple_scene(
            1, 4,
            voiceover_text="In 1990 a young person",
            setting_category="airport_transit",
            location_anchor="airport departure curb with one borrowed suitcase",
            shot_type="WS",
            camera_motion="SLOW_PULL_BACK",
            face_reference_mode="match_age",
            face_reference_target_age=None,
            transition_type="dissolve",
        ),
        _simple_scene(
            2, 4,
            voiceover_text="left for a better life.",
            setting_category="street_city",
            location_anchor="cold city sidewalk outside student housing",
            shot_type="MS",
            camera_motion="SLOW_PUSH_IN",
            face_reference_mode="age_down_to",
            face_reference_target_age=19,
            era_year=1990,
            subject_life_stage="late teen",
            age_continuity_note="Subject rendered at age 19, visibly younger than reference photo.",
            transition_type="fade",
        ),
        _simple_scene(
            3, 6,
            voiceover_text="A back-of-head silhouette closes the loop.",
            setting_category="home_office",
            location_anchor="quiet home desk at dusk",
            shot_type="WS",
            camera_motion="STATIC_LOCK",
            face_reference_mode="skip_face_ref",
            face_reference_target_age=None,
            era_year=2010,
            subject_life_stage="adult",
            age_continuity_note="Adult subject; face is not visible in this shot.",
            transition_type="fadeblack",
            transition_duration=0.5,
        ),
    ],
    "timing_calculation": "4 + 4 + 6 = 14s; transitions 0.3 + 0.3 = 0.6 → stitched 13.4s.",
    "setting_plan": "airport_transit → street_city → home_office.",
    "word_coverage_check": "All alignment words covered in order across 3 scenes.",
}


_RICH_SCENE_TEXTS = [
    SCRIPT_FIXTURE["structure"]["hook"],
    SCRIPT_FIXTURE["structure"]["setup"],
    "He learned code under a ceiling fan. Slept beside textbooks.",
    "Skipped dinner when fees ran short. In 1994, Test Person landed abroad with eighty dollars and one phone number.",
    "Lab keys. Night buses. Cold coffee.",
    "He stayed because going home meant explaining the risk.",
    SCRIPT_FIXTURE["structure"]["landing"],
]

_RICH_SETTING_CATEGORIES = [
    "airport_transit",
    "street_city",
    "dorm_apartment",
    "computer_lab",
    "commute",
    "workplace_office",
    "home_office",
]

_RICH_LOCATION_ANCHORS = [
    "airport departure curb with one borrowed suitcase",
    "cold city sidewalk outside student housing",
    "small rented student room beside textbooks",
    "university computer lab with peers and workstations",
    "night bus stop between campus and cheap food",
    "early workplace office whiteboard with a small team",
    "quiet home desk with the old suitcase in frame",
]


_RICH_FACE_MODES = [
    ("match_age", None),
    ("match_age", None),
    ("age_down_to", 22),
    ("match_age", None),
    ("skip_face_ref", None),
    ("match_age", None),
    ("match_age", None),
]

_RICH_ERA_YEARS = [1990, 1990, 1991, 1992, 1993, 1994, 2010]


def _rich_scene(i: int, duration: int, shot: str, motion: str, part: str) -> dict:
    transition = "fadeblack" if i == 7 else "dissolve"
    face_mode, target_age = _RICH_FACE_MODES[i - 1]
    era_year = _RICH_ERA_YEARS[i - 1]
    return {
        "scene_id": f"{i:02d}",
        "start_time": 0.0,
        "end_time": float(duration),
        "duration_seconds": duration,
        "voiceover_text": _RICH_SCENE_TEXTS[i - 1],
        "setting_category": _RICH_SETTING_CATEGORIES[i - 1],
        "location_anchor": _RICH_LOCATION_ANCHORS[i - 1],
        "subject_life_stage": "young adult" if i <= 5 else "adult",
        "age_continuity_note": (
            f"Subject scaled to era {era_year}; face_reference_mode={face_mode}."
        ),
        "era_year": era_year,
        "visual_beat": f"Visual beat {i} in {_RICH_LOCATION_ANCHORS[i - 1]}",
        "shot_type": shot,
        "camera_motion": motion,
        "image_slot": f"img_{i:02d}",
        "face_reference_mode": face_mode,
        "face_reference_target_age": target_age,
        "image_description": {
            "subject_and_pose": f"Subject mid-arc pose for scene {i}",
            "environment": f"{_RICH_LOCATION_ANCHORS[i - 1]} with era-{era_year} physical detail",
            "camera_framing": f"{shot} framing locked",
            "lighting": "Soft window light at 3200K",
            "color_palette": "Kodak Portra 400, ochre, cream, teal, charcoal",
            "era_constraints": (
                f"Era {era_year}: no smartphones, no LED billboards, no readable text on any "
                "screen, monitor, sign, or display surface."
            ),
            "camera_angle": "front-3/4",
            "no_text_displays": True,
            "realism_directive": (
                "photorealistic, documentary still, 35mm film grain, indistinguishable "
                "from a real archival photograph, no illustration, no CGI, no glossy AI sheen"
            ),
        },
        "motion_arc": {
            "camera_move": f"{motion} over {duration} seconds",
            "subject_action": "strides forward with weight on the front foot",
            "traversal": "from frame-left through center to frame-right",
            "era_atmosphere": f"Dust in light; ambient {era_year} detail; background extras drift softly",
        },
        "transition_out": {
            "type": transition,
            "duration_seconds": 0.5 if transition == "fadeblack" else 0.3,
        },
    }


RICH_STORYBOARD_FIXTURE = {
    "total_scenes": 7,
    "total_clips": 7,
    "total_images": 7,
    "image_count": 7,
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
    "timing_calculation": "4+6+4+6+4+6+8 = 38s; transitions ~1.8s → stitched ~36.2s.",
    "setting_plan": (
        "airport_transit → street_city → dorm_apartment → computer_lab → commute → "
        "workplace_office → home_office."
    ),
    "word_coverage_check": "All alignment words covered exactly across 7 scenes.",
}


def make_test_image(width: int = 600, height: int = 800, fmt: str = "JPEG") -> bytes:
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()
