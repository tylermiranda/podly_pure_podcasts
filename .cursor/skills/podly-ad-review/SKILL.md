---
name: podly-ad-review
description: >-
  Event-driven Podly ad-detection quality checks on Tower. Poll for new
  episodes, score Stats with heuristics, optionally apply config ladder and
  reprocess via LM Studio. Use when reviewing Podly ad cuts, running
  podly_ad_review scripts, or investigating false-positive ads.
---

# Podly ad-detection review

## Intent

Not a 24/7 improver. About every 1–2 hours, check **new** episodes on Tower. Score ad labels. On fail, optionally one config ladder step + reprocess. Persist progress in `state.json`.

## Where things run

| Piece | Location |
|-------|----------|
| Check script + launchd | This Mac |
| LM Studio (classify) | `http://192.168.1.24:1234/v1` |
| whisper-mlx | `http://192.168.1.24:9001/v1` (do not change) |
| Podly | `http://192.168.1.5:5001` |

Default LLM model id for litellm + LM Studio: `openai/google/gemma-4-12b`
(LM Studio lists `google/gemma-4-12b`; the `openai/` prefix is required by litellm.)

## Commands

```bash
# Score one or more GUIDs (exit 1 if any fail)
uv run python scripts/podly_ad_review_score.py --guid <guid> [...]

# Poll watched feeds for new/unseen processed posts; score; optional --fix
uv run python scripts/podly_ad_review_check_new.py
uv run python scripts/podly_ad_review_check_new.py --fix
uv run python scripts/podly_ad_review_check_new.py --bootstrap-corpus --fix

# Point Tower at LM Studio
uv run python scripts/podly_ad_review_check_new.py --point-llm
```

State: `.cursor/skills/podly-ad-review/state.json`  
Logs: `~/.podly-ad-review/logs/`

## Heuristic fail rules

1. Ad-labeled text matching content patterns (credits, “thank our professor”, “back from the break”, cold opens).
2. Same segment has sponsor cue AND content-resume phrase (bleed).
3. Zero strong sponsor/underwriting cues when episode has substantial ad%.
4. Refiner responses using content phrases as ad bounds.
5. Soft ad% bands → **fail** when outside the band for that show.

## Config ladder (one step per fix)

1. Point LLM at LM Studio + lock model + shrink chunks (`num_segments_to_input_to_prompt` ≈ 24–30 for 8k local context)
2. `min_ad_segement_separation_seconds` → 10  
3. `min_confidence` → 0.85  
4. Harden tags for npr / history-of-the-90s / everything-80s  
5. Disable word-level boundary refiner  
6. Disable full boundary refinement  

Prefer `reprocess/keep-transcript`. Never fall back to cloud LLM.

### Local LLM caveats

- Litellm model id must be `openai/google/gemma-4-12b` (prefix required).
- LM Studio rejects `response_format: json_object` — Podly skips it for LAN/local base URLs (`supports_json_object_response_format`).
- Tower container may need a hot-patch until that fix is in the published image.
- Word-level boundary refiner is very slow on Gemma; leave it off for local runs.

## launchd

`~/Library/LaunchAgents/com.podly.ad-review.plist` — StartInterval 7200, runs `~/.podly-ad-review/run_check.sh` (Framework Python; avoids Documents TCC blocks on `/usr/bin/python3`). Does not require an open Cursor chat.
