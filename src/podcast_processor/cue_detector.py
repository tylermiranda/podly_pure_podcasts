import re
from re import Pattern


class CueDetector:
    def __init__(self) -> None:
        self.url_pattern: Pattern[str] = re.compile(
            r"\b([a-z0-9\-\.]+\.(?:com|net|org|io))\b", re.I
        )
        self.promo_pattern: Pattern[str] = re.compile(
            r"\b(code|promo|save|discount)\s+\w+\b", re.I
        )
        self.phone_pattern: Pattern[str] = re.compile(
            r"\b(?:\+?1[ -]?)?\d{3}[ -]?\d{3}[ -]?\d{4}\b"
        )
        self.cta_pattern: Pattern[str] = re.compile(
            r"\b(visit|go to|head to|check out|head over|sign up|start today|"
            r"start now|use code|offer|deal|free trial|store near you)\b",
            re.I,
        )
        self.transition_pattern: Pattern[str] = re.compile(
            r"\b(back to the show|after the break|stay tuned|we'll be right back|now back)\b",
            re.I,
        )
        self.self_promo_pattern: Pattern[str] = re.compile(
            r"\b(my|our)\s+(book|course|newsletter|fund|patreon|substack|community|platform)\b",
            re.I,
        )
        # Generic sponsor language — not rotating CPG brand names.
        self.sponsor_language_pattern: Pattern[str] = re.compile(
            r"(?:"
            r"\bthis message comes from\b|"
            r"\bsupport comes from\b|"
            r"\bthis ad is sponsored\b|"
            r"\bbrought to you by\b|"
            r"\bpromo code\b|"
            r"\bhere'?s a show we recommend\b|"
            r"\bavailability and coverage vary\b|"
            r"\bacast(?:\.com)?\b"
            r")",
            re.I,
        )
        # Promotional phrasing without a URL yet (network premium, unlock CTAs).
        self.promotional_copy_pattern: Pattern[str] = re.compile(
            r"(?:"
            r"\bwithout (?:ads|adverts|advertisements)\b|"
            r"\bunlock more episodes\b|"
            r"\bhit the link\b|"
            r"\bepisode description\b"
            r")",
            re.I,
        )

    def has_cue(self, text: str) -> bool:
        return bool(
            self.url_pattern.search(text)
            or self.promo_pattern.search(text)
            or self.phone_pattern.search(text)
            or self.cta_pattern.search(text)
            or self.promotional_copy_pattern.search(text)
        )

    def has_promotional_copy(self, text: str) -> bool:
        return bool(self.promotional_copy_pattern.search(text or ""))

    def has_strong_ad_cue(self, text: str) -> bool:
        """URL, promo code, phone, or sponsor-language — not generic 'go to' CTAs."""
        stripped = text or ""
        if not stripped:
            return False
        return bool(
            self.url_pattern.search(stripped)
            or self.promo_pattern.search(stripped)
            or self.phone_pattern.search(stripped)
            or self.sponsor_language_pattern.search(stripped)
        )

    def analyze(self, text: str) -> dict[str, bool]:
        return {
            "url": bool(self.url_pattern.search(text)),
            "promo": bool(self.promo_pattern.search(text)),
            "phone": bool(self.phone_pattern.search(text)),
            "cta": bool(self.cta_pattern.search(text)),
            "transition": bool(self.transition_pattern.search(text)),
            "self_promo": bool(self.self_promo_pattern.search(text)),
            "sponsor_language": bool(self.sponsor_language_pattern.search(text)),
            "promotional_copy": bool(self.promotional_copy_pattern.search(text)),
        }

    def highlight_cues(self, text: str) -> str:
        """
        Highlights detected cues in the text by wrapping them in *** ***.
        Useful for drawing attention to cues in LLM prompts.
        """
        matches: list[tuple[int, int]] = []
        patterns = [
            self.url_pattern,
            self.promo_pattern,
            self.phone_pattern,
            self.cta_pattern,
            self.transition_pattern,
            self.self_promo_pattern,
            self.promotional_copy_pattern,
            self.sponsor_language_pattern,
        ]

        for pattern in patterns:
            for match in pattern.finditer(text):
                matches.append(match.span())

        if not matches:
            return text

        # Sort by start, then end (descending) to handle containment
        matches.sort(key=lambda x: (x[0], -x[1]))

        # Merge overlapping intervals
        merged: list[tuple[int, int]] = []
        if matches:
            curr_start, curr_end = matches[0]
            for next_start, next_end in matches[1:]:
                if next_start < curr_end:  # Overlap
                    curr_end = max(curr_end, next_end)
                else:
                    merged.append((curr_start, curr_end))
                    curr_start, curr_end = next_start, next_end
            merged.append((curr_start, curr_end))

        # Reconstruct string backwards to avoid index shifting
        result_parts = []
        last_idx = len(text)

        for start, end in reversed(merged):
            result_parts.append(text[end:last_idx])  # Unchanged suffix
            result_parts.append(" ***")
            result_parts.append(text[start:end])  # The match
            result_parts.append("*** ")
            last_idx = start

        result_parts.append(text[:last_idx])  # Remaining prefix

        return "".join(reversed(result_parts))
