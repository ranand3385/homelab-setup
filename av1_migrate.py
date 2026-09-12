#!/usr/bin/env python3
"""AV1 Migration v3 - Movies + TV, sorted by seeders (fast first, slow last)."""

import json, time, sys, os, requests
from datetime import datetime, timezone

RADARR_URL = f"http://localhost:{os.environ['RADARR_PORT']}/api/v3"
RADARR_KEY = os.environ["RADARR_API_KEY"]
SONARR_URL = f"http://localhost:{os.environ['SONARR_PORT']}/api/v3"
SONARR_KEY = os.environ["SONARR_API_KEY"]

STALL_TIMEOUT = 600       # 10 min stall → swap
ETA_MAX_HOURS = 12        # ETA > 12h → swap
CHECK_INTERVAL = 600      # monitor every 10 min
MAX_ATTEMPTS = 3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "av1_migration.log")
STATE_FILE = os.path.join(BASE_DIR, "av1_migration_progress.json")

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def api(base, key, method, path, data=None):
    headers = {"X-Api-Key": key, "Content-Type": "application/json"}
    try:
        if method == "GET":
            r = requests.get(f"{base}/{path}", headers=headers, timeout=30)
        elif method == "POST":
            r = requests.post(f"{base}/{path}", headers=headers, json=data, timeout=30)
        elif method == "DELETE":
            r = requests.delete(f"{base}/{path}", headers=headers, timeout=30)
            return r.status_code
        return r.json()
    except Exception as e:
        log(f"  API error: {e}")
        return None

def radarr(method, path, data=None):
    return api(RADARR_URL, RADARR_KEY, method, path, data)

def sonarr(method, path, data=None):
    return api(SONARR_URL, SONARR_KEY, method, path, data)

def parse_eta(eta_str):
    if not eta_str:
        return None
    try:
        eta = datetime.fromisoformat(eta_str.replace("Z", "+00:00"))
        return (eta - datetime.now(timezone.utc)).total_seconds() / 3600
    except:
        return None

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=list)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            s = json.load(f)
            for k in ("movie_done","movie_skip","movie_fail","tv_done","tv_skip","tv_fail","blocklisted_guids"):
                s.setdefault(k, [])
            s.setdefault("active_swaps", {})
            return s
    return {"movie_done":[],"movie_skip":[],"movie_fail":[],"tv_done":[],"tv_skip":[],"tv_fail":[],
            "blocklisted_guids":[],"active_swaps":{}}

def find_best_av1_movie(movie_id, exclude_guids=None):
    exclude_guids = exclude_guids or set()
    releases = radarr("GET", f"release?movieId={movie_id}")
    if not releases:
        return None, None, 0

    av1_4k, av1_1080 = [], []
    for r in releases:
        guid = r.get("guid", "")
        if guid in exclude_guids or r.get("rejected"):
            continue
        cf_names = [cf.get("name","") for cf in r.get("customFormats",[])]
        if "AV1" not in cf_names and "4K AV1" not in cf_names:
            continue
        quality = r.get("quality",{}).get("quality",{}).get("name","")
        entry = {"guid":guid,"indexerId":r.get("indexerId"),"title":r.get("title",""),
                 "seeders":r.get("seeders",0),"size_gb":r.get("size",0)/1e9,"quality":quality}
        if "2160p" in quality:
            av1_4k.append(entry)
        elif "1080p" in quality:
            av1_1080.append(entry)

    av1_4k.sort(key=lambda x: x["seeders"], reverse=True)
    av1_1080.sort(key=lambda x: x["seeders"], reverse=True)
    if av1_4k:
        return av1_4k[0], "4K AV1", av1_4k[0]["seeders"]
    if av1_1080:
        return av1_1080[0], "1080p AV1", av1_1080[0]["seeders"]
    return None, None, 0

def remove_radarr_queue(queue_id):
    return radarr("DELETE", f"queue/{queue_id}?removeFromClient=true&blocklist=true&skipRedownload=true")

def remove_sonarr_queue(queue_id):
    return sonarr("DELETE", f"queue/{queue_id}?removeFromClient=true&blocklist=true&skipRedownload=true")

# ── PHASE 1: Scan all movies, sort by seeders (fast first) ──

def scan_and_sort_movies(state):
    """Check AV1 availability for all non-AV1 movies, return sorted by seeders desc."""
    movies = radarr("GET", "movie") or []
    candidates = []

    for m in movies:
        mid = m["id"]
        if not m.get("hasFile"):
            continue
        if mid in state["movie_done"] or mid in state["movie_skip"] or mid in state["movie_fail"]:
            continue
        mf = m.get("movieFile", {})
        cfs = [cf.get("name","") for cf in mf.get("customFormats",[])]
        if "AV1" in cfs or "4K AV1" in cfs:
            continue
        candidates.append((mid, m.get("title","")))

    log(f"Scanning {len(candidates)} movies for AV1 releases...")
    results = []

    for mid, title in candidates:
        release, quality, seeders = find_best_av1_movie(mid, set(state["blocklisted_guids"]))
        if not release:
            log(f"  SKIP: [{mid}] {title} - no AV1")
            state["movie_skip"].append(mid)
            save_state(state)
        else:
            results.append((mid, title, release, quality, seeders))
            log(f"  FOUND: [{mid}] {title} | {quality} | {seeders}S | {release['size_gb']:.1f}GB")
        time.sleep(0.5)

    # Sort by seeders descending — fast downloads first, slow ones last
    results.sort(key=lambda x: x[4], reverse=True)
    return results

def grab_sorted_movies(sorted_movies, state):
    """Grab all movies in seeder-sorted order (fast first)."""
    grabbed = 0
    for mid, title, release, quality, seeders in sorted_movies:
        log(f"  GRAB: [{mid}] {title} | {quality} | {seeders}S | {release['size_gb']:.1f}GB")
        if radarr("POST", "release", {"guid": release["guid"], "indexerId": release["indexerId"]}):
            grabbed += 1
            state["active_swaps"][str(mid)] = {
                "type":"movie","attempts":1,"tried_guids":[release["guid"]],
                "start_time":time.time(),"title":title,
            }
            save_state(state)
        else:
            log(f"  FAIL grab: [{mid}] {title}")
        time.sleep(1)
    return grabbed

# ── PHASE 2: TV SHOWS ──

def search_all_tv(state):
    series_list = sonarr("GET", "series") or []
    for s in series_list:
        sid = s["id"]
        title = s.get("title","")
        if sid in state["tv_done"] or sid in state["tv_skip"]:
            continue
        log(f"  TV SEARCH: [{sid}] {title}")
        sonarr("POST", "command", {"name": "SeriesSearch", "seriesId": sid})
        state["active_swaps"][f"tv_{sid}"] = {
            "type":"tv","seriesId":sid,"start_time":time.time(),"title":title,
        }
        save_state(state)
        time.sleep(2)

# ── MONITOR ──

def monitor_cycle(state):
    rq = api(RADARR_URL, RADARR_KEY, "GET", "queue?pageSize=100")
    sq = api(SONARR_URL, SONARR_KEY, "GET", "queue?pageSize=200")
    radarr_records = rq.get("records",[]) if rq else []
    sonarr_records = sq.get("records",[]) if sq else []

    downloading = 0
    stalled = 0
    removed = 0

    for records, app, remove_fn in [
        (radarr_records, "radarr", remove_radarr_queue),
        (sonarr_records, "sonarr", remove_sonarr_queue),
    ]:
        for r in records:
            status = r.get("status","")
            err = r.get("errorMessage","")
            size = r.get("size",0)
            left = r.get("sizeleft",0)
            pct = ((size-left)/size*100) if size else 0
            eta_hours = parse_eta(r.get("estimatedCompletionTime",""))
            title = r.get("title","")[:60]
            q_id = r["id"]

            # Find start_time from swaps
            if app == "radarr":
                swap_key = str(r.get("movieId",""))
            else:
                swap_key = f"tv_{r.get('seriesId','')}"
            swap_info = state["active_swaps"].get(swap_key, {})
            start_time = swap_info.get("start_time", time.time())
            elapsed = time.time() - start_time

            is_stalled = ("metadata" in err.lower() or "stalled" in err.lower() or
                         "no connections" in err.lower() or
                         (status == "warning" and pct < 1) or
                         (status == "queued" and size == 0 and elapsed > 120))

            need_remove = False
            reason = ""

            if is_stalled and elapsed > STALL_TIMEOUT:
                need_remove = True
                reason = f"stalled {elapsed/60:.0f}min"
                stalled += 1
            elif eta_hours is not None and eta_hours > ETA_MAX_HOURS and elapsed > STALL_TIMEOUT:
                need_remove = True
                reason = f"ETA {eta_hours:.1f}h"

            if need_remove:
                log(f"  REMOVE: [{app}] {title} - {reason}")
                remove_fn(q_id)
                removed += 1

                if app == "radarr" and r.get("movieId"):
                    mid = r["movieId"]
                    attempts = swap_info.get("attempts",0) + 1
                    tried = set(swap_info.get("tried_guids",[]))

                    if attempts <= MAX_ATTEMPTS:
                        time.sleep(2)
                        release, quality, seeders = find_best_av1_movie(mid, tried)
                        if release:
                            log(f"  RETRY #{attempts}: {quality} | {seeders}S | {release['title'][:50]}")
                            radarr("POST","release",{"guid":release["guid"],"indexerId":release["indexerId"]})
                            tried.add(release["guid"])
                            state["active_swaps"][str(mid)] = {
                                "type":"movie","attempts":attempts,"tried_guids":list(tried),
                                "start_time":time.time(),"title":swap_info.get("title",""),
                            }
                            save_state(state)
                            continue

                    log(f"  GAVE UP: movie {mid}")
                    state["movie_fail"].append(mid)
                    state["active_swaps"].pop(str(mid), None)
                    save_state(state)
            elif status == "downloading":
                downloading += 1

    # Check completed movies
    active_movie_ids = {r.get("movieId") for r in radarr_records}
    for key in list(state["active_swaps"].keys()):
        info = state["active_swaps"][key]
        if info["type"] == "movie":
            mid = int(key)
            if mid not in active_movie_ids and mid not in state["movie_done"]:
                m = radarr("GET", f"movie/{mid}")
                if m and m.get("hasFile"):
                    mf = m.get("movieFile",{})
                    cfs = [cf.get("name","") for cf in mf.get("customFormats",[])]
                    q = mf.get("quality",{}).get("quality",{}).get("name","")
                    sz = mf.get("size",0)/1e9
                    if "AV1" in cfs or "4K AV1" in cfs:
                        log(f"  DONE: [{mid}] {info['title']} | {q} {sz:.1f}GB [AV1]")
                        state["movie_done"].append(mid)
                del state["active_swaps"][key]
                save_state(state)

    total_q = len(radarr_records) + len(sonarr_records)
    log(f"Queue: {total_q} ({downloading} dl, {stalled} stalled, {removed} removed) | "
        f"M: {len(state['movie_done'])}done/{len(state['movie_skip'])}skip/{len(state['movie_fail'])}fail | "
        f"TV: {len(state['tv_done'])}done/{len(state['tv_skip'])}skip")

def main():
    state = load_state()
    log("=== AV1 MIGRATION v3 START ===")
    log("Strategy: scan all → sort by seeders (fast first, slow last) → grab all → monitor")

    # Phase 1: Scan and sort movies
    sorted_movies = scan_and_sort_movies(state)
    if sorted_movies:
        log(f"\n--- Grabbing {len(sorted_movies)} movies (fastest first) ---")
        log(f"Fastest: {sorted_movies[0][1]} ({sorted_movies[0][4]}S) | Slowest: {sorted_movies[-1][1]} ({sorted_movies[-1][4]}S)")
        grabbed = grab_sorted_movies(sorted_movies, state)
        log(f"Grabbed {grabbed} movies")

    # Phase 2: Search TV shows
    log("\n--- Searching TV shows ---")
    search_all_tv(state)

    # Phase 3: Monitor every 10 min
    log("\n--- Monitoring (10 min intervals) ---")
    while True:
        time.sleep(CHECK_INTERVAL)
        rq = api(RADARR_URL, RADARR_KEY, "GET", "queue?pageSize=100")
        sq = api(SONARR_URL, SONARR_KEY, "GET", "queue?pageSize=200")
        r_count = rq.get("totalRecords",0) if rq else 0
        s_count = sq.get("totalRecords",0) if sq else 0

        if r_count == 0 and s_count == 0 and not state["active_swaps"]:
            break

        monitor_cycle(state)

    log("=== AV1 MIGRATION COMPLETE ===")
    log(f"Movies - Done:{len(state['movie_done'])} Skip:{len(state['movie_skip'])} Fail:{len(state['movie_fail'])}")
    log(f"TV - Done:{len(state['tv_done'])} Skip:{len(state['tv_skip'])}")

if __name__ == "__main__":
    main()
