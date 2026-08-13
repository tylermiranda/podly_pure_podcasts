"""Heuristic scorecard for Podly episode ad Stats."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from podcast_processor.cue_detector import CueDetector  # noqa: E402

_CUE_DETECTOR = CueDetector()

CONTENT_PATTERNS = [
    r"\bwelcome to\b",
    r"\bwelcome back\b",
    r"\bback from the break\b",
    r"\ball right,? class\b",
    r"\bi'?m your host\b",
    r"\bi want to thank our professor\b",
    r"\bsummer school is produced\b",
    r"\bthat'?s it for this episode\b",
    r"\bphew,? we'?ve been around the world\b",
    r"\bfor a generation of kids\b",
    r"\bfrom the viewpoint of a kid\b",
    r"\bit'?s edited by\b",
    r"\bfact-?checked by\b",
]


def _has_sponsor(text: str) -> bool:
    return _CUE_DETECTOR.has_strong_ad_cue(text)


REFINER_BAD_PHRASES = [
    "i want to thank",
    "all right, class, back",
    "back from the break",
    "welcome to the",
    "summer school is produced",
]

AD_PCT_BANDS = {
    "planet money": (2.0, 14.0),
    "history of the 90": (5.0, 28.0),
    "everything 80": (2.0, 16.0),
    "default": (1.0, 35.0),
}


def _match_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def _band_for_feed(feed_title: str | None) -> tuple[float, float]:
    title_l = (feed_title or "").lower()
    for key, band in AD_PCT_BANDS.items():
        if key != "default" and key in title_l:
            return band
    return AD_PCT_BANDS["default"]


def _score_ad_labels(
    ids: list[dict[str, Any]],
    by_id: dict[Any, dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, int, int]:
    failures: list[dict[str, Any]] = []
    sponsor_hits = 0
    content_as_ad = 0
    bleeds = 0
    for a in ids:
        if (a.get("label") or "ad") != "ad":
            continue
        text = (a.get("segment_text") or "").strip()
        if not text and a.get("transcript_segment_id") in by_id:
            text = (by_id[a["transcript_segment_id"]].get("text") or "").strip()
        start = a.get("segment_start_time")
        end = a.get("segment_end_time")
        has_sponsor = _has_sponsor(text)
        has_content = _match_any(text, CONTENT_PATTERNS)
        if has_sponsor:
            sponsor_hits += 1
        if has_content and not has_sponsor:
            content_as_ad += 1
            failures.append(
                {
                    "type": "content_as_ad",
                    "start": start,
                    "end": end,
                    "confidence": a.get("confidence"),
                    "text": text[:180],
                }
            )
        elif has_sponsor and has_content:
            bleeds += 1
            failures.append(
                {
                    "type": "boundary_bleed",
                    "start": start,
                    "end": end,
                    "confidence": a.get("confidence"),
                    "text": text[:180],
                }
            )
    return failures, sponsor_hits, content_as_ad, bleeds


def _score_refiner(model_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for mc in model_calls:
        resp = (mc.get("response") or "").lower()
        if "refined_" not in resp and "refined_start" not in resp:
            continue
        for phrase in REFINER_BAD_PHRASES:
            if phrase in resp:
                failures.append(
                    {
                        "type": "refiner_bad_phrase",
                        "model_call_id": mc.get("id"),
                        "phrase": phrase,
                        "response_preview": (mc.get("response") or "")[:240],
                    }
                )
                break
    return failures


def score_stats(
    stats: dict[str, Any],
    *,
    feed_title: str | None = None,
) -> dict[str, Any]:
    """Return scorecard with pass bool and structured failures."""
    ids = stats.get("identifications") or []
    segs = stats.get("transcript_segments") or []
    by_id = {s["id"]: s for s in segs if "id" in s}
    ps = stats.get("processing_stats") or {}
    ad_pct = float(ps.get("ad_percentage") or 0.0)
    duration = float(ps.get("original_duration_seconds") or 0.0)

    label_failures, sponsor_hits, content_as_ad, bleeds = _score_ad_labels(ids, by_id)
    failures = label_failures + _score_refiner(stats.get("model_calls") or [])
    warnings: list[str] = []

    lo, hi = _band_for_feed(feed_title)
    if duration > 60 and (ad_pct < lo or ad_pct > hi):
        msg = f"ad_pct {ad_pct} outside soft band [{lo},{hi}]"
        warnings.append(msg)
        failures.append(
            {
                "type": "ad_pct_band",
                "ad_pct": ad_pct,
                "band": [lo, hi],
                "detail": msg,
            }
        )

    ad_label_count = sum(1 for i in ids if (i.get("label") or "ad") == "ad")
    if duration > 60 and ad_label_count == 0:
        missed = [s for s in segs if _has_sponsor((s.get("text") or "").strip())]
        if missed:
            failures.append(
                {
                    "type": "missed_sponsor_cues",
                    "count": len(missed),
                    "examples": [
                        {
                            "start": s.get("start_time"),
                            "text": ((s.get("text") or "").strip())[:120],
                        }
                        for s in missed[:3]
                    ],
                }
            )

    if ids and sponsor_hits == 0 and ad_pct >= 3.0:
        failures.append(
            {
                "type": "no_sponsor_cues",
                "ad_pct": ad_pct,
                "ad_identifications": len(ids),
            }
        )

    return {
        "pass": len(failures) == 0,
        "ad_pct": ad_pct,
        "duration_seconds": duration,
        "ad_identifications": len(ids),
        "sponsor_hits": sponsor_hits,
        "content_as_ad": content_as_ad,
        "bleeds": bleeds,
        "failures": failures,
        "warnings": warnings,
    }
