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
_MIN_JOIN_HEAD_TRIM_SECONDS = 1.00
_MIN_JOIN_HEAD_TRIM_RATIO = 0.30
_MAX_JOIN_HEAD_TRIM_SECONDS = 2.50
_JOIN_TAIL_TRIM_SECONDS = 0.45
_HIDDEN_JOIN_SECONDS = 0.12
_TRIM_SAMPLE_FPS = 4
_TRIM_SAMPLE_WIDTH = 96
_TRIM_SAMPLE_HEIGHT = 170
_HEAD_DEPARTURE_MAE = 45.0
_FRAME_SYNTHESIS_SPEED_FACTOR = 1.08
_MOTION_INTERPOLATION_FILTER = "minterpolate=fps=24:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"


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


async def _run_bytes(cmd: list[str], log_path: Optional[Path] = None) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    stderr_text = stderr.decode(errors="replace")
    if log_path:
        with open(log_path, "a") as f:
            f.write(f"\n{'=' * 60}\nCMD: {' '.join(cmd)}\n")
            if stderr_text:
                f.write(f"STDERR:\n{stderr_text}\n")
    if proc.returncode != 0:
        raise FFmpegError(f"FFmpeg error (rc={proc.returncode}):\n{stderr_text[-2000:]}")
    return stdout


def _mean_absolute_error(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / max(len(a), 1)


async def _detect_head_trim(src: Path, scene_duration: float, log_path: Path) -> float:
    max_trim = min(_MAX_JOIN_HEAD_TRIM_SECONDS, scene_duration * 0.60)
    min_trim = min(
        max(_MIN_JOIN_HEAD_TRIM_SECONDS, scene_duration * _MIN_JOIN_HEAD_TRIM_RATIO),
        max_trim,
    )
    sample_frames = int(max_trim * _TRIM_SAMPLE_FPS) + 2
    frame_size = _TRIM_SAMPLE_WIDTH * _TRIM_SAMPLE_HEIGHT

    data = await _run_bytes([
        _FFMPEG, "-v", "error",
        "-i", str(src),
        "-vf",
        (
            f"fps={_TRIM_SAMPLE_FPS},"
            f"scale={_TRIM_SAMPLE_WIDTH}:{_TRIM_SAMPLE_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={_TRIM_SAMPLE_WIDTH}:{_TRIM_SAMPLE_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
            "format=gray"
        ),
        "-frames:v", str(sample_frames),
        "-f", "rawvideo",
        "-",
    ], log_path)

    frames = [data[i:i + frame_size] for i in range(0, len(data), frame_size)]
    frames = [frame for frame in frames if len(frame) == frame_size]
    if not frames:
        return min_trim

    first = frames[0]
    selected = max_trim
    for idx, frame in enumerate(frames[1:], start=1):
        sample_time = idx / _TRIM_SAMPLE_FPS
        if sample_time < min_trim:
            continue
        if _mean_absolute_error(first, frame) >= _HEAD_DEPARTURE_MAE:
            selected = min(sample_time, max_trim)
            break
    return selected


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
    voiceover = session_dir / "voiceover_approved.mp3"
    scene_total = sum(float(scene["duration_seconds"]) for scene in scenes)
    join_total = _HIDDEN_JOIN_SECONDS * max(len(scenes) - 1, 0)
    voice_duration = await _probe_duration(voiceover, log_path)
    extra_per_clip = max(voice_duration + join_total - scene_total, 0) / max(len(scenes), 1)
    can_interpolate = await _has_filter("minterpolate")

    normalized = []
    for i in range(1, len(scenes) + 1):
        src = session_dir / "videos" / f"clip_{i:02d}_approved.mp4"
        dst = tmp_dir / f"clip_{i:02d}_norm.mp4"
        scene_duration = float(scenes[i - 1]["duration_seconds"])
        trim_start = await _detect_head_trim(src, scene_duration, log_path) if i > 1 else 0.0
        trim_end = min(_JOIN_TAIL_TRIM_SECONDS, scene_duration * 0.12) if i < len(scenes) else 0.0
        trim_end_time = scene_duration - trim_end
        target_duration = scene_duration + extra_per_clip
        source_duration = trim_end_time - trim_start
        if source_duration <= 0:
            raise FFmpegError(f"Clip {src.name}: trim window removed the whole clip")
        speed_factor = target_duration / source_duration
        frame_rate_filter = (
            _MOTION_INTERPOLATION_FILTER
            if can_interpolate and speed_factor > _FRAME_SYNTHESIS_SPEED_FACTOR
            else "fps=24"
        )
        filters = [
            f"trim=start={trim_start:.4f}:end={trim_end_time:.4f}",
            f"setpts=(PTS-STARTPTS)*{speed_factor:.8f}",
            frame_rate_filter,
            "settb=1/24000",
            "scale=1080:1920:force_original_aspect_ratio=decrease",
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        ]
        with open(log_path, "a") as f:
            f.write(
                f"\nClip {i:02d} trim: start={trim_start:.2f}s "
                f"tail={trim_end:.2f}s source={source_duration:.2f}s "
                f"target={target_duration:.2f}s speed_factor={speed_factor:.4f} "
                f"frame_rate_filter={frame_rate_filter.split('=')[0]}\n"
            )
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
    """Trim duplicated boundary beats and stitch with a tiny hidden blend."""
    session_dir = get_session_dir(session_id)
    tmp_dir = session_dir / "_tmp"
    log_path = session_dir / "logs" / "ffmpeg.log"

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
        trans_dur = _HIDDEN_JOIN_SECONDS
        offset = cumulative_duration - cumulative_transitions - trans_dur
        out_label = f"[v{i:02d}]" if i < n - 1 else "[vout]"
        filter_parts.append(
            f"{last_label}[{i}:v]xfade=transition=fade:duration={trans_dur:.4f}:offset={offset:.4f}{out_label}"
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


async def extend_to_voiceover(session_id: str, video_path: Path) -> Path:
    """Hold the final frame if transitions make the picture shorter than the VO."""
    session_dir = get_session_dir(session_id)
    voiceover = session_dir / "voiceover_approved.mp3"
    tmp_dir = session_dir / "_tmp"
    log_path = session_dir / "logs" / "ffmpeg.log"
    out_path = tmp_dir / "concat_extended.mp4"

    video_duration = await _probe_duration(video_path, log_path)
    voice_duration = await _probe_duration(voiceover, log_path)
    hold_duration = voice_duration - video_duration

    if hold_duration <= 0.05:
        shutil.copy2(video_path, out_path)
        return out_path

    await _run([
        _FFMPEG, "-y", "-i", str(video_path),
        "-vf", f"tpad=stop_mode=clone:stop_duration={hold_duration + 0.05:.4f}",
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
    await preflight_check(session_id)

    normalized = await normalize_clips(session_id)

    await status("Building subtitle file…")
    ass_path = generate_ass(session_id)

    await status("Stitching clips with transitions…")
    concat_path = await concat_with_transitions(session_id, normalized)
    concat_path = await extend_to_voiceover(session_id, concat_path)

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
