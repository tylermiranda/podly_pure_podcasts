"""Tests for deterministic ad/content guards."""

import pytest

from podcast_processor.ad_merger import AdGroup, AdMerger
from podcast_processor.content_guard import (
    ad_cut_window,
    is_content_only,
    is_mixed_ad_content,
)


def test_content_only_outro() -> None:
    text = (
        "Okay, that's it for this episode of the Tupac murder trial from "
        "History of the 90s, presented by me, Kathy Gonzora."
    )
    assert is_content_only(text)
    assert ad_cut_window(925.4, 955.1, text) is None


def test_mixed_acast_welcome_is_trimmed() -> None:
    text = (
        "ACAST.com Welcome to the Tupac Murder Trial from the history of the 90s. "
        "I'm your host, Kathy Kanzora."
    )
    assert is_mixed_ad_content(text)
    window = ad_cut_window(158.5, 174.1, text)
    assert window is not None
    start, end = window
    assert start == 158.5
    assert end < 165.0
    assert end > 158.5


def test_mixed_grow_disclaimer_then_cold_open() -> None:
    text = (
        "Availability and coverage vary by state and insurance plan. "
        "For a generation of kids, we only knew the big three."
    )
    window = ad_cut_window(111.7, 130.1, text)
    assert window is not None
    assert window[1] < 125.0


def test_pure_sponsor_unchanged() -> None:
    text = "This message comes from WISE, the smart way to manage your money around the world."
    assert ad_cut_window(10.0, 25.0, text) == (10.0, 25.0)
    assert not is_content_only(text)


def test_mixed_uses_word_start_instead_of_char_ratio() -> None:
    text = (
        "ACAST.com Welcome to the Tupac Murder Trial from the history of the 90s. "
        "I'm your host, Kathy Kanzora."
    )
    words = [
        {"word": "ACAST.com", "start": 158.5, "end": 159.4},
        {"word": " Welcome", "start": 162.4, "end": 162.9},
        {"word": " to", "start": 162.9, "end": 163.1},
        {"word": " the", "start": 163.1, "end": 163.3},
    ]
    ratio_window = ad_cut_window(158.5, 174.1, text)
    word_window = ad_cut_window(158.5, 174.1, text, words=words)
    assert ratio_window is not None
    assert word_window is not None
    assert word_window[0] == 158.5
    assert word_window[1] == pytest.approx(162.2)
    assert word_window[1] != ratio_window[1]


def test_mixed_falls_back_to_ratio_when_words_do_not_align() -> None:
    text = "ACAST.com Welcome to the show."
    ratio_window = ad_cut_window(10.0, 30.0, text)
    fallback = ad_cut_window(
        10.0,
        30.0,
        text,
        words=[{"word": "zzz", "start": 18.0, "end": 18.4}],
    )
    assert fallback == ratio_window


def test_merger_does_not_glue_unrelated_high_confidence_ads() -> None:
    merger = AdMerger()
    left = AdGroup(
        segments=[],
        identifications=[],
        start_time=0.0,
        end_time=120.0,
        confidence_avg=0.98,
        keywords=["zocdoc.com"],
    )
    right = AdGroup(
        segments=[],
        identifications=[],
        start_time=128.0,
        end_time=160.0,
        confidence_avg=0.98,
        keywords=["growtherapy.com"],
    )
    assert merger._should_merge(left, right) is False


def test_merger_still_merges_shared_sponsor() -> None:
    merger = AdMerger()
    left = AdGroup(
        segments=[],
        identifications=[],
        start_time=0.0,
        end_time=20.0,
        confidence_avg=0.9,
        keywords=["zocdoc.com"],
    )
    right = AdGroup(
        segments=[],
        identifications=[],
        start_time=24.0,
        end_time=40.0,
        confidence_avg=0.9,
        keywords=["zocdoc.com"],
    )
    assert merger._should_merge(left, right) is True
