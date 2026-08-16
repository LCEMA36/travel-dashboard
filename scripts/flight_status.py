#!/usr/bin/env python3
"""
Pulls live status for any flight in data/trips.json that is currently active
(departing within the next 12 hours or landed within the last 6) from
AviationStack, and writes the result to data/live-status.json.

Run manually:
    AVIATIONSTACK_KEY=xxxx python3 scripts/flight_status.py

Run on a schedule via .github/workflows/flight-status.yml (see README).

Free AviationStack tier = 100 requests/month, so this script only calls the
API for flights that are actually "in window" rather than every flight in
trips.json, to conserve quota.
"""
import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

def _locate(filename):
    """Find a data file whether the repo uses the data/ folder layout or a
    flattened one (a GitHub web-UI upload puts everything at the root).
    Falls back to the data/ path so a fresh checkout still writes there."""
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    candidates = [
        os.path.join(repo_root, "data", filename),
        os.path.join(repo_root, filename),
        os.path.join(here, "data", filename),
        os.path.join(here, filename),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


TRIPS_PATH = _locate("trips.json")
LIVE_PATH = _locate("live-status.json")

API_KEY = os.environ.get("AVIATIONSTACK_KEY")
API_URL = "https://api.aviationstack.com/v1/flights"

WINDOW_BEFORE = timedelta(hours=12)   # start checking a flight this long before scheduled departure
WINDOW_AFTER = timedelta(hours=6)     # keep checking this long after scheduled arrival


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r") as f:
        return json.load(f)


def is_in_window(flight, now):
    dep_raw = flight.get("origin", {}).get("scheduled_departure")
    arr_raw = flight.get("destination", {}).get("scheduled_arrival")
    if not dep_raw or not arr_raw or not flight.get("flight_number"):
        # Can't track a flight AviationStack has no flight number for, or
        # with unparsed dates (e.g. a TripIt event the sync couldn't fully parse).
        return False
    dep = datetime.fromisoformat(dep_raw)
    arr = datetime.fromisoformat(arr_raw)
    return (dep - WINDOW_BEFORE) <= now <= (arr + WINDOW_AFTER)


def fetch_status(flight):
    params = {
        "access_key": API_KEY,
        "flight_iata": flight["flight_number"],
    }
    url = API_URL + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  WARN: request failed for {flight['flight_number']}: {e}", file=sys.stderr)
        return None

    results = data.get("data") or []
    if not results:
        return None

    # AviationStack can return multiple legs for a flight number across dates;
    # pick the one whose departure date matches our scheduled flight.
    dep_date = flight["origin"]["scheduled_departure"][:10]
    match = None
    for r in results:
        if (r.get("departure") or {}).get("scheduled", "").startswith(dep_date):
            match = r
            break
    if not match:
        match = results[0]

    live = match.get("live") or {}
    dep = match.get("departure") or {}
    arr = match.get("arrival") or {}
    flight_status = match.get("flight_status") or "scheduled"

    status_map = {
        "scheduled": "scheduled",
        "active": "active",
        "landed": "landed",
        "cancelled": "cancelled",
        "incident": "diverted",
        "diverted": "diverted",
    }
    status = status_map.get(flight_status, "unknown")
    if status == "scheduled" and dep.get("delay"):
        status = "delayed"

    return {
        "id": flight["id"],
        "status": status,
        "departure_gate": dep.get("gate"),
        "departure_terminal": dep.get("terminal"),
        "arrival_gate": arr.get("gate"),
        "arrival_terminal": arr.get("terminal"),
        "estimated_departure": dep.get("estimated"),
        "estimated_arrival": arr.get("estimated"),
        "delay_minutes": dep.get("delay"),
        "live_latitude": live.get("latitude"),
        "live_longitude": live.get("longitude"),
        "live_altitude": live.get("altitude"),
        "live_speed": live.get("speed_horizontal"),
    }


def main():
    trips = load_json(TRIPS_PATH, {"flights": []})
    now = datetime.now(timezone.utc)

    in_window = [f for f in trips.get("flights", []) if is_in_window(f, now)]
    print(f"{len(in_window)} flight(s) in tracking window.")

    if not API_KEY:
        print("No AVIATIONSTACK_KEY set — skipping live lookups, writing empty status.", file=sys.stderr)
        results = []
    elif not in_window:
        results = []
    else:
        results = []
        for f in in_window:
            print(f"  Checking {f['flight_number']}...")
            status = fetch_status(f)
            if status:
                results.append(status)

    live_data = {
        "_comment": "Auto-generated by scripts/flight_status.py. Do not edit by hand.",
        "updated_at": now.isoformat(),
        "flights": results,
    }
    with open(LIVE_PATH, "w") as f:
        json.dump(live_data, f, indent=2)
    print(f"Wrote {LIVE_PATH}")


if __name__ == "__main__":
    main()
