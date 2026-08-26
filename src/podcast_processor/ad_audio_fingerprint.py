"""Chromaprint audio fingerprint index for repeated ad audio."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def fpcalc_available() -> bool:
    return shutil.which("fpcalc") is not None


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def fingerprint_window(
    audio_path: str,
    start: float,
    end: float,
    *,
    subprocess_run: Any = subprocess.run,
    subprocess_popen: Any = subprocess.Popen,
) -> str | None:
    """Return raw Chromaprint fingerprint string for [start, end) seconds."""
    duration = max(0.0, end - start)
    if duration <= 0:
        return None
    if not os.path.isfile(audio_path):
        logger.warning("audio file not found for fingerprint: %s", audio_path)
        return None
    if not fpcalc_available():
        logger.warning("fpcalc not found on PATH; audio fingerprinting disabled")
        return None
    if not ffmpeg_available():
        logger.warning("ffmpeg not found on PATH; audio fingerprinting disabled")
        return None

    ffmpeg_cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(max(0.0, start)),
        "-t",
        str(duration),
        "-i",
        audio_path,
        "-f",
        "wav",
        "pipe:1",
    ]
    try:
        ffmpeg_proc = subprocess_popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if ffmpeg_proc.stdout is None:
            logger.warning("ffmpeg did not provide stdout for %s", audio_path)
            return None
        fpcalc_proc = subprocess_run(
            ["fpcalc", "-raw", "-"],
            stdin=ffmpeg_proc.stdout,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        ffmpeg_proc.stdout.close()
        ffmpeg_stderr = ""
        if ffmpeg_proc.stderr is not None:
            ffmpeg_stderr = ffmpeg_proc.stderr.read().decode(errors="replace")
        ffmpeg_rc = ffmpeg_proc.wait(timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("fingerprint pipeline failed for %s: %s", audio_path, exc)
        return None

    if ffmpeg_rc != 0:
        logger.warning(
            "ffmpeg segment extract failed for %s: %s",
            audio_path,
            ffmpeg_stderr.strip() or f"exit {ffmpeg_rc}",
        )
        return None
    if fpcalc_proc.returncode != 0:
        logger.warning(
            "fpcalc failed for %s: %s",
            audio_path,
            (fpcalc_proc.stderr or "").strip() or f"exit {fpcalc_proc.returncode}",
        )
        return None
    for line in fpcalc_proc.stdout.splitlines():
        if line.startswith("FINGERPRINT="):
            return line.split("=", 1)[1].strip()
    return None


def _parse_fingerprint(raw: str) -> list[int]:
    return [int(x) for x in raw.split(",") if x.strip()]


def fingerprint_distance(left: str, right: str) -> float:
    """Normalized Hamming distance in [0, 1] between two raw fingerprints."""
    a_vals = _parse_fingerprint(left)
    b_vals = _parse_fingerprint(right)
    if not a_vals or not b_vals:
        return 1.0
    n = min(len(a_vals), len(b_vals))
    bits_diff = sum((a_vals[i] ^ b_vals[i]).bit_count() for i in range(n))
    total_bits = n * 32
    return bits_diff / total_bits if total_bits else 1.0


def match_fingerprints(
    query: str,
    catalog: list[Any],
    *,
    threshold: float = 0.15,
) -> list[Any]:
    hits: list[Any] = []
    for row in catalog:
        stored = getattr(row, "fingerprint", None) or ""
        if not stored:
            continue
        if fingerprint_distance(query, stored) <= threshold:
            hits.append(row)
    return hits


def load_feed_fingerprints(
    *,
    feed_id: int,
    kind: str | None = None,
    prompt_tag_id: int | None = None,
    limit: int = 200,
) -> list[Any]:
    from app.extensions import db
    from app.models import AdAudioFingerprint

    query = db.session.query(AdAudioFingerprint).filter(
        AdAudioFingerprint.feed_id == feed_id
    )
    if kind is not None:
        query = query.filter(AdAudioFingerprint.kind == kind)
    if prompt_tag_id is not None:
        query = query.filter(
            (AdAudioFingerprint.prompt_tag_id == prompt_tag_id)
            | (AdAudioFingerprint.prompt_tag_id.is_(None))
        )
    return (
        query.order_by(
            AdAudioFingerprint.hit_count.desc(),
            AdAudioFingerprint.updated_at.desc(),
        )
        .limit(limit)
        .all()
    )


def upsert_fingerprint(
    *,
    feed_id: int,
    fingerprint: str,
    duration_seconds: float,
    kind: str = "creative",
    source_post_id: int | None = None,
    source_start: float | None = None,
    source_end: float | None = None,
    prompt_tag_id: int | None = None,
    commit: bool = True,
) -> bool:
    from app.extensions import db
    from app.models import AdAudioFingerprint

    if not fingerprint.strip():
        return False
    now = datetime.now(UTC).replace(tzinfo=None)
    existing = (
        db.session.query(AdAudioFingerprint)
        .filter_by(feed_id=feed_id, fingerprint=fingerprint, kind=kind)
        .first()
    )
    if existing is None:
        row = AdAudioFingerprint(
            feed_id=feed_id,
            prompt_tag_id=prompt_tag_id,
            kind=kind,
            fingerprint=fingerprint,
            duration_seconds=duration_seconds,
            source_post_id=source_post_id,
            source_start=source_start,
            source_end=source_end,
            hit_count=1,
            created_at=now,
            updated_at=now,
        )
        db.session.add(row)
    else:
        existing.hit_count = int(existing.hit_count or 0) + 1
        existing.updated_at = now
        if source_post_id is not None:
            existing.source_post_id = source_post_id
        if source_start is not None:
            existing.source_start = source_start
        if source_end is not None:
            existing.source_end = source_end
        if prompt_tag_id is not None and existing.prompt_tag_id is None:
            existing.prompt_tag_id = prompt_tag_id
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return True


def upsert_from_cut_windows(
    *,
    post: Any,
    windows: list[tuple[float, float]],
    audio_path: str | None,
    kind: str = "creative",
    min_duration: float = 3.0,
    subprocess_run: Any = subprocess.run,
) -> int:
    feed_id = getattr(post, "feed_id", None)
    if feed_id is None or not windows or not audio_path:
        return 0
    feed = getattr(post, "feed", None)
    prompt_tag_id = getattr(feed, "prompt_tag_id", None) if feed is not None else None
    touched = 0
    for start, end in windows:
        if end - start < min_duration:
            continue
        fp = fingerprint_window(audio_path, start, end, subprocess_run=subprocess_run)
        if not fp:
            continue
        if upsert_fingerprint(
            feed_id=int(feed_id),
            fingerprint=fp,
            duration_seconds=end - start,
            kind=kind,
            source_post_id=getattr(post, "id", None),
            source_start=start,
            source_end=end,
            prompt_tag_id=prompt_tag_id,
            commit=False,
        ):
            touched += 1
    if touched:
        from app.extensions import db

        db.session.commit()
    return touched


def scan_episode_for_matches(
    *,
    audio_path: str,
    catalog: list[Any],
    threshold: float = 0.15,
    hop_seconds: float = 30.0,
    scan_limit_seconds: float = 180.0,
    window_seconds: float | None = None,
    subprocess_run: Any = subprocess.run,
) -> list[tuple[float, float]]:
    """Coarse scan returning matched (start, end) windows."""
    if not catalog or not audio_path or not fpcalc_available():
        return []
    hits: list[tuple[float, float]] = []
    t = 0.0
    while t < scan_limit_seconds:
        for row in catalog:
            duration = float(getattr(row, "duration_seconds", 0.0) or 0.0)
            win = window_seconds if window_seconds is not None else duration
            if win <= 0:
                win = 5.0
            end = t + win
            fp = fingerprint_window(audio_path, t, end, subprocess_run=subprocess_run)
            if not fp:
                continue
            if fingerprint_distance(fp, getattr(row, "fingerprint", "")) <= threshold:
                hits.append((t, end))
                break
        t += hop_seconds
    return hits


def match_windows_around_candidates(
    *,
    audio_path: str,
    catalog: list[Any],
    candidate_windows: list[tuple[float, float]],
    threshold: float = 0.15,
    pad_seconds: float = 2.0,
    subprocess_run: Any = subprocess.run,
) -> list[tuple[float, float]]:
    if not catalog or not audio_path:
        return []
    hits: list[tuple[float, float]] = []
    seen: set[tuple[int, int]] = set()
    for cand_start, cand_end in candidate_windows:
        start = max(0.0, cand_start - pad_seconds)
        end = cand_end + pad_seconds
        for row in catalog:
            duration = float(getattr(row, "duration_seconds", 0.0) or 0.0)
            if duration <= 0:
                duration = end - start
            fp = fingerprint_window(
                audio_path, start, start + duration, subprocess_run=subprocess_run
            )
            if not fp:
                continue
            if fingerprint_distance(fp, getattr(row, "fingerprint", "")) <= threshold:
                key = (int(start * 10), int((start + duration) * 10))
                if key not in seen:
                    seen.add(key)
                    hits.append((start, start + duration))
    return hits
