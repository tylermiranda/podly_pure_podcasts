"""Map podcast-client User-Agent strings to short display names."""

from __future__ import annotations

# Order matters: more specific patterns first.
_UA_PATTERNS: tuple[tuple[str, str], ...] = (
    ("applecoremedia", "Apple Podcasts"),
    ("podcasts/", "Apple Podcasts"),
    ("itunes", "Apple Podcasts"),
    ("overcast", "Overcast"),
    ("antenna", "AntennaPod"),
    ("pocket casts", "Pocket Casts"),
    ("pocketcasts", "Pocket Casts"),
    ("sh.podcast.castro", "Castro"),
    ("castro", "Castro"),
    ("podcastaddict", "Podcast Addict"),
    ("podcast addict", "Podcast Addict"),
    ("gpodder", "gPodder"),
    ("beyondpod", "BeyondPod"),
    ("player fm", "Player FM"),
    ("playerfm", "Player FM"),
    ("spotify", "Spotify"),
    # YouTube Music before generic Google / Mozilla fallbacks.
    ("com.google.ios.youtubemusic", "YouTube Music"),
    ("youtube music", "YouTube Music"),
    ("youtubemusic", "YouTube Music"),
    ("feedfetcher", "YouTube Music"),
    ("googlebot-podcast", "Google Podcasts"),
    ("google podcasts", "Google Podcasts"),
    ("downcast", "Downcast"),
    ("breaker", "Breaker"),
    ("castbox", "Castbox"),
    ("stitcher", "Stitcher"),
    ("radio public", "RadioPublic"),
    ("radiopublic", "RadioPublic"),
    ("rssguard", "RSS Guard"),
    ("feedbin", "Feedbin"),
    ("feedly", "Feedly"),
    ("inoreader", "Inoreader"),
    ("podverse", "Podverse"),
    ("fountain", "Fountain"),
    ("podbean", "Podbean"),
    ("podcastrepublic", "Podcast Republic"),
    ("podcast guru", "Podcast Guru"),
    ("podcastguru", "Podcast Guru"),
    ("harmony", "Harmony"),
    ("okhttp", "Android Client"),
    ("cfnetwork", "Apple Podcasts"),
)


def normalize_client_user_agent(user_agent: str | None) -> str | None:
    """Return a short client name for a User-Agent, or None if empty."""
    if user_agent is None:
        return None
    ua = user_agent.strip()
    if not ua:
        return None

    lowered = ua.lower()
    for needle, name in _UA_PATTERNS:
        if needle in lowered:
            return name

    # First token of an unknown UA, capped for display.
    first = ua.split()[0].strip()
    if not first:
        return "Other"
    # Strip version suffixes like Client/1.2
    base = first.split("/")[0].strip() or first
    if len(base) > 40:
        base = base[:37] + "..."
    return base or "Other"
