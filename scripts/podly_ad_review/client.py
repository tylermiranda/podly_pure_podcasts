"""Shared Tower API client for Podly ad-review scripts."""

from __future__ import annotations

import http.cookiejar
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = REPO_ROOT / ".cursor" / "skills" / "podly-ad-review" / "state.json"
PKM_SECRETS = Path("/Users/tyler/Documents/git/pkm/secrets.md")
DEFAULT_PODLY = "http://192.168.1.5:5001"
PORTAINER = "http://192.168.1.5:9000/api"


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    return json.loads(path.read_text())


def save_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.write_text(json.dumps(state, indent=2) + "\n")


def _portainer_admin_creds() -> tuple[str, str]:
    secrets = PKM_SECRETS.read_text()
    api_key_m = re.search(r"ptr_\S+", secrets)
    if not api_key_m:
        raise RuntimeError("Portainer API key not found in pkm/secrets.md")
    api_key = api_key_m.group(0)
    filters = urllib.parse.quote(json.dumps({"name": ["podly-pure-podcasts"]}))
    containers = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(
                f"{PORTAINER}/endpoints/2/docker/containers/json?all=true&filters={filters}",
                headers={"X-API-Key": api_key},
            ),
            timeout=30,
        ).read()
    )
    if not containers:
        raise RuntimeError("podly-pure-podcasts container not found")
    insp = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(
                f"{PORTAINER}/endpoints/2/docker/containers/{containers[0]['Id']}/json",
                headers={"X-API-Key": api_key},
            ),
            timeout=30,
        ).read()
    )
    env = {
        e.split("=", 1)[0]: e.split("=", 1)[1]
        for e in insp["Config"]["Env"]
        if "=" in e
    }
    return env["PODLY_ADMIN_USERNAME"], env["PODLY_ADMIN_PASSWORD"]


class PodlyClient:
    def __init__(self, base_url: str = DEFAULT_PODLY) -> None:
        self.base_url = base_url.rstrip("/")
        self._cj = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cj)
        )

    def login(self, username: str | None = None, password: str | None = None) -> None:
        if not username or not password:
            username, password = _portainer_admin_creds()
        self.request(
            "POST",
            "/api/auth/login",
            {"username": username, "password": password},
        )

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
        timeout: float = 120,
    ) -> Any:
        body = None if data is None else json.dumps(data).encode()
        headers = {"Content-Type": "application/json"} if data is not None else {}
        req = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise RuntimeError(f"{method} {path} -> {e.code}: {detail[:500]}") from e

    def get_config(self) -> dict[str, Any]:
        return self.request("GET", "/api/config")

    def put_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("PUT", "/api/config", payload)

    def get_feeds(self) -> list[dict[str, Any]]:
        return self.request("GET", "/feeds")

    def list_posts(
        self, feed_id: int, page: int = 1, per_page: int = 15
    ) -> list[dict[str, Any]]:
        data = self.request(
            "GET", f"/api/feeds/{feed_id}/posts?page={page}&per_page={per_page}"
        )
        if isinstance(data, list):
            return data
        return list(data.get("items") or data.get("posts") or [])

    def get_stats(self, guid: str) -> dict[str, Any]:
        enc = urllib.parse.quote(guid, safe="")
        return self.request("GET", f"/api/posts/{enc}/stats")

    def get_status(self, guid: str) -> dict[str, Any]:
        enc = urllib.parse.quote(guid, safe="")
        return self.request("GET", f"/api/posts/{enc}/status")

    def reprocess_keep_transcript(self, guid: str) -> dict[str, Any]:
        enc = urllib.parse.quote(guid, safe="")
        return self.request("POST", f"/api/posts/{enc}/reprocess/keep-transcript")

    def list_tags(self) -> list[dict[str, Any]]:
        return self.request("GET", "/api/tags")

    def patch_tag(self, tag_id: int, prompt: str) -> dict[str, Any]:
        return self.request("PATCH", f"/api/tags/{tag_id}", {"prompt": prompt})

    def active_jobs(self, limit: int = 20) -> Any:
        return self.request("GET", f"/api/jobs/active?limit={limit}")
