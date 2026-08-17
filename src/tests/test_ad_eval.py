from types import SimpleNamespace

from podcast_processor.ad_eval import score_windows, window_iou
from podcast_processor.ad_spans import (
    apply_content_guard_to_span,
    expand_cut_windows,
    resolve_prediction_spans,
)
from podcast_processor.model_output import AdSegmentPrediction
from tests.salvador_dali_fixture import (
    NARRATIVE_START_TIMES,
    SALVADOR_DALI_GOLD_WINDOWS,
    labeled_ad_windows,
    salvador_dali_segments,
    windows_cover,
)


def test_window_iou_perfect_and_partial() -> None:
    assert window_iou((0.0, 10.0), (0.0, 10.0)) == 1.0
    assert window_iou((0.0, 10.0), (5.0, 15.0)) == 5.0 / 15.0


def test_score_windows_counts_true_positives() -> None:
    result = score_windows(
        predicted=[(0.0, 40.0), (200.0, 230.0)],
        gold=[(0.0, 41.0), (198.0, 232.0), (400.0, 430.0)],
        iou_threshold=0.5,
    )
    assert result["true_positives"] == 2
    assert result["false_positives"] == 0
    assert result["false_negatives"] == 1
    assert result["recall"] == 2 / 3


def test_gold_mixed_whisper_line_is_trimmed_not_whole_segment() -> None:
    """Method check: CTA+cold-open must not cut the storytelling half."""
    segment = SimpleNamespace(
        id=1,
        start_time=111.7,
        end_time=130.1,
        text=(
            "Availability and coverage vary by state and insurance plan. "
            "For a generation of kids, we only knew the big three."
        ),
        words=None,
        sequence_num=0,
    )
    pred = AdSegmentPrediction(segment_offset=111.7, confidence=0.9)
    spans = resolve_prediction_spans(pred, [segment])
    assert len(spans) == 1
    window = apply_content_guard_to_span(segment, spans[0].start, spans[0].end)
    assert window is not None
    gold = (111.7, 120.0)
    assert window_iou(window, gold) >= 0.5
    assert window[1] < 125.0


def test_salvador_dali_expanded_windows_match_gold() -> None:
    segs = salvador_dali_segments()
    predicted = expand_cut_windows(labeled_ad_windows(segs), segs)
    result = score_windows(predicted, SALVADOR_DALI_GOLD_WINDOWS, iou_threshold=0.5)
    assert result["false_negatives"] == 0
    assert result["false_positives"] == 0
    assert result["recall"] == 1.0
    assert result["precision"] == 1.0
    for stamp in NARRATIVE_START_TIMES:
        assert not windows_cover(predicted, stamp)
