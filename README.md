# Homelab stack

Docker Compose stack for a home media server: Jellyfin, Sonarr/Radarr/Prowlarr,
qBittorrent behind Gluetun (VPN), Jellyseerr, WireGuard, CouchDB, and Watchtower.

## Setup

```sh
cp .env.example .env    # fill in real values; .env is gitignored
docker compose up -d
```

App config/data lives in per-service directories next to the compose file
(`./jellyfin/config`, `./sonarr/config`, ...). Those are gitignored on purpose.

## Scripts

- `av1_*.py`: one-off AV1 re-encode/migration helpers for Radarr/Sonarr

Scripts read credentials from `.env`. `av1_pipeline.py` reads its queue from a local,
gitignored `av1_remux_queue.json` (list of `{"movieId", "title", "type"}`).
