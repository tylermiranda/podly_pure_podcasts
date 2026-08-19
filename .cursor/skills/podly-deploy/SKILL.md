---
name: podly-deploy
description: >-
  Commit, push, tear down the local Podly stack (writer 50001, Flask 5001,
  Vite 5174), wait for GHCR, and redeploy the Tower Portainer stack. Use when
  the user asks to deploy Podly to Tower, publish the fork image, or ship
  local Podly changes to production.
---

# Podly deploy (Tower)

Ship `main` on the **fork** (`tylermiranda/podly_pure_podcasts`) to Portainer stack **podly** (id **115**, endpoint **2**) on Tower.

Production UI is baked into the Docker image (`npm run build` in the Dockerfile). Git push alone is not enough; wait for GHCR then recreate the stack.

## Constants

| Item | Value |
|------|--------|
| Git remote | `origin` → `https://github.com/tylermiranda/podly_pure_podcasts.git` |
| GH repo for Actions/GHCR | `tylermiranda/podly_pure_podcasts` (do **not** use default `podly-pure-podcasts/podly_pure_podcasts`) |
| Image | `ghcr.io/tylermiranda/podly-pure-podcasts:main-latest-amd64` |
| Portainer | `http://192.168.1.5:9000` (API key `ptr_…` in `/Users/tyler/Documents/git/pkm/secrets.md`; never commit it) |
| Stack | name `podly`, id `115`, endpoint `2` |
| Container | `podly-pure-podcasts` |
| Public URL | `https://pods.tylermiranda.com` |
| LAN UI | `http://192.168.1.5:5001` |

Local ports to tear down: **50001** writer, **5001** Flask, **5174** Vite. Do not kill other projects' 5173.

## Procedure

Copy and track:

```
- [ ] 1. Commit (if dirty)
- [ ] 2. Push origin main
- [ ] 3. Tear down local stack
- [ ] 4. Dispatch GHCR amd64 latest build; wait success
- [ ] 5. Portainer pull + recreate stack 115
- [ ] 6. Confirm container healthy
```

### 1. Commit

Only if the working tree is dirty. Do not amend auto-commits. Follow repo git rules (no `--no-verify`, HEREDOC message).

### 2. Push

```bash
git push origin HEAD
```

### 3. Tear down local stack

**Always** stop local writer / Flask / Vite before Tower recreate so this Mac is not accidentally serving or proxying against live data.

```bash
bash .cursor/skills/podly-deploy/scripts/teardown-local.sh
```

Expect 5174, 5001, and 50001 to have no LISTEN after this.

### 4. Publish image

Fork Actions do not always run on push; dispatch explicitly. Tower only needs amd64 `latest` (tag `main-latest-amd64`):

```bash
gh workflow run "Build and Publish Docker Images" \
  --repo tylermiranda/podly_pure_podcasts \
  --ref main \
  -f build_latest=true \
  -f build_lite=false \
  -f build_gpu_nvidia=false \
  -f build_gpu_amd=false \
  -f build_amd64=true \
  -f build_arm64=false
```

Wait until that run is `success` (`gh run watch` / `gh run list --repo tylermiranda/podly_pure_podcasts --branch main --limit 5`). Typical duration ~7–15 minutes.

### 5–6. Redeploy Tower

```bash
uv run python .cursor/skills/podly-deploy/scripts/portainer-redeploy.py
```

Prints pull progress, stack update, and waits until `podly-pure-podcasts` is `running` and `healthy`.

Smoke: `curl -sS -o /dev/null -w '%{http_code}\n' http://192.168.1.5:5001/` should be `200` (or `401` if hitting an authed JSON route). Public UI: `https://pods.tylermiranda.com` (Cloudflare Access in front).

## Notes

- Keep the `./src/instance` volume; never recreate the stack with prune of named volumes.
- Do not put Portainer tokens, GHCR creds, or Podly passwords in the skill, scripts, or commit messages.
- `gh` default repo in this workspace may be **upstream**; always pass `--repo tylermiranda/podly_pure_podcasts`.
