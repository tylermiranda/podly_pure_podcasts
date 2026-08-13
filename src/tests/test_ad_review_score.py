"""Tests for the Tower ad-review heuristic scorer."""

import importlib.util
from pathlib import Path

_SCORE_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "podly_ad_review" / "score.py"
)
_SPEC = importlib.util.spec_from_file_location("podly_ad_review_score", _SCORE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_SCORE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SCORE)
score_stats = _SCORE.score_stats


def test_zero_ads_with_acast_transcript_is_missed_sponsor() -> None:
    stats = {
        "identifications": [],
        "transcript_segments": [
            {
                "id": 1,
                "start_time": 0.0,
                "end_time": 25.0,
                "text": "ACAST powers the world's best podcasts. Here's a show we recommend.",
            },
            {
                "id": 2,
                "start_time": 43.5,
                "end_time": 68.4,
                "text": "Coming up today, the final phase of jury selection.",
            },
        ],
        "processing_stats": {
            "ad_percentage": 0.0,
            "original_duration_seconds": 691.7,
        },
        "model_calls": [],
    }
    card = score_stats(stats, feed_title="History of the 90s")
    assert card["pass"] is False
    types = [f["type"] for f in card["failures"]]
    assert "missed_sponsor_cues" in types
    missed = next(f for f in card["failures"] if f["type"] == "missed_sponsor_cues")
    assert missed["count"] >= 1
