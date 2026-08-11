YTM follow-up tests (after A and B both failed)
================================================
C = ZeroAds feed clone on YOUR test host
    - If C fails: YouTube Music is rejecting the host (trycloudflare etc.), not the feed content
    - If C passes: host is fine; content combination matters

D = good itunes tags + clean ZeroAds audio URLs (both fixes together)
    - Only interpret if C passed
    - If D passes and A/B failed: YouTube Music needs BOTH tags and clean URLs

Also re-check ZeroAds original URL still adds successfully as a control.
