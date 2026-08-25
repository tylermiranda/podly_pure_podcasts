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
ANALYZE_MAX_CORRECTIONS = 40
ANALYZE_SNIPPET_CHARS = 200
ANALYZE_HEURISTIC_EXAMPLES = 4
ANALYZE_PROMPT_SYSTEM = (
    "You write short, durable show-specific rules for podcast ad classification. "
    "Given human corrections (missed ads and false-positive content), produce plain "
    "text instructions that help an LLM correctly label similar segments on future "
    "episodes of the same show. Separate CONTENT (do not cut) patterns from AD "
    "(cut) patterns when both appear. Do not dump transcript quotes at length; "
    "generalize briefly with short illustrative phrases only when useful. "
    "Prefer rules that are not already covered by the existing show prompt, but "
    "always return at least one concrete rule derived from the corrections — never "
    "an empty reply. Return only the rules as a few short sentences or bullets — "
    "no preamble."
)


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


def suggested_prompt_status(
    *,
    feed_id: int,
    existing_prompt: str | None = None,
    min_repeats: int = PROMOTION_MIN_REPEATS,
) -> dict[str, Any]:
    corrections = active_corrections_query(feed_ids=[feed_id]).all()
    counts: dict[str, list[AdCorrection]] = {}
    for correction in corrections:
        key = example_fingerprint(correction)
        if key.endswith(":"):
            continue
        counts.setdefault(key, []).append(correction)
    best: list[AdCorrection] | None = None
    for group in counts.values():
        if best is None or len(group) > len(best):
            best = group
    repeat_count = len(best) if best else 0
    label = best[0].label if best else None
    snippet: str | None = None
    if best and len(best) >= min_repeats:
        sample = best[0]
        example = (sample.example_text or "").strip()
        if example:
            if len(example) > 140:
                example = example[:137].rstrip() + "..."
            if sample.label == "content":
                suggestion = (
                    f'Treat lines like "{example}" as CONTENT, not ads. '
                    "Do not cut date cold-opens or narrative resumes."
                )
            else:
                suggestion = (
                    f'Cut sponsor reads matching "{example}". '
                    "Keep the surrounding show content."
                )
            existing = (existing_prompt or "").strip()
            if not (existing and suggestion in existing):
                snippet = suggestion
    return {
        "snippet": snippet,
        "repeat_count": repeat_count,
        "min_repeats": min_repeats,
        "label": label,
    }


def suggested_prompt_snippet(
    *,
    feed_id: int,
    existing_prompt: str | None = None,
    min_repeats: int = PROMOTION_MIN_REPEATS,
) -> str | None:
    return suggested_prompt_status(
        feed_id=feed_id,
        existing_prompt=existing_prompt,
        min_repeats=min_repeats,
    ).get("snippet")


def _truncate_snippet(text: str | None, *, limit: int = ANALYZE_SNIPPET_CHARS) -> str:
    snippet = (text or "").strip()
    if len(snippet) > limit:
        return snippet[: limit - 3].rstrip() + "..."
    return snippet


def format_corrections_for_prompt_analysis(
    corrections: list[Any],
) -> str:
    """Compact human-readable list of corrections for LLM analysis."""
    lines: list[str] = []
    for correction in corrections[:ANALYZE_MAX_CORRECTIONS]:
        label = "AD" if correction.label == "ad" else "CONTENT"
        snippet = _truncate_snippet(correction.example_text)
        reason = (correction.reason or "").strip()
        line = (
            f"[{float(correction.start_time):.1f}-{float(correction.end_time):.1f}] "
            f"{label} ({correction.kind})"
        )
        if snippet:
            line += f' "{snippet}"'
        if reason:
            line += f" — note: {reason}"
        lines.append(line)
    omitted = len(corrections) - ANALYZE_MAX_CORRECTIONS
    if omitted > 0:
        lines.append(f"...and {omitted} more correction(s) omitted")
    return "\n".join(lines)


def build_analyze_prompt_messages(
    *,
    corrections_block: str,
    existing_prompt: str | None = None,
) -> list[dict[str, str]]:
    existing = (existing_prompt or "").strip()
    existing_section = existing if existing else "(none)"
    user_content = (
        "Existing show prompt (prefer not to duplicate):\n"
        f"{existing_section}\n\n"
        "Human corrections from one episode:\n"
        f"{corrections_block}\n\n"
        "Write durable show rules from these corrections. Always include at least "
        "one rule; never reply with an empty message."
    )
    return [
        {"role": "system", "content": ANALYZE_PROMPT_SYSTEM},
        {"role": "user", "content": user_content},
    ]


def heuristic_prompt_draft_from_corrections(
    corrections: list[Any],
    *,
    existing_prompt: str | None = None,
    max_examples: int = ANALYZE_HEURISTIC_EXAMPLES,
) -> str:
    """Deterministic draft when the LLM returns nothing useful.

    Groups corrections by label and builds short CONTENT/AD rules from example
    snippets. Skips suggestions already present in ``existing_prompt``.
    """
    existing = (existing_prompt or "").strip()
    by_label: dict[str, list[str]] = {"content": [], "ad": []}
    seen: set[str] = set()
    for correction in corrections:
        label = (getattr(correction, "label", None) or "").strip().lower()
        if label not in by_label:
            continue
        snippet = _truncate_snippet(
            getattr(correction, "example_text", None), limit=120
        )
        if not snippet:
            continue
        key = snippet.casefold()
        if key in seen:
            continue
        seen.add(key)
        by_label[label].append(snippet)

    lines: list[str] = []
    content_examples = by_label["content"][:max_examples]
    if content_examples:
        quoted = "; ".join(f'"{ex}"' for ex in content_examples)
        lines.append(
            f"Treat lines like {quoted} as CONTENT, not ads. "
            "Do not cut credits, copyright notices, or narrative resumes."
        )
    ad_examples = by_label["ad"][:max_examples]
    if ad_examples:
        quoted = "; ".join(f'"{ex}"' for ex in ad_examples)
        lines.append(
            f"Cut sponsor reads matching {quoted}. Keep the surrounding show content."
        )

    draft_lines = [line for line in lines if line and line not in existing]
    if not draft_lines and lines:
        # Still surface something so the UI can review even if already covered
        draft_lines = lines
    return "\n".join(draft_lines).strip()


def analyze_corrections_for_prompt(
    *,
    post_id: int,
    existing_prompt: str | None = None,
    config: Any | None = None,
) -> dict[str, Any]:
    """LLM-synthesize feed-prompt draft from this episode's active corrections.

    Does not write the feed. Raises ValueError when there are no corrections.
    Falls back to a heuristic draft if the model returns empty text.
    """
    import litellm

    from podcast_processor.llm_model_call_utils import extract_litellm_content
    from shared.llm_utils import model_uses_max_completion_tokens

    corrections = load_active_corrections_for_post(post_id)
    if not corrections:
        raise ValueError("No saved corrections to analyze")

    if config is None:
        from app.runtime_config import config as runtime_config

        config = runtime_config

    corrections_block = format_corrections_for_prompt_analysis(corrections)
    messages = build_analyze_prompt_messages(
        corrections_block=corrections_block,
        existing_prompt=existing_prompt,
    )

    model_name = getattr(config, "llm_model", None) or "gpt-4o"
    completion_args: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "temperature": 0.2,
        "timeout": int(getattr(config, "openai_timeout", 300) or 300),
        "api_key": getattr(config, "llm_api_key", None),
    }
    if model_uses_max_completion_tokens(model_name):
        completion_args["max_completion_tokens"] = 800
    else:
        completion_args["max_tokens"] = 800
    base_url = getattr(config, "openai_base_url", None)
    if isinstance(base_url, str) and base_url.strip():
        completion_args["base_url"] = base_url.strip()

    response = litellm.completion(**completion_args)
    draft = extract_litellm_content(response).strip()
    if not draft:
        draft = heuristic_prompt_draft_from_corrections(
            corrections,
            existing_prompt=existing_prompt,
        )
        if draft:
            logger.warning(
                "analyze-prompt: model returned empty content for post %s; "
                "using heuristic draft",
                post_id,
            )
        else:
            raise ValueError("Model returned an empty prompt draft")

    existing = (existing_prompt or "").strip() or None
    return {
        "draft": draft,
        "correction_count": len(corrections),
        "existing_prompt": existing,
    }


def processed_audio_needs_recut(post: Post) -> bool:
    """True when saved corrections are newer than the processed MP3 on disk."""
    corrections = load_active_corrections_for_post(post.id)
    if not corrections:
        return False

    from shared.processing_paths import find_existing_processed_audio_path

    feed = getattr(post, "feed", None)
    feed_title = getattr(feed, "title", None) if feed is not None else None
    processed_path = find_existing_processed_audio_path(
        processed_audio_path=post.processed_audio_path,
        unprocessed_audio_path=post.unprocessed_audio_path,
        feed_title=feed_title,
        post_title=post.title,
    )
    if processed_path is None:
        return True

    latest = max(
        corrections,
        key=lambda row: row.created_at.timestamp() if row.created_at else 0.0,
    )
    if latest.created_at is None:
        return True

    try:
        file_mtime = processed_path.stat().st_mtime
    except OSError:
        return True

    return latest.created_at.timestamp() > file_mtime


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
