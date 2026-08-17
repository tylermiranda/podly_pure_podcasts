"""Resolve LLM ad predictions onto time spans instead of Whisper line starts.

Whisper lines are evidence. Cut windows are [start, end) seconds, optionally
inside a mixed CTA+cold-open segment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from podcast_processor.content_guard import (
    ad_cut_window,
    has_content_resume,
    is_content_only,
)

OFFSET_START_TOLERANCE = 0.5
INTERIOR_OFFSET_THRESHOLD = 0.5
AD_MERGE_PROXIMITY_SECONDS = 8.0
# LLM often labels only the CTA/URL line. Fill unlabeled ad-copy between two
# nearby ad windows, and walk backward from a CTA through the rest of the read.
AD_HOLE_FILL_SECONDS = 24.0
AD_LEAD_IN_SECONDS = 45.0
AD_LEAD_IN_MAX_GAP_SECONDS = 5.0


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


def _segment_text(segment: Any) -> str:
    return str(getattr(segment, "text", None) or "")


def _segment_bounds(segment: Any) -> tuple[float, float]:
    start = float(getattr(segment, "start_time", 0.0) or 0.0)
    end = float(getattr(segment, "end_time", start) or start)
    return start, end


def _is_show_content(segment: Any) -> bool:
    text = _segment_text(segment)
    return is_content_only(text) or has_content_resume(text)


def merge_overlapping_windows(
    windows: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    if not windows:
        return []
    ordered = sorted(windows)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 0.05:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _gap_has_show_content(
    segments: list[Any], gap_start: float, gap_end: float
) -> bool:
    for segment in segments:
        start, end = _segment_bounds(segment)
        if end <= gap_start + 0.05 or start >= gap_end - 0.05:
            continue
        if _is_show_content(segment):
            return True
    return False


def fill_ad_holes(
    windows: list[tuple[float, float]],
    segments: list[Any],
    *,
    max_gap: float = AD_HOLE_FILL_SECONDS,
) -> list[tuple[float, float]]:
    """Merge nearby ad windows when the unlabeled gap is not show content."""
    ordered = merge_overlapping_windows(windows)
    if len(ordered) < 2:
        return ordered
    filled = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = filled[-1]
        gap = start - prev_end
        if 0 < gap <= max_gap and not _gap_has_show_content(segments, prev_end, start):
            filled[-1] = (prev_start, end)
        else:
            filled.append((start, end))
    return filled


def lead_in_ad_windows(
    windows: list[tuple[float, float]],
    segments: list[Any],
    *,
    max_lead: float = AD_LEAD_IN_SECONDS,
    max_gap: float = AD_LEAD_IN_MAX_GAP_SECONDS,
) -> list[tuple[float, float]]:
    """Extend each ad window backward through unlabeled ad-copy, not cold-opens."""
    if not windows:
        return []
    ordered_segs = sorted(segments, key=lambda seg: _segment_bounds(seg)[0])
    expanded: list[tuple[float, float]] = []
    for start, end in windows:
        new_start = start
        cursor = start
        previous = [
            seg for seg in ordered_segs if _segment_bounds(seg)[1] <= start + 0.05
        ]
        for seg in reversed(previous):
            seg_start, seg_end = _segment_bounds(seg)
            if start - seg_start > max_lead:
                break
            if cursor - seg_end > max_gap:
                break
            if _is_show_content(seg):
                break
            new_start = seg_start
            cursor = seg_start
        expanded.append((new_start, end))
    return merge_overlapping_windows(expanded)


def expand_cut_windows(
    windows: list[tuple[float, float]],
    segments: list[Any],
) -> list[tuple[float, float]]:
    """Recover full ad reads from CTA-only LLM spans."""
    return fill_ad_holes(lead_in_ad_windows(windows, segments), segments)
