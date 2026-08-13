from types import SimpleNamespace

import pytest

from podcast_processor.ad_spans import (
    apply_content_guard_to_span,
    find_containing_segment,
    resolve_prediction_spans,
)
from podcast_processor.model_output import AdSegmentPrediction


def _seg(
    *,
    id: int,
    start: float,
    end: float,
    text: str,
    words: list | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        start_time=start,
        end_time=end,
        text=text,
        words=words,
        sequence_num=id,
    )


def test_find_containing_segment_uses_interior_offset() -> None:
    mixed = _seg(
        id=1,
        start=158.5,
        end=174.1,
        text="ACAST.com Welcome to the Tupac Murder Trial. I'm your host.",
    )
    matched = find_containing_segment([mixed], 170.2)
    assert matched is mixed


def test_offset_near_start_still_matches() -> None:
    seg = _seg(id=1, start=0.0, end=10.0, text="This message comes from WISE.")
    assert find_containing_segment([seg], 0.2) is seg


def test_start_end_prediction_clips_overlapping_segments() -> None:
    segs = [
        _seg(id=1, start=0.0, end=10.0, text="This message comes from WISE."),
        _seg(id=2, start=10.0, end=20.0, text="Visit wise.com to learn more."),
        _seg(id=3, start=20.0, end=30.0, text="Welcome back to the show."),
    ]
    pred = AdSegmentPrediction(start=2.0, end=18.0, confidence=0.95)
    spans = resolve_prediction_spans(pred, segs)
    assert [s.segment.id for s in spans] == [1, 2]
    assert spans[0].start == 2.0
    assert spans[0].end == 10.0
    assert spans[1].start == 10.0
    assert spans[1].end == 18.0


def test_mixed_line_guard_trims_cold_open() -> None:
    mixed = _seg(
        id=1,
        start=158.5,
        end=174.1,
        text=(
            "ACAST.com Welcome to the Tupac Murder Trial from the history of the 90s. "
            "I'm your host, Kathy Kanzora."
        ),
        words=[
            {"word": "ACAST.com", "start": 158.5, "end": 159.4},
            {"word": " Welcome", "start": 162.4, "end": 162.9},
            {"word": " to", "start": 162.9, "end": 163.1},
        ],
    )
    window = apply_content_guard_to_span(mixed, 158.5, 174.1)
    assert window is not None
    assert window[0] == 158.5
    assert window[1] == pytest.approx(162.2)
