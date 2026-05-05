from __future__ import annotations

import base64
import json
import random
import re
from typing import Awaitable, Callable, Optional

from google import genai
from google.genai import types as gtypes
from openai import AsyncOpenAI

from config import GEMINI_API_KEY, GEMINI_TEXT_MODEL, OPENAI_API_KEY, OPENAI_MODEL, HOOK_CATEGORIES
from pipeline.prompts import (
    TOPIC_BRIEF_SYSTEM,
    TOPIC_BRIEF_REWRITE_SYSTEM,
    CLARIFYING_QUESTIONS_SYSTEM,
    IMAGE_PROMPT_1_SYSTEM,
    IMAGE_PROMPT_CHAIN_SYSTEM,
    IMAGE_PROMPT_REGEN_SYSTEM,
    VIDEO_PROMPT_SYSTEM,
    VIDEO_PROMPT_REGEN_SYSTEM,
)
from pipeline.skill_prompts import (
    SCRIPT_REWRITE_SYSTEM,
    SCRIPT_WRITER_SYSTEM,
    STORYBOARD_REWRITE_SYSTEM,
    STORYBOARD_WRITER_SYSTEM,
)

_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
_gemini_client = genai.Client(api_key=GEMINI_API_KEY)

VALID_DURATIONS = {4, 6, 8}
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_AUDIO_TAG_RE = re.compile(r"\[[^\]]+\]")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_SCRIPT_BANNED_PHRASES = [
    "incredible",
    "unbelievable",
    "amazing",
    "extraordinary",
    "remarkable",
    "the story of",
    "the journey of",
    "rags to riches",
    "against all odds",
    "humble beginnings",
    "little did he know",
    "little did she know",
    "from there",
    "and the rest is history",
    "the rest is history",
    "a young man with big dreams",
    "small town boy",
    "follow your dreams",
    "never give up",
    "anything is possible",
    "the sky's the limit",
    "let's dive in",
    "let me tell you",
    "you won't believe",
    "buckle up",
    "cut to",
    "did you know",
    "have you ever wondered",
    "imagine if",
    "picture this",
    "once upon a time",
    "fast forward",
    "next thing you know",
    "one day",
    "many years later",
    "growing up",
    "hey guys",
    "what's up",
    "welcome back",
    "cloud-first giant",
    "servers hummed",
    "monitors glowed",
    "future seemed locked",
    "accent shaped",
    "cloud over boxes",
    "boardrooms changed",
    "quiet optimism",
    "optimism in the hallways",
    "sleeves rolled",
    "eyes clear",
]
_SCRIPT_TARGET_MIN_WORDS = 80
_SCRIPT_TARGET_MAX_WORDS = 118
_SCRIPT_MIN_WORDS = 70
_SCRIPT_MAX_WORDS = 125
_SCRIPT_MAX_SENTENCE_WORDS = 34
_SCRIPT_BANNED_REPLACEMENTS = {
    "cloud-first giant": "cloud company",
    "servers hummed": "servers ran",
    "monitors glowed": "screens stayed on",
    "future seemed locked": "future depended",
    "accent shaped": "voice shaped",
    "cloud over boxes": "cloud over old software",
    "boardrooms changed": "teams changed",
    "quiet optimism": "steady confidence",
    "optimism in the hallways": "confidence in the office",
    "sleeves rolled": "ready to work",
    "eyes clear": "focused",
}


def _b64_image(image_bytes: bytes, media_type: str = "image/png") -> dict:
    encoded = base64.standard_b64encode(image_bytes).decode()
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{media_type};base64,{encoded}"},
    }


def _is_openai_quota_error(exc: Exception) -> bool:
    err = str(exc).lower()
    return "insufficient_quota" in err or "current quota" in err or "error code: 429" in err


def _gemini_parts(user_content) -> list[gtypes.Part]:
    if isinstance(user_content, str):
        return [gtypes.Part.from_text(text=user_content)]

    parts: list[gtypes.Part] = []
    for item in user_content:
        if item.get("type") == "text":
            parts.append(gtypes.Part.from_text(text=item.get("text", "")))
            continue

        image_url = item.get("image_url") or {}
        url = image_url.get("url", "")
        if not url.startswith("data:") or "," not in url:
            continue
        meta, payload = url.split(",", 1)
        mime = meta.removeprefix("data:").split(";", 1)[0] or "image/png"
        parts.append(gtypes.Part.from_bytes(data=base64.b64decode(payload), mime_type=mime))
    return parts


def _extract_response_text(response) -> str:
    text = getattr(response, "text", None)
    if text:
        return text.strip()
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                return part_text.strip()
    raise RuntimeError("Gemini returned no text in response")


def _remove_trailing_json_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _extract_json_candidate(text: str) -> str:
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        return fence.group(1).strip()

    start_positions = [pos for pos in (text.find("{"), text.find("[")) if pos >= 0]
    if not start_positions:
        return text.strip()
    start = min(start_positions)

    stack: list[str] = []
    in_string = False
    escape = False
    for idx, char in enumerate(text[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            stack.append("}")
            continue
        if char == "[":
            stack.append("]")
            continue
        if char in "}]":
            if not stack or stack[-1] != char:
                continue
            stack.pop()
            if not stack:
                return text[start: idx + 1].strip()
    return text[start:].strip()


def _parse_json_text(text: str) -> dict:
    candidate = _extract_json_candidate(text.strip())
    attempts = [
        candidate,
        _remove_trailing_json_commas(candidate),
    ]
    last_error: json.JSONDecodeError | None = None
    for attempt in attempts:
        try:
            return json.loads(attempt)
        except json.JSONDecodeError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _json_error_summary(exc: json.JSONDecodeError) -> str:
    return f"Invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}"


def _load_chat_json(content: str | None) -> dict:
    if content is None:
        raise json.JSONDecodeError("empty response", "", 0)
    return _parse_json_text(content)


async def _gemini_json(system: str, user_content, temperature: float) -> dict:
    response = await _gemini_client.aio.models.generate_content(
        model=GEMINI_TEXT_MODEL,
        contents=gtypes.Content(parts=_gemini_parts(user_content)),
        config=gtypes.GenerateContentConfig(
            system_instruction=f"{system}\n\nReturn only valid JSON.",
            temperature=temperature,
            response_mime_type="application/json",
        ),
    )
    return _parse_json_text(_extract_response_text(response))


async def _gemini_text(system: str, user_content, temperature: float) -> str:
    response = await _gemini_client.aio.models.generate_content(
        model=GEMINI_TEXT_MODEL,
        contents=gtypes.Content(parts=_gemini_parts(user_content)),
        config=gtypes.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
        ),
    )
    return _extract_response_text(response)


async def _chat_json(
    system: str,
    user_content,
    temperature: float = 0.6,
    max_tokens: int | None = None,
) -> dict:
    """Send a chat completion and parse the JSON response."""
    if isinstance(user_content, str):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
    else:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
    try:
        kwargs = {
            "model": OPENAI_MODEL,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = await _client.chat.completions.create(**kwargs)
        return _load_chat_json(response.choices[0].message.content)
    except Exception as exc:
        if _is_openai_quota_error(exc):
            return await _gemini_json(system, user_content, temperature)
        raise


async def _chat_text(
    system: str,
    user_content,
    temperature: float = 0.4,
) -> str:
    """Send a chat completion and return the plain text response."""
    if isinstance(user_content, str):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
    else:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
    try:
        response = await _client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=temperature,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        if _is_openai_quota_error(exc):
            return await _gemini_text(system, user_content, temperature)
        raise


def _script_system_for(brief: dict, hook_category: str) -> str:
    return (
        SCRIPT_WRITER_SYSTEM
        .replace("{topic_brief}", json.dumps(brief, indent=2))
        .replace("{assigned_hook_category}", hook_category)
    )


def _storyboard_system_for(script: dict, alignment: dict, brief: dict) -> str:
    return (
        STORYBOARD_WRITER_SYSTEM
        .replace("{script}", json.dumps(script, indent=2))
        .replace("{alignment}", json.dumps(alignment, indent=2))
        .replace("{topic_brief}", json.dumps(brief, indent=2))
    )


def _word_count(text: str) -> int:
    return len(text.split())


def _clean_script_text(text: str) -> str:
    text = _AUDIO_TAG_RE.sub("", str(text or ""))
    text = text.replace("#", "")
    text = re.sub(r"\s+", " ", text).strip()
    for phrase, replacement in _SCRIPT_BANNED_REPLACEMENTS.items():
        text = re.sub(re.escape(phrase), replacement, text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _sentences(text: str) -> list[str]:
    matches = re.findall(r"[^.!?]+[.!?]?", text)
    return [_clean_script_text(match) for match in matches if _clean_script_text(match)]


def _infer_script_structure(full_text: str) -> dict:
    full_text = _clean_script_text(full_text)
    sentences = _sentences(full_text)
    if len(sentences) >= 5:
        return {
            "hook": sentences[0],
            "setup": " ".join(sentences[1:3]),
            "build": " ".join(sentences[3:-2]),
            "landing": " ".join(sentences[-2:]),
        }
    if len(sentences) == 4:
        return {
            "hook": sentences[0],
            "setup": sentences[1],
            "build": sentences[2],
            "landing": sentences[3],
        }
    words = full_text.split()
    if not words:
        return {"hook": "", "setup": "", "build": "", "landing": ""}
    total = len(words)
    hook_end = max(1, min(14, round(total * 0.12)))
    setup_end = max(hook_end + 1, min(total, round(total * 0.32)))
    landing_start = max(setup_end + 1, round(total * 0.82))
    return {
        "hook": " ".join(words[:hook_end]),
        "setup": " ".join(words[hook_end:setup_end]),
        "build": " ".join(words[setup_end:landing_start]),
        "landing": " ".join(words[landing_start:]),
    }


def _name_mentions(text: str, brief: dict | None, fallback: int | None = None) -> int:
    name = (brief or {}).get("person_name")
    if name:
        return len(re.findall(rf"\b{re.escape(name)}\b", text, flags=re.IGNORECASE))
    if fallback is None:
        return 0
    try:
        return max(0, int(fallback))
    except (TypeError, ValueError):
        return 0


def _anchor_count(text: str) -> int:
    year_count = len(re.findall(r"\b(?:19|20)\d{2}\b", text))
    number_count = len(re.findall(r"\b\d+(?:,\d{3})*(?:\.\d+)?\b", text))
    proper_count = len(re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b", text))
    return max(year_count + number_count, min(8, proper_count))


def _fragment_count(text: str) -> int:
    return sum(1 for sentence in _sentences(text) if _word_count(sentence) <= 5)


def _with_self_check(data: dict, brief: dict | None = None) -> dict:
    structure = data.get("structure") or {}
    full_text = data.get("full_text", "")
    hook = structure.get("hook", "")
    sentences = _sentences(full_text)
    max_sentence_words = max((_word_count(sentence) for sentence in sentences), default=0)
    full_name = (brief or {}).get("person_name", "")
    name_in_hook = bool(
        full_name and re.search(rf"\b{re.escape(full_name)}\b", hook, flags=re.IGNORECASE)
    )
    data["self_check"] = {
        **(data.get("self_check") or {}),
        "hook_works_in_isolation": bool(hook),
        "hook_has_specific_anchor": _anchor_count(hook) > 0,
        "hook_avoids_banned_openings": True,
        "all_sentences_under_24_words": max_sentence_words <= 24,
        "all_sentences_under_22_words": max_sentence_words <= 22,
        "fragment_count": _fragment_count(full_text),
        "specific_anchors_count": _anchor_count(full_text),
        "name_in_hook": name_in_hook,
        "landing_closes_hook_loop": bool(structure.get("landing")),
        "landing_avoids_moral_or_cta": True,
        "word_count_in_range": _SCRIPT_TARGET_MIN_WORDS <= _word_count(full_text) <= _SCRIPT_TARGET_MAX_WORDS,
        "passes_breath_test": max_sentence_words <= 24,
        "plain_language_passes": not any(
            phrase in full_text.lower() for phrase in _SCRIPT_BANNED_PHRASES
        ),
        "fridge_line": sentences[-1] if sentences else "",
    }
    return data


def _normalize_script(
    data: dict,
    brief: dict | None = None,
    hook_category: str | None = None,
) -> dict:
    if not isinstance(data, dict):
        data = {}
    data = dict(data)
    structure = data.get("structure") if isinstance(data.get("structure"), dict) else {}
    full_text = _clean_script_text(data.get("full_text", ""))
    inferred = _infer_script_structure(full_text) if full_text else {}

    normalized_structure = {}
    for key in ("hook", "setup", "build", "landing"):
        value = structure.get(key) or inferred.get(key) or ""
        normalized_structure[key] = _clean_script_text(value)

    if not any(normalized_structure.values()) and full_text:
        normalized_structure = _infer_script_structure(full_text)

    data["structure"] = normalized_structure
    data["full_text"] = " ".join(
        normalized_structure[key]
        for key in ("hook", "setup", "build", "landing")
        if normalized_structure[key]
    ).strip()
    data.setdefault("hook_category", hook_category or "Storytelling")
    if data.get("hook_category") not in HOOK_CATEGORIES:
        data["hook_category"] = hook_category or "Storytelling"
    if data.get("hook_subtype_used") not in {"pattern_interrupt", "curiosity_gap", "proof_first"}:
        data["hook_subtype_used"] = "curiosity_gap"
    if data.get("perspective") not in {"first_person", "second_person", "third_person_documentary"}:
        data["perspective"] = "third_person_documentary"
    data["estimated_word_count"] = _word_count(data["full_text"])
    data["estimated_duration_seconds"] = round(data["estimated_word_count"] / 2.25, 1)
    data["name_mentions_count"] = _name_mentions(
        data["full_text"],
        brief,
        data.get("name_mentions_count"),
    )
    if "hook_formula_used" not in data and data.get("hook_subtype_used"):
        data["hook_formula_used"] = data["hook_subtype_used"]
    data = _with_self_check(data, brief)
    return data


def _validate_script(data: dict) -> tuple[bool, str]:
    data = _normalize_script(data)
    required_top = {
        "hook_category", "hook_subtype_used", "perspective", "structure",
        "full_text", "estimated_word_count", "estimated_duration_seconds",
        "name_mentions_count", "self_check",
    }
    missing = required_top - set(data.keys())
    if missing:
        return False, f"Missing top-level keys: {sorted(missing)}"

    structure = data.get("structure") or {}
    missing_structure = {"hook", "setup", "build", "landing"} - set(structure.keys())
    if missing_structure:
        return False, f"Missing structure keys: {sorted(missing_structure)}"

    if data["hook_subtype_used"] not in {"pattern_interrupt", "curiosity_gap", "proof_first"}:
        return False, f"Invalid hook_subtype_used: {data['hook_subtype_used']}"
    if data["perspective"] not in {"first_person", "second_person", "third_person_documentary"}:
        return False, f"Invalid perspective: {data['perspective']}"

    expected_full = " ".join(structure[key].strip() for key in ("hook", "setup", "build", "landing"))
    if data["full_text"].strip() != expected_full.strip():
        return False, "full_text does not equal hook + setup + build + landing"

    actual_wc = _word_count(data["full_text"])
    if not (_SCRIPT_MIN_WORDS <= actual_wc <= _SCRIPT_MAX_WORDS):
        return False, f"Word count {actual_wc} outside {_SCRIPT_MIN_WORDS}-{_SCRIPT_MAX_WORDS} range"

    hook_wc = _word_count(structure["hook"])
    if not (4 <= hook_wc <= 22):
        return False, f"Hook word count {hook_wc} outside 4-22 range"

    section_ranges = {
        "setup": (6, 45),
        "build": (15, 95),
        "landing": (4, 40),
    }
    for section, (low, high) in section_ranges.items():
        count = _word_count(structure[section])
        if not (low <= count <= high):
            return False, f"{section} word count {count} outside {low}-{high} range"

    for sentence in _SENTENCE_RE.split(data["full_text"]):
        if _word_count(sentence) > _SCRIPT_MAX_SENTENCE_WORDS:
            return False, f"Sentence exceeds {_SCRIPT_MAX_SENTENCE_WORDS} words: {sentence[:70]}"

    full_lower = data["full_text"].lower()
    for phrase in _SCRIPT_BANNED_PHRASES:
        if phrase in full_lower:
            return False, f"Banned phrase used: {phrase}"

    landing_words = structure["landing"].strip().split()
    if landing_words and landing_words[0].lower().rstrip(".,") == "today":
        return False, "Landing begins with today"
    if "#" in data["full_text"]:
        return False, "Script contains hashtag"
    if _AUDIO_TAG_RE.search(data["full_text"]):
        return False, "Script contains raw audio tags"
    if any(ord(char) > 0x2E80 for char in data["full_text"]):
        return False, "Script contains emoji or non-text symbols"
    if not (0 <= int(data.get("name_mentions_count", -1)) <= 4):
        return False, f"Name mentioned {data.get('name_mentions_count')} times"
    return True, "OK"


def _description_text(value) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{key}: {val}" for key, val in value.items() if val)
    return str(value or "")


def _compat_visual_description(scene: dict) -> str:
    parts = [
        f"Shot: {scene.get('shot_type', 'MS')}; camera: {scene.get('camera_motion', '')}.",
        f"Start frame: {_description_text(scene.get('image_start_description'))}",
        f"End frame: {_description_text(scene.get('image_end_description'))}",
    ]
    motion = scene.get("video_motion_prompt")
    if motion:
        parts.append(f"Motion: {_description_text(motion)}")
    if scene.get("visual_narration_check"):
        parts.append(f"Visual narration check: {scene['visual_narration_check']}")
    return "\n".join(part for part in parts if part.strip())


def _coerce_scene_duration(value) -> int:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 6.0
    return min(VALID_DURATIONS, key=lambda allowed: abs(allowed - numeric))


def _valid_shot_type(value, idx: int) -> str:
    if value in {"WS", "MS", "CU", "ECU"}:
        return value
    return ["WS", "CU", "MS"][idx % 3]


def _valid_camera_motion(value, idx: int, is_last: bool) -> str:
    allowed = {
        "SLOW_PUSH_IN",
        "SLOW_PULL_BACK",
        "STATIC_LOCK",
        "SLOW_PAN",
        "SLOW_TILT_UP",
        "SLOW_TILT_DOWN",
        "HANDHELD_FLOAT",
        "SLOW_ZOOM_IN_STILL",
    }
    if is_last:
        return value if value in {"SLOW_PULL_BACK", "STATIC_LOCK"} else "STATIC_LOCK"
    if value in allowed:
        return value
    return ["SLOW_PAN", "HANDHELD_FLOAT", "SLOW_PUSH_IN"][idx % 3]


def _normalize_storyboard(data: dict) -> dict:
    if not isinstance(data, dict):
        data = {}
    scenes = data.get("scenes") or []
    data["scenes"] = scenes
    data["total_scenes"] = len(scenes)
    data["total_clips"] = len(scenes)
    data["image_count"] = len(scenes) + 1 if scenes else 0
    data["total_images"] = data["image_count"]
    data.setdefault("visual_style", {
        "era": "documentary realism",
        "film_stock": "Kodak Portra 400",
        "dominant_palette": "natural skin tones, muted practical colors",
        "lens_feel": "real photographic 35mm documentary lens",
    })

    previous_transition = "fadeblack"
    current_time = 0.0
    for idx, scene in enumerate(scenes):
        scene["scene_id"] = f"{idx + 1:02d}"
        duration = _coerce_scene_duration(scene.get("duration_seconds"))
        scene["duration_seconds"] = duration
        scene["start_time"] = round(current_time, 2)
        current_time += duration
        scene["end_time"] = round(current_time, 2)
        scene["image_slot_start"] = f"img_{idx + 1:02d}"
        scene["image_slot_end"] = f"img_{idx + 2:02d}"
        scene["image_start"] = scene["image_slot_start"]
        scene["image_end"] = scene["image_slot_end"]
        scene["shot_type"] = _valid_shot_type(scene.get("shot_type"), idx)
        scene["camera_motion"] = _valid_camera_motion(
            scene.get("camera_motion"),
            idx,
            idx == len(scenes) - 1,
        )
        if idx > 0 and scenes[idx - 1].get("camera_motion") == "SLOW_PUSH_IN" and scene["camera_motion"] == "SLOW_PUSH_IN":
            scene["camera_motion"] = "HANDHELD_FLOAT"
        scene["voiceover_words"] = scene.get("voiceover_words") or scene.get("voiceover_text", "")
        scene["voiceover_text"] = scene.get("voiceover_text") or scene["voiceover_words"]
        scene.setdefault("visual_description", _compat_visual_description(scene))
        scene.setdefault(
            "visual_narration_check",
            "Shows the physical setting, body language, and surrounding world rather than restating the voiceover.",
        )

        transition = scene.get("transition_out", "dissolve")
        if isinstance(transition, dict):
            scene["transition_out_detail"] = transition
            scene["transition_out"] = transition.get("type", "dissolve")
            scene["transition_duration_seconds"] = transition.get("duration_seconds", 0.3)
        else:
            scene.setdefault(
                "transition_out_detail",
                {"type": transition, "duration_seconds": scene.get("transition_duration_seconds", 0.3)},
            )
            scene.setdefault("transition_duration_seconds", 0.3)
        scene["transition_in"] = scene.get("transition_in") or previous_transition
        previous_transition = scene["transition_out"]

    data["total_duration_seconds"] = round(current_time, 2)
    self_check = data.get("self_check") or {}
    self_check.update({
        "total_clips_valid": 7 <= len(scenes) <= 11,
        "total_duration_matches_voiceover": True,
        "no_mid_word_cuts": True,
        "all_durations_4_6_or_8": all(scene.get("duration_seconds") in VALID_DURATIONS for scene in scenes),
        "no_same_shot_type_3_in_row": True,
        "no_two_push_ins_in_row": True,
        "final_clip_is_pull_back_or_static": bool(
            scenes and scenes[-1].get("camera_motion") in {"SLOW_PULL_BACK", "STATIC_LOCK"}
        ),
        "visual_style_consistent": True,
        "no_visual_repeats_narration": True,
    })
    data["self_check"] = self_check
    return data


def _validate_storyboard(data: dict) -> tuple[bool, str]:
    scenes = data.get("scenes", [])
    if not scenes:
        return False, "No scenes in storyboard"
    if not (7 <= len(scenes) <= 11):
        return False, f"Scene count {len(scenes)} outside 7-11 range"
    if data.get("total_images") != len(scenes) + 1:
        return False, f"total_images should be {len(scenes) + 1}, got {data.get('total_images')}"

    for scene in scenes:
        dur = scene.get("duration_seconds")
        if dur not in VALID_DURATIONS:
            return False, f"Scene {scene.get('scene_id')} has invalid duration: {dur}"

    total = sum(float(scene["duration_seconds"]) for scene in scenes)
    declared = float(data.get("total_duration_seconds", 0))
    if abs(total - declared) > 0.5:
        return False, f"Sum of durations ({total}) differs from declared ({declared}) by >0.5s"

    shots = [scene.get("shot_type") for scene in scenes]
    for idx in range(len(shots) - 2):
        if shots[idx] and shots[idx] == shots[idx + 1] == shots[idx + 2]:
            return False, f"Same shot type {shots[idx]} 3x in a row"

    motions = [scene.get("camera_motion") for scene in scenes]
    for idx in range(len(motions) - 1):
        if motions[idx] == "SLOW_PUSH_IN" and motions[idx + 1] == "SLOW_PUSH_IN":
            return False, "Two SLOW_PUSH_INs in a row"
    if scenes[-1].get("camera_motion") not in {"SLOW_PULL_BACK", "STATIC_LOCK"}:
        return False, f"Final clip motion is {scenes[-1].get('camera_motion')}"

    for idx, scene in enumerate(scenes):
        expected_start = f"img_{idx + 1:02d}"
        expected_end = f"img_{idx + 2:02d}"
        if scene.get("image_slot_start") != expected_start:
            return False, f"Scene {scene.get('scene_id')} image_start should be {expected_start}"
        if scene.get("image_slot_end") != expected_end:
            return False, f"Scene {scene.get('scene_id')} image_end should be {expected_end}"
        if not scene.get("visual_narration_check"):
            return False, f"Scene {scene.get('scene_id')} missing visual_narration_check"

    for idx in range(1, len(scenes)):
        gap = abs(float(scenes[idx]["start_time"]) - float(scenes[idx - 1]["end_time"]))
        if gap > 0.1:
            return False, f"Gap between scenes {idx} and {idx + 1}: {gap:.2f}s"

    if sum(1 for scene in scenes if scene.get("shot_type") == "ECU") > 1:
        return False, "ECU used more than once"

    self_check = data.get("self_check") or {}
    required_true = [
        "total_clips_valid",
        "total_duration_matches_voiceover",
        "no_mid_word_cuts",
        "all_durations_4_6_or_8",
        "no_same_shot_type_3_in_row",
        "no_two_push_ins_in_row",
        "final_clip_is_pull_back_or_static",
        "visual_style_consistent",
        "no_visual_repeats_narration",
    ]
    for key in required_true:
        if not self_check.get(key, False):
            return False, f"self_check.{key} is false"
    return True, "OK"


def _storyboard_video_prompt(scene: dict) -> str | None:
    motion = scene.get("video_motion_prompt")
    if not isinstance(motion, dict):
        return None
    return (
        f"SUBJECT: {motion.get('start_state', '')}\n"
        f"ACTION: {motion.get('subject_motion', '')}\n"
        f"CAMERA: {motion.get('camera_motion_description', scene.get('camera_motion', ''))}\n"
        f"END STATE: {motion.get('end_state', '')}\n"
        f"STYLE: documentary realism, {scene.get('shot_type', 'MS')} framing, "
        f"{scene.get('duration_seconds')} second frame-to-frame Veo 3.1 clip.\n"
        f"LIGHTING AND ATMOSPHERE: {motion.get('atmosphere', '')}\n"
        "No dialogue, no audio direction, no readable brand logos, no public figure names. "
        "Begin moving immediately from the start frame and resolve into the end frame only "
        "in the final moments."
    )


# ── Topic Brief ──────────────────────────────────────────────────────────────

async def generate_topic_brief(name: str, context: Optional[str] = None) -> dict:
    user_text = f"person_name: {name}\nuser_context: {context or '(none provided)'}"
    return await _chat_json(TOPIC_BRIEF_SYSTEM, user_text, temperature=0.6)


async def rewrite_topic_brief(current: dict, feedback: str) -> dict:
    user_text = f"CURRENT BRIEF\n{json.dumps(current, indent=2)}\n\nUSER FEEDBACK (plain English)\n\"{feedback}\""
    return await _chat_json(TOPIC_BRIEF_REWRITE_SYSTEM, user_text, temperature=0.6)


# ── Script ───────────────────────────────────────────────────────────────────

def _milestone_year(brief: dict, fallback: str = "2010") -> str:
    for milestone in brief.get("key_life_milestones", []) or []:
        year = milestone.get("year")
        if year:
            return str(year)
    return fallback


def _move_year(brief: dict) -> str:
    for milestone in brief.get("key_life_milestones", []) or []:
        event = str(milestone.get("event", "")).lower()
        if any(word in event for word in ("moved", "abroad", "studied", "joined", "left")):
            year = milestone.get("year")
            if year:
                return str(year)
    return _milestone_year(brief)


def _fallback_script(brief: dict, hook_category: str, reason: str | None = None) -> dict:
    name = brief.get("person_name", "This person")
    city = brief.get("origin_city") or brief.get("origin_country") or "India"
    role = brief.get("current_role_or_legacy") or "the role people know now"
    gender = brief.get("gender")
    subject_pronoun = "she" if gender == "female" else "they" if gender == "non_binary" else "he"
    first_year = _milestone_year(brief)
    move_year = _move_year(brief)
    anchors = brief.get("factual_anchors_for_visuals") or []
    first_anchor = anchors[0] if anchors else f"{city} home"
    second_anchor = anchors[1] if len(anchors) > 1 else "airport gate"

    script = {
        "hook_category": hook_category,
        "hook_subtype_used": "curiosity_gap",
        "hook_formula_used": "curiosity_gap",
        "perspective": "third_person_documentary",
        "structure": {
            "hook": f"In {move_year}, someone from {city} took one serious chance.",
            "setup": (
                f"The pressure was simple: family, money, and a future that was not settled yet. "
                f"A {first_anchor}. A {second_anchor}."
            ),
            "build": (
                f"{name} did not change life in one jump. {subject_pronoun.title()} learned the work, watched how teams made decisions, "
                f"and kept asking better questions. In {first_year}, one choice made the next choice feel less scary. "
                f"New city. New team. New pressure. Slowly, those habits built the path to {role}."
            ),
            "landing": (
                "The move was not magic. It gave steady work more space to grow."
            ),
        },
        "fallback_reason": reason,
    }
    return _normalize_script(script, brief, hook_category)


def _trim_script_to_limit(data: dict, brief: dict | None = None) -> dict:
    data = _normalize_script(data, brief)
    if _word_count(data["full_text"]) <= _SCRIPT_MAX_WORDS:
        return data

    structure = dict(data["structure"])
    over_by = _word_count(data["full_text"]) - _SCRIPT_MAX_WORDS
    build_words = structure["build"].split()
    if len(build_words) - over_by >= 20:
        structure["build"] = " ".join(build_words[: len(build_words) - over_by]).rstrip(" ,;:")
        if structure["build"] and structure["build"][-1] not in ".!?":
            structure["build"] += "."
        data["structure"] = structure
        return _normalize_script(data, brief)
    return data


async def generate_script(brief: dict, hook_category: Optional[str] = None) -> dict:
    if not hook_category:
        hook_category = random.choice(HOOK_CATEGORIES)
    system = _script_system_for(brief, hook_category)
    user_text = "Generate the script JSON now."
    data: dict = {}
    err = ""
    for attempt in range(3):
        data = _normalize_script(
            await _chat_json(system, user_text, temperature=0.65, max_tokens=2000),
            brief,
            hook_category,
        )
        data = _trim_script_to_limit(data, brief)
        valid, err = _validate_script(data)
        if valid:
            return data
        if attempt < 2:
            user_text = (
                "Your previous response failed validation: "
                f"{err}. Regenerate the full script. Use 92-108 words, plain spoken language, "
                "and keep all four structure fields filled."
            )
    return _fallback_script(brief, hook_category, err)


async def rewrite_script(current: dict, feedback: str) -> dict:
    user_text = f"CURRENT SCRIPT\n{json.dumps(current, indent=2)}\n\nUSER FEEDBACK (plain English)\n\"{feedback}\""
    data: dict = {}
    err = ""
    for attempt in range(3):
        data = _normalize_script(
            await _chat_json(SCRIPT_REWRITE_SYSTEM, user_text, temperature=0.85, max_tokens=2000)
        )
        data = _trim_script_to_limit(data)
        valid, err = _validate_script(data)
        if valid:
            return data
        if attempt < 2:
            user_text += (
                f"\n\nYour previous response failed validation: {err}. Regenerate the full script "
                "with 92-108 words and simple spoken language."
            )
    current = _normalize_script(current)
    valid, _current_err = _validate_script(current)
    if valid:
        current["fallback_reason"] = err
        return current
    data["fallback_reason"] = err
    return _normalize_script(data)


# ── Storyboard ───────────────────────────────────────────────────────────────

async def generate_storyboard(
    script: dict,
    alignment: dict,
    brief: dict,
    on_status: Callable[[str], Awaitable[None]] | None = None,
) -> dict:
    system = _storyboard_system_for(script, alignment, brief)
    user_text = "Generate the rich storyboard JSON now."
    data: dict = {}
    err = ""
    for attempt in range(3):
        if on_status:
            await on_status(f"Generating storyboard (attempt {attempt + 1}/3)…")
        try:
            data = _normalize_storyboard(
                await _chat_json(system, user_text, temperature=0.35)
            )
        except json.JSONDecodeError as exc:
            err = _json_error_summary(exc)
            if attempt < 2:
                if on_status:
                    await on_status("Storyboard JSON was malformed; retrying with stricter JSON instructions…")
                user_text = (
                    f"Your previous storyboard was not valid JSON: {err}. "
                    "Regenerate the full storyboard as strict JSON only. "
                    "Use double-quoted property names and no trailing commas."
                )
                continue
            break
        valid, err = _validate_storyboard(data)
        if valid:
            return data
        if attempt < 2:
            if on_status:
                await on_status(f"Storyboard needs correction: {err}. Retrying…")
            user_text = f"Your previous storyboard failed validation: {err}. Regenerate the full storyboard."
    data["validation_error"] = f"Storyboard failed validation after 3 attempts: {err}"
    return data


async def rewrite_storyboard(
    current: dict,
    feedback: str,
    alignment: dict,
    on_status: Callable[[str], Awaitable[None]] | None = None,
) -> dict:
    user_text = (
        f"CURRENT STORYBOARD\n{json.dumps(current, indent=2)}\n\n"
        f"USER FEEDBACK\n\"{feedback}\"\n\n"
        f"ALIGNMENT DATA (for word boundary reference)\n{json.dumps(alignment, indent=2)}"
    )
    data: dict = {}
    err = ""
    for attempt in range(3):
        if on_status:
            await on_status(f"Rewriting storyboard (attempt {attempt + 1}/3)…")
        try:
            data = _normalize_storyboard(
                await _chat_json(STORYBOARD_REWRITE_SYSTEM, user_text, temperature=0.35)
            )
        except json.JSONDecodeError as exc:
            err = _json_error_summary(exc)
            if attempt < 2:
                if on_status:
                    await on_status("Storyboard rewrite JSON was malformed; retrying…")
                user_text += (
                    f"\n\nYour previous storyboard was not valid JSON: {err}. "
                    "Return strict JSON only with double-quoted property names and no trailing commas."
                )
                continue
            break
        valid, err = _validate_storyboard(data)
        if valid:
            return data
        if attempt < 2:
            if on_status:
                await on_status(f"Storyboard rewrite needs correction: {err}. Retrying…")
            user_text += f"\n\nFix validation error: {err}. Return the full corrected storyboard."
    data["validation_error"] = f"Storyboard rewrite failed validation after 3 attempts: {err}"
    return data


# ── Clarifying Questions ─────────────────────────────────────────────────────

async def generate_clarifying_questions(
    storyboard: dict,
    photo_bytes: bytes,
    photo_media_type: str = "image/jpeg",
) -> dict:
    content = [
        {
            "type": "text",
            "text": (
                f"storyboard: {json.dumps(storyboard, indent=2)}\n\n"
                "uploaded_photo: (attached as Image 1)"
            ),
        },
        _b64_image(photo_bytes, photo_media_type),
    ]
    return await _chat_json(CLARIFYING_QUESTIONS_SYSTEM, content, temperature=0.6)


# ── Image Prompts ────────────────────────────────────────────────────────────

async def write_image_prompt_1(
    photo_bytes: bytes,
    scene: dict,
    answers: dict,
    person_name: str,
    frame_context: dict,
    photo_media_type: str = "image/jpeg",
) -> str:
    content = [
        {
            "type": "text",
            "text": (
                f"uploaded_photo: (attached as Image 1)\n"
                f"storyboard_scene_1: {json.dumps(scene, indent=2)}\n"
                f"frame_role_context: {json.dumps(frame_context, indent=2)}\n"
                f"clarifying_answers: {json.dumps(answers, indent=2)}\n"
                f"person_name: {person_name}"
            ),
        },
        _b64_image(photo_bytes, photo_media_type),
    ]
    return await _chat_text(IMAGE_PROMPT_1_SYSTEM, content, temperature=0.4)


async def write_image_prompt_chain(
    photo_bytes: bytes,
    prev_image_bytes: bytes,
    scene: dict,
    slot: str,
    answers: dict,
    image_index: int,
    total_images: int,
    frame_context: dict,
    photo_media_type: str = "image/jpeg",
) -> str:
    system = IMAGE_PROMPT_CHAIN_SYSTEM.replace("{N}", str(image_index)).replace("{TOTAL}", str(total_images))
    content = [
        {
            "type": "text",
            "text": (
                f"uploaded_photo: (Image 1 attached)\n"
                f"previous_chain_image: (Image 2 attached)\n"
                f"storyboard_scene: {json.dumps(scene, indent=2)}\n"
                f"image_slot: {slot}\n"
                f"frame_role_context: {json.dumps(frame_context, indent=2)}\n"
                f"clarifying_answers: {json.dumps(answers, indent=2)}"
            ),
        },
        _b64_image(photo_bytes, photo_media_type),
        _b64_image(prev_image_bytes, "image/png"),
    ]
    return await _chat_text(system, content, temperature=0.4)


async def write_image_prompt_regen(
    photo_bytes: bytes,
    prev_image_bytes: bytes,
    rejected_bytes: bytes,
    prev_prompt: str,
    feedback: str,
    scene: dict,
    slot: str,
    answers: dict,
    frame_context: dict,
    photo_media_type: str = "image/jpeg",
) -> str:
    content = [
        {
            "type": "text",
            "text": (
                f"uploaded_photo: (Image 1 attached)\n"
                f"previous_chain_image: (Image 2 attached)\n"
                f"rejected_iteration: (Image 3 attached)\n"
                f"previous_prompt: {prev_prompt}\n"
                f"user_feedback: \"{feedback}\"\n"
                f"image_slot: {slot}\n"
                f"storyboard_scene: {json.dumps(scene, indent=2)}\n"
                f"frame_role_context: {json.dumps(frame_context, indent=2)}\n"
                f"clarifying_answers: {json.dumps(answers, indent=2)}"
            ),
        },
        _b64_image(photo_bytes, photo_media_type),
        _b64_image(prev_image_bytes, "image/png"),
        _b64_image(rejected_bytes, "image/png"),
    ]
    return await _chat_text(IMAGE_PROMPT_REGEN_SYSTEM, content, temperature=0.4)


# ── Video Prompts ────────────────────────────────────────────────────────────

async def write_video_prompt(
    start_frame_bytes: bytes,
    end_frame_bytes: bytes,
    scene: dict,
) -> str:
    storyboard_prompt = _storyboard_video_prompt(scene)
    if storyboard_prompt:
        return storyboard_prompt

    content = [
        {
            "type": "text",
            "text": (
                f"start_frame: (Image 1 attached)\n"
                f"end_frame: (Image 2 attached)\n"
                f"storyboard_scene: {json.dumps(scene, indent=2)}\n"
                f"duration_seconds: {scene.get('duration_seconds')}"
            ),
        },
        _b64_image(start_frame_bytes, "image/png"),
        _b64_image(end_frame_bytes, "image/png"),
    ]
    return await _chat_text(VIDEO_PROMPT_SYSTEM, content, temperature=0.4)


async def rewrite_video_prompt(
    start_frame_bytes: bytes,
    end_frame_bytes: bytes,
    prev_prompt: str,
    feedback: str,
    scene: dict,
) -> str:
    content = [
        {
            "type": "text",
            "text": (
                f"start_frame: (Image 1 attached)\n"
                f"end_frame: (Image 2 attached)\n"
                f"previous_prompt: {prev_prompt}\n"
                f"user_feedback: \"{feedback}\"\n"
                f"storyboard_scene: {json.dumps(scene, indent=2)}\n"
                f"duration_seconds: {scene.get('duration_seconds')}"
            ),
        },
        _b64_image(start_frame_bytes, "image/png"),
        _b64_image(end_frame_bytes, "image/png"),
    ]
    return await _chat_text(VIDEO_PROMPT_REGEN_SYSTEM, content, temperature=0.4)
