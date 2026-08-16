#!/usr/bin/env python3
"""
Pulls upcoming trips (flights + hotels) from your TripIt calendar feed
(an .ics URL from your TripIt account — no API approval needed) and writes
them into data/trips.json.

Get your feed URL:
    TripIt web app -> your name (top right) -> Profile -> Settings ->
    "Calendar Feed" (or https://www.tripit.com/feed/ical/private/...). Treat
    this URL as a secret — anyone with it can see your itinerary. See
    README.md "Getting your TripIt calendar feed URL" for the full walkthrough.

Set it as a GitHub repo secret named TRIPIT_ICAL_URL (never commit it).

Run manually:
    TRIPIT_ICAL_URL="https://www.tripit.com/feed/ical/..." python3 scripts/sync_tripit_calendar.py

Debug mode — dump every raw event TripIt sends without writing trips.json:
    TRIPIT_ICAL_URL="..." python3 scripts/sync_tripit_calendar.py --debug

Until TRIPIT_ICAL_URL is set, this script exits quietly (no-op) — the
dashboard keeps using whatever is in data/trips.json (edited by hand).

FORMAT NOTES (reverse-engineered from a real TripIt feed, Aug 2026):
TripIt's calendar feed has no published spec, so this was built by pulling
a live sample and inspecting it directly, not guessed from docs. As of that
sample:

  - Flights: SUMMARY is like "DL997 ATL to LGA" (flight number + route —
    parsed directly from here). DESCRIPTION has the human-readable detail,
    e.g.:
        8:45 PM EDT
        [Flight] LGA to MEM

        Delta Air Lines 5599, Terminal C, Gate B12

        10:50 PM CDT
        Arrive Memphis (MEM)
        Terminal A, Gate 4, 44m layover
    DTSTART/DTEND are UTC instants ("Z" suffix) — the *local* departure and
    arrival times/timezones are only spelled out as text in DESCRIPTION
    (e.g. "8:45 PM EDT" / "10:50 PM CDT" — note departure and arrival can be
    in different zones). This script converts the UTC instants to local
    wall-clock time using those zone abbreviations (see TZ_OFFSETS below).

  - Hotels: TripIt emits TWO separate events per stay — "Check-in: <hotel
    name>" and "Check-out: <hotel name>" — not one event spanning the whole
    stay. This script pairs them back together by matching hotel name.

  - Trip names (e.g. "Jim Ellis ATL Accounts", "Team B Meeting") show up as
    their own all-day header events, with the real "so-and-so is in
    <city> from ... to ..." text buried in the DESCRIPTION rather than the
    SUMMARY. This script uses them to fill in each flight/hotel's "purpose"
    field with the actual trip name instead of a generic placeholder.

  - Car rentals, restaurant reservations, and other segment types are left
    out of the dashboard entirely (it's flights + hotels only) but are
    still recognized so they aren't miscounted as unparseable.

If your feed's wording differs from this, run with --debug first — it
prints every event's SUMMARY/DESCRIPTION/times untouched so parsing can be
adjusted to match. Nothing is silently dropped: anything that can't be
cleanly parsed still comes through as raw_summary text on the dashboard.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, date, timezone, timedelta

try:
    from icalendar import Calendar
except ImportError:
    print("Missing dependency: pip install icalendar", file=sys.stderr)
    raise

def _locate(filename):
    """Find a data file whether the repo uses the data/ folder layout or a
    flattened one (a GitHub web-UI upload puts everything at the root)."""
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

# .strip() because pasting a URL into a GitHub secret box very easily picks up
# a trailing newline or space, which makes the request fail in a confusing way.
FEED_URL = (os.environ.get("TRIPIT_ICAL_URL") or "").strip()
DEBUG = "--debug" in sys.argv
# When a human clicked "Run workflow", a missing secret should fail loudly
# rather than exit 0 and look like a successful sync that did nothing.
MANUAL_RUN = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"

# How far back to keep trips that already happened (so a hotel checkout
# earlier today doesn't vanish from the dashboard mid-day).
PAST_CUTOFF = timedelta(hours=24)

# --- Push notifications (ntfy) ------------------------------------------
# Set NTFY_TOPIC as a GitHub secret. The topic name is the ONLY thing
# protecting these messages — on the public ntfy.sh server anyone who knows
# the topic can read it (and publish to it) — so it must never appear in
# this repo, which is public. Unset = notifications are simply skipped.
NTFY_TOPIC = (os.environ.get("NTFY_TOPIC") or "").strip()
NTFY_SERVER = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").strip().rstrip("/")
DASHBOARD_URL = "https://lcema36.github.io/travel-dashboard/"

# US timezone abbreviations TripIt uses in its DESCRIPTION text, mapped to
# UTC offset in hours. The abbreviation already encodes DST (EDT vs EST),
# so no separate DST calculation is needed. Add more here if your itinerary
# includes zones outside the continental US / Alaska / Hawaii.
TZ_OFFSETS = {
    "EST": -5, "EDT": -4,
    "CST": -6, "CDT": -5,
    "MST": -7, "MDT": -6,
    "PST": -8, "PDT": -7,
    "AKST": -9, "AKDT": -8,
    "HST": -10, "HDT": -9,
    "UTC": 0, "GMT": 0,
}

SUMMARY_FLIGHT_RE = re.compile(r"^([A-Z]{1,2}\d{2,5})\s+([A-Z]{3})\s+to\s+([A-Z]{3})\s*$")
# Usually its own line ("11:00 AM EDT"), but TripIt sometimes prefixes it
# with a weekday/date on the same line ("Mon, May 18 3:00 PM EDT") — the
# leading part is optional here to handle both.
TIME_TZ_RE = re.compile(r"^(?:[A-Za-z]{3},\s*[A-Za-z]+\s+\d{1,2}\s+)?(\d{1,2}:\d{2}\s*[AP]M)\s+([A-Z]{2,5})$")
AIRLINE_LINE_RE = re.compile(r"^(?P<airline>.+?)\s+\d+,\s*Terminal\s*(?P<term>[^,]*),\s*Gate\s*(?P<gate>.*)$")
ARRIVE_RE = re.compile(r"^Arrive\s+(?P<city>.+?)\s*\((?P<code>[A-Z]{3})\)\s*$")
TERMGATE_RE = re.compile(r"^Terminal\s*(?P<term>[^,]*),\s*Gate\s*(?P<gate>[^,]*)")
CHECKINOUT_RE = re.compile(r"^Check-(?:in|out):\s*(.+)$", re.IGNORECASE)
TRIP_HEADER_RE = re.compile(r"is in .+ from .+ to ", re.IGNORECASE)


def get_lines(text):
    return [l.strip() for l in (text or "").split("\n") if l.strip()]


def find_index(lines, prefix):
    for i, l in enumerate(lines):
        if l.startswith(prefix):
            return i
    return -1


def local_from_utc(dt_utc, tz_abbrev):
    """Convert a UTC datetime to the wall-clock local time TripIt's text
    says it should be, using the zone abbreviation from DESCRIPTION.
    Falls back to leaving it in UTC (still a correct instant, just not
    converted) if the abbreviation isn't recognized."""
    if not isinstance(dt_utc, datetime):
        return None
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    offset = TZ_OFFSETS.get((tz_abbrev or "").upper())
    if offset is None:
        return dt_utc
    return dt_utc.astimezone(timezone(timedelta(hours=offset)))


def to_iso(dt):
    return dt.isoformat() if dt else None


def aware(dt):
    """Normalize to a timezone-aware datetime. Mixing naive and aware
    datetimes raises TypeError as soon as you compare or sort them, which is
    an easy crash to hit on a real feed containing a floating-time event."""
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def parse_flight(uid, summary, description, dtstart, dtend):
    lines = get_lines(description)
    idx = find_index(lines, "[Flight]")

    flight_number = orig_code = dest_code = None
    m = SUMMARY_FLIGHT_RE.match(summary or "")
    if m:
        flight_number, orig_code, dest_code = m.groups()

    airline = dep_terminal = dep_gate = None
    arr_city = arr_terminal = arr_gate = None
    dep_local = aware(dtstart)
    arr_local = aware(dtend)

    if idx != -1:
        if idx > 0 and (m := TIME_TZ_RE.match(lines[idx - 1])):
            dep_local = local_from_utc(dtstart, m.group(2))
        if idx + 1 < len(lines) and (m := AIRLINE_LINE_RE.match(lines[idx + 1])):
            airline, dep_terminal, dep_gate = m.group("airline").strip(), m.group("term").strip(), m.group("gate").strip()
        if idx + 2 < len(lines) and (m := TIME_TZ_RE.match(lines[idx + 2])):
            arr_local = local_from_utc(dtend, m.group(2))
        if idx + 3 < len(lines) and (m := ARRIVE_RE.match(lines[idx + 3])):
            arr_city = m.group("city").strip()
            dest_code = dest_code or m.group("code")
        if idx + 4 < len(lines) and (m := TERMGATE_RE.match(lines[idx + 4])):
            arr_terminal, arr_gate = m.group("term").strip(), m.group("gate").strip()

    return {
        "id": uid,
        "airline": airline,
        "flight_number": flight_number,
        "origin": {
            "airport": orig_code,
            "city": None,
            "terminal": dep_terminal or None,
            "gate": dep_gate or None,
            "scheduled_departure": to_iso(dep_local),
        },
        "destination": {
            "airport": dest_code,
            "city": arr_city,
            "terminal": arr_terminal or None,
            "gate": arr_gate or None,
            "scheduled_arrival": to_iso(arr_local),
        },
        "confirmation": None,
        "purpose": None,  # filled in later from the matching trip-header event
        "raw_summary": summary,
        "_sort_key": to_iso(dep_local),
    }


def parse_lodging_event(uid, summary, description, dtstart):
    lines = get_lines(description)
    idx = find_index(lines, "[Lodging]")
    m = CHECKINOUT_RE.match(summary or "")
    kind = "checkin" if (m and m.group(0).lower().startswith("check-in")) else \
           ("checkout" if m else None)
    name = m.group(1).strip() if m else (lines[idx][7:].strip() if idx != -1 else summary)

    local_dt = aware(dtstart)
    address = phone = None
    if idx != -1:
        if idx > 0 and (tm := TIME_TZ_RE.match(lines[idx - 1])):
            local_dt = local_from_utc(dtstart, tm.group(2))
        if idx + 2 < len(lines) and "," in lines[idx + 2]:
            address = lines[idx + 2]
        if idx + 3 < len(lines) and re.match(r"^[\d()\-.\s]{7,}$", lines[idx + 3]):
            phone = lines[idx + 3]

    return {
        "uid": uid,
        "name": name,
        "kind": kind,
        "local_dt": local_dt,
        "address": address,
        "phone": phone,
        "raw_summary": summary,
        "raw_description": description or None,
    }


def merge_lodging(raw_events):
    by_name = {}
    for ev in raw_events:
        by_name.setdefault(ev["name"], []).append(ev)

    hotels = []
    for name, events in by_name.items():
        checkins = sorted((e for e in events if e["kind"] == "checkin"), key=lambda e: aware(e["local_dt"]) or datetime.min.replace(tzinfo=timezone.utc))
        checkouts = sorted((e for e in events if e["kind"] == "checkout"), key=lambda e: aware(e["local_dt"]) or datetime.min.replace(tzinfo=timezone.utc))
        # Best-effort pairing: zip sequential check-ins with the next
        # check-out at the same hotel. Doesn't handle multiple *overlapping*
        # stays at the same hotel name, but that's rare.
        for i in range(max(len(checkins), len(checkouts))):
            cin = checkins[i] if i < len(checkins) else None
            cout = checkouts[i] if i < len(checkouts) else None
            hotels.append({
                "id": (cin or cout)["uid"],
                "name": name,
                "city": None,
                "address": (cin or cout or {}).get("address"),
                "check_in": to_iso(cin["local_dt"]) if cin else None,
                "check_out": to_iso(cout["local_dt"]) if cout else None,
                # Used by the dashboard's "Call" button.
                "phone": (cin or cout or {}).get("phone"),
                "confirmation": None,
                "purpose": None,
                "raw_summary": (cin or cout)["raw_summary"],
                "raw_description": (cin or cout).get("raw_description"),
                "_sort_key": to_iso(cin["local_dt"]) if cin else to_iso(cout["local_dt"]),
            })
    return hotels


def parse_trip_header(description, dtstart, dtend):
    """Returns (name, start_date, end_date) for an all-day 'X is in Y from
    ... to ...' style event, or None if this doesn't look like one."""
    if not TRIP_HEADER_RE.search(description or ""):
        return None
    start = dtstart if isinstance(dtstart, (date, datetime)) else None
    end = dtend if isinstance(dtend, (date, datetime)) else start
    if start is None:
        return None
    if isinstance(start, datetime):
        start = start.date()
    if isinstance(end, datetime):
        end = end.date()
    return (start, end)


def assign_purpose(items, date_key, trip_windows, default="Cox Automotive"):
    for item in items:
        iso = item.get(date_key)
        if not iso:
            item["purpose"] = default
            continue
        try:
            d = datetime.fromisoformat(iso).date()
        except ValueError:
            item["purpose"] = default
            continue
        match = next((name for (start, end, name) in trip_windows if start <= d <= end), None)
        item["purpose"] = match or default
        item.pop("_sort_key", None)


def geocode(address, cache):
    """Look up lat/lon for a hotel address so the dashboard can draw a map
    preview. Uses Nominatim (free, no key). Results are cached by address in
    the previous trips.json, so a repeat sync doesn't re-query — Nominatim's
    usage policy asks for exactly that kind of restraint."""
    if not address:
        return None, None
    if address in cache:
        return cache[address]

    url = ("https://nominatim.openstreetmap.org/search?format=json&limit=1&q="
           + urllib.parse.quote(address))
    try:
        req = urllib.request.Request(url, headers={
            # Nominatim requires a identifying User-Agent.
            "User-Agent": "travel-dashboard/1.0 (github.com/LCEMA36/travel-dashboard)"
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            hits = json.loads(resp.read().decode())
        if hits:
            lat, lon = hits[0].get("lat"), hits[0].get("lon")
            cache[address] = (lat, lon)
            print(f"  geocoded {address[:45]!r} -> {lat},{lon}")
            time.sleep(1.1)   # Nominatim asks for <= 1 request/second
            return lat, lon
        print(f"  WARN: no geocode result for {address[:45]!r}", file=sys.stderr)
    except Exception as e:
        # A map is a nice-to-have — never fail the sync over it.
        print(f"  WARN: geocoding failed for {address[:45]!r}: {e}", file=sys.stderr)
    cache[address] = (None, None)
    return None, None


def _pretty(iso, with_date=True):
    """'2026-08-17T07:08:00-05:00' -> 'Mon Aug 17, 7:08 AM'."""
    if not iso:
        return "time TBD"
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return str(iso)
    t = dt.strftime("%-I:%M %p")
    return f"{dt.strftime('%a %b %-d')}, {t}" if with_date else t


def describe_flight(f):
    o, d = f.get("origin") or {}, f.get("destination") or {}
    line = (f"{f.get('flight_number') or 'Flight'}  "
            f"{o.get('airport') or '???'} to {d.get('airport') or '???'}")
    return f"{line}\n   {_pretty(o.get('scheduled_departure'))} - " \
           f"{_pretty(d.get('scheduled_arrival'), with_date=False)}"


def describe_hotel(h):
    nights = ""
    try:
        ci = datetime.fromisoformat(h["check_in"]).date()
        co = datetime.fromisoformat(h["check_out"]).date()
        n = (co - ci).days
        nights = f" ({n} night{'s' if n != 1 else ''})"
    except Exception:
        pass
    return (f"{h.get('name') or 'Hotel'}{nights}\n"
            f"   {_pretty(h.get('check_in'))} -> {_pretty(h.get('check_out'))}")


def _ntfy_send(title, message, tags):
    """Publish one notification. Never raises — a failed push must not
    fail the sync, or a flaky ntfy would stop trips syncing entirely."""
    payload = json.dumps({
        "topic": NTFY_TOPIC,
        "title": title,
        "message": message,
        "tags": tags,
        "click": DASHBOARD_URL,
        "priority": 4,
    }).encode("utf-8")
    req = urllib.request.Request(
        NTFY_SERVER, data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            print(f"Notification sent ({resp.status}): {title}")
        return True
    except Exception as e:
        print(f"WARN: couldn't send notification ({type(e).__name__}: {e}). "
              f"Sync itself is unaffected.", file=sys.stderr)
        return False


def notify_changes(old_trips, flights, hotels):
    """Compare what we just parsed against the previously committed
    trips.json and push a notification for anything genuinely new."""
    if not NTFY_TOPIC:
        print("NTFY_TOPIC not set — skipping notifications.")
        return

    old_flights = {f.get("id"): f for f in old_trips.get("flights", []) if f.get("id")}
    old_hotels = {h.get("id"): h for h in old_trips.get("hotels", []) if h.get("id")}

    # First ever run against an empty dashboard would announce the whole
    # itinerary at once. Stay quiet and start watching from here.
    if not old_flights and not old_hotels:
        print("No previous trips on record — skipping the first notification "
              "so we don't announce the entire existing itinerary.")
        return

    new_flights = [f for f in flights if f.get("id") not in old_flights]
    new_hotels = [h for h in hotels if h.get("id") not in old_hotels]

    # A schedule change matters as much as a brand-new booking.
    moved = []
    for f in flights:
        prev = old_flights.get(f.get("id"))
        if not prev:
            continue
        was = (prev.get("origin") or {}).get("scheduled_departure")
        now_ = (f.get("origin") or {}).get("scheduled_departure")
        if was and now_ and was != now_:
            moved.append((f, was))

    if not (new_flights or new_hotels or moved):
        print("Nothing new to notify about.")
        return

    # Trip name, when every new item shares one, makes a much better title.
    purposes = {i.get("purpose") for i in new_flights + new_hotels if i.get("purpose")}
    if new_flights or new_hotels:
        if len(purposes) == 1:
            title = f"Beau: trip added - {purposes.pop()}"
        else:
            title = "Beau: new travel booked"
    else:
        title = "Beau: flight time changed"

    lines = []
    if new_flights:
        lines.append("FLIGHTS")
        lines += [describe_flight(f) for f in new_flights]
    if new_hotels:
        if lines:
            lines.append("")
        lines.append("HOTELS")
        lines += [describe_hotel(h) for h in new_hotels]
    for f, was in moved:
        if lines:
            lines.append("")
        lines.append(f"CHANGED: {f.get('flight_number') or 'Flight'} now departs "
                     f"{_pretty((f.get('origin') or {}).get('scheduled_departure'))} "
                     f"(was {_pretty(was)})")

    _ntfy_send(title, "\n".join(lines), ["airplane"])


def main():
    if not FEED_URL:
        msg = ("TRIPIT_ICAL_URL is not set. Add it under "
               "Settings -> Secrets and variables -> Actions.")
        if MANUAL_RUN:
            # Someone clicked "Run workflow" expecting a sync — don't let this
            # pass as a green check.
            print(f"ERROR: {msg}", file=sys.stderr)
            sys.exit(1)
        print(f"{msg} Skipping scheduled sync (no-op).", file=sys.stderr)
        return

    # file:// is allowed so a saved .ics can be tested locally.
    if not FEED_URL.startswith(("http://", "https://", "webcal://", "file://")):
        print(f"ERROR: TRIPIT_ICAL_URL doesn't look like a URL "
              f"(starts with {FEED_URL[:12]!r}). It should look like "
              f"https://www.tripit.com/feed/ical/private/<id>/tripit.ics",
              file=sys.stderr)
        sys.exit(1)

    # webcal:// is what some calendar apps hand you — same thing over https.
    url = FEED_URL.replace("webcal://", "https://", 1)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "travel-dashboard/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        print(f"ERROR: TripIt returned HTTP {e.code} for the feed URL.", file=sys.stderr)
        if e.code in (401, 403, 404):
            print("       That usually means the URL is wrong or the feed was "
                  "reset. Get a fresh one from TripIt -> Profile -> Settings -> "
                  "Calendar Feed and update the TRIPIT_ICAL_URL secret.", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"ERROR: couldn't reach TripIt: {e.reason}", file=sys.stderr)
        sys.exit(1)

    if not raw.lstrip().startswith(b"BEGIN:VCALENDAR"):
        preview = raw[:120].decode("utf-8", "replace")
        print("ERROR: that URL didn't return a calendar file. First bytes:",
              file=sys.stderr)
        print(f"       {preview!r}", file=sys.stderr)
        print("       Make sure the secret holds the .ics *feed* URL, not the "
              "TripIt website URL.", file=sys.stderr)
        sys.exit(1)

    cal = Calendar.from_ical(raw)
    now = datetime.now(timezone.utc)
    cutoff = now - PAST_CUTOFF

    flights, lodging_raw, trip_windows = [], [], []
    skipped_other = 0

    events = list(cal.walk("VEVENT"))
    print(f"Feed contains {len(events)} event(s).")
    bad_events = 0

    for component in events:
        # One malformed event shouldn't take down the whole sync — note it
        # and keep going, so a single odd trip can't hide all the others.
        try:
            summary = str(component.get("SUMMARY", "")).strip()
            description = str(component.get("DESCRIPTION", "")).strip()
            uid = str(component.get("UID", "")) or f"tripit-{abs(hash(summary + str(component.get('DTSTART'))))}"
            dtstart = component.get("DTSTART").dt if component.get("DTSTART") else None
            dtend = component.get("DTEND").dt if component.get("DTEND") else dtstart

            if DEBUG:
                print(f"--- UID: {uid}\nSUMMARY: {summary}\nDESCRIPTION: {description}\nSTART: {dtstart}  END: {dtend}\n")
                continue

            if "[Flight]" in description:
                flights.append(parse_flight(uid, summary, description, dtstart, dtend))
            elif "[Lodging]" in description:
                lodging_raw.append(parse_lodging_event(uid, summary, description, dtstart))
            elif (hdr := parse_trip_header(description, dtstart, dtend)):
                start, end = hdr
                trip_windows.append((start, end, summary))
            else:
                skipped_other += 1  # car rentals, restaurants, activities, etc. — not shown on the dashboard
        except Exception as e:
            bad_events += 1
            print(f"  WARN: skipped an event that failed to parse "
                  f"({type(e).__name__}: {e}) — SUMMARY was "
                  f"{str(component.get('SUMMARY', ''))[:60]!r}", file=sys.stderr)

    if DEBUG:
        print("Debug mode — nothing written to trips.json.")
        return

    hotels = merge_lodging(lodging_raw)

    # Drop anything fully in the past.
    def still_relevant(iso):
        if not iso:
            return True
        try:
            dt = aware(datetime.fromisoformat(iso))
        except ValueError:
            return True
        return dt is None or dt >= cutoff

    flights = [f for f in flights if still_relevant(f["destination"]["scheduled_arrival"])]
    hotels = [h for h in hotels if still_relevant(h["check_out"])]

    assign_purpose(flights, "_sort_key", trip_windows)   # uses each flight's departure date, then removes _sort_key
    assign_purpose(hotels, "_sort_key", trip_windows)     # uses each hotel's check-in date, then removes _sort_key

    flights.sort(key=lambda f: f["origin"]["scheduled_departure"] or "")
    hotels.sort(key=lambda h: h["check_in"] or h["check_out"] or "")

    print(f"Parsed {len(flights)} flight(s), {len(hotels)} hotel stay(s) from "
          f"{len(lodging_raw)} check-in/out event(s); {len(trip_windows)} named "
          f"trip(s); {skipped_other} other event(s) ignored (car rentals, "
          f"activities, etc.).")

    with open(TRIPS_PATH, "r") as f:
        trips = json.load(f)

    # Reuse coordinates we already looked up, keyed by address.
    geo_cache = {}
    for old in trips.get("hotels", []):
        if old.get("address") and old.get("lat") and old.get("lon"):
            geo_cache[old["address"]] = (old["lat"], old["lon"])

    # Notify BEFORE overwriting trips.json — `trips` still holds the
    # previously committed state, which is what "new" is measured against.
    notify_changes(trips, flights, hotels)

    for h in hotels:
        h["lat"], h["lon"] = geocode(h.get("address"), geo_cache)

    trips["flights"] = flights
    trips["hotels"] = hotels

    with open(TRIPS_PATH, "w") as f:
        json.dump(trips, f, indent=2, default=str)
    print(f"Wrote {TRIPS_PATH}")


if __name__ == "__main__":
    main()
