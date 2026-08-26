#!/usr/bin/env python3
"""Score predicted ad cut windows against gold JSON.

Usage (from repo root, with app import path):

  PYTHONPATH=src python scripts/eval_ad_detection.py \\
    --gold src/tests/fixtures/ad_gold/salvador_dali.json \\
    --predicted '[[1,55.9],[678.2,733.1]]'

  PYTHONPATH=src python scripts/eval_ad_detection.py \\
    --gold src/tests/fixtures/ad_gold/salvador_dali.json \\
    --guid e60d215e-7c7c-11f1-8d29-b70ac5a24b6a

When --guid is set, loads the Flask app and scores final_cut_windows for that post.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _repo_src() -> Path:
    return Path(__file__).resolve().parent.parent / "src"


def _load_gold(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    windows = data.get("windows") or []
    parsed: list[tuple[float, float]] = []
    for item in windows:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        start, end = float(item[0]), float(item[1])
        if end > start:
            parsed.append((start, end))
    data["windows"] = parsed
    return data


def _parse_predicted(raw: str) -> list[tuple[float, float]]:
    data = json.loads(raw)
    out: list[tuple[float, float]] = []
    for item in data:
        start, end = float(item[0]), float(item[1])
        if end > start:
            out.append((start, end))
    return out


def _predicted_from_db(guid: str) -> list[tuple[float, float]]:
    sys.path.insert(0, str(_repo_src()))
    from app.extensions import db
    from app.models import Identification, ModelCall, Post, TranscriptSegment
    from app.routes.post_stats_utils import (
        cut_eligible_identifications,
        final_cut_windows,
        parse_refined_windows,
    )
    from podcast_processor.ad_corrections import load_active_corrections_for_post

    # App factory import is environment-specific; prefer create_app if present.
    try:
        from app import create_app
    except ImportError:  # pragma: no cover
        from main import create_app  # type: ignore

    app = create_app()
    with app.app_context():
        post = Post.query.filter_by(guid=guid).first()
        if post is None:
            raise SystemExit(f"Post not found for guid={guid}")
        identifications = (
            db.session.query(Identification)
            .join(
                TranscriptSegment,
                Identification.transcript_segment_id == TranscriptSegment.id,
            )
            .filter(TranscriptSegment.post_id == post.id)
            .all()
        )
        transcript_segments = (
            db.session.query(TranscriptSegment)
            .filter(TranscriptSegment.post_id == post.id)
            .order_by(TranscriptSegment.sequence_num)
            .all()
        )
        model_calls = (
            db.session.query(ModelCall).filter(ModelCall.post_id == post.id).all()
        )
        from app.runtime_config import config as runtime_config

        eligible = cut_eligible_identifications(
            identifications,
            model_calls,
            min_confidence=float(runtime_config.output.min_confidence),
        )
        refined = parse_refined_windows(
            getattr(post, "refined_ad_boundaries", None)
        )
        corrections = load_active_corrections_for_post(post.id)
        _labeled, effective = final_cut_windows(
            eligible,
            transcript_segments,
            refined_windows=refined or None,
            corrections=corrections,
        )
        return effective


def _print_score(score: dict[str, Any], *, guid: str | None, title: str | None) -> None:
    label = title or guid or "eval"
    print(f"=== Ad detection eval: {label} ===")
    if guid:
        print(f"guid: {guid}")
    keys = [
        "precision",
        "recall",
        "f1",
        "mean_iou",
        "mae_start",
        "mae_end",
        "false_cut_seconds",
        "residual_ad_seconds",
        "true_positives",
        "false_positives",
        "false_negatives",
        "predicted_ad_seconds",
        "gold_ad_seconds",
        "overlap_seconds",
    ]
    for key in keys:
        if key not in score:
            continue
        value = score[key]
        if isinstance(value, float):
            print(f"{key}: {value:.4f}")
        else:
            print(f"{key}: {value}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", required=True, type=Path, help="Gold JSON path")
    parser.add_argument(
        "--predicted",
        type=str,
        default=None,
        help='JSON list of [start,end] windows, e.g. "[[1,55],[100,140]]"',
    )
    parser.add_argument(
        "--guid",
        type=str,
        default=None,
        help="Load predicted windows from DB final_cut_windows for this post guid",
    )
    parser.add_argument(
        "--iou",
        type=float,
        default=0.5,
        help="IoU threshold for window matching (default 0.5)",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(_repo_src()))
    from podcast_processor.ad_eval import score_windows_detailed

    gold_data = _load_gold(args.gold)
    gold_windows = gold_data["windows"]
    guid = args.guid or gold_data.get("guid")

    if args.predicted:
        predicted = _parse_predicted(args.predicted)
    elif args.guid:
        predicted = _predicted_from_db(args.guid)
    else:
        raise SystemExit("Provide --predicted JSON or --guid to load from DB")

    score = score_windows_detailed(
        predicted, gold_windows, iou_threshold=float(args.iou)
    )
    _print_score(score, guid=guid, title=gold_data.get("title"))


if __name__ == "__main__":
    main()
