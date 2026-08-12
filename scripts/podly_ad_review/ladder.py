"""Config ladder steps for Podly ad-review auto-fix."""

from __future__ import annotations

from typing import Any

from .client import PodlyClient

TAG_PROMPTS = {
    "npr": (
        "NPR / Planet Money public-radio podcast.\n"
        "ONLY mark as ads: underwriting blocks that start with cues like "
        "'Support comes from', 'This message comes from', or clear sponsor CTAs "
        "with URLs/promo codes.\n"
        "NEVER mark as ads: host thank-yous to professors/guests ('I want to thank "
        "our professor'), production credits ('produced by', 'edited by', "
        "'fact-checked by'), listener tip asks, book plugs for the show's own "
        "reporting, 'back from the break' / 'All right, class' content resumes, "
        "or economic storytelling that merely mentions companies as examples.\n"
        "Stop an ad block at the first clear return-to-content line."
    ),
    "history-of-the-90s": (
        "History of the 90s / Tupac trial narrative podcast (often Acast).\n"
        "Mark ZocDoc/Grow/Acast cross-promo host-reads and network promos as ads.\n"
        "NEVER mark as ads: 'Welcome to the Tupac Murder Trial', 'I'm your host', "
        "trial narration, or episode outros that only remind listeners of updates "
        "without a sponsor CTA.\n"
        "If a Whisper segment mixes a sponsor CTA and the show cold-open, mark only "
        "the sponsor portion conceptually—prefer stopping before 'Welcome to'."
    ),
    "everything-80s": (
        "Everything 80s (Jamie Logie) indie history podcast.\n"
        "Mark Whole Foods/Grow/Jerry/Wayfair/OnDeck-style host-reads and "
        "'will return after these messages' midrolls as ads.\n"
        "NEVER mark as ads: 'I'm Jamie and this is Everything 80s', 1980s "
        "storytelling, or cold-opens that resume after a CTA in the same segment "
        "('For a generation of kids', 'From the viewpoint of a kid').\n"
        "Prefer cutting whole sponsor blocks; do not extend into show content."
    ),
}


def apply_ladder_step(
    client: PodlyClient,
    step: int,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Apply ladder step (1-based). Returns description of change."""
    if step == 1:
        payload = {
            "llm": {
                "openai_base_url": state.get(
                    "lmstudio_base_url", "http://192.168.1.24:1234/v1"
                ),
                "llm_model": state.get("llm_model", "openai/google/gemma-4-12b"),
                "llm_api_key": "lm-studio",
            },
            # Local Gemma 8k context cannot fit the default 60-segment chunks.
            # Keep num_segments >= default max_overlap (30) unless overlap is
            # lowered via deployment defaults.
            "processing": {
                "num_segments_to_input_to_prompt": int(
                    state.get("local_num_segments_per_prompt") or 30
                ),
            },
        }
        client.put_config(payload)
        return {
            "step": 1,
            "action": "point_llm_lmstudio",
            "payload": {
                **payload["llm"],
                **payload["processing"],
            },
        }

    if step == 2:
        payload = {"output": {"min_ad_segement_separation_seconds": 10}}
        client.put_config(payload)
        return {"step": 2, "action": "separation_10s"}

    if step == 3:
        payload = {"output": {"min_confidence": 0.85}}
        client.put_config(payload)
        return {"step": 3, "action": "min_confidence_0.85"}

    if step == 4:
        tags = {t["name"]: t for t in client.list_tags()}
        updated = []
        for name, prompt in TAG_PROMPTS.items():
            tag = tags.get(name)
            if not tag:
                continue
            client.patch_tag(int(tag["id"]), prompt)
            updated.append(name)
        return {"step": 4, "action": "harden_tags", "tags": updated}

    if step == 5:
        payload = {"llm": {"enable_word_level_boundary_refinder": False}}
        client.put_config(payload)
        return {"step": 5, "action": "disable_word_boundary_refiner"}

    if step == 6:
        payload = {"llm": {"enable_boundary_refinement": False}}
        client.put_config(payload)
        return {"step": 6, "action": "disable_boundary_refinement"}

    return {"step": step, "action": "noop", "error": "unknown step"}
