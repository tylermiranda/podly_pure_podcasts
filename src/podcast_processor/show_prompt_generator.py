"""Research a newly added feed and draft a per-feed show prompt for ad classification."""

from __future__ import annotations

import html
import ipaddress
import json
import logging
import re
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse, urlunparse

import feedparser
import litellm
import requests

from app.extensions import db
from app.models import Feed
from app.runtime_config import config
from app.writer.client import writer_client
from podcast_processor.llm_concurrency_limiter import (
    ConcurrencyContext,
    get_concurrency_limiter,
)
from podcast_processor.llm_model_call_utils import extract_litellm_content
from shared.llm_utils import model_uses_max_completion_tokens

logger = logging.getLogger("global_logger")

DIRECTORY_SEARCH_URL = "http://api.podcastindex.org/search"
WEBSITE_FETCH_TIMEOUT_SEC = 8
WEBSITE_MAX_BYTES = 200_000
WEBSITE_TEXT_MAX_CHARS = 4_000
EPISODE_SNIPPET_CHARS = 180
MAX_RECENT_EPISODES = 8
GENERATE_MAX_TOKENS = 800

GENERATE_SHOW_PROMPT_SYSTEM = (
    "You write short, durable show-specific rules for podcast ad classification. "
    "Given research about a podcast (RSS metadata, directory listing, and optionally "
    "the show website), produce plain text instructions that help an LLM correctly "
    "label sponsor ads vs content on future episodes of this show. Separate CONTENT "
    "(do not cut) patterns from AD (cut) patterns when both appear. Call out "
    "self-promotion (host products, newsletter, Patreon, live shows) as CONTENT "
    "unless clearly an external sponsor read. Prefer network/host/format quirks "
    "(pre-roll hosts, midroll bumper language, scripted credits) over generic advice. "
    "Do not invent sponsors you cannot support from the research. Return only a few "
    "short sentences or bullets — no preamble."
)


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self._chunks.append(text)

    def get_text(self) -> str:
        return " ".join(self._chunks)


def _strip_html(raw: str) -> str:
    if not raw:
        return ""
    parser = _HTMLTextExtractor()
    try:
        parser.feed(raw)
        parser.close()
        text = parser.get_text()
    except Exception:  # noqa: BLE001
        text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _truncate(text: str, max_chars: int) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


def _normalize_url_for_match(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "").rstrip("/")
    return f"{host}{path}"


def is_safe_public_http_url(url: str) -> bool:
    """Return True when URL is http(s) and resolves only to public IPs."""
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.username or parsed.password:
        return False
    host = parsed.hostname
    if not host:
        return False
    host_lower = host.lower()
    if host_lower in {"localhost", "metadata.google.internal"}:
        return False
    try:
        ip = ipaddress.ip_address(host_lower)
        return ip.is_global
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


def _safe_request_url(url: str) -> str | None:
    if not is_safe_public_http_url(url):
        return None
    parsed = urlparse(url)
    # Drop fragment; keep query.
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path or "/", "", parsed.query, "")
    )


def fetch_directory_match(title: str, rss_url: str) -> dict[str, Any] | None:
    term = (title or "").strip()
    if not term:
        return None
    try:
        response = requests.get(
            DIRECTORY_SEARCH_URL,
            headers={
                "User-Agent": "PodlyShowPromptGenerator/1.0 (+https://github.com/podly)"
            },
            params={"term": term},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.info("show-prompt directory lookup failed for %r: %s", term, exc)
        return None

    results = payload.get("results") or []
    if not isinstance(results, list):
        return None

    target = _normalize_url_for_match(rss_url)
    best: dict[str, Any] | None = None
    for item in results:
        if not isinstance(item, dict):
            continue
        feed_url = str(item.get("feedUrl") or "")
        if target and _normalize_url_for_match(feed_url) == target:
            best = item
            break
        if best is None:
            # Prefer first result with a feed URL as weak fallback.
            if feed_url:
                best = item
    if best is None:
        return None
    return {
        "title": best.get("collectionName")
        or best.get("trackName")
        or best.get("title"),
        "author": best.get("artistName") or best.get("author"),
        "genres": best.get("genres") or [],
        "feedUrl": best.get("feedUrl"),
        "description": best.get("collectionCensoredName")
        or best.get("trackCensoredName")
        or best.get("description")
        or "",
    }


def fetch_website_text(url: str | None) -> str | None:
    safe = _safe_request_url(url or "")
    if not safe:
        return None
    try:
        with requests.get(
            safe,
            headers={
                "User-Agent": "PodlyShowPromptGenerator/1.0 (+https://github.com/podly)",
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            },
            timeout=WEBSITE_FETCH_TIMEOUT_SEC,
            stream=True,
            allow_redirects=True,
        ) as response:
            # Re-validate final URL after redirects.
            if not is_safe_public_http_url(response.url):
                logger.info(
                    "show-prompt website fetch blocked unsafe redirect: %s",
                    response.url,
                )
                return None
            response.raise_for_status()
            content_type = (response.headers.get("Content-Type") or "").lower()
            if "html" not in content_type and "text/" not in content_type:
                return None
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                remaining = WEBSITE_MAX_BYTES - total
                if remaining <= 0:
                    break
                if len(chunk) > remaining:
                    chunks.append(chunk[:remaining])
                    break
                chunks.append(chunk)
                total += len(chunk)
            raw = b"".join(chunks).decode(
                response.encoding or "utf-8", errors="replace"
            )
    except requests.RequestException as exc:
        logger.info("show-prompt website fetch failed for %s: %s", safe, exc)
        return None

    text = _strip_html(raw)
    if not text:
        return None
    return _truncate(text, WEBSITE_TEXT_MAX_CHARS)


def extract_channel_link(feed_meta: Any) -> str | None:
    if feed_meta is None:
        return None
    link = feed_meta.get("link") if hasattr(feed_meta, "get") else None
    if isinstance(link, str) and link.strip():
        return link.strip()
    return None


def load_channel_link_from_rss(rss_url: str) -> str | None:
    try:
        parsed = feedparser.parse(rss_url)
    except Exception as exc:  # noqa: BLE001
        logger.info("show-prompt RSS reparse failed for %s: %s", rss_url, exc)
        return None
    feed_meta = getattr(parsed, "feed", None)
    return extract_channel_link(feed_meta)


def _categories_from_feed(feed: Feed) -> list[str]:
    raw = getattr(feed, "itunes_categories", None)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    out: list[str] = []
    for item in parsed:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            if text:
                out.append(text)
            for sub in item.get("subs") or []:
                sub_text = str(sub or "").strip()
                if sub_text:
                    out.append(sub_text)
        elif isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


def build_research_pack(
    feed: Feed,
    *,
    channel_link: str | None = None,
    directory: dict[str, Any] | None = None,
    website_text: str | None = None,
) -> dict[str, Any]:
    episodes: list[dict[str, str]] = []
    posts = list(getattr(feed, "posts", None) or [])
    for post in posts[:MAX_RECENT_EPISODES]:
        title = str(getattr(post, "title", "") or "").strip()
        description = _truncate(
            _strip_html(str(getattr(post, "description", "") or "")),
            EPISODE_SNIPPET_CHARS,
        )
        if title or description:
            episodes.append({"title": title, "description": description})

    tag = getattr(feed, "prompt_tag", None)
    tag_prompt = None
    if tag is not None:
        tag_prompt = (getattr(tag, "prompt", None) or "").strip() or None

    pack: dict[str, Any] = {
        "title": (feed.title or "").strip(),
        "author": (feed.author or "").strip(),
        "description": _truncate(_strip_html(feed.description or ""), 1_500),
        "categories": _categories_from_feed(feed),
        "rss_url": feed.rss_url,
        "channel_link": channel_link,
        "episodes": episodes,
        "prompt_tag": tag_prompt,
    }
    if directory:
        pack["directory"] = directory
    if website_text:
        pack["website_text"] = website_text
    return pack


def format_research_pack_for_prompt(pack: dict[str, Any]) -> str:
    lines: list[str] = [
        f"Title: {pack.get('title') or '(unknown)'}",
        f"Author: {pack.get('author') or '(unknown)'}",
        f"Categories: {', '.join(pack.get('categories') or []) or '(none)'}",
        f"Description: {pack.get('description') or '(none)'}",
    ]
    directory = pack.get("directory")
    if isinstance(directory, dict):
        genres = directory.get("genres") or []
        genre_text = (
            ", ".join(str(g) for g in genres)
            if isinstance(genres, list)
            else str(genres)
        )
        lines.append(
            "Directory match: "
            f"title={directory.get('title') or ''}; "
            f"author={directory.get('author') or ''}; "
            f"genres={genre_text}"
        )
    if pack.get("channel_link"):
        lines.append(f"Website URL: {pack['channel_link']}")
    if pack.get("website_text"):
        lines.append(f"Website text excerpt: {pack['website_text']}")
    tag_prompt = pack.get("prompt_tag")
    if tag_prompt:
        lines.append(
            "Existing prompt tag (do not duplicate; add show-specific rules only):\n"
            f"{tag_prompt}"
        )
    episodes = pack.get("episodes") or []
    if episodes:
        lines.append("Recent episodes:")
        for ep in episodes:
            snippet = ep.get("description") or ""
            lines.append(f"- {ep.get('title') or '(untitled)'}: {snippet}")
    return "\n".join(lines)


def heuristic_show_prompt_draft(pack: dict[str, Any]) -> str:
    title = pack.get("title") or "This show"
    author = pack.get("author") or "the hosts"
    categories = pack.get("categories") or []
    genre_hint = ""
    if categories:
        genre_hint = f" Genre/format cues: {', '.join(categories[:4])}."
    return (
        f"CONTENT: Treat {author}'s own products, newsletter, live shows, and "
        f"credits for '{title}' as content, not ads.{genre_hint}\n"
        "AD: Cut external sponsor reads with promo codes, URLs, or 'brought to you by' "
        "language, including pre-roll and midroll host-reads."
    )


def draft_show_prompt_with_llm(pack: dict[str, Any]) -> str:
    messages = [
        {"role": "system", "content": GENERATE_SHOW_PROMPT_SYSTEM},
        {
            "role": "user",
            "content": (
                "Draft show-specific ad classification rules from this research:\n\n"
                f"{format_research_pack_for_prompt(pack)}"
            ),
        },
    ]
    model_name = getattr(config, "llm_model", None) or "gpt-4o"
    completion_args: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "temperature": 0.2,
        "timeout": int(getattr(config, "openai_timeout", 300) or 300),
        "api_key": getattr(config, "llm_api_key", None),
    }
    if model_uses_max_completion_tokens(model_name):
        completion_args["max_completion_tokens"] = GENERATE_MAX_TOKENS
    else:
        completion_args["max_tokens"] = GENERATE_MAX_TOKENS
    base_url = getattr(config, "openai_base_url", None)
    if isinstance(base_url, str) and base_url.strip():
        completion_args["base_url"] = base_url.strip()

    max_concurrent = int(getattr(config, "llm_max_concurrent_calls", 3) or 3)
    limiter = get_concurrency_limiter(max_concurrent)
    with ConcurrencyContext(limiter, timeout=60.0):
        response = litellm.completion(**completion_args)
    draft = extract_litellm_content(response).strip()
    if not draft:
        draft = heuristic_show_prompt_draft(pack)
        logger.warning(
            "show-prompt: model returned empty content for feed %r; using heuristic",
            pack.get("title"),
        )
    return draft


def gather_research_for_feed(feed: Feed) -> dict[str, Any]:
    channel_link = load_channel_link_from_rss(feed.rss_url)
    directory = fetch_directory_match(feed.title or "", feed.rss_url)
    website_text = fetch_website_text(channel_link) if channel_link else None
    return build_research_pack(
        feed,
        channel_link=channel_link,
        directory=directory,
        website_text=website_text,
    )


def llm_is_configured() -> bool:
    key = getattr(config, "llm_api_key", None)
    return isinstance(key, str) and bool(key.strip())


def generate_and_persist_show_prompt(
    feed_id: int,
    *,
    force: bool = False,
) -> str | None:
    """Research + LLM draft; persist to Feed.custom_llm_ad_prompt.

    Returns the draft when persisted (or when force overwrote). Returns None when
    skipped (already set without force, missing LLM, etc.).
    """
    feed = db.session.get(Feed, feed_id)
    if feed is None:
        logger.warning("show-prompt: feed %s not found", feed_id)
        return None

    existing = (getattr(feed, "custom_llm_ad_prompt", None) or "").strip()
    if existing and not force:
        logger.info(
            "show-prompt: skipping feed %s — custom prompt already set", feed_id
        )
        return None

    if not llm_is_configured():
        logger.info(
            "show-prompt: skipping feed %s — LLM API key not configured", feed_id
        )
        return None

    pack = gather_research_for_feed(feed)
    try:
        draft = draft_show_prompt_with_llm(pack)
    except Exception as exc:  # noqa: BLE001
        logger.error("show-prompt: LLM draft failed for feed %s: %s", feed_id, exc)
        draft = heuristic_show_prompt_draft(pack)

    draft = draft.strip()
    if not draft:
        return None

    # Re-check emptiness unless forcing, to avoid racing a manual edit.
    db.session.expire(feed)
    feed = db.session.get(Feed, feed_id)
    if feed is None:
        return None
    current = (getattr(feed, "custom_llm_ad_prompt", None) or "").strip()
    if current and not force:
        logger.info(
            "show-prompt: aborting write for feed %s — prompt set concurrently",
            feed_id,
        )
        return None

    result = writer_client.update(
        "Feed",
        feed_id,
        {"custom_llm_ad_prompt": draft},
        wait=True,
    )
    if result is None or not result.success:
        logger.error(
            "show-prompt: failed to persist prompt for feed %s: %s",
            feed_id,
            getattr(result, "error", None),
        )
        return None

    db.session.expire_all()
    logger.info("show-prompt: wrote custom prompt for feed %s", feed_id)
    return draft


def maybe_auto_generate_show_prompt(feed_id: int) -> None:
    """Entry point for post-add async hook."""
    if not bool(getattr(config, "auto_generate_show_prompt", True)):
        logger.info("show-prompt: auto-generate disabled; skipping feed %s", feed_id)
        return
    try:
        generate_and_persist_show_prompt(feed_id, force=False)
    except Exception as exc:  # noqa: BLE001
        logger.error("show-prompt: auto-generate failed for feed %s: %s", feed_id, exc)
