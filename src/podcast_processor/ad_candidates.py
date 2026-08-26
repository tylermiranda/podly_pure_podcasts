"""Candidate span index for two-stage ad classification."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from podcast_processor.cue_detector import CueDetector


@dataclass
class CandidateSpan:
    """Inclusive transcript segment index range with reason tags."""

    start_idx: int
    end_idx: int
    reasons: list[str] = field(default_factory=list)

    def merge_key(self) -> tuple[int, int]:
        return (self.start_idx, self.end_idx)


def _seq_to_index(segments: list[Any]) -> dict[int, int]:
    return {
        int(getattr(seg, "sequence_num", idx)): idx for idx, seg in enumerate(segments)
    }


def _index_for_time(segments: list[Any], seconds: float) -> int | None:
    for idx, seg in enumerate(segments):
        start = float(getattr(seg, "start_time", 0.0) or 0.0)
        end = float(getattr(seg, "end_time", start) or start)
        if start <= seconds <= end:
            return idx
    if segments and seconds >= float(getattr(segments[-1], "end_time", 0.0) or 0.0):
        return len(segments) - 1
    return None


def _span_from_indices(start_idx: int, end_idx: int, reason: str) -> CandidateSpan:
    lo = max(0, min(start_idx, end_idx))
    hi = max(start_idx, end_idx)
    return CandidateSpan(start_idx=lo, end_idx=hi, reasons=[reason])


def _merge_spans(spans: list[CandidateSpan]) -> list[CandidateSpan]:
    if not spans:
        return []
    ordered = sorted(spans, key=lambda s: (s.start_idx, s.end_idx))
    merged: list[CandidateSpan] = [ordered[0]]
    for span in ordered[1:]:
        last = merged[-1]
        if span.start_idx <= last.end_idx + 1:
            last.end_idx = max(last.end_idx, span.end_idx)
            for reason in span.reasons:
                if reason not in last.reasons:
                    last.reasons.append(reason)
        else:
            merged.append(span)
    return merged


def _pad_spans(
    spans: list[CandidateSpan], *, pad: int, total: int
) -> list[CandidateSpan]:
    if pad <= 0 or not spans:
        return spans
    out: list[CandidateSpan] = []
    for span in spans:
        out.append(
            CandidateSpan(
                start_idx=max(0, span.start_idx - pad),
                end_idx=min(total - 1, span.end_idx + pad),
                reasons=list(span.reasons),
            )
        )
    return _merge_spans(out)


def build_cue_spans(
    segments: list[Any],
    *,
    cue_detector: CueDetector | None = None,
) -> list[CandidateSpan]:
    detector = cue_detector or CueDetector()
    spans: list[CandidateSpan] = []
    for idx, seg in enumerate(segments):
        text = (getattr(seg, "text", None) or "").strip()
        if not text:
            continue
        if detector.has_strong_ad_cue(text) or detector.has_cue(text):
            spans.append(_span_from_indices(idx, idx, "cue"))
    return spans


def build_creative_spans(
    segments: list[Any],
    creatives: list[Any],
    *,
    jaccard_threshold: float = 0.85,
) -> list[CandidateSpan]:
    from podcast_processor.ad_creatives import match_segment_to_creatives

    spans: list[CandidateSpan] = []
    for idx, seg in enumerate(segments):
        text = getattr(seg, "text", None) or ""
        if match_segment_to_creatives(
            text, creatives, jaccard_threshold=jaccard_threshold
        ):
            spans.append(_span_from_indices(idx, idx, "text_creative"))
    return spans


def build_edge_spans(
    segments: list[Any],
    *,
    preroll_seconds: float,
    outro_seconds: float,
) -> list[CandidateSpan]:
    if not segments:
        return []
    spans: list[CandidateSpan] = []
    episode_end = float(getattr(segments[-1], "end_time", 0.0) or 0.0)
    preroll_end_idx = _index_for_time(segments, preroll_seconds)
    if preroll_end_idx is not None:
        spans.append(_span_from_indices(0, preroll_end_idx, "edge_preroll"))
    outro_start = max(0.0, episode_end - outro_seconds)
    outro_start_idx = _index_for_time(segments, outro_start)
    if outro_start_idx is not None:
        spans.append(
            _span_from_indices(outro_start_idx, len(segments) - 1, "edge_outro")
        )
    return spans


def build_time_spans(
    segments: list[Any],
    windows: list[tuple[float, float]],
    reason: str,
) -> list[CandidateSpan]:
    spans: list[CandidateSpan] = []
    for start_s, end_s in windows:
        start_idx = _index_for_time(segments, start_s)
        end_idx = _index_for_time(segments, end_s)
        if start_idx is None and end_idx is None:
            continue
        if start_idx is None:
            start_idx = 0
        if end_idx is None:
            end_idx = len(segments) - 1
        spans.append(_span_from_indices(start_idx, end_idx, reason))
    return spans


def build_candidate_spans(
    *,
    segments: list[Any],
    cue_detector: CueDetector | None = None,
    creatives: list[Any] | None = None,
    creative_jaccard: float = 0.85,
    audio_fp_windows: list[tuple[float, float]] | None = None,
    gap_windows: list[tuple[float, float]] | None = None,
    jingle_windows: list[tuple[float, float]] | None = None,
    preroll_seconds: float = 120.0,
    outro_seconds: float = 60.0,
    pad_segments: int = 5,
) -> list[CandidateSpan]:
    """Union cue, creative, audio, gap, jingle, and edge spans."""
    spans: list[CandidateSpan] = []
    spans.extend(build_cue_spans(segments, cue_detector=cue_detector))
    if creatives:
        spans.extend(
            build_creative_spans(
                segments, creatives, jaccard_threshold=creative_jaccard
            )
        )
    if audio_fp_windows:
        spans.extend(build_time_spans(segments, audio_fp_windows, "audio_fp"))
    if gap_windows:
        spans.extend(build_time_spans(segments, gap_windows, "audio_gap"))
    if jingle_windows:
        spans.extend(build_time_spans(segments, jingle_windows, "jingle"))
    spans.extend(
        build_edge_spans(
            segments,
            preroll_seconds=preroll_seconds,
            outro_seconds=outro_seconds,
        )
    )
    merged = _merge_spans(spans)
    return _pad_spans(merged, pad=pad_segments, total=len(segments))


def candidate_indices(spans: list[CandidateSpan]) -> set[int]:
    indices: set[int] = set()
    for span in spans:
        indices.update(range(span.start_idx, span.end_idx + 1))
    return indices


def format_candidate_summary(spans: list[CandidateSpan]) -> str:
    if not spans:
        return ""
    lines = [
        "Candidate ad regions (prioritize these; still classify overlapping content carefully):"
    ]
    for span in spans[:24]:
        tags = ",".join(span.reasons)
        lines.append(f"- segments {span.start_idx}-{span.end_idx} [{tags}]")
    if len(spans) > 24:
        lines.append(f"- … and {len(spans) - 24} more candidate spans")
    return "\n".join(lines)
