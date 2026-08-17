from types import SimpleNamespace

import pytest

from podcast_processor.ad_spans import (
    apply_content_guard_to_span,
    expand_cut_windows,
    fill_ad_holes,
    find_containing_segment,
    lead_in_ad_windows,
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


def test_fill_ad_holes_glues_cta_islands_across_ad_copy() -> None:
    segs = [
        _seg(id=1, start=4.9, end=11.3, text="Bilt members earn points at Amazon.com."),
        _seg(
            id=2,
            start=11.6,
            end=16.1,
            text="Bilt also gives members access to its concierge in the Bilt app.",
        ),
        _seg(
            id=3,
            start=29.7,
            end=35.1,
            text="Download the Bilt app at joinbuilt.com slash ACAST.",
        ),
        _seg(id=4, start=104.1, end=106.9, text="This is the Tupac Murder Trial."),
        _seg(id=5, start=107.1, end=108.7, text="I'm your host, Kathy Kanzora."),
    ]
    filled = fill_ad_holes([(4.9, 11.3), (29.7, 35.1)], segs)
    assert filled == [(4.9, 35.1)]


def test_fill_ad_holes_does_not_cross_content_resume() -> None:
    segs = [
        _seg(id=1, start=0.0, end=8.0, text="Visit wise.com to learn more."),
        _seg(id=2, start=10.0, end=18.0, text="I'm your host, Kathy Kanzora."),
        _seg(id=3, start=20.0, end=28.0, text="This message comes from Ethos."),
    ]
    filled = fill_ad_holes([(0.0, 8.0), (20.0, 28.0)], segs)
    assert filled == [(0.0, 8.0), (20.0, 28.0)]


def test_lead_in_walks_back_through_ad_copy_not_show() -> None:
    segs = [
        _seg(
            id=1,
            start=0.0,
            end=5.0,
            text="And when Sam finally has to answer for his crimes, he'll reveal a whole other layer of fraud.",
        ),
        _seg(
            id=2,
            start=11.1,
            end=15.0,
            text="That's where 7th Generation comes in.",
        ),
        _seg(
            id=3,
            start=15.2,
            end=18.0,
            text="With the VentureX Business Card from Capital One, you earn unlimited double miles.",
        ),
        _seg(
            id=4,
            start=18.2,
            end=20.0,
            text="See CapitalOne.com for details.",
        ),
        _seg(
            id=5,
            start=21.0,
            end=23.0,
            text="From Audible Originals, I'm Saatchi Cole.",
        ),
    ]
    expanded = lead_in_ad_windows([(18.2, 20.0)], segs)
    assert expanded == [(11.1, 20.0)]


def test_expand_cut_windows_recovers_full_preroll() -> None:
    segs = [
        _seg(
            id=1, start=0.1, end=2.4, text="Are you getting rewarded for paying rent?"
        ),
        _seg(id=2, start=2.8, end=4.5, text="If not, you need to check out Bilt."),
        _seg(
            id=3,
            start=4.9,
            end=11.3,
            text="Bilt members earn points on Amazon.com purchases.",
        ),
        _seg(
            id=4, start=11.6, end=16.1, text="Bilt also gives members concierge access."
        ),
        _seg(
            id=5,
            start=29.7,
            end=35.1,
            text="Download the Bilt app at joinbuilt.com slash ACAST.",
        ),
        _seg(id=6, start=104.1, end=106.9, text="This is the Tupac Murder Trial."),
        _seg(id=7, start=107.1, end=108.7, text="I'm your host, Kathy Kanzora."),
    ]
    windows = expand_cut_windows([(4.9, 11.3), (29.7, 35.1)], segs)
    assert windows == [(0.1, 35.1)]
