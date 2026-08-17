"""Human ad/content corrections: retrieval, serialization, and recut helpers."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from sqlalchemy import or_

from app.extensions import db
from app.model_call_utils import whisper_model_call_filter
from app.models import AdCorrection, Feed, ModelCall, Post
from app.writer.client import writer_client
from podcast_processor.ad_spans import (
    apply_corrections_to_windows,
    content_bounds_from_corrections,
)

logger = logging.getLogger("global_logger")

CORRECTION_KINDS = ("missed_ad", "false_positive", "retime")
CORRECTION_LABELS = ("ad", "content")
EXAMPLE_LIMIT = 6
PROMOTION_MIN_REPEATS = 3


def default_kind_for_label(label: str) -> str:
    if label == "ad":
        return "missed_ad"
    return "false_positive"


def snap_range_to_words(
    start: float,
    end: float,
    segments: list[Any],
    *,
    tolerance: float = 0.75,
) -> tuple[float, float]:
    """Snap a selected range to nearby word timestamps when present."""
    words: list[tuple[float, float]] = []
    for segment in segments:
        raw_words = getattr(segment, "words", None)
        if not isinstance(raw_words, list):
            continue
        for word in raw_words:
            if not isinstance(word, dict):
                continue
            word_start = word.get("start")
            word_end = word.get("end")
            if word_start is None or word_end is None:
                continue
            try:
                words.append((float(word_start), float(word_end)))
            except (TypeError, ValueError):
                continue
    if not words:
        return start, end

    start_candidates = [item[0] for item in words if abs(item[0] - start) <= tolerance]
    end_candidates = [item[1] for item in words if abs(item[1] - end) <= tolerance]
    snapped_start = (
        min(start_candidates, key=lambda value: abs(value - start))
        if start_candidates
        else start
    )
    snapped_end = (
        min(end_candidates, key=lambda value: abs(value - end))
        if end_candidates
        else end
    )
    if snapped_end <= snapped_start:
        return start, end
    return snapped_start, snapped_end


def example_text_for_range(
    start: float,
    end: float,
    segments: list[Any],
) -> str:
    parts: list[str] = []
    for segment in segments:
        seg_start = float(getattr(segment, "start_time", 0.0) or 0.0)
        seg_end = float(getattr(segment, "end_time", 0.0) or 0.0)
        if seg_end <= start or seg_start >= end:
            continue
        text = str(getattr(segment, "text", "") or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts).strip()


def current_transcript_model_call_id(post_id: int) -> int | None:
    call = (
        db.session.query(ModelCall)
        .filter(ModelCall.post_id == post_id)
        .filter(whisper_model_call_filter())
        .order_by(ModelCall.id.desc())
        .first()
    )
    return int(call.id) if call is not None else None


def _superseded_ids() -> set[int]:
    rows = (
        db.session.query(AdCorrection.supersedes_id)
        .filter(AdCorrection.supersedes_id.isnot(None))
        .all()
    )
    return {int(row[0]) for row in rows if row[0] is not None}


def active_corrections_query(
    *,
    post_id: int | None = None,
    feed_ids: list[int] | None = None,
):
    query = db.session.query(AdCorrection).filter(AdCorrection.stale.is_(False))
    superseded = _superseded_ids()
    if superseded:
        query = query.filter(~AdCorrection.id.in_(superseded))
    if post_id is not None:
        query = query.filter(AdCorrection.post_id == post_id)
        current_call_id = current_transcript_model_call_id(post_id)
        if current_call_id is not None:
            query = query.filter(
                or_(
                    AdCorrection.transcript_model_call_id.is_(None),
                    AdCorrection.transcript_model_call_id == current_call_id,
                )
            )
    if feed_ids is not None:
        query = query.filter(AdCorrection.feed_id.in_(feed_ids))
    return query.order_by(AdCorrection.created_at.asc(), AdCorrection.id.asc())


def load_active_corrections_for_post(post_id: int) -> list[AdCorrection]:
    return active_corrections_query(post_id=post_id).all()


def serialize_correction(correction: AdCorrection) -> dict[str, Any]:
    created_at = getattr(correction, "created_at", None)
    return {
        "id": correction.id,
        "post_id": correction.post_id,
        "feed_id": correction.feed_id,
        "kind": correction.kind,
        "label": correction.label,
        "start_time": round(float(correction.start_time), 1),
        "end_time": round(float(correction.end_time), 1),
        "segment_ids": correction.segment_ids,
        "reason": correction.reason,
        "example_text": correction.example_text,
        "stale": bool(correction.stale),
        "supersedes_id": correction.supersedes_id,
        "transcript_model_call_id": correction.transcript_model_call_id,
        "created_at": created_at.isoformat() if created_at is not None else None,
    }


def mark_ad_corrections_stale_for_post(post_id: int) -> int:
    updated = (
        db.session.query(AdCorrection)
        .filter(AdCorrection.post_id == int(post_id), AdCorrection.stale.is_(False))
        .update({AdCorrection.stale: True}, synchronize_session=False)
    )
    return int(updated or 0)


def normalize_example_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def token_overlap(left: str, right: str) -> float:
    left_tokens = set(normalize_example_text(left).split())
    right_tokens = set(normalize_example_text(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def example_fingerprint(correction: AdCorrection) -> str:
    tokens = normalize_example_text(correction.example_text or "").split()[:8]
    return f"{correction.label}:{' '.join(tokens)}"


def _feed_ids_for_retrieval(feed_id: int, prompt_tag_id: int | None) -> list[int]:
    if prompt_tag_id is None:
        return [feed_id]
    rows = (
        db.session.query(Feed.id).filter(Feed.prompt_tag_id == int(prompt_tag_id)).all()
    )
    ids = [int(row[0]) for row in rows]
    return ids or [feed_id]


def retrieve_correction_examples(
    *,
    feed_id: int,
    prompt_tag_id: int | None,
    query_text: str,
    limit: int = EXAMPLE_LIMIT,
) -> list[AdCorrection]:
    feed_ids = _feed_ids_for_retrieval(feed_id, prompt_tag_id)
    candidates = active_corrections_query(feed_ids=feed_ids).all()
    scored: list[tuple[float, AdCorrection]] = []
    for correction in candidates:
        if not (correction.example_text or "").strip():
            continue
        score = token_overlap(query_text, correction.example_text or "")
        scored.append((score, correction))
    scored.sort(key=lambda item: (-item[0], item[1].id))
    selected: list[AdCorrection] = []
    seen: set[int] = set()
    for _score, correction in scored:
        if correction.id in seen:
            continue
        selected.append(correction)
        seen.add(correction.id)
        if len(selected) >= limit:
            break
    return selected


def format_correction_examples_prompt(examples: list[AdCorrection]) -> str:
    if not examples:
        return ""
    lines = ["Human-reviewed examples for this show:"]
    for correction in examples:
        snippet = (correction.example_text or "").strip()
        if len(snippet) > 180:
            snippet = snippet[:177].rstrip() + "..."
        label = "AD" if correction.label == "ad" else "CONTENT"
        lines.append(
            f'[{correction.start_time:.1f}-{correction.end_time:.1f}] {label} "{snippet}"'
        )
    return "\n".join(lines)


def suggested_prompt_snippet(
    *,
    feed_id: int,
    existing_prompt: str | None = None,
    min_repeats: int = PROMOTION_MIN_REPEATS,
) -> str | None:
    corrections = active_corrections_query(feed_ids=[feed_id]).all()
    counts: dict[str, list[AdCorrection]] = {}
    for correction in corrections:
        key = example_fingerprint(correction)
        if key.endswith(":"):
            continue
        counts.setdefault(key, []).append(correction)
    best: list[AdCorrection] | None = None
    for group in counts.values():
        if len(group) < min_repeats:
            continue
        if best is None or len(group) > len(best):
            best = group
    if not best:
        return None
    sample = best[0]
    snippet = (sample.example_text or "").strip()
    if len(snippet) > 140:
        snippet = snippet[:137].rstrip() + "..."
    if sample.label == "content":
        suggestion = (
            f'Treat lines like "{snippet}" as CONTENT, not ads. '
            "Do not cut date cold-opens or narrative resumes."
        )
    else:
        suggestion = (
            f'Cut sponsor reads matching "{snippet}". '
            "Keep the surrounding show content."
        )
    existing = (existing_prompt or "").strip()
    if existing and suggestion in existing:
        return None
    return suggestion


def apply_post_corrections(
    windows: list[tuple[float, float]],
    post_id: int,
) -> tuple[list[tuple[float, float]], list[AdCorrection]]:
    corrections = load_active_corrections_for_post(post_id)
    return apply_corrections_to_windows(windows, corrections), corrections


def content_bounds_for_post(post_id: int) -> list[tuple[float, float]]:
    return content_bounds_from_corrections(load_active_corrections_for_post(post_id))


def _default_unprocessed_dest(post: Post) -> str:
    from podcast_processor.podcast_downloader import sanitize_title
    from shared.processing_paths import get_in_root

    feed_title = getattr(getattr(post, "feed", None), "title", None) or "feed"
    post_title = post.title or post.guid
    dest_dir = get_in_root() / sanitize_title(feed_title)
    dest_dir.mkdir(parents=True, exist_ok=True)
    return str(dest_dir / f"{sanitize_title(post_title) or post.guid}.mp3")


def ensure_unprocessed_audio(post: Post) -> str | None:
    path = post.unprocessed_audio_path
    if path and os.path.isfile(path) and os.path.getsize(path) > 0:
        return path

    from podcast_processor.podcast_downloader import PodcastDownloader

    dest = _default_unprocessed_dest(post)
    downloaded = PodcastDownloader().download_episode(post, dest)
    if not downloaded:
        logger.warning("Could not download source audio for recut of post %s", post.id)
        return None
    result = writer_client.update(
        "Post",
        post.id,
        {"unprocessed_audio_path": downloaded},
        wait=True,
    )
    if not result or not result.success:
        raise RuntimeError(getattr(result, "error", "Failed to store unprocessed path"))
    post.unprocessed_audio_path = downloaded
    return downloaded


def recut_post_audio(post: Post) -> dict[str, Any]:
    """Recut processed audio from the original file using effective corrections."""
    from app.runtime_config import config as runtime_config
    from podcast_processor.audio_processor import AudioProcessor
    from shared.processing_paths import (
        get_processed_audio_path_candidates,
        get_srv_root,
    )

    source = ensure_unprocessed_audio(post)
    if not source:
        raise ValueError("Could not locate or download source audio for recut")

    feed_title = getattr(getattr(post, "feed", None), "title", None)
    candidates = get_processed_audio_path_candidates(
        processed_audio_path=post.processed_audio_path,
        unprocessed_audio_path=source,
        feed_title=feed_title,
        post_title=post.title,
    )
    output = (
        str(candidates[0]) if candidates else str(get_srv_root() / f"{post.guid}.mp3")
    )
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    AudioProcessor(config=runtime_config).process_audio(post, output)
    return {
        "post_id": post.id,
        "processed_audio_path": output,
        "duration": post.duration,
    }
