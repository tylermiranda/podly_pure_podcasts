<h2 align="center">
<img width="50%" src="src/app/static/images/logos/logo_with_text.png" />

</h2>

<p align="center">
<p align="center">Ad-block for podcasts. Create an ad-free RSS feed.</p>
<p align="center">
  <a href="https://discord.gg/FRB98GtF6N" target="_blank">
      <img src="https://img.shields.io/badge/discord-join-blue.svg?logo=discord&logoColor=white" alt="Discord">
  </a>
</p>

## Overview

Podly uses Whisper and Chat GPT to remove ads from podcasts.

<img width="100%" src="docs/images/screenshot.png" alt="Podly home: feed list with prompt tags and client/upstream freshness, plus feed detail" />

<p align="center"><em>Feed list shows prompt-tag badges and <code>Last fetched … via {client}</code>; detail shows upstream RSS freshness and the assigned tag.</em></p>

## This fork

Fork of [podly-pure-podcasts/podly_pure_podcasts](https://github.com/podly-pure-podcasts/podly_pure_podcasts) with fixes for upgrades and tighter ad removal. Published image:

```text
ghcr.io/tylermiranda/podly-pure-podcasts:main-latest
```

### Changes vs upstream

| Area | Change |
|------|--------|
| **Migrations** | Merges Alembic dual heads so existing DBs can upgrade (upstream [#234](https://github.com/podly-pure-podcasts/podly_pure_podcasts/issues/234)). |
| **Custom ad prompt** | Per-feed **Custom Ad Detection Instructions** in Feed Settings (`custom_llm_ad_prompt`), appended to the LLM system prompt during classification. |
| **Prompt tags** | Reusable prompt templates (e.g. `noiser`) managed under **Config → Prompt Tags** (also Feed Settings / Add Feed → Manage tags). Assign one tag per feed at add time or in settings; composition is `base → tag.prompt → per-feed custom`. |
| **Cut defaults** | New installs / reset defaults: `fade_ms=0` (was 3000), `min_ad_segment_length_seconds=5` (was 14), `min_confidence=0.7` (was 0.8). Existing DB values are unchanged until you update Output settings. |
| **Client poll freshness** | Feed list shows `Last fetched … via {client}` from the podcast app User-Agent that last requested the Podly RSS URL. |
| **Upstream freshness** | `last_fetched_at` (Podly → publisher RSS) is shown on the feed detail pane as `Upstream RSS refreshed …`. |
| **Transcript corrections** | Fullscreen **Transcript Segments** workspace: mark ad/content spans, then **Improve show prompt and recut audio** (analyze → append feed prompt → recut) or **Recut audio only**. |

**Why the cut defaults matter:** a 3s fade leaves audible ad bleed at every cut; a 14s minimum length drops short prerolls. Prefer `fade_ms=0` and a lower length threshold when ads still play after processing. For host-read / network-specific patterns (e.g. Wondery / Noiser), assign a prompt tag or set a custom prompt on that feed and reprocess.

### Transcript corrections workspace

Open an episode’s **Transcript** button (or Stats → **Transcript Segments**). The modal uses the full viewport so you can listen to original audio, select ranges, and fix cuts without cramped scrolling.

<img width="100%" src="docs/images/screenshot-transcript-workspace.png" alt="Fullscreen Transcript Segments modal with original audio, mark controls, and segment table" />

<p align="center"><em>Fullscreen Stats modal on the Transcript Segments tab — original audio player, segment table, and correction controls.</em></p>

**Workflow**

1. Click a row to play from that segment; check boxes (Shift+click for a range) or set Start/End seconds.
2. **Mark ad** or **Mark content** to save corrections (does not change the processed MP3 yet).
3. When finished marking, click **Improve show prompt and recut audio** to analyze corrections, append a show-prompt draft to the feed, and recut the processed MP3 in one step — or **Recut audio only** if you only want cuts updated.

<img width="100%" src="docs/images/screenshot-transcript-corrections.png" alt="Mark ad/content controls with Improve show prompt and recut audio and Recut audio only" />

<p align="center"><em>Mark ad / Mark content, optional jingle template, then improve the show prompt and recut (or recut only).</em></p>

You do **not** need **Reprocess** (Whisper/LLM) for these fixes. Effective cuts show in red. For repeating full ad reads, prefer corrections + feed prompt; use **Save as jingle template** for short intro/outro stingers (check Stats → Ad Detection Signals for jingle hits after a later reprocess).

### Prompt tags (examples)

<img width="100%" src="docs/images/screenshot-prompt-tags.png" alt="Config → Default → Prompt Tags manager with creator tags and prompts" />

<p align="center"><em>Create reusable tags under <strong>Config → Default → Prompt Tags</strong> (e.g. <code>wondery</code>, <code>noiser</code>, <code>acquired</code>).</em></p>

<img width="80%" src="docs/images/screenshot-feed-settings.png" alt="Feed Settings with Prompt tag and Custom Ad Detection Instructions" />

<p align="center"><em>Assign one tag per feed in <strong>Feed Settings</strong>; optional per-feed custom instructions append after the tag prompt.</em></p>

Create tags once under **Config → Default → Prompt Tags** (or **Manage tags** from Feed Settings / Add Feed), then assign **one tag per feed**. At classify time Podly builds the LLM instructions as:

```text
[global base ad-detection prompt]
+ [selected tag.prompt, if any]
+ [feed custom_llm_ad_prompt, if any]
```

Use a tag for patterns shared across a network or creator; use the per-feed custom field only for show-specific quirks.

**Example tags**

| Tag | Good for | Prompt gist |
|-----|----------|-------------|
| `wondery` | Wondery originals | Preroll network/show promo; midroll “brought to you by” host-reads; cross-promo for other Wondery titles. Keep narrative storytelling as content. |
| `noiser` | Noiser shows (`Short History Of…`, `Real Survival Stories`, …) | Complete preroll/midroll/partner blocks (including back-to-back advertisers and repeated creatives); Noiser+ promo. Keep date cold-opens and history narration. |
| `npr` | NPR / public radio | Short underwriting (“support for … comes from”), membership/NPR Plus pitches. Keep reporting and explainers. |
| `acquired` | Long-form host-read interview shows | Distinct midroll sponsor reads with promo codes/URLs; do not mark host banter or business analysis as ads. |

**Example tag prompt** (`noiser`):

```text
This is a Noiser production (e.g. Short History Of..., Real Survival Stories). Ads often:
- Open with a Noiser/network or partner preroll (sometimes two advertisers back-to-back)
- Repeat the same midroll sponsor creative later in the episode; each repeat is an ad
- Promote Noiser+ or other Noiser shows at episode boundaries
Identify complete sponsor blocks as ads, including the pitch before each CTA/URL and the next advertiser after a short gap. Do not emit only the CTA line. Preserve narrative history storytelling, dramatic reenactment, and date cold-opens ("It is July the 1st, 1936", "It's 7 in the morning") as content. "That's next time" is show content, not an ad. Prefer cutting whole ad blocks rather than leaving short sponsor tails.
```

**Example workflow**

1. Create tag `wondery` with the Wondery prompt above.
2. In each Wondery feed’s **Feed Settings**, set **Prompt tag** → `wondery` (or pick it when adding the feed).
3. Optionally add a per-feed custom line, e.g. `Also treat the cold-open Audible promo in the first 45s as an ad.`
4. Reprocess episodes so classification picks up the new instructions.

**API sketch** (authenticated session):

```bash
# Create a tag
curl -X POST "$PODLY/api/tags" -H 'Content-Type: application/json' \
  -d '{"name":"noiser","prompt":"This is a Noiser production..."}'

# Assign to a feed
curl -X PATCH "$PODLY/api/feeds/16/settings" -H 'Content-Type: application/json' \
  -d '{"prompt_tag_id":3}'
```

## How To Run

You have a few options to get started:

- [![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/podly?referralCode=NMdeg5&utm_medium=integration&utm_source=template&utm_campaign=generic)
   - quick and easy setup in the cloud, follow our [Railway deployment guide](docs/how_to_run_railway.md). 
   - Use this if you want to share your Podly server with others.
- **Run Locally**: 
   - For local development and customization, 
   - see our [beginner's guide for running locally](docs/how_to_run_beginners.md). 
   - Use this for the most cost-optimal & private setup.
- **[Join The Preview Server](https://podly.up.railway.app/)**: 
   - pay what you want (limited sign ups available)


## How it works:

- You request an episode
- Podly downloads the requested episode
- Whisper transcribes the episode
- LLM labels ad segments
- Podly removes the ad segments
- Podly delivers the ad-free version of the podcast to you

### Cost Breakdown
*Monthly cost breakdown for 5 podcasts*

| Cost    | Hosting  | Transcription | LLM    |
|---------|----------|---------------|--------|
| **free**| local    | local         | local  |
| **$2**  | local    | local         | remote |
| **$5**  | local    | remote        | remote |
| **$10** | public (railway)  | remote        | remote |
| **Pay What You Want** | [preview server](https://podly.up.railway.app/)    | n/a         | n/a  |
| **$5.99/mo** | https://zeroads.ai/ | production fork of podly | |


## Contributing

See [contributing guide](docs/contributors.md) for local setup & contribution instructions.
