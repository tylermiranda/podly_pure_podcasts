"""Time-overlap scoring for ad cut windows (gold vs predicted)."""

from __future__ import annotations


def window_iou(pred: tuple[float, float], gold: tuple[float, float]) -> float:
    pred_start, pred_end = pred
    gold_start, gold_end = gold
    inter = max(0.0, min(pred_end, gold_end) - max(pred_start, gold_start))
    union = max(pred_end, gold_end) - min(pred_start, gold_start)
    if union <= 0.0:
        return 1.0 if inter == 0.0 else 0.0
    return inter / union


def merge_windows(
    windows: list[tuple[float, float]], *, gap_seconds: float = 1.0
) -> list[tuple[float, float]]:
    if not windows:
        return []
    ordered = sorted((s, e) for s, e in windows if e > s)
    if not ordered:
        return []
    merged: list[tuple[float, float]] = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + gap_seconds:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def score_windows(
    predicted: list[tuple[float, float]],
    gold: list[tuple[float, float]],
    *,
    iou_threshold: float = 0.5,
    gap_seconds: float = 1.0,
) -> dict[str, float | int]:
    """Greedy IoU match. Precision/recall count windows, not seconds."""
    preds = merge_windows(predicted, gap_seconds=gap_seconds)
    golds = merge_windows(gold, gap_seconds=gap_seconds)
    if not golds and not preds:
        return {
            "precision": 1.0,
            "recall": 1.0,
            "mean_iou": 1.0,
            "true_positives": 0,
            "false_positives": 0,
            "false_negatives": 0,
        }

    used_gold: set[int] = set()
    ious: list[float] = []
    true_positives = 0
    for pred in preds:
        best_i = -1
        best_iou = 0.0
        for idx, g in enumerate(golds):
            if idx in used_gold:
                continue
            iou = window_iou(pred, g)
            if iou > best_iou:
                best_iou = iou
                best_i = idx
        if best_i >= 0 and best_iou >= iou_threshold:
            used_gold.add(best_i)
            true_positives += 1
            ious.append(best_iou)
        else:
            ious.append(0.0)

    false_positives = len(preds) - true_positives
    false_negatives = len(golds) - len(used_gold)
    precision = (
        true_positives / (true_positives + false_positives)
        if (true_positives + false_positives)
        else 1.0
    )
    recall = (
        true_positives / (true_positives + false_negatives)
        if (true_positives + false_negatives)
        else 1.0
    )
    matched_ious = [v for v in ious if v > 0.0]
    mean_iou = sum(matched_ious) / len(matched_ious) if matched_ious else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "mean_iou": mean_iou,
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
    }
