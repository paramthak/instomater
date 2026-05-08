"""Manual QA harness for the rewritten storyboard pipeline.

Calls the real OpenAI API. Loads existing sessions whose voiceover is ≤90s
(filter rule per the rebuild plan), generates a fresh storyboard with the new
GPT-5.4 + Structured Outputs path, and prints a structured pass/fail report.

Run with: ``python -m tests.qa_storyboard_e2e`` from ``backend/``.

Stop condition: 3 consecutive first-try storyboard successes across distinct
test sessions, with no Python normalisation patches required.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import sys
import unicodedata
from pathlib import Path

# Allow running directly from backend/.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import openai_svc  # noqa: E402

MAX_TEST_DURATION = 90.0
SETTING_TOLERANCE = 0.5
TRANSITION_DEFAULT = 0.45


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _list_eligible_sessions(sessions_dir: Path) -> list[tuple[float, Path]]:
    out: list[tuple[float, Path]] = []
    for align_path in sessions_dir.rglob("alignment.json"):
        try:
            alignment = json.loads(align_path.read_text())
            words = alignment.get("words") or []
            if not words:
                continue
            duration = max(float(w.get("end") or 0) for w in words)
            if duration > MAX_TEST_DURATION:
                continue
            session_dir = align_path.parent
            if not (session_dir / "script_approved.json").exists():
                continue
            out.append((duration, session_dir))
        except Exception as exc:  # noqa: BLE001
            print(f"skip {align_path}: {exc}")
    out.sort()
    return out


def _pretty_words_per_scene(scene: dict, alignment_words: list[dict]) -> str:
    voice = scene.get("voiceover_text", "")
    voice_tokens = [_normalize(w) for w in voice.split() if _normalize(w)]
    if not voice_tokens:
        return "<empty>"
    norm_align = [_normalize(w.get("text", "")) for w in alignment_words]
    # Find first matching token and slice
    try:
        start_idx = norm_align.index(voice_tokens[0])
    except ValueError:
        return f"voiceover_text starts with token '{voice_tokens[0]}' but not in alignment"
    end_idx = start_idx + len(voice_tokens)
    if end_idx > len(alignment_words):
        return "voiceover extends past alignment"
    start_t = alignment_words[start_idx].get("start", 0)
    end_t = alignment_words[min(end_idx, len(alignment_words)) - 1].get("end", 0)
    return f"words[{start_idx + 1}:{end_idx}] ({start_t:.2f}-{end_t:.2f}s)"


def _check_storyboard(storyboard: dict, alignment: dict) -> dict:
    """Return a dict of checks: each value is (bool_passed, detail_string)."""
    scenes = storyboard.get("scenes") or []
    words = alignment.get("words") or []
    audio_duration = max(float(w.get("end") or 0) for w in words) if words else 0.0
    norm_align_tokens = [_normalize(w.get("text", "")) for w in words]
    norm_align_tokens = [t for t in norm_align_tokens if t]

    checks: dict = {}

    # 1. Validation error from openai_svc?
    checks["no_validation_error"] = (
        not storyboard.get("validation_error"),
        storyboard.get("validation_error", "ok"),
    )

    # 2. Scene count + 4/6/8 durations
    durations = [s.get("duration_seconds") for s in scenes]
    checks["all_durations_4_6_8"] = (
        all(d in {4, 6, 8} for d in durations),
        f"durations: {durations}",
    )

    # 3. Stitched timing within ±0.5s of audio
    raw_sum = sum(durations)
    transition_total = sum(
        float((s.get("transition_out") or {}).get("duration_seconds") or TRANSITION_DEFAULT)
        for s in scenes[:-1]
    )
    stitched = raw_sum - transition_total
    drift = abs(stitched - audio_duration)
    checks["stitched_timing_within_tolerance"] = (
        drift <= SETTING_TOLERANCE,
        f"stitched={stitched:.2f}s audio={audio_duration:.2f}s drift={drift:.2f}s",
    )

    # 4. Voiceover word coverage — concat all voiceover_text tokens, must equal alignment tokens in order
    sb_tokens: list[str] = []
    for scene in scenes:
        sb_tokens.extend(_normalize(w) for w in (scene.get("voiceover_text") or "").split())
    sb_tokens = [t for t in sb_tokens if t]
    coverage_match = sb_tokens == norm_align_tokens
    if coverage_match:
        cov_detail = f"all {len(norm_align_tokens)} alignment words covered exactly"
    else:
        # Find first divergence
        diverge_at = next(
            (i for i in range(min(len(sb_tokens), len(norm_align_tokens)))
             if sb_tokens[i] != norm_align_tokens[i]),
            min(len(sb_tokens), len(norm_align_tokens)),
        )
        cov_detail = (
            f"diverge at token {diverge_at + 1}: sb={sb_tokens[diverge_at] if diverge_at < len(sb_tokens) else '<end>'} "
            f"vs align={norm_align_tokens[diverge_at] if diverge_at < len(norm_align_tokens) else '<end>'} "
            f"(sb={len(sb_tokens)} tokens, align={len(norm_align_tokens)})"
        )
    checks["voiceover_covers_alignment"] = (coverage_match, cov_detail)

    # 5. Adjacent scenes: distinct setting_category AND distinct location_anchor
    cat_seq = [s.get("setting_category", "") for s in scenes]
    anchor_seq = [s.get("location_anchor", "") for s in scenes]
    adj_cat_clash = next(
        (i for i in range(1, len(cat_seq)) if cat_seq[i] and cat_seq[i] == cat_seq[i - 1]),
        None,
    )
    adj_anchor_clash = next(
        (i for i in range(1, len(anchor_seq)) if anchor_seq[i] and anchor_seq[i] == anchor_seq[i - 1]),
        None,
    )
    checks["no_adjacent_setting_repeat"] = (
        adj_cat_clash is None and adj_anchor_clash is None,
        f"category_seq={cat_seq}; first adj cat clash idx={adj_cat_clash}; first adj anchor clash idx={adj_anchor_clash}",
    )

    # 6. Distinct setting categories ≥ max(5, ceil(scenes/2))
    distinct = len(set(c for c in cat_seq if c))
    needed = max(5, math.ceil(len(scenes) / 2))
    checks["distinct_setting_count"] = (
        distinct >= min(needed, len(scenes)),
        f"distinct={distinct} needed≥{min(needed, len(scenes))}",
    )

    # 7. Shot type rules — no 3 in a row, ECU max once
    shots = [s.get("shot_type", "") for s in scenes]
    three_in_row = any(
        shots[i] and shots[i] == shots[i + 1] == shots[i + 2]
        for i in range(len(shots) - 2)
    )
    ecu_count = sum(1 for s in shots if s == "ECU")
    checks["shot_rotation"] = (
        not three_in_row and ecu_count <= 1,
        f"shots={shots}; three_in_row={three_in_row}; ecu_count={ecu_count}",
    )

    # 8. Final clip motion is SLOW_PULL_BACK or STATIC_LOCK
    last_motion = scenes[-1].get("camera_motion", "") if scenes else ""
    checks["final_clip_motion_settles"] = (
        last_motion in {"SLOW_PULL_BACK", "STATIC_LOCK"},
        f"final camera_motion={last_motion}",
    )

    # 9. Final scene contains the final spoken words
    if scenes and norm_align_tokens:
        final_voice = [_normalize(w) for w in (scenes[-1].get("voiceover_text") or "").split()]
        final_voice = [t for t in final_voice if t]
        last_n = norm_align_tokens[-len(final_voice):] if final_voice else []
        checks["final_scene_has_final_words"] = (
            final_voice == last_n,
            f"final voice {len(final_voice)} tokens; last alignment slice match: {final_voice == last_n}",
        )

    # 10. Single-image-per-clip schema: every scene has image_slot, image_description
    #     (with all required sub-fields), motion_arc, face_reference_mode (valid enum).
    #     None of the legacy paired-frame fields may be present.
    REQUIRED_IMG_DESC = {
        "subject_and_pose", "environment", "camera_framing", "lighting",
        "color_palette", "era_constraints", "camera_angle",
        "no_text_displays", "realism_directive",
    }
    REQUIRED_MOTION_ARC = {"camera_move", "subject_action", "traversal", "era_atmosphere"}
    VALID_FACE_MODES = {"match_age", "age_down_to", "skip_face_ref"}
    LEGACY_FIELDS = {
        "image_slot_start", "image_slot_end",
        "image_start_description", "image_end_description",
        "video_motion_prompt",
    }
    schema_failures: list[str] = []
    for scene in scenes:
        sid = scene.get("scene_id")
        if not scene.get("image_slot"):
            schema_failures.append(f"{sid}: missing image_slot")
        face_mode = scene.get("face_reference_mode")
        if face_mode not in VALID_FACE_MODES:
            schema_failures.append(f"{sid}: face_reference_mode={face_mode!r} not in {VALID_FACE_MODES}")
        if face_mode == "age_down_to" and not isinstance(scene.get("face_reference_target_age"), int):
            schema_failures.append(f"{sid}: age_down_to but no face_reference_target_age int")
        img_desc = scene.get("image_description") or {}
        missing = REQUIRED_IMG_DESC - set(img_desc.keys())
        if missing:
            schema_failures.append(f"{sid}: image_description missing {missing}")
        arc = scene.get("motion_arc") or {}
        missing_arc = REQUIRED_MOTION_ARC - set(arc.keys())
        if missing_arc:
            schema_failures.append(f"{sid}: motion_arc missing {missing_arc}")
        leaked = LEGACY_FIELDS & set(scene.keys())
        if leaked:
            schema_failures.append(f"{sid}: legacy fields present: {leaked}")
    checks["single_image_schema_valid"] = (
        not schema_failures,
        f"failures: {schema_failures}" if schema_failures else "all scenes conform to single-image schema",
    )

    return checks


def _emit_report(label: str, storyboard: dict, alignment: dict) -> bool:
    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")
    if storyboard.get("validation_error"):
        print(f"VALIDATION_ERROR: {storyboard['validation_error']}")
    scenes = storyboard.get("scenes") or []
    words = alignment.get("words") or []
    print(f"Scenes: {len(scenes)}  Audio: {(max(float(w.get('end') or 0) for w in words) if words else 0):.2f}s")
    print(f"Settings: {' → '.join(s.get('setting_category', '?') for s in scenes)}")
    print(f"Anchors:  {' → '.join(s.get('location_anchor', '?')[:30] for s in scenes)}")
    print(f"Shots:    {' → '.join(s.get('shot_type', '?') for s in scenes)}")
    print(f"Motion:   {' → '.join(s.get('camera_motion', '?') for s in scenes)}")
    print(f"Durations:{' → '.join(str(s.get('duration_seconds', '?')) for s in scenes)}")

    print("\nPer-scene voiceover_text mapping:")
    for s in scenes:
        sid = s.get("scene_id")
        dur = s.get("duration_seconds")
        text = s.get("voiceover_text", "")
        mapping = _pretty_words_per_scene(s, words)
        print(f"  {sid} ({dur}s) {mapping}: {text[:80]}{'…' if len(text) > 80 else ''}")

    print("\nSingle-image anchor + motion arc (first 3 scenes):")
    for s in scenes[:3]:
        sid = s.get("scene_id")
        img_desc = s.get("image_description") or {}
        arc = s.get("motion_arc") or {}
        env = img_desc.get("environment", "")[:80]
        angle = img_desc.get("camera_angle", "")
        face_mode = s.get("face_reference_mode", "?")
        target_age = s.get("face_reference_target_age")
        action = arc.get("subject_action", "")[:60]
        traversal = arc.get("traversal", "")[:60]
        print(f"  {sid} image_slot={s.get('image_slot')} face={face_mode} target_age={target_age}")
        print(f"  {sid} environment[{angle}]: {env}")
        print(f"  {sid} subject_action: {action}")
        print(f"  {sid} traversal: {traversal}")

    print("\nSelf-check (model-reported):")
    print(f"  timing_calculation: {storyboard.get('timing_calculation', '<missing>')}")
    print(f"  setting_plan:       {storyboard.get('setting_plan', '<missing>')}")
    print(f"  word_coverage_check: {storyboard.get('word_coverage_check', '<missing>')}")

    checks = _check_storyboard(storyboard, alignment)
    print("\nAutomated checks:")
    all_passed = True
    for name, (passed, detail) in checks.items():
        marker = "PASS" if passed else "FAIL"
        if not passed:
            all_passed = False
        print(f"  [{marker}] {name}: {detail}")

    print(f"\nVERDICT: {'✓ ALL PASS' if all_passed else '✗ FAILURES'}")
    return all_passed


async def run_one(session_dir: Path) -> tuple[str, bool, dict]:
    script = json.loads((session_dir / "script_approved.json").read_text())
    alignment = json.loads((session_dir / "alignment.json").read_text())
    storyboard = await openai_svc.generate_storyboard(script, alignment)
    label = f"Session: {session_dir.name}"
    passed = _emit_report(label, storyboard, alignment)
    return session_dir.name, passed, storyboard


async def main() -> int:
    sessions_dir = ROOT / "sessions"
    eligible = _list_eligible_sessions(sessions_dir)
    if not eligible:
        print("No eligible sessions (alignment + script_approved + audio ≤ 90s). Exiting.")
        return 1

    print(f"Eligible sessions (≤{MAX_TEST_DURATION:.0f}s):")
    for dur, path in eligible:
        print(f"  {dur:.2f}s  {path.name}")

    targets = [path for _, path in eligible[:3]]
    results: list[tuple[str, bool]] = []
    for path in targets:
        try:
            name, passed, sb = await run_one(path)
            results.append((name, passed))
            # Persist for inspection
            out_path = path / "qa_storyboard.json"
            out_path.write_text(json.dumps(sb, indent=2))
            print(f"  → wrote {out_path}")
        except Exception as exc:  # noqa: BLE001
            print(f"\nERROR for {path.name}: {type(exc).__name__}: {exc}")
            results.append((path.name, False))

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for name, passed in results:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
    pass_count = sum(1 for _, p in results if p)
    print(f"\n{pass_count}/{len(results)} first-try storyboard successes.")
    return 0 if pass_count == len(results) else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
