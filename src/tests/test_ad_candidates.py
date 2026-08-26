from podcast_processor.ad_candidates import (
    CandidateSpan,
    _merge_spans,
    build_candidate_spans,
    build_edge_spans,
    candidate_indices,
    format_candidate_summary,
)


def _seg(seq: int, start: float, end: float, text: str = "hello") -> dict:
    return {
        "sequence_num": seq,
        "start_time": start,
        "end_time": end,
        "text": text,
    }


def test_merge_spans_combines_overlapping() -> None:
    spans = [
        CandidateSpan(0, 2, ["cue"]),
        CandidateSpan(2, 4, ["text_creative"]),
        CandidateSpan(10, 12, ["audio_fp"]),
    ]
    merged = _merge_spans(spans)
    assert len(merged) == 2
    assert merged[0].start_idx == 0
    assert merged[0].end_idx == 4
    assert "cue" in merged[0].reasons
    assert "text_creative" in merged[0].reasons


def test_build_edge_spans_includes_preroll_and_outro() -> None:
    segments = [_seg(i, float(i * 10), float(i * 10 + 9)) for i in range(20)]
    spans = build_edge_spans(segments, preroll_seconds=120.0, outro_seconds=60.0)
    reasons = {r for span in spans for r in span.reasons}
    assert "edge_preroll" in reasons
    assert "edge_outro" in reasons


def test_candidate_indices_with_padding() -> None:
    segments = [_seg(i, float(i), float(i + 1)) for i in range(10)]
    spans = build_candidate_spans(
        segments=segments,
        creatives=[],
        audio_fp_windows=[(1.0, 2.0)],
        pad_segments=1,
    )
    indices = candidate_indices(spans)
    assert 0 in indices or 1 in indices


def test_format_candidate_summary_empty() -> None:
    assert format_candidate_summary([]) == ""


def test_merge_key_stable() -> None:
    span = CandidateSpan(1, 3, ["cue"])
    assert span.merge_key() == (1, 3)
