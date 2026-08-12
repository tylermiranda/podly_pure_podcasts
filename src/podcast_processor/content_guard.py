"""Deterministic guards so LLM ad labels do not cut show content.

Whisper often puts a sponsor CTA and the cold-open in one segment. Flash-class
models then label the whole segment (or nearby credits/outros) as ads. These
heuristics run after the LLM so we can drop content-only hits and shrink mixed
segments before audio is cut.
"""

from __future__ import annotations

import re
from re import Pattern

# Show content that is frequently mislabeled as an ad.
CONTENT_RESUME_PATTERNS: tuple[Pattern[str], ...] = (
    re.compile(r"\bwelcome to\b", re.I),
    re.compile(r"\bwelcome back\b", re.I),
    re.compile(r"\bback from the break\b", re.I),
    re.compile(r"\ball right,? class\b", re.I),
    re.compile(r"\bi'?m (?:your host|jamie)\b", re.I),
    re.compile(r"\bi want to thank our professor\b", re.I),
    re.compile(r"\bsummer school is produced\b", re.I),
    re.compile(r"\bthat'?s it for this episode\b", re.I),
    re.compile(r"\bphew,? we'?ve been around the world\b", re.I),
    re.compile(r"\bfor a generation of kids\b", re.I),
    re.compile(r"\bfrom the viewpoint of a kid\b", re.I),
    re.compile(r"\bit'?s edited by\b", re.I),
    re.compile(r"\bfact-?checked by\b", re.I),
    re.compile(r"\bpresented by me,?\b", re.I),
    re.compile(r"\btoday on (?:the show|summer school|planet money)\b", re.I),
)

SPONSOR_PATTERNS: tuple[Pattern[str], ...] = (
    re.compile(r"\bthis message comes from\b", re.I),
    re.compile(r"\bsupport comes from\b", re.I),
    re.compile(r"\bthis ad is sponsored\b", re.I),
    re.compile(r"\bbrought to you by\b", re.I),
    re.compile(r"\bpromo code\b", re.I),
    re.compile(r"\bzocdoc\b", re.I),
    re.compile(r"\bgrowtherapy\b", re.I),
    re.compile(r"\bgrow therapy\b", re.I),
    re.compile(r"\bjerry\.ai\b", re.I),
    re.compile(r"\bbombas\b", re.I),
    re.compile(r"\bmint mobile\b", re.I),
    re.compile(r"\bacast\.com\b", re.I),
    re.compile(r"\bwise\.com\b", re.I),
    re.compile(r"\bvisit\s+\S+\.(?:com|net|org|io)\b", re.I),
    re.compile(r"\bgo to\s+\S+\.(?:com|net|org|io)\b", re.I),
    re.compile(r"\bavailability and coverage vary\b", re.I),
)


def _first_match(text: str, patterns: tuple[Pattern[str], ...]) -> re.Match[str] | None:
    earliest: re.Match[str] | None = None
    for pattern in patterns:
        match = pattern.search(text)
        if match is None:
            continue
        if earliest is None or match.start() < earliest.start():
            earliest = match
    return earliest


def has_content_resume(text: str) -> bool:
    return _first_match(text, CONTENT_RESUME_PATTERNS) is not None


def has_sponsor_cue(text: str) -> bool:
    return _first_match(text, SPONSOR_PATTERNS) is not None


def is_content_only(text: str) -> bool:
    """True when the text is show content with no sponsor cue."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    return has_content_resume(stripped) and not has_sponsor_cue(stripped)


def is_mixed_ad_content(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    return has_sponsor_cue(stripped) and has_content_resume(stripped)


def estimate_content_resume_time(start: float, end: float, text: str) -> float | None:
    """Estimate when show content resumes inside a mixed Whisper segment."""
    stripped = text or ""
    if not stripped or end <= start:
        return None
    match = _first_match(stripped, CONTENT_RESUME_PATTERNS)
    if match is None:
        return None
    ratio = match.start() / max(len(stripped), 1)
    return start + (end - start) * ratio


def ad_cut_window(start: float, end: float, text: str) -> tuple[float, float] | None:
    """Return the time window that should be cut, or None to keep the audio.

    Content-only labels are dropped. Mixed CTA+cold-open segments are trimmed
    to the sponsor portion (with a 0.4s pad before the content phrase).
    """
    stripped = text or ""
    if is_content_only(stripped):
        return None
    if is_mixed_ad_content(stripped):
        resume_at = estimate_content_resume_time(start, end, stripped)
        if resume_at is None:
            return (start, end)
        cut_end = max(start, min(end, resume_at - 0.4))
        if cut_end - start < 1.0:
            # Sponsor tail is too short to cut without eating the cold-open.
            return None
        return (start, cut_end)
    return (start, end)
