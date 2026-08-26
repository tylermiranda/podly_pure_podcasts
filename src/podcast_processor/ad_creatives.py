"""Cross-episode text creative index for repeated sponsor copy."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

from podcast_processor.ad_spans import normalize_ad_copy

logger = logging.getLogger(__name__)


def creative_fingerprint(normalized_text: str) -> str:
    digest = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
    return digest[:32]


def token_set(text: str) -> set[str]:
    tokens: set[str] = set()
    for tok in normalize_ad_copy(text).split():
        cleaned = tok.strip(".,!?;:\"'()[]{}").lower()
        if cleaned:
            tokens.add(cleaned)
    return tokens


def jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    inter = len(left & right)
    union = len(left | right)
    return inter / union if union else 0.0


def extract_creative_texts_from_windows(
    segments: list[Any],
    windows: list[tuple[float, float]],
    *,
    min_chars: int = 24,
) -> list[str]:
    """Collect normalized segment texts that overlap cut windows."""
    out: list[str] = []
    seen: set[str] = set()
    for segment in segments:
        text = getattr(segment, "text", None) or ""
        normalized = normalize_ad_copy(text)
        if len(normalized) < min_chars:
            continue
        start = float(getattr(segment, "start_time", 0.0) or 0.0)
        end = float(getattr(segment, "end_time", start) or start)
        if not any(start < we and end > ws for ws, we in windows):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def upsert_creatives_for_feed(
    *,
    feed_id: int,
    texts: list[str],
    source_post_id: int | None = None,
    prompt_tag_id: int | None = None,
    min_chars: int = 24,
    commit: bool = True,
) -> int:
    """Insert or bump hit_count for creatives. Returns number of rows touched."""
    from app.extensions import db
    from app.models import AdCreative

    touched = 0
    now = datetime.now(UTC).replace(tzinfo=None)
    for text in texts:
        normalized = normalize_ad_copy(text)
        if len(normalized) < min_chars:
            continue
        fp = creative_fingerprint(normalized)
        existing = (
            db.session.query(AdCreative)
            .filter_by(feed_id=feed_id, fingerprint=fp)
            .first()
        )
        if existing is None:
            row = AdCreative(
                feed_id=feed_id,
                prompt_tag_id=prompt_tag_id,
                normalized_text=normalized,
                fingerprint=fp,
                sample_text=normalized[:500],
                source_post_id=source_post_id,
                hit_count=1,
                created_at=now,
                updated_at=now,
            )
            db.session.add(row)
            touched += 1
        else:
            existing.hit_count = int(existing.hit_count or 0) + 1
            existing.updated_at = now
            if source_post_id is not None:
                existing.source_post_id = source_post_id
            if prompt_tag_id is not None and existing.prompt_tag_id is None:
                existing.prompt_tag_id = prompt_tag_id
            touched += 1
    if touched:
        if commit:
            db.session.commit()
        else:
            db.session.flush()
    return touched


def load_feed_creatives(
    *,
    feed_id: int,
    prompt_tag_id: int | None = None,
    limit: int = 200,
) -> list[Any]:
    from app.extensions import db
    from app.models import AdCreative

    query = db.session.query(AdCreative).filter(AdCreative.feed_id == feed_id)
    if prompt_tag_id is not None:
        query = query.filter(
            (AdCreative.prompt_tag_id == prompt_tag_id)
            | (AdCreative.prompt_tag_id.is_(None))
        )
    return (
        query.order_by(AdCreative.hit_count.desc(), AdCreative.updated_at.desc())
        .limit(limit)
        .all()
    )


def match_segment_to_creatives(
    segment_text: str,
    creatives: list[Any],
    *,
    jaccard_threshold: float = 0.85,
) -> Any | None:
    normalized = normalize_ad_copy(segment_text)
    if not normalized:
        return None
    for creative in creatives:
        creat_norm = getattr(creative, "normalized_text", None) or ""
        if normalized == creat_norm:
            return creative
    seg_tokens = token_set(normalized)
    best = None
    best_score = 0.0
    for creative in creatives:
        creat_norm = getattr(creative, "normalized_text", None) or ""
        score = jaccard(seg_tokens, token_set(creat_norm))
        if score >= jaccard_threshold and score > best_score:
            best = creative
            best_score = score
    return best


def format_creative_prompt_hints(
    creatives: list[Any], *, max_items: int = 8
) -> str:
    if not creatives:
        return ""
    lines = ["Known sponsor creatives for this show (treat matching lines as ads):"]
    for creative in creatives[:max_items]:
        sample = (getattr(creative, "sample_text", None) or "").strip()
        if len(sample) > 140:
            sample = sample[:137] + "..."
        if sample:
            lines.append(f'- "{sample}"')
    return "\n".join(lines)


def upsert_from_post_cut_windows(
    *,
    post: Any,
    windows: list[tuple[float, float]],
    segments: list[Any],
    min_chars: int = 24,
) -> int:
    feed_id = getattr(post, "feed_id", None)
    if feed_id is None or not windows:
        return 0
    feed = getattr(post, "feed", None)
    prompt_tag_id = getattr(feed, "prompt_tag_id", None) if feed is not None else None
    texts = extract_creative_texts_from_windows(
        segments, windows, min_chars=min_chars
    )
    if not texts:
        return 0
    return upsert_creatives_for_feed(
        feed_id=int(feed_id),
        texts=texts,
        source_post_id=getattr(post, "id", None),
        prompt_tag_id=prompt_tag_id,
        min_chars=min_chars,
    )
