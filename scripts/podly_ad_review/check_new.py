"""Poll Tower for new episodes, score, optionally fix via config ladder."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .client import PodlyClient, load_state, save_state
from .ladder import apply_ladder_step
from .score import score_stats

LOG_DIR = Path.home() / ".podly-ad-review" / "logs"


def _now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"


def _log(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{_now()} {msg}"
    print(line, flush=True)
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    (LOG_DIR / f"ad-review-{day}.log").open("a").write(line + "\n")


def point_llm(client: PodlyClient, state: dict[str, Any]) -> None:
    result = apply_ladder_step(client, 1, state)
    state["ladder_step"] = max(int(state.get("ladder_step") or 0), 1)
    _log(f"point_llm: {json.dumps(result)}")


def wait_for_post(
    client: PodlyClient, guid: str, timeout_sec: float = 3600
) -> dict[str, Any]:
    """Poll until post processing reaches a terminal status (or timeout)."""
    deadline = time.time() + timeout_sec
    last: dict[str, Any] = {}
    while time.time() < deadline:
        try:
            last = client.get_status(guid)
        except Exception as e:  # noqa: BLE001
            _log(f"status error {guid}: {e}")
            time.sleep(15)
            continue
        status = (last.get("status") or "").lower()
        step = last.get("step_name") or ""
        _log(f"status {guid}: {status} step={step} {last.get('progress_percentage')}%")
        if status in {"failed", "error", "cancelled"}:
            return last
        if status in {"completed", "complete", "success"}:
            return last
        time.sleep(20)
    _log(f"timeout waiting for {guid}")
    return last


def score_guid(
    client: PodlyClient,
    guid: str,
    feed_title: str | None = None,
) -> dict[str, Any]:
    stats = client.get_stats(guid)
    card = score_stats(stats, feed_title=feed_title)
    card["guid"] = guid
    card["feed_title"] = feed_title
    return card


def maybe_fix(
    client: PodlyClient,
    state: dict[str, Any],
    guid: str,
    feed_title: str | None,
    *,
    max_attempts: int = 1,
) -> dict[str, Any]:
    max_steps = int(state.get("max_ladder_steps") or 6)
    last_result: dict[str, Any] = {
        "fixed": False,
        "reason": "no_attempt",
        "score": None,
    }
    attempts = 0
    while attempts < max_attempts:
        step = int(state.get("ladder_step") or 0)
        if step >= max_steps:
            last_result = {
                "fixed": False,
                "reason": "ladder_exhausted",
                "score": last_result.get("score"),
            }
            break

        next_step = step + 1
        change = apply_ladder_step(client, next_step, state)
        state["ladder_step"] = next_step
        save_state(state)
        _log(f"ladder applied: {json.dumps(change)}")

        try:
            client.reprocess_keep_transcript(guid)
        except Exception as e:  # noqa: BLE001
            return {
                "fixed": False,
                "reason": f"reprocess_failed: {e}",
                "change": change,
            }

        wait_for_post(client, guid, timeout_sec=5400)
        card = score_guid(client, guid, feed_title=feed_title)
        last_result = {"fixed": bool(card.get("pass")), "change": change, "score": card}
        attempts += 1
        if card.get("pass"):
            break
        _log(
            f"still failing after step {next_step} guid={guid} "
            f"ad_pct={card.get('ad_pct')} failures={json.dumps(card.get('failures')[:2])}"
        )
    return last_result


def _collect_targets(
    client: PodlyClient,
    state: dict[str, Any],
    *,
    bootstrap: bool,
) -> list[tuple[str, str | None, int | None]]:
    seen: dict[str, Any] = state.setdefault("seen", {})
    targets: list[tuple[str, str | None, int | None]] = []
    if bootstrap:
        for item in state.get("corpus") or []:
            targets.append(
                (
                    item["guid"],
                    state.get("watched_feed_names", {}).get(str(item.get("feed_id"))),
                    item.get("feed_id"),
                )
            )
        return targets

    for feed_id in state.get("watched_feed_ids") or []:
        feed_name = (state.get("watched_feed_names") or {}).get(str(feed_id))
        posts = client.list_posts(int(feed_id), page=1, per_page=10)
        for p in posts:
            guid = p.get("guid")
            if not guid:
                continue
            if not p.get("has_processed_audio"):
                if p.get("whitelisted"):
                    _log(
                        f"pending process feed={feed_id} guid={guid} "
                        f"title={p.get('title')}"
                    )
                continue
            prev = seen.get(guid) or {}
            if prev.get("force"):
                targets.append((guid, feed_name or p.get("title"), int(feed_id)))
                continue
            if prev.get("pass") or prev.get("needs_attention"):
                continue
            targets.append((guid, feed_name or p.get("title"), int(feed_id)))
    return targets


def _apply_pass(
    seen: dict[str, Any],
    failures: dict[str, Any],
    guid: str,
    card: dict[str, Any],
    *,
    fixed: bool = False,
) -> None:
    entry = {"pass": True, "ad_pct": card["ad_pct"], "checked_at": _now()}
    if fixed:
        entry["fixed"] = True
    seen[guid] = entry
    failures.pop(guid, None)


def _apply_fail(
    seen: dict[str, Any],
    failures: dict[str, Any],
    guid: str,
    card: dict[str, Any],
    fix_result: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "needs_attention": True,
        "last_score": card,
        "checked_at": _now(),
    }
    if fix_result is not None:
        payload["fix"] = fix_result
    failures[guid] = payload
    seen[guid] = {
        "pass": False,
        "needs_attention": True,
        "checked_at": _now(),
    }


def check_new(*, fix: bool, bootstrap: bool) -> int:
    state = load_state()
    client = PodlyClient(state.get("podly_url", "http://192.168.1.5:5001"))
    client.login()

    exit_code = 0
    seen: dict[str, Any] = state.setdefault("seen", {})
    failures: dict[str, Any] = state.setdefault("failures", {})
    log_entries: list[dict[str, Any]] = state.setdefault("iteration_log", [])
    fix_attempts = (
        max(
            1,
            int(state.get("max_ladder_steps") or 6)
            - int(state.get("ladder_step") or 0),
        )
        if bootstrap
        else 1
    )

    targets = _collect_targets(client, state, bootstrap=bootstrap)
    if not targets:
        _log("no new/unseen processed episodes to check")
        state["status"] = "idle"
        save_state(state)
        return 0

    for guid, feed_title, feed_id in targets:
        _log(f"scoring guid={guid} feed={feed_title}")
        try:
            card = score_guid(client, guid, feed_title=feed_title)
        except Exception as e:  # noqa: BLE001
            _log(f"score error {guid}: {e}")
            exit_code = 1
            continue

        entry: dict[str, Any] = {
            "ts": _now(),
            "guid": guid,
            "feed_id": feed_id,
            "feed_title": feed_title,
            "pass": card["pass"],
            "ad_pct": card["ad_pct"],
            "failures": card["failures"],
            "warnings": card["warnings"],
        }

        if card["pass"]:
            _apply_pass(seen, failures, guid, card)
            _log(f"PASS guid={guid} ad_pct={card['ad_pct']}")
        else:
            exit_code = 1
            _log(
                f"FAIL guid={guid} ad_pct={card['ad_pct']} "
                f"failures={json.dumps(card['failures'][:3])}"
            )
            if fix and state.get("auto_fix", True):
                result = maybe_fix(
                    client,
                    state,
                    guid,
                    feed_title,
                    max_attempts=fix_attempts,
                )
                entry["fix_attempt"] = {
                    "change": result.get("change"),
                    "fixed": result.get("fixed"),
                    "reason": result.get("reason"),
                }
                if result.get("score"):
                    entry["score_after_fix"] = {
                        "pass": result["score"]["pass"],
                        "ad_pct": result["score"]["ad_pct"],
                        "failures": result["score"]["failures"],
                    }
                if result.get("fixed"):
                    _apply_pass(seen, failures, guid, result["score"], fixed=True)
                    _log(f"FIXED guid={guid}")
                else:
                    _apply_fail(seen, failures, guid, card, result)
                    _log(f"NEEDS_ATTENTION guid={guid}")
            else:
                _apply_fail(seen, failures, guid, card)

        log_entries.append(entry)
        state["iteration_log"] = log_entries[-200:]
        save_state(state)

    state["status"] = "idle" if exit_code == 0 else "has_failures"
    state["last_run_at"] = _now()
    save_state(state)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Podly ad-review new-episode checker")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="On fail, apply next ladder step and reprocess keep-transcript",
    )
    parser.add_argument(
        "--bootstrap-corpus",
        action="store_true",
        help="Check the configured corpus GUIDs (one-time bad episodes)",
    )
    parser.add_argument(
        "--point-llm",
        action="store_true",
        help="Only point Tower LLM at LM Studio and exit",
    )
    args = parser.parse_args(argv)

    state = load_state()
    client = PodlyClient(state.get("podly_url", "http://192.168.1.5:5001"))
    client.login()

    if args.point_llm:
        point_llm(client, state)
        save_state(state)
        return 0

    return check_new(fix=args.fix, bootstrap=args.bootstrap_corpus)


if __name__ == "__main__":
    sys.exit(main())
