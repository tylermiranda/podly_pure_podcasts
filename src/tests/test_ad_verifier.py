"""Unit tests for ad verify adjustment application."""

from types import SimpleNamespace

from podcast_processor.ad_verifier import (
    _suspicious_gap_lines,
    apply_verify_adjustments,
    build_verify_messages,
    parse_verify_response,
    windows_to_refined_payload,
)


def test_suspicious_gap_lines_includes_audio_gaps() -> None:
    segments = [
        SimpleNamespace(start_time=0.0, end_time=5.0, text="hello world"),
    ]
    text = _suspicious_gap_lines(
        [(20.0, 30.0)],
        segments,
        audio_gaps=[(5.0, 15.0)],
    )
    assert "SUSPECT_GAP" in text
    assert "5.0-15.0" in text


def test_build_verify_messages_includes_audio_gaps() -> None:
    messages = build_verify_messages(
        draft_windows=[(10.0, 20.0)],
        segments=[],
        title="Episode",
        audio_gaps=[(50.0, 60.0)],
    )
    assert "SUSPECT_GAP" in messages[1]["content"]


def test_apply_verify_expands_and_drops() -> None:
    draft = [(10.0, 20.0), (100.0, 130.0), (200.0, 220.0)]
    adjustments = [
        {"action": "expand", "start": 5.0, "end": 25.0, "confidence": 0.9},
        {"action": "drop", "start": 100.0, "end": 130.0, "confidence": 0.95},
        {"action": "add", "start": 300.0, "end": 340.0, "confidence": 0.8},
        {"action": "confirm", "start": 200.0, "end": 220.0, "confidence": 0.7},
    ]
    result = apply_verify_adjustments(draft, adjustments)
    assert (5.0, 25.0) in result
    assert (200.0, 220.0) in result
    assert (300.0, 340.0) in result
    assert not any(abs(s - 100.0) < 0.1 for s, _e in result)


def test_apply_verify_ignores_low_confidence() -> None:
    draft = [(10.0, 20.0)]
    result = apply_verify_adjustments(
        draft,
        [{"action": "drop", "start": 10.0, "end": 20.0, "confidence": 0.2}],
    )
    assert result == [(10.0, 20.0)]


def test_parse_verify_response_json_and_fenced() -> None:
    raw = '{"adjustments":[{"action":"add","start":1,"end":2,"confidence":0.9}]}'
    assert parse_verify_response(raw)[0]["action"] == "add"
    noisy = 'Here you go:\n```json\n{"adjustments":[{"action":"drop","start":1,"end":2,"confidence":0.9}]}\n```'
    assert parse_verify_response(noisy)[0]["action"] == "drop"


def test_windows_to_refined_payload_shape() -> None:
    payload = windows_to_refined_payload([(1.0, 5.0)])
    assert payload[0]["refined_start"] == 1.0
    assert payload[0]["refined_end"] == 5.0
