#!/usr/bin/env python3
"""CLI: score one or more Podly episode GUIDs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as script from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent))

from podly_ad_review.client import PodlyClient, load_state
from podly_ad_review.score import score_stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--guid", action="append", dest="guids", required=True)
    parser.add_argument("--feed-title", default=None)
    args = parser.parse_args()

    state = load_state()
    client = PodlyClient(state.get("podly_url", "http://192.168.1.5:5001"))
    client.login()

    exit_code = 0
    out = []
    for guid in args.guids:
        stats = client.get_stats(guid)
        card = score_stats(stats, feed_title=args.feed_title)
        card["guid"] = guid
        out.append(card)
        if not card["pass"]:
            exit_code = 1
    print(json.dumps(out if len(out) > 1 else out[0], indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
