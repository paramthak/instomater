"""Manual QA harness for the single-image-per-clip generation path.

Takes a passing storyboard from qa_storyboard_e2e and runs scene 1 through:
  write_image_prompt → gemini_svc.generate_image (img_01, the SOLE anchor)
  write_video_prompt → gemini_svc.run_video_job (clip_01.mp4, start frame only)

Outputs to ``backend/sessions/<id>/qa_clip/``. Veo drives motion from the
prompt + start frame alone; there is no end-frame anchor in this flow.

Run: ``python -m tests.qa_clip_e2e <session_id>`` from ``backend/``.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import openai_svc, gemini_svc  # noqa: E402


async def main(session_id: str) -> int:
    sess = ROOT / "sessions" / session_id
    if not sess.exists():
        print(f"Session not found: {sess}")
        return 1

    storyboard_path = sess / "qa_storyboard.json"
    if not storyboard_path.exists():
        print(f"No qa_storyboard.json in {sess.name}. Run qa_storyboard_e2e first.")
        return 1
    storyboard = json.loads(storyboard_path.read_text())
    if storyboard.get("validation_error"):
        print(f"Storyboard validation_error: {storyboard['validation_error']}")
        return 1

    photo_candidates = list(sess.glob("uploaded_photo.*"))
    if not photo_candidates:
        print(f"No uploaded_photo in {sess.name}.")
        return 1
    photo_path = photo_candidates[0]
    photo_bytes = photo_path.read_bytes()
    photo_mime = f"image/{'jpeg' if photo_path.suffix in ('.jpg', '.jpeg') else photo_path.suffix.lstrip('.')}"

    metadata = json.loads((sess / "metadata.json").read_text()) if (sess / "metadata.json").exists() else {}
    person_name = metadata.get("person_name", "the subject")

    out_dir = sess / "qa_clip"
    out_dir.mkdir(exist_ok=True)

    scene = storyboard["scenes"][0]
    print(f"Scene 1 setting: {scene['setting_category']} / {scene['location_anchor']}")
    print(f"Scene 1 voiceover: {scene['voiceover_text'][:100]}…")
    print(f"Scene 1 duration: {scene['duration_seconds']}s")
    print(f"Scene 1 face_reference_mode: {scene.get('face_reference_mode')}")

    # ── img_01: the single anchor frame ───────────────────────────────────
    print("\n[1/3] Writing img_01 prompt (single-image-per-clip)…")
    img_prompt = await openai_svc.write_image_prompt(
        photo_bytes, scene, person_name, photo_mime,
    )
    (out_dir / "img_01_prompt.txt").write_text(img_prompt)
    print(f"  saved img_01_prompt.txt ({len(img_prompt)} chars)")

    print("[2/3] Generating img_01 via Gemini Imagen…")
    img_bytes = await gemini_svc.generate_image(
        img_prompt, [photo_bytes], [photo_mime],
    )
    (out_dir / "img_01.png").write_bytes(img_bytes)
    print(f"  saved img_01.png ({len(img_bytes)} bytes)")

    # ── clip_01: Veo motion from the single anchor frame ──────────────────
    print("\n[3/3] Writing video prompt + running Veo (this takes ~30-60s)…")
    video_prompt = await openai_svc.write_video_prompt(img_bytes, scene)
    (out_dir / "clip_01_prompt.txt").write_text(video_prompt)
    print(f"  saved clip_01_prompt.txt ({len(video_prompt)} chars)")

    async def status_cb(msg: str):
        print(f"    Veo: {msg}")

    try:
        video_bytes = await gemini_svc.run_video_job(
            prompt=video_prompt,
            start_frame_bytes=img_bytes,
            duration_seconds=scene["duration_seconds"],
            model_variant="fast",
            status_callback=status_cb,
        )
    except Exception as exc:
        print(f"  Veo failed: {type(exc).__name__}: {exc}")
        return 2

    (out_dir / "clip_01.mp4").write_bytes(video_bytes)
    print(f"  saved clip_01.mp4 ({len(video_bytes)} bytes)")

    print("\n" + "=" * 70)
    print(f"Output: {out_dir}")
    print("Inspect: open img_01.png and clip_01.mp4 side by side.")
    print("Verify: clip animates the anchor pose forward and backward without")
    print("teleporting or morphing identity. There is no end-frame anchor.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m tests.qa_clip_e2e <session_id>")
        sys.exit(1)
    sys.exit(asyncio.run(main(sys.argv[1])))
