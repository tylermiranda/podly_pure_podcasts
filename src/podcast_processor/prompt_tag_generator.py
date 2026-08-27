"""Research a newly added feed and create/assign a reusable prompt Tag."""

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
from app.models import Feed, Tag
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
TAG_NAME_MAX_LEN = 128

GENERATE_PROMPT_TAG_SYSTEM = (
    "You create reusable podcast ad-detection prompt tags. A prompt tag is a short "
    "name plus durable instructions shared across shows from the same network, "
    "production company, or format family (e.g. 'npr', 'wondery', 'noiser'). "
    "Given research about one podcast plus a list of existing tag names, respond "
    "with ONLY a JSON object: "
    '{"name":"<short-slug>","prompt":"<rules>"}. '
    "Prefer reusing an existing tag name when the show clearly matches that network "
    "or pattern. Otherwise invent a short lowercase slug (letters, digits, hyphens). "
    "The prompt must be a few short sentences or bullets separating CONTENT (do not "
    "cut) from AD (cut) patterns: network bumpers, host-read style, self-promo vs "
    "external sponsors. Do not invent sponsors. No markdown fences, no preamble."
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
                "User-Agent": "PodlyPromptTagGenerator/1.0 (+https://github.com/podly)"
            },
            params={"term": term},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.info("prompt-tag directory lookup failed for %r: %s", term, exc)
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
        if best is None and feed_url:
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
                "User-Agent": "PodlyPromptTagGenerator/1.0 (+https://github.com/podly)",
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            },
            timeout=WEBSITE_FETCH_TIMEOUT_SEC,
            stream=True,
            allow_redirects=True,
        ) as response:
            if not is_safe_public_http_url(response.url):
                logger.info(
                    "prompt-tag website fetch blocked unsafe redirect: %s",
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
        logger.info("prompt-tag website fetch failed for %s: %s", safe, exc)
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
        logger.info("prompt-tag RSS reparse failed for %s: %s", rss_url, exc)
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


def list_existing_tag_names() -> list[str]:
    return [str(t.name) for t in Tag.query.order_by(Tag.name.asc()).all() if t.name]


def build_research_pack(
    feed: Feed,
    *,
    channel_link: str | None = None,
    directory: dict[str, Any] | None = None,
    website_text: str | None = None,
    existing_tag_names: list[str] | None = None,
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

    pack: dict[str, Any] = {
        "title": (feed.title or "").strip(),
        "author": (feed.author or "").strip(),
        "description": _truncate(_strip_html(feed.description or ""), 1_500),
        "categories": _categories_from_feed(feed),
        "rss_url": feed.rss_url,
        "channel_link": channel_link,
        "episodes": episodes,
        "existing_tag_names": list(existing_tag_names or []),
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
    existing = pack.get("existing_tag_names") or []
    if existing:
        lines.append("Existing prompt tag names (reuse when appropriate):")
        lines.append(", ".join(str(n) for n in existing))
    episodes = pack.get("episodes") or []
    if episodes:
        lines.append("Recent episodes:")
        for ep in episodes:
            snippet = ep.get("description") or ""
            lines.append(f"- {ep.get('title') or '(untitled)'}: {snippet}")
    return "\n".join(lines)


def slugify_tag_name(raw: str) -> str:
    text = (raw or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    if not text:
        text = "podcast"
    return text[:TAG_NAME_MAX_LEN]


def heuristic_prompt_tag_draft(pack: dict[str, Any]) -> dict[str, str]:
    author = (pack.get("author") or "").strip()
    title = (pack.get("title") or "").strip()
    existing = {
        str(n).casefold(): str(n) for n in (pack.get("existing_tag_names") or [])
    }
    candidate = slugify_tag_name(author or title or "podcast")
    # Prefer an existing tag whose name appears in author/title.
    for key, original in existing.items():
        hay = f"{author} {title}".casefold()
        if key and key in hay:
            candidate = original
            break
        if hay and (
            author.casefold().startswith(key) or title.casefold().startswith(key)
        ):
            candidate = original
            break
    if candidate.casefold() in existing:
        candidate = existing[candidate.casefold()]
    categories = pack.get("categories") or []
    genre_hint = ""
    if categories:
        genre_hint = f" Genre/format cues: {', '.join(categories[:4])}."
    prompt = (
        f"CONTENT: Treat {author or 'the hosts'}' own products, newsletter, live "
        f"shows, and network/show credits as content, not ads.{genre_hint}\n"
        "AD: Cut external sponsor reads with promo codes, URLs, or 'brought to you by' "
        "language, including pre-roll and midroll host-reads."
    )
    return {"name": candidate, "prompt": prompt}


def _parse_tag_json(raw: str) -> dict[str, str] | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    prompt = data.get("prompt")
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    return {"name": name.strip(), "prompt": prompt.strip()}


def draft_prompt_tag_with_llm(pack: dict[str, Any]) -> dict[str, str]:
    messages = [
        {"role": "system", "content": GENERATE_PROMPT_TAG_SYSTEM},
        {
            "role": "user",
            "content": (
                "Create or choose a reusable prompt tag from this research:\n\n"
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
    raw = extract_litellm_content(response).strip()
    parsed = _parse_tag_json(raw)
    if parsed is None:
        logger.warning(
            "prompt-tag: model returned unparseable content for feed %r; using heuristic",
            pack.get("title"),
        )
        return heuristic_prompt_tag_draft(pack)
    parsed["name"] = slugify_tag_name(parsed["name"])
    # Preserve casing of an exact existing tag name when slug matches.
    for existing in pack.get("existing_tag_names") or []:
        if str(existing).casefold() == parsed["name"].casefold():
            parsed["name"] = str(existing)
            break
    return parsed


def gather_research_for_feed(feed: Feed) -> dict[str, Any]:
    channel_link = load_channel_link_from_rss(feed.rss_url)
    directory = fetch_directory_match(feed.title or "", feed.rss_url)
    website_text = fetch_website_text(channel_link) if channel_link else None
    return build_research_pack(
        feed,
        channel_link=channel_link,
        directory=directory,
        website_text=website_text,
        existing_tag_names=list_existing_tag_names(),
    )


def llm_is_configured() -> bool:
    key = getattr(config, "llm_api_key", None)
    return isinstance(key, str) and bool(key.strip())


def find_tag_by_name(name: str) -> Tag | None:
    wanted = name.casefold()
    for tag in Tag.query.all():
        if str(tag.name or "").casefold() == wanted:
            return tag
    return None


def _ensure_tag(name: str, prompt: str, *, force_update_prompt: bool) -> Tag | None:
    existing = find_tag_by_name(name)
    if existing is not None:
        if force_update_prompt and (existing.prompt or "").strip() != prompt.strip():
            result = writer_client.update(
                "Tag",
                existing.id,
                {"prompt": prompt},
                wait=True,
            )
            if result is None or not result.success:
                logger.error(
                    "prompt-tag: failed to update tag %s: %s",
                    existing.id,
                    getattr(result, "error", None),
                )
                return None
            db.session.expire_all()
            return db.session.get(Tag, existing.id)
        return existing

    result = writer_client.create(
        "Tag",
        {"name": name, "prompt": prompt},
        wait=True,
    )
    if result is None or not result.success:
        # Race: another worker created the same name.
        raced = find_tag_by_name(name)
        if raced is not None:
            return raced
        logger.error(
            "prompt-tag: failed to create tag %r: %s",
            name,
            getattr(result, "error", None),
        )
        return None
    tag_id = (result.data or {}).get("id")
    db.session.expire_all()
    if tag_id is None:
        return find_tag_by_name(name)
    return db.session.get(Tag, tag_id)


def generate_and_persist_prompt_tag(
    feed_id: int,
    *,
    force: bool = False,
) -> dict[str, Any] | None:
    """Research + LLM draft; create/reuse Tag and assign Feed.prompt_tag_id.

    Returns ``{tag_id, name, prompt}`` when assigned. None when skipped.
    """
    feed = db.session.get(Feed, feed_id)
    if feed is None:
        logger.warning("prompt-tag: feed %s not found", feed_id)
        return None

    if getattr(feed, "prompt_tag_id", None) is not None and not force:
        logger.info("prompt-tag: skipping feed %s — prompt_tag_id already set", feed_id)
        return None

    if not llm_is_configured():
        logger.info(
            "prompt-tag: skipping feed %s — LLM API key not configured", feed_id
        )
        return None

    pack = gather_research_for_feed(feed)
    try:
        draft = draft_prompt_tag_with_llm(pack)
    except Exception as exc:  # noqa: BLE001
        logger.error("prompt-tag: LLM draft failed for feed %s: %s", feed_id, exc)
        draft = heuristic_prompt_tag_draft(pack)

    name = slugify_tag_name(draft["name"])
    prompt = draft["prompt"].strip()
    if not name or not prompt:
        return None

    tag = _ensure_tag(name, prompt, force_update_prompt=force)
    if tag is None:
        return None

    db.session.expire(feed)
    feed = db.session.get(Feed, feed_id)
    if feed is None:
        return None
    if getattr(feed, "prompt_tag_id", None) is not None and not force:
        logger.info(
            "prompt-tag: aborting assign for feed %s — tag set concurrently",
            feed_id,
        )
        return None

    result = writer_client.update(
        "Feed",
        feed_id,
        {"prompt_tag_id": tag.id},
        wait=True,
    )
    if result is None or not result.success:
        logger.error(
            "prompt-tag: failed to assign tag %s to feed %s: %s",
            tag.id,
            feed_id,
            getattr(result, "error", None),
        )
        return None

    db.session.expire_all()
    logger.info(
        "prompt-tag: assigned tag %s (%r) to feed %s", tag.id, tag.name, feed_id
    )
    return {
        "tag_id": tag.id,
        "name": tag.name,
        "prompt": tag.prompt,
        "prompt_tag_id": tag.id,
    }


def maybe_auto_generate_prompt_tag(feed_id: int) -> None:
    """Entry point for post-add async hook."""
    if not bool(getattr(config, "auto_generate_prompt_tag", True)):
        logger.info("prompt-tag: auto-generate disabled; skipping feed %s", feed_id)
        return
    try:
        generate_and_persist_prompt_tag(feed_id, force=False)
    except Exception as exc:  # noqa: BLE001
        logger.error("prompt-tag: auto-generate failed for feed %s: %s", feed_id, exc)
