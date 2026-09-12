#!/usr/bin/env python3
"""AV1 pipeline manager - monitors downloads, grabs next remuxes, handles stalls."""

import json, time, requests, os
from datetime import datetime, timezone

RADARR_URL = f"http://localhost:{os.environ['RADARR_PORT']}/api/v3"
RADARR_KEY = os.environ["RADARR_API_KEY"]
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "av1_pipeline.log")
STATE_FILE = os.path.join(BASE_DIR, "av1_pipeline_state.json")

# Local, gitignored list of {"movieId", "title", "type": "4k_remux"|"1080p_remux"}
REMUX_QUEUE_FILE = os.path.join(BASE_DIR, "av1_remux_queue.json")
with open(REMUX_QUEUE_FILE) as f:
    REMUX_QUEUE = json.load(f)

STALL_TIMEOUT = 600
ETA_MAX_HOURS = 12

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def radarr(method, path, data=None):
    headers = {"X-Api-Key": RADARR_KEY, "Content-Type": "application/json"}
    try:
        if method == "GET":
            r = requests.get(f"{RADARR_URL}/{path}", headers=headers, timeout=30)
        elif method == "POST":
            r = requests.post(f"{RADARR_URL}/{path}", headers=headers, json=data, timeout=30)
        elif method == "DELETE":
            r = requests.delete(f"{RADARR_URL}/{path}", headers=headers, timeout=30)
            return r.status_code
        return r.json()
    except Exception as e:
        log(f"  API error: {e}")
        return None

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"remux_grabbed": [], "remux_done": [], "decisions_needed": [], "av1_imported": []}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def find_remux(movie_id, prefer_4k=True):
    releases = radarr("GET", f"release?movieId={movie_id}")
    if not releases:
        return None
    remuxes = []
    for r in (releases or []):
        rejections = [str(x) for x in r.get("rejections", [])]
        if any("Unknown Movie" in x for x in rejections):
            continue
        quality = r.get("quality", {}).get("quality", {}).get("name", "")
        if "remux" not in quality.lower():
            continue
        seeders = r.get("seeders", 0)
        if seeders < 1:
            continue
        is_4k = "2160p" in quality.lower()
        remuxes.append({"release": r, "is_4k": is_4k, "seeders": seeders,
                        "size_gb": r.get("size", 0) / 1e9, "quality": quality})

    if prefer_4k:
        r4k = [r for r in remuxes if r["is_4k"]]
        if r4k:
            return max(r4k, key=lambda x: x["seeders"])
    r1080 = [r for r in remuxes if not r["is_4k"]]
    if r1080:
        return max(r1080, key=lambda x: x["seeders"])
    if remuxes:
        return max(remuxes, key=lambda x: x["seeders"])
    return None

def check_and_grab_next_remux(state):
    queue = radarr("GET", "queue?pageSize=50")
    records = queue.get("records", []) if queue else []
    remux_downloading = any(r.get("size", 0) / 1e9 > 25 for r in records)

    if remux_downloading:
        return

    for item in REMUX_QUEUE:
        mid = item["movieId"]
        if mid in state["remux_grabbed"] or mid in state["remux_done"]:
            continue
        prefer_4k = "4k" in item["type"]
        remux = find_remux(mid, prefer_4k)
        if remux:
            rel = remux["release"]
            log(f"GRAB REMUX: [{mid}] {item['title']} | {remux['quality']} | {remux['seeders']}S | {remux['size_gb']:.0f}GB")
            radarr("POST", "release", {"guid": rel["guid"], "indexerId": rel["indexerId"]})
            state["remux_grabbed"].append(mid)
            save_state(state)
        else:
            log(f"NO REMUX: [{mid}] {item['title']}")
            state["decisions_needed"].append({"movieId": mid, "title": item["title"], "issue": "no remux found"})
            save_state(state)
        break

def handle_stalls(state):
    queue = radarr("GET", "queue?pageSize=50")
    records = queue.get("records", []) if queue else []

    for r in records:
        err = r.get("errorMessage", "")
        status = r.get("status", "")
        title = r.get("title", "")[:60]
        size = r.get("size", 0)
        left = r.get("sizeleft", 0)
        pct = ((size - left) / size * 100) if size else 0

        is_stalled = ("no connections" in err.lower() or "stalled" in err.lower() or
                      "metadata" in err.lower())

        if size / 1e9 > 25:
            continue

        if is_stalled and pct < 5:
            log(f"STALLED: {title} | {pct:.0f}% | {err}")
            state["decisions_needed"].append({
                "title": title, "issue": f"stalled at {pct:.0f}%: {err}",
                "queue_id": r["id"], "movie_id": r.get("movieId")
            })
            save_state(state)

def monitor_cycle(state):
    queue = radarr("GET", "queue?pageSize=50")
    records = queue.get("records", []) if queue else []

    downloading = 0
    completed = 0
    for r in records:
        status = r.get("status", "")
        size = r.get("size", 0)
        left = r.get("sizeleft", 0)
        pct = ((size - left) / size * 100) if size else 0
        title = r.get("title", "")[:50]
        size_gb = size / 1e9

        if status == "downloading":
            downloading += 1
        elif status == "completed":
            completed += 1
            mid = r.get("movieId")
            if mid and mid in state["remux_grabbed"] and mid not in state["remux_done"]:
                log(f"REMUX READY: [{mid}] {title} - ready for PC to pull")
                state["remux_done"].append(mid)
                save_state(state)

    log(f"Queue: {len(records)} items ({downloading} dl, {completed} done) | "
        f"Remux grabbed: {len(state['remux_grabbed'])} | Remux ready: {len(state['remux_done'])}")

    check_and_grab_next_remux(state)
    handle_stalls(state)

def main():
    state = load_state()
    log("=== AV1 PIPELINE MONITOR START ===")

    while True:
        monitor_cycle(state)
        time.sleep(600)

if __name__ == "__main__":
    main()
