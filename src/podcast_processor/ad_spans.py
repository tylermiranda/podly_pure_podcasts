"""Resolve LLM ad predictions onto time spans instead of Whisper line starts.

Whisper lines are evidence. Cut windows are [start, end) seconds, optionally
inside a mixed CTA+cold-open segment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from podcast_processor.content_guard import ad_cut_window

OFFSET_START_TOLERANCE = 0.5
INTERIOR_OFFSET_THRESHOLD = 0.5
AD_MERGE_PROXIMITY_SECONDS = 8.0


@dataclass(frozen=True)
class ResolvedSpan:
    segment: Any
    start: float
    end: float


def prediction_bounds(pred: Any) -> tuple[float, float | None]:
    """Return (start, optional end) from an AdSegmentPrediction-like object."""
    start = getattr(pred, "start", None)
    if start is None:
        start = getattr(pred, "segment_offset", None)
    if start is None:
        raise ValueError("prediction is missing start/segment_offset")
    end = getattr(pred, "end", None)
    end_f = float(end) if end is not None else None
    start_f = float(start)
    if end_f is not None and end_f <= start_f:
        end_f = None
    return start_f, end_f


def _segment_contains(segment: Any, offset: float, *, tolerance: float) -> bool:
    start = float(segment.start_time or 0.0)
    end = float(segment.end_time or start)
    return (start - tolerance) <= offset <= (end + 0.05)


def find_containing_segment(
    segments: list[Any],
    offset: float,
    *,
    tolerance: float = OFFSET_START_TOLERANCE,
) -> Any | None:
    """Prefer a segment that contains `offset`; else nearest start within tolerance."""
    containing = [
        seg for seg in segments if _segment_contains(seg, offset, tolerance=tolerance)
    ]
    if containing:
        containing.sort(
            key=lambda seg: abs(
                offset - (float(seg.start_time) + float(seg.end_time)) / 2.0
            )
        )
        return containing[0]

    nearest = None
    nearest_diff = float("inf")
    for seg in segments:
        diff = abs(float(seg.start_time or 0.0) - offset)
        if diff < nearest_diff and diff <= tolerance:
            nearest = seg
            nearest_diff = diff
    return nearest


def overlapping_segments(segments: list[Any], start: float, end: float) -> list[Any]:
    hits = [
        seg
        for seg in segments
        if float(seg.end_time or 0.0) > start and float(seg.start_time or 0.0) < end
    ]
    hits.sort(key=lambda seg: float(seg.start_time or 0.0))
    return hits


def resolve_prediction_spans(pred: Any, segments: list[Any]) -> list[ResolvedSpan]:
    """Map one LLM prediction onto one or more clipped segment spans."""
    start, end = prediction_bounds(pred)
    if end is not None:
        spans: list[ResolvedSpan] = []
        for seg in overlapping_segments(segments, start, end):
            clipped_start = max(start, float(seg.start_time or 0.0))
            clipped_end = min(end, float(seg.end_time or clipped_start))
            if clipped_end - clipped_start < 0.05:
                continue
            spans.append(
                ResolvedSpan(segment=seg, start=clipped_start, end=clipped_end)
            )
        return spans

    matched = find_containing_segment(segments, start)
    if matched is None:
        return []
    seg_start = float(matched.start_time or 0.0)
    seg_end = float(matched.end_time or seg_start)
    cut_start = start if (start - seg_start) > INTERIOR_OFFSET_THRESHOLD else seg_start
    cut_start = min(max(cut_start, seg_start), seg_end)
    if seg_end - cut_start < 0.05:
        return []
    return [ResolvedSpan(segment=matched, start=cut_start, end=seg_end)]


def apply_content_guard_to_span(
    segment: Any, start: float, end: float
) -> tuple[float, float] | None:
    """Intersect a requested span with content-guard (mixed-line trim / drop)."""
    seg_start = float(segment.start_time or 0.0)
    seg_end = float(segment.end_time or seg_start)
    guarded = ad_cut_window(
        seg_start,
        seg_end,
        segment.text or "",
        words=getattr(segment, "words", None),
    )
    if guarded is None:
        return None
    g0, g1 = guarded
    lo = max(start, g0)
    hi = min(end, g1)
    if hi - lo >= 0.5:
        return (lo, hi)
    # Interior offset landed in the cold-open; keep the guarded sponsor slice.
    if g1 - g0 >= 0.5:
        return guarded
    return None


def identification_cut_window(
    identification: Any, segment: Any
) -> tuple[float, float] | None:
    """Cut window for a stored identification, honoring optional start/end columns."""
    seg_start = float(segment.start_time or 0.0)
    seg_end = float(segment.end_time or seg_start)
    ident_start = getattr(identification, "start_time", None)
    ident_end = getattr(identification, "end_time", None)
    start = float(ident_start) if ident_start is not None else seg_start
    end = float(ident_end) if ident_end is not None else seg_end
    return apply_content_guard_to_span(segment, start, end)


def identification_span(
    identification: Any, segment: Any | None = None
) -> tuple[float, float]:
    """Raw stored span, falling back to the full Whisper line."""
    seg = (
        segment
        if segment is not None
        else getattr(identification, "transcript_segment", None)
    )
    seg_start = float(getattr(seg, "start_time", 0.0) or 0.0)
    seg_end = float(getattr(seg, "end_time", seg_start) or seg_start)
    ident_start = getattr(identification, "start_time", None)
    ident_end = getattr(identification, "end_time", None)
    start = float(ident_start) if ident_start is not None else seg_start
    end = float(ident_end) if ident_end is not None else seg_end
    if end < start:
        return (start, start)
    return (start, end)
