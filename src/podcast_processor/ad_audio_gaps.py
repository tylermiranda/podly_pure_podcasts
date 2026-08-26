"""Detect suspicious audio-only regions via ffmpeg silencedetect."""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_SILENCE_START = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END = re.compile(
    r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)"
)


@dataclass(frozen=True)
class AudioRun:
    start: float
    end: float


def parse_silencedetect_output(stderr: str) -> list[tuple[float, float]]:
    """Return silence intervals (start, end) parsed from ffmpeg stderr."""
    silences: list[tuple[float, float]] = []
    pending_start: float | None = None
    for line in stderr.splitlines():
        start_match = _SILENCE_START.search(line)
        if start_match:
            pending_start = float(start_match.group(1))
            continue
        end_match = _SILENCE_END.search(line)
        if end_match and pending_start is not None:
            silences.append((pending_start, float(end_match.group(1))))
            pending_start = None
    return silences


def non_silent_runs(
    silences: list[tuple[float, float]], *, duration: float
) -> list[AudioRun]:
    if duration <= 0:
        return []
    if not silences:
        return [AudioRun(0.0, duration)]
    ordered = sorted(silences, key=lambda s: s[0])
    runs: list[AudioRun] = []
    cursor = 0.0
    for silence_start, silence_end in ordered:
        if silence_start > cursor:
            runs.append(AudioRun(cursor, silence_start))
        cursor = max(cursor, silence_end)
    if cursor < duration:
        runs.append(AudioRun(cursor, duration))
    return runs


def _segment_overlaps(
    seg_start: float, seg_end: float, run_start: float, run_end: float
) -> bool:
    return seg_start < run_end and seg_end > run_start


def _segment_has_transcript(seg: Any) -> bool:
    text = (getattr(seg, "text", None) or "").strip()
    return bool(text)


def detect_suspicious_gaps(
    *,
    audio_path: str,
    segments: list[Any],
    duration: float | None = None,
    min_seconds: float = 4.0,
    noise_db: int = -30,
    subprocess_run: Any = subprocess.run,
) -> list[tuple[float, float]]:
    """Non-silent runs with no overlapping transcript segment."""
    if not audio_path:
        return []
    episode_duration = duration
    if episode_duration is None and segments:
        episode_duration = float(getattr(segments[-1], "end_time", 0.0) or 0.0)
    if not episode_duration or episode_duration <= 0:
        return []

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        audio_path,
        "-af",
        f"silencedetect=noise={noise_db}dB:d=0.3",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess_run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("silencedetect failed for %s: %s", audio_path, exc)
        return []

    silences = parse_silencedetect_output(proc.stderr)
    runs = non_silent_runs(silences, duration=episode_duration)
    suspicious: list[tuple[float, float]] = []
    for run in runs:
        if run.end - run.start < min_seconds:
            continue
        has_transcript = False
        for seg in segments:
            seg_start = float(getattr(seg, "start_time", 0.0) or 0.0)
            seg_end = float(getattr(seg, "end_time", seg_start) or seg_start)
            if not _segment_overlaps(seg_start, seg_end, run.start, run.end):
                continue
            if _segment_has_transcript(seg):
                has_transcript = True
                break
        if not has_transcript:
            suspicious.append((run.start, run.end))
    return suspicious


def format_gap_lines(gaps: list[tuple[float, float]]) -> str:
    if not gaps:
        return "(none)"
    lines = []
    for start, end in gaps[:40]:
        lines.append(f"SUSPECT_GAP {start:.1f}-{end:.1f} (no transcript overlap)")
    return "\n".join(lines)
