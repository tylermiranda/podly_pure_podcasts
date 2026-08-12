"""Deterministic guards so LLM ad labels do not cut show content.

Whisper often puts a sponsor CTA and the cold-open in one segment. Flash-class
models then label the whole segment (or nearby credits/outros) as ads. These
heuristics run after the LLM so we can drop content-only hits and shrink mixed
segments before audio is cut.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from re import Pattern
from typing import Any

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


def _normalize_words(words: Sequence[Any] | None) -> list[dict[str, Any]]:
    if not words:
        return []
    normalized: list[dict[str, Any]] = []
    for item in words:
        if isinstance(item, Mapping):
            token = item.get("word", item.get("text"))
            start = item.get("start")
        else:
            token = getattr(item, "word", None) or getattr(item, "text", None)
            start = getattr(item, "start", None)
        if token is None or start is None:
            continue
        normalized.append({"word": str(token), "start": float(start)})
    return normalized


def _word_start_for_char_offset(
    words: list[dict[str, Any]], char_offset: int, *, spaced: bool = False
) -> float | None:
    cursor = 0
    for index, item in enumerate(words):
        token = item["word"].strip() if spaced else item["word"]
        if spaced:
            if index > 0:
                cursor += 1
            next_cursor = cursor + len(token)
        else:
            next_cursor = cursor + len(token)
        if char_offset < next_cursor:
            return item["start"]
        cursor = next_cursor
    return None


def _resume_time_from_words(text: str, words: list[dict[str, Any]]) -> float | None:
    reconstructed = "".join(item["word"] for item in words)
    match = _first_match(reconstructed, CONTENT_RESUME_PATTERNS)
    if match is not None:
        return _word_start_for_char_offset(words, match.start())

    spaced = " ".join(item["word"].strip() for item in words if item["word"].strip())
    match = _first_match(spaced, CONTENT_RESUME_PATTERNS)
    if match is None:
        match = _first_match(text, CONTENT_RESUME_PATTERNS)
        if match is None:
            return None
        if reconstructed.strip() == (text or "").strip():
            leading = len(reconstructed) - len(reconstructed.lstrip())
            return _word_start_for_char_offset(words, match.start() + leading)
        return None
    return _word_start_for_char_offset(words, match.start(), spaced=True)


def estimate_content_resume_time(
    start: float,
    end: float,
    text: str,
    words: Sequence[Any] | None = None,
) -> tuple[float | None, bool]:
    """Estimate when show content resumes inside a mixed Whisper segment.

    Returns (timestamp, used_word_times). Word times win when they can be
    aligned to the content-resume phrase; otherwise a character-ratio guess
    is used.
    """
    stripped = text or ""
    if not stripped or end <= start:
        return None, False
    parsed_words = _normalize_words(words)
    if parsed_words:
        word_time = _resume_time_from_words(stripped, parsed_words)
        if word_time is not None:
            return max(start, min(end, word_time)), True
    match = _first_match(stripped, CONTENT_RESUME_PATTERNS)
    if match is None:
        return None, False
    ratio = match.start() / max(len(stripped), 1)
    return start + (end - start) * ratio, False


def ad_cut_window(
    start: float,
    end: float,
    text: str,
    words: Sequence[Any] | None = None,
) -> tuple[float, float] | None:
    """Return the time window that should be cut, or None to keep the audio.

    Content-only labels are dropped. Mixed CTA+cold-open segments are trimmed
    to the sponsor portion (0.2s pad before a word-aligned content phrase,
    0.4s pad for the character-ratio fallback).
    """
    stripped = text or ""
    if is_content_only(stripped):
        return None
    if is_mixed_ad_content(stripped):
        resume_at, used_words = estimate_content_resume_time(
            start, end, stripped, words
        )
        if resume_at is None:
            return (start, end)
        pad = 0.2 if used_words else 0.4
        cut_end = max(start, min(end, resume_at - pad))
        if cut_end - start < 1.0:
            # Sponsor tail is too short to cut without eating the cold-open.
            return None
        return (start, cut_end)
    return (start, end)
