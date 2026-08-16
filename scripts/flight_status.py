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

API_KEY = (os.environ.get("AVIATIONSTACK_KEY") or "").strip()
MANUAL_RUN = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
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


def lookup_aircraft(aircraft):
    """AviationStack's free tier returns an `aircraft` object where the type
    and registration are usually null, but the icao24 transponder address is
    populated. hexdb.io resolves that hex code to the actual airframe (free,
    no key). Returns {} on any failure — aircraft info is decorative and must
    never break the status update."""
    if aircraft.get("iata") or aircraft.get("registration"):
        # If AviationStack ever does return real values, prefer them.
        return {
            "type": aircraft.get("iata"),
            "manufacturer": None,
            "registration": aircraft.get("registration"),
        }

    hexcode = (aircraft.get("icao24") or "").strip().upper()
    if not hexcode:
        return {}
    try:
        req = urllib.request.Request(
            f"https://hexdb.io/api/v1/aircraft/{urllib.parse.quote(hexcode)}",
            headers={"User-Agent": "travel-dashboard/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            d = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  WARN: aircraft lookup failed for {hexcode}: {e}", file=sys.stderr)
        return {}

    # Prefer the short ICAO type code (A321) over the verbose one (A321 211SL).
    return {
        "type": d.get("ICAOTypeCode") or d.get("Type"),
        "manufacturer": d.get("Manufacturer"),
        "registration": d.get("Registration"),
    }


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
    aircraft = match.get("aircraft") or {}
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

    ac = lookup_aircraft(aircraft)

    return {
        "id": flight["id"],
        "status": status,
        "aircraft_type": ac.get("type"),
        "aircraft_manufacturer": ac.get("manufacturer"),
        "aircraft_registration": ac.get("registration"),
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
        msg = ("AVIATIONSTACK_KEY is not set. Add it under Settings -> "
               "Secrets and variables -> Actions.")
        if MANUAL_RUN:
            print(f"ERROR: {msg}", file=sys.stderr)
            sys.exit(1)
        print(f"{msg} Skipping live lookups.", file=sys.stderr)
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

    # Aircraft assignment is only knowable once a flight enters the tracking
    # window, but it shouldn't disappear again afterwards. Keep it in its own
    # map, separate from the ephemeral live status, and carry forward anything
    # learned on an earlier run.
    previous = load_json(LIVE_PATH, {})
    aircraft_map = dict(previous.get("aircraft") or {})
    for r in results:
        if r.get("aircraft_type") or r.get("aircraft_registration"):
            aircraft_map[r["id"]] = {
                "type": r.get("aircraft_type"),
                "manufacturer": r.get("aircraft_manufacturer"),
                "registration": r.get("aircraft_registration"),
            }
    # Drop entries for trips that are no longer in trips.json at all.
    known_ids = {f.get("id") for f in trips.get("flights", [])}
    aircraft_map = {k: v for k, v in aircraft_map.items() if k in known_ids}

    live_data = {
        "_comment": "Auto-generated by scripts/flight_status.py. Do not edit by hand.",
        "updated_at": now.isoformat(),
        "flights": results,
        "aircraft": aircraft_map,
    }
    with open(LIVE_PATH, "w") as f:
        json.dump(live_data, f, indent=2)
    print(f"Wrote {LIVE_PATH} ({len(results)} live, {len(aircraft_map)} aircraft known)")


if __name__ == "__main__":
    main()
