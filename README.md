# Beau's Travel Dashboard

A simple, free, static travel dashboard for family to see upcoming flights,
hotels, and live flight status — hosted on GitHub Pages.

## How it works

- `index.html` — the dashboard itself. Reads two JSON files and renders cards.
- `data/trips.json` — the source of truth for flights & hotels. Edit this
  directly, or let it be synced automatically from your TripIt calendar feed
  once that's set up (see below).
- `data/live-status.json` — auto-generated live flight status (gate, delay,
  estimated times). Rewritten every 30 minutes by a scheduled GitHub Action.
- `.github/workflows/` — the automation:
  - `flight-status.yml` — every 30 min, checks any flight in `trips.json`
    that's within ~12 hours of departure or ~6 hours past arrival, pulls
    live status from AviationStack, commits the update.
  - `sync-tripit.yml` — every 3 hours, pulls upcoming trips from your TripIt
    calendar feed into `trips.json` (currently a no-op until your feed URL
    is set — see below).
  - `pages.yml` — deploys the site to GitHub Pages on every push to `main`.

## Local testing before you've pushed to GitHub

There's a `.env` file (already filled in with your AviationStack key if you
gave me one) for testing scripts locally, e.g.:

```bash
set -a; source .env; set +a
python3 scripts/flight_status.py
```

`.env` is listed in `.gitignore` — running `git add .` / `git commit` will
never pick it up, so the real key stays off GitHub entirely. Once you push
the repo, add the same key as a GitHub *secret* (step 4 below) — that's the
only place it needs to live for the scheduled Actions to use it. If you
ever want to hand this repo to someone else, delete your local `.env` first.

## 1. Get this onto GitHub

```bash
cd travel-dashboard
git init
git add .
git commit -m "Initial travel dashboard"
gh repo create travel-dashboard --public --source=. --push
# or create the repo on github.com and: git remote add origin <url>; git push -u origin main
```

## 2. Turn on GitHub Pages

Repo → **Settings → Pages** → under "Build and deployment," set **Source** to
**GitHub Actions**. Push to `main` and the `pages.yml` workflow will deploy
automatically. Your dashboard will be live at
`https://<your-username>.github.io/travel-dashboard/`.

Since the repo is public (per your call — keeps this simple and free), the
dashboard URL is technically reachable by anyone who has the link. Nothing
in it is placed there automatically beyond flight numbers, hotel names, and
dates — no passport numbers, loyalty numbers, or payment info are stored in
`trips.json`, so keep it that way when you add real trips.

## 3. Adding trips (works today, no setup needed)

Until TripIt sync is live, add trips by editing `data/trips.json` right in
GitHub's web UI (or locally + `git push`). Copy the example flight/hotel
blocks already in the file, fill in the real details, and delete the
`"notes": "Example row..."` ones. Any family member with write access to the
repo can add a trip this way in under a minute.

## 4. Live flight tracking (AviationStack)

1. Sign up for a free AviationStack account: https://aviationstack.com/
   (100 requests/month free — plenty for occasional personal travel).
2. Copy your API access key.
3. In the repo: **Settings → Secrets and variables → Actions → New repository
   secret**, name it `AVIATIONSTACK_KEY`, paste the key.
4. That's it — `flight-status.yml` picks it up automatically on its next run.
   You can also trigger it manually from the **Actions** tab
   ("Update live flight status" → **Run workflow**) to test it.

The `flight_number` field in `trips.json` must be the IATA code (e.g.
`DL1234`) for AviationStack to find it — this is filled in automatically by
the TripIt sync once that's connected (step 5), or type it in by hand for
manual entries.

## 5. Getting your TripIt calendar feed URL (for automatic trip sync)

Since Concur API access isn't an option, this uses TripIt's calendar feed
instead — it's a personal-account feature (no company/IT approval needed),
just a private .ics URL you subscribe to. TripIt builds your itinerary the
usual way: forward confirmation emails to `plans@tripit.com`, or connect
your inbox in the TripIt app so it does that automatically.

1. Log into [tripit.com](https://www.tripit.com) → click your name (top
   right) → **Profile** → **Settings** → **Calendar Feed**. TripIt will show
   you a private feed URL (looks like
   `https://www.tripit.com/feed/ical/private/<long-id>/tripit.ics`).
2. Copy that URL. **Treat it like a password** — anyone who has it can see
   your itinerary. Don't post it anywhere public or commit it to the repo.
3. In the repo: **Settings → Secrets and variables → Actions → New repository
   secret**, name it `TRIPIT_ICAL_URL`, paste the URL.
4. `sync-tripit.yml` will pick it up on its next scheduled run (every 3
   hours), or trigger it manually from the **Actions** tab ("Sync trips from
   TripIt calendar feed" → **Run workflow**) to test it right away.
5. If your feed URL ever leaks or you want to invalidate it, TripIt has a
   "reset calendar feed" option on that same Settings page — just update the
   `TRIPIT_ICAL_URL` secret afterward.

**One honest caveat:** TripIt doesn't publish a spec for its calendar feed's
event format, so `scripts/sync_tripit_calendar.py` was built and tested
against a real sample pulled from an actual TripIt feed (flights, hotel
check-in/check-out pairs, named trips, car rentals) rather than guessed —
it correctly separates flights from hotels, converts each leg's UTC
timestamp to the right local time using the timezone TripIt prints in the
event text, pairs "Check-in:"/"Check-out:" events for the same hotel back
into one stay, and even picks up your named trips (e.g. "Jim Ellis ATL
Accounts") to use as each flight/hotel's "purpose." That said, it was
validated against one real feed, not every possible TripIt account
configuration (e.g. international trips, rail segments, or unusual event
wording could differ). To check yours:

```bash
TRIPIT_ICAL_URL="<your feed URL>" python3 scripts/sync_tripit_calendar.py --debug
```

This prints every raw event TripIt sends (summary, description, times)
without touching `trips.json` — worth a first run once you have a real
feed URL, so we can see exactly how your events are worded and tune
the parsing if anything comes through as `raw_summary` fallback text
instead of a clean flight number / airport code. Nothing is ever silently
dropped — if a field can't be parsed, the dashboard just falls back to
showing the event's raw title instead of a blank.

Until the feed URL is set, `sync_tripit_calendar.py` runs as a harmless
no-op — manual entry in `trips.json` is the dashboard's real data source in
the meantime, and remains a fine permanent fallback for any trip you'd
rather enter yourself.

## Customizing

- Colors, badges, and layout live entirely in `index.html` (inline
  `<style>`/`<script>`, no build step).
- To change how far in advance a flight starts showing live tracking, edit
  `WINDOW_BEFORE` / `WINDOW_AFTER` in `scripts/flight_status.py`.
- To change how often live status refreshes, edit the `cron` line in
  `.github/workflows/flight-status.yml` (minimum practical interval is
  about 5 minutes; every 30 min keeps AviationStack's free 100/month quota
  from running out mid-trip).
- To change how often TripIt syncs, edit the `cron` line in
  `.github/workflows/sync-tripit.yml`.
