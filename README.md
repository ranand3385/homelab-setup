# Homelab stack

Docker Compose stack for a home media server: Jellyfin, Sonarr/Radarr/Prowlarr/Bazarr,
qBittorrent behind Gluetun (VPN), Jellyseerr, Kavita/Komga/Suwayomi/Kapowarr,
Pi-hole, WireGuard, Actual Budget (via Tailscale), CouchDB, and Watchtower.

## Setup

```sh
cp .env.example .env    # fill in real values; .env is gitignored
docker compose up -d
```

App config/data lives in per-service directories next to the compose file
(`./jellyfin/config`, `./sonarr/config`, ...). Those are gitignored on purpose.

## Scripts

- `update-port.sh`: syncs Gluetun's forwarded port into qBittorrent (cron, every 5 min)
- `scripts/kavita-scan.sh`: triggers a Kavita library scan (cron)
- `av1_*.py`: one-off AV1 re-encode/migration helpers for Radarr/Sonarr

Scripts read credentials from `.env`. `av1_pipeline.py` reads its queue from a local,
gitignored `av1_remux_queue.json` (list of `{"movieId", "title", "type"}`).
