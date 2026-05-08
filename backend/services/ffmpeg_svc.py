from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Callable, Optional

from services.session_svc import get_session_dir, load_json_asset

_FFMPEG_FULL_BIN = Path("/opt/homebrew/opt/ffmpeg-full/bin")


def _tool_path(env_name: str, binary: str) -> str:
    override = os.getenv(env_name)
    if override:
        return override
    full_binary = _FFMPEG_FULL_BIN / binary
    if full_binary.exists():
        return str(full_binary)
    return binary


_FFMPEG = _tool_path("FFMPEG_BIN", "ffmpeg")
_FFPROBE = _tool_path("FFPROBE_BIN", "ffprobe")

_DEFAULT_TRANSITION_SECONDS = 0.45
_MAX_AUDIO_VIDEO_DRIFT_SECONDS = 0.5

class FFmpegError(Exception):
    pass


def _escape_filter_path(path: Path) -> str:
    """Escape a path for use inside an FFmpeg filter argument."""
    return str(path.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


async def _run(cmd: list[str], log_path: Optional[Path] = None) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    stdout_text = stdout.decode(errors="replace")
    stderr_text = stderr.decode(errors="replace")
    if log_path:
        with open(log_path, "a") as f:
            f.write(f"\n{'=' * 60}\nCMD: {' '.join(cmd)}\n")
            if stdout_text:
                f.write(f"STDOUT:\n{stdout_text}\n")
            if stderr_text:
                f.write(f"STDERR:\n{stderr_text}\n")
    if proc.returncode != 0:
        raise FFmpegError(f"FFmpeg error (rc={proc.returncode}):\n{stderr_text[-2000:]}")
    return stdout_text or stderr_text


async def _has_filter(name: str) -> bool:
    filters = await _run([_FFMPEG, "-hide_banner", "-filters"])
    return any(line.split()[1:2] == [name] for line in filters.splitlines() if line.split())


async def _probe_duration(path: Path, log_path: Path) -> float:
    out = await _run([
        _FFPROBE, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ], log_path)
    return float(json.loads(out)["format"]["duration"])


def _transition_duration(scene: dict, default: float = _DEFAULT_TRANSITION_SECONDS) -> float:
    value = scene.get("transition_duration_seconds")
    detail = scene.get("transition_out_detail")
    if value is None and isinstance(detail, dict):
        value = detail.get("duration_seconds")
    try:
        duration = float(value if value is not None else default)
    except (TypeError, ValueError):
        duration = default
    return max(0.05, min(1.5, duration))


def planned_stitched_duration(storyboard: dict) -> float:
    """Return the post-xfade video duration implied by the storyboard."""
    scenes = storyboard.get("scenes") or []
    if not scenes:
        return 0.0
    clip_total = sum(float(scene.get("duration_seconds") or 0) for scene in scenes)
    transition_total = sum(_transition_duration(scene) for scene in scenes[:-1])
    return round(max(0.0, clip_total - transition_total), 3)


async def validate_assembly_timing(session_id: str) -> None:
    """Fail fast when the stitched visual plan cannot cover the approved voiceover."""
    session_dir = get_session_dir(session_id)
    storyboard = load_json_asset(session_id, "storyboard_approved.json")
    voiceover = session_dir / "voiceover_approved.mp3"
    log_path = session_dir / "logs" / "ffmpeg.log"

    if not voiceover.exists():
        raise FFmpegError("Missing approved voiceover: voiceover_approved.mp3")

    scenes = storyboard.get("scenes") or []
    for idx, scene in enumerate(scenes, start=1):
        if not (scene.get("voiceover_text") or scene.get("voiceover_words") or "").strip():
            raise FFmpegError(
                f"Storyboard scene {idx:02d} has no voiceover_text. "
                "Silent b-roll scenes cannot be assembled over narration."
            )

    voiceover_duration = await _probe_duration(voiceover, log_path)
    visual_duration = planned_stitched_duration(storyboard)
    drift = voiceover_duration - visual_duration
    if abs(drift) > _MAX_AUDIO_VIDEO_DRIFT_SECONDS:
        relation = "shorter" if drift > 0 else "longer"
        raise FFmpegError(
            "Storyboard timing mismatch: stitched visuals are "
            f"{abs(drift):.2f}s {relation} than the voiceover "
            f"(visual {visual_duration:.2f}s, voiceover {voiceover_duration:.2f}s). "
            "Regenerate or edit the storyboard so post-transition duration matches the approved narration."
        )


async def preflight_check(session_id: str) -> None:
    """Verify all approved clips exist with correct resolution, fps, and duration."""
    session_dir = get_session_dir(session_id)
    storyboard = load_json_asset(session_id, "storyboard_approved.json")
    log_path = session_dir / "logs" / "ffmpeg.log"

    for i, scene in enumerate(storyboard["scenes"], start=1):
        clip = session_dir / "videos" / f"clip_{i:02d}_approved.mp4"
        if not clip.exists():
            raise FFmpegError(f"Missing approved clip: {clip.name}")

        probe_cmd = [
            _FFPROBE, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate,duration",
            "-of", "json",
            str(clip),
        ]
        out = await _run(probe_cmd, log_path)
        info = json.loads(out)
        stream = info.get("streams", [{}])[0]

        w = stream.get("width")
        h = stream.get("height")
        fps_str = stream.get("r_frame_rate", "0/1")
        dur = float(stream.get("duration", 0))
        expected_dur = scene["duration_seconds"]

        if not w or not h or h <= w:
            raise FFmpegError(f"Clip {clip.name}: expected portrait video, got {w}×{h}")

        num, denom = fps_str.split("/")
        fps = int(num) / int(denom)
        if abs(fps - 24) > 0.5:
            raise FFmpegError(f"Clip {clip.name}: expected 24fps, got {fps:.2f}")

        if abs(dur - expected_dur) > 0.1:
            raise FFmpegError(
                f"Clip {clip.name}: expected {expected_dur}s, got {dur:.2f}s"
            )


async def normalize_clips(session_id: str) -> list[Path]:
    """Strip audio, normalize fps/timebase/resolution per clip. Returns list of normalized paths."""
    session_dir = get_session_dir(session_id)
    storyboard = load_json_asset(session_id, "storyboard_approved.json")
    scenes = storyboard["scenes"]
    tmp_dir = session_dir / "_tmp"
    tmp_dir.mkdir(exist_ok=True)
    log_path = session_dir / "logs" / "ffmpeg.log"
    normalized = []
    for i in range(1, len(scenes) + 1):
        src = session_dir / "videos" / f"clip_{i:02d}_approved.mp4"
        dst = tmp_dir / f"clip_{i:02d}_norm.mp4"
        filters = [
            "fps=24",
            "settb=1/24000",
            "scale=1080:1920:force_original_aspect_ratio=decrease",
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        ]
        with open(log_path, "a") as f:
            f.write(f"\nClip {i:02d} normalize: fps=24 scale=1080x1920 no trim no interpolation\n")
        await _run([
            _FFMPEG, "-y", "-i", str(src),
            "-an",
            "-vf", ",".join(filters),
            "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-tune", "film",
            "-pix_fmt", "yuv420p",
            str(dst),
        ], log_path)
        normalized.append(dst)
    return normalized


def _time_to_ass(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def generate_ass(session_id: str) -> Path:
    """Build ASS subtitle file from alignment data. Chunk 5–7 words per caption."""
    session_dir = get_session_dir(session_id)
    alignment = load_json_asset(session_id, "alignment.json")
    words = alignment.get("words", [])
    tmp_dir = session_dir / "_tmp"
    tmp_dir.mkdir(exist_ok=True)
    ass_path = tmp_dir / "captions.ass"

    CHUNK_MIN = 5
    CHUNK_MAX = 7

    # Group into sentence chunks at natural boundaries
    chunks = []
    chunk = []
    for word in words:
        chunk.append(word)
        text = word["text"].rstrip(",;")
        ends_sentence = text.endswith((".","!","?")) or word == words[-1]
        if len(chunk) >= CHUNK_MAX or (len(chunk) >= CHUNK_MIN and ends_sentence):
            chunks.append(chunk)
            chunk = []
    if chunk:
        chunks.append(chunk)

    header = """\
[Script Info]
Title: Instomator subtitles
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Inter,52,&H00FFFFFF,&H00000000,&H99000000,1,0,0,0,100,100,0,0,3,12,0,2,80,80,300,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dialogues = []
    for c in chunks:
        start = _time_to_ass(c[0]["start"])
        end = _time_to_ass(c[-1]["end"])
        text = " ".join(w["text"] for w in c)
        dialogues.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    ass_path.write_text(header + "\n".join(dialogues) + "\n")
    return ass_path


async def concat_with_transitions(session_id: str, normalized_clips: list[Path]) -> Path:
    """Stitch clips with the storyboard's normal xfade transitions."""
    session_dir = get_session_dir(session_id)
    tmp_dir = session_dir / "_tmp"
    log_path = session_dir / "logs" / "ffmpeg.log"
    storyboard = load_json_asset(session_id, "storyboard_approved.json")
    scenes = storyboard["scenes"]

    n = len(normalized_clips)
    out_path = tmp_dir / "concat.mp4"

    if n == 1:
        shutil.copy2(normalized_clips[0], out_path)
        return out_path

    # Build input args
    inputs = []
    for clip in normalized_clips:
        inputs += ["-i", str(clip)]

    filter_parts = []
    last_label = "[0:v]"
    durations = [await _probe_duration(clip, log_path) for clip in normalized_clips]
    cumulative_duration = durations[0]
    cumulative_transitions = 0.0

    for i in range(1, n):
        prev_scene = scenes[i - 1] if i - 1 < len(scenes) else {}
        transition_value = prev_scene.get("transition_out") or "dissolve"
        transition = (
            transition_value.get("type", "dissolve")
            if isinstance(transition_value, dict)
            else transition_value
        )
        trans_dur = _transition_duration(prev_scene)
        offset = cumulative_duration - cumulative_transitions - trans_dur
        out_label = f"[v{i:02d}]" if i < n - 1 else "[vout]"
        filter_parts.append(
            f"{last_label}[{i}:v]xfade=transition={transition}:duration={trans_dur:.4f}:offset={offset:.4f}{out_label}"
        )
        last_label = out_label
        cumulative_duration += durations[i]
        cumulative_transitions += trans_dur

    filter_complex = ";".join(filter_parts)

    await _run([
        _FFMPEG, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ], log_path)
    return out_path


async def burn_subtitles(session_id: str, concat_path: Path, ass_path: Path) -> Path:
    session_dir = get_session_dir(session_id)
    tmp_dir = session_dir / "_tmp"
    log_path = session_dir / "logs" / "ffmpeg.log"
    out_path = tmp_dir / "concat_subs.mp4"

    if not await _has_filter("subtitles"):
        raise FFmpegError("FFmpeg subtitles filter unavailable. Install/use ffmpeg-full with libass.")

    await _run([
        _FFMPEG, "-y", "-i", str(concat_path),
        "-vf", f"subtitles=filename='{_escape_filter_path(ass_path)}'",
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ], log_path)
    return out_path


async def layer_voiceover(session_id: str, video_path: Path) -> Path:
    session_dir = get_session_dir(session_id)
    voiceover = session_dir / "voiceover_approved.mp3"
    tmp_dir = session_dir / "_tmp"
    log_path = session_dir / "logs" / "ffmpeg.log"
    out_path = tmp_dir / "concat_with_audio.mp4"

    video_duration = await _probe_duration(video_path, log_path)
    audio_duration = await _probe_duration(voiceover, log_path)
    drift = audio_duration - video_duration
    if drift > _MAX_AUDIO_VIDEO_DRIFT_SECONDS:
        raise FFmpegError(
            "Voiceover is longer than the assembled video by "
            f"{drift:.2f}s (video {video_duration:.2f}s, voiceover {audio_duration:.2f}s). "
            "Refusing to cut off narration."
        )

    if drift > 0.05:
        pad = drift + 0.1
        await _run([
            _FFMPEG, "-y",
            "-i", str(video_path),
            "-i", str(voiceover),
            "-filter_complex",
            f"[0:v]tpad=stop_mode=clone:stop_duration={pad:.3f}[v];"
            "[1:a]loudnorm=I=-14:TP=-1.5:LRA=11[a]",
            "-map", "[v]",
            "-map", "[a]",
            "-t", f"{audio_duration:.3f}",
            "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-tune", "film",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ac", "1",
            str(out_path),
        ], log_path)
        return out_path

    await _run([
        _FFMPEG, "-y",
        "-i", str(video_path),
        "-i", str(voiceover),
        "-filter_complex", "[1:a]loudnorm=I=-14:TP=-1.5:LRA=11[a]",
        "-map", "0:v",
        "-map", "[a]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ac", "1",
        "-shortest",
        str(out_path),
    ], log_path)
    return out_path


async def final_encode(session_id: str, av_path: Path, version: int) -> Path:
    session_dir = get_session_dir(session_id)
    final_dir = session_dir / "final"
    final_dir.mkdir(exist_ok=True)
    log_path = session_dir / "logs" / "ffmpeg.log"
    out_path = final_dir / f"reel_v{version}.mp4"

    await _run([
        _FFMPEG, "-y", "-i", str(av_path),
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-tune", "film", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path),
    ], log_path)

    # Update symlink
    latest = final_dir / "reel_latest.mp4"
    if latest.exists():
        latest.unlink()
    shutil.copy2(out_path, latest)
    return out_path


async def run_assembly(
    session_id: str,
    version: int,
    status_callback: Optional[Callable] = None,
) -> Path:
    """Run the full 7-step FFmpeg assembly pipeline."""
    session_dir = get_session_dir(session_id)
    tmp_dir = session_dir / "_tmp"
    tmp_dir.mkdir(exist_ok=True)

    async def status(msg: str):
        if status_callback:
            await status_callback(msg)

    await status("Preparing video clips…")
    await validate_assembly_timing(session_id)
    await preflight_check(session_id)

    normalized = await normalize_clips(session_id)

    await status("Building subtitle file…")
    ass_path = generate_ass(session_id)

    await status("Stitching clips with transitions…")
    concat_path = await concat_with_transitions(session_id, normalized)

    await status("Burning subtitles…")
    subs_path = await burn_subtitles(session_id, concat_path, ass_path)

    await status("Mixing voiceover…")
    av_path = await layer_voiceover(session_id, subs_path)

    await status("Normalizing loudness…")  # already done in layer_voiceover

    await status("Encoding final MP4…")
    final_path = await final_encode(session_id, av_path, version)

    # Cleanup temp files
    shutil.rmtree(tmp_dir, ignore_errors=True)

    size_mb = final_path.stat().st_size / (1024 * 1024)
    duration_sec = await _probe_duration(final_path, session_dir / "logs" / "ffmpeg.log")
    await status(f"Done — {duration_sec:.0f} sec, {size_mb:.1f} MB")

    return final_path
