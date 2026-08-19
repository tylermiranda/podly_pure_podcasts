#!/usr/bin/env python3
"""Pull GHCR Podly image on Tower and recreate Portainer stack 115."""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

SECRETS = Path("/Users/tyler/Documents/git/pkm/secrets.md")
BASE = "http://192.168.1.5:9000/api"
ENDPOINT_ID = 2
STACK_ID = 115
IMAGE = "ghcr.io/tylermiranda/podly-pure-podcasts"
TAG = "main-latest-amd64"
CONTAINER_NAME = "podly-pure-podcasts"


def api_key() -> str:
    text = SECRETS.read_text()
    match = re.search(r"ptr_\S+", text)
    if not match:
        raise SystemExit("Portainer API key not found in pkm/secrets.md")
    return match.group(0)


def req(method: str, path: str, data: dict | None = None, timeout: int = 180, raw: bool = False):
    body = None if data is None else json.dumps(data).encode()
    headers = {"X-API-Key": api_key()}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        payload = resp.read()
        if raw:
            return resp.status, payload
        text = payload.decode(errors="replace")
        return resp.status, (json.loads(text) if text else None)


def main() -> None:
    qs = urllib.parse.urlencode({"fromImage": IMAGE, "tag": TAG})
    print("pulling", f"{IMAGE}:{TAG}", flush=True)
    status, payload = req("POST", f"/endpoints/{ENDPOINT_ID}/docker/images/create?{qs}", timeout=600, raw=True)
    print("pull_http", status, "bytes", len(payload), flush=True)
    tail = payload.decode(errors="replace")[-800:].replace("\r", "\n")
    for line in [ln for ln in tail.splitlines() if ln.strip()][-5:]:
        print(" ", line[:220], flush=True)

    _, stack = req("GET", f"/stacks/{STACK_ID}")
    _, stack_file = req("GET", f"/stacks/{STACK_ID}/file")
    compose = stack_file["StackFileContent"]
    env = stack.get("Env") or []
    print("stack", stack.get("Name"), "id", stack.get("Id"), "status", stack.get("Status"), flush=True)
    for line in compose.splitlines():
        if "image:" in line or "container_name:" in line:
            print(" ", line.strip(), flush=True)

    body = {
        "StackFileContent": compose,
        "Env": env,
        "Prune": False,
        "PullImage": True,
    }
    status, updated = req("PUT", f"/stacks/{STACK_ID}?endpointId={ENDPOINT_ID}", body, timeout=180)
    print(
        "stack_update",
        status,
        "id",
        (updated or {}).get("Id"),
        "name",
        (updated or {}).get("Name"),
        flush=True,
    )

    filters = urllib.parse.quote(json.dumps({"name": [CONTAINER_NAME]}))
    for i in range(36):
        containers = req("GET", f"/endpoints/{ENDPOINT_ID}/docker/containers/json?all=true&filters={filters}")[1]
        if not containers:
            print(f"container[{i}] missing", flush=True)
            time.sleep(5)
            continue
        container = containers[0]
        state = container.get("State")
        health = container.get("Status")
        print(f"container[{i}]", state, health, flush=True)
        if state == "running" and "healthy" in (health or ""):
            print("healthy", flush=True)
            return
        time.sleep(5)
    raise SystemExit("container did not become healthy")


if __name__ == "__main__":
    main()
