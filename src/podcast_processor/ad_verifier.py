"""Second-pass LLM verify of draft ad cut windows before audio cut.

Mutates shared cut inputs by writing verified windows to
``Post.refined_ad_boundaries`` (same field consumed by Stats + ffmpeg).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from app.writer.client import writer_client
from podcast_processor.cue_detector import CueDetector

logger = logging.getLogger(__name__)

VERIFY_SYSTEM_PROMPT = """You verify podcast ad cut windows before publishing.
Given draft cut ranges and transcript context, return JSON only:
{"adjustments":[{"action":"confirm|expand|shrink|add|drop","start":0.0,"end":0.0,"confidence":0.0,"reason":"..."}]}

Rules:
- confirm: draft range is correct (echo start/end).
- expand/shrink: return corrected start/end for that ad block.
- add: a missed ad not in the draft.
- drop: draft cut is false (host outro, credits, narrative) — return the draft start/end to drop.
- Prefer dropping false cuts over removing show content.
- Prefer expanding when CTA/URL/sponsor language is adjacent but outside the draft.
- Confidence 0-1. Only include adjustments with confidence >= 0.6.
- Do not invent ads without transcript evidence.
"""

_ACTION_SET = frozenset({"confirm", "expand", "shrink", "add", "drop"})
_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


def apply_verify_adjustments(
    draft_windows: list[tuple[float, float]],
    adjustments: list[dict[str, Any]],
    *,
    min_confidence: float = 0.6,
) -> list[tuple[float, float]]:
    """Apply verify adjustments to draft windows.

    Matching is by maximum IoU against draft windows for expand/shrink/drop/confirm.
    """
    from podcast_processor.ad_eval import merge_windows, window_iou

    remaining = list(draft_windows)
    adds: list[tuple[float, float]] = []
    drops: set[int] = set()
    replacements: dict[int, tuple[float, float]] = {}

    for raw in adjustments:
        if not isinstance(raw, dict):
            continue
        action = str(raw.get("action") or "").strip().lower()
        if action not in _ACTION_SET:
            continue
        try:
            confidence = float(raw.get("confidence", 0.0) or 0.0)
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if confidence < min_confidence or end <= start:
            continue

        if action == "add":
            adds.append((start, end))
            continue

        # Match to a draft window
        best_i = -1
        best_iou = 0.0
        candidate = (start, end)
        for idx, draft in enumerate(remaining):
            if idx in drops:
                continue
            iou = window_iou(candidate, draft)
            # Also allow matching by containment / near overlap for shrink/expand
            if action in {"expand", "shrink", "confirm", "drop"} and iou == 0.0:
                ds, de = draft
                if start <= de and end >= ds:
                    iou = 0.01
            if iou > best_iou:
                best_iou = iou
                best_i = idx
        if best_i < 0:
            if action in {"expand", "add"}:
                adds.append((start, end))
            continue

        if action == "drop":
            drops.add(best_i)
        elif action in {"confirm", "expand", "shrink"}:
            if action == "confirm":
                replacements[best_i] = remaining[best_i]
            else:
                replacements[best_i] = (start, end)

    result: list[tuple[float, float]] = []
    for idx, draft in enumerate(remaining):
        if idx in drops:
            continue
        result.append(replacements.get(idx, draft))
    result.extend(adds)
    return merge_windows(result, gap_seconds=1.0)


def windows_to_refined_payload(
    windows: list[tuple[float, float]],
) -> list[dict[str, float]]:
    """Store verified windows in refined_ad_boundaries shape for parse_refined_windows."""
    return [
        {
            "orig_start": float(start),
            "orig_end": float(end),
            "refined_start": float(start),
            "refined_end": float(end),
            "confidence": 0.9,
        }
        for start, end in windows
        if end > start
    ]


def _format_window_block(
    windows: list[tuple[float, float]],
    segments: list[Any],
    *,
    context_pad: float = 20.0,
) -> str:
    lines: list[str] = []
    for idx, (start, end) in enumerate(windows):
        lines.append(f"DRAFT[{idx}] {start:.1f}-{end:.1f}")
        for seg in segments:
            seg_start = float(getattr(seg, "start_time", 0.0) or 0.0)
            seg_end = float(getattr(seg, "end_time", 0.0) or 0.0)
            if seg_end < start - context_pad or seg_start > end + context_pad:
                continue
            marker = "IN" if seg_start < end and seg_end > start else "CTX"
            text = (getattr(seg, "text", None) or "").strip().replace("\n", " ")
            if len(text) > 180:
                text = text[:177] + "..."
            lines.append(f"  [{marker}] {seg_start:.1f}-{seg_end:.1f} {text}")
    return "\n".join(lines)


def _suspicious_gap_lines(
    draft_windows: list[tuple[float, float]],
    segments: list[Any],
    *,
    cue_detector: CueDetector | None = None,
) -> str:
    detector = cue_detector or CueDetector()
    covered: list[tuple[float, float]] = list(draft_windows)
    lines: list[str] = []
    for seg in segments:
        text = (getattr(seg, "text", None) or "").strip()
        if not text:
            continue
        start = float(getattr(seg, "start_time", 0.0) or 0.0)
        end = float(getattr(seg, "end_time", 0.0) or 0.0)
        if not detector.has_strong_ad_cue(text) and not detector.has_cue(text):
            continue
        in_draft = any(start < we and end > ws for ws, we in covered)
        if in_draft:
            continue
        snippet = text if len(text) <= 160 else text[:157] + "..."
        lines.append(f"SUSPECT {start:.1f}-{end:.1f} {snippet}")
    if not lines:
        return "(none)"
    return "\n".join(lines[:40])


def parse_verify_response(content: str) -> list[dict[str, Any]]:
    text = (content or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK.search(text)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        adjustments = data.get("adjustments")
        if isinstance(adjustments, list):
            return [item for item in adjustments if isinstance(item, dict)]
    return []


def build_verify_messages(
    *,
    draft_windows: list[tuple[float, float]],
    segments: list[Any],
    title: str | None = None,
) -> list[dict[str, str]]:
    draft_block = _format_window_block(draft_windows, segments)
    suspects = _suspicious_gap_lines(draft_windows, segments)
    user = (
        f"Episode: {title or '(untitled)'}\n\n"
        f"Draft cut windows with transcript context:\n{draft_block or '(none)'}\n\n"
        f"Suspicious uncued-or-cued gaps not in draft:\n{suspects}\n\n"
        "Return JSON adjustments only."
    )
    return [
        {"role": "system", "content": VERIFY_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


class AdVerifier:
    """LLM second pass over draft cut windows."""

    def __init__(self, config: Any, logger_override: logging.Logger | None = None):
        self.config = config
        self.logger = logger_override or logger

    def _model_name(self) -> str:
        verify_model = getattr(self.config, "llm_verify_model", None)
        if isinstance(verify_model, str) and verify_model.strip():
            return verify_model.strip()
        return getattr(self.config, "llm_model", None) or "gpt-4o"

    def verify_and_store(
        self,
        *,
        post: Any,
        draft_windows: list[tuple[float, float]],
        segments: list[Any],
    ) -> list[tuple[float, float]]:
        """Run verify, persist refined_ad_boundaries, return verified windows."""
        if not draft_windows and not segments:
            return []

        adjustments = self._call_llm(
            draft_windows=draft_windows,
            segments=segments,
            title=getattr(post, "title", None),
        )
        verified = apply_verify_adjustments(draft_windows, adjustments)
        # If LLM returned nothing useful, keep draft.
        if not adjustments:
            verified = list(draft_windows)

        payload = windows_to_refined_payload(verified)
        try:
            res = writer_client.update(
                "Post",
                post.id,
                {
                    "refined_ad_boundaries": payload or None,
                    "refined_ad_boundaries_updated_at": datetime.now(UTC).replace(
                        tzinfo=None
                    ),
                },
                wait=True,
            )
            if not res or not res.success:
                raise RuntimeError(
                    getattr(res, "error", "Failed to store verified ad boundaries")
                )
        except Exception:  # noqa: BLE001
            self.logger.exception(
                "Failed to persist verified boundaries for post %s",
                getattr(post, "id", None),
            )
            raise

        self.logger.info(
            "Ad verify for post %s: draft=%s verified=%s adjustments=%s",
            getattr(post, "id", None),
            len(draft_windows),
            len(verified),
            len(adjustments),
        )
        return verified

    def _call_llm(
        self,
        *,
        draft_windows: list[tuple[float, float]],
        segments: list[Any],
        title: str | None,
    ) -> list[dict[str, Any]]:
        import litellm

        from podcast_processor.llm_model_call_utils import extract_litellm_content
        from shared.llm_utils import (
            model_uses_max_completion_tokens,
            supports_json_object_response_format,
        )

        messages = build_verify_messages(
            draft_windows=draft_windows,
            segments=segments,
            title=title,
        )
        model_name = self._model_name()
        completion_args: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.1,
            "timeout": int(getattr(self.config, "openai_timeout", 300) or 300),
            "api_key": getattr(self.config, "llm_api_key", None),
        }
        if model_uses_max_completion_tokens(model_name):
            completion_args["max_completion_tokens"] = 1200
        else:
            completion_args["max_tokens"] = 1200
        base_url = getattr(self.config, "openai_base_url", None)
        if isinstance(base_url, str) and base_url.strip():
            completion_args["base_url"] = base_url.strip()
        if supports_json_object_response_format(base_url):
            completion_args["response_format"] = {"type": "json_object"}

        try:
            response = litellm.completion(**completion_args)
            content = extract_litellm_content(response).strip()
            return parse_verify_response(content)
        except Exception:  # noqa: BLE001
            self.logger.exception("Ad verify LLM call failed; keeping draft windows")
            return []
