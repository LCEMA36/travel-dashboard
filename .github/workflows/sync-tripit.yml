name: Sync trips from TripIt calendar feed

on:
  schedule:
    # Every 3 hours. TripIt's own feed refresh is 15min-24h depending on
    # client, so this is a reasonable outer bound.
    - cron: "0 */3 * * *"
  workflow_dispatch: {}

permissions:
  contents: write

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install icalendar

      - name: Sync trips from TripIt
        env:
          TRIPIT_ICAL_URL: ${{ secrets.TRIPIT_ICAL_URL }}
        run: |
          # Works whether the repo uses scripts/ or has everything at the root.
          SCRIPT=$(ls scripts/sync_tripit_calendar.py sync_tripit_calendar.py 2>/dev/null | head -1)
          if [ -z "$SCRIPT" ]; then echo "sync_tripit_calendar.py not found"; exit 1; fi
          echo "Running $SCRIPT"
          python3 "$SCRIPT"

      - name: Commit if changed
        run: |
          git config user.name "travel-dashboard-bot"
          git config user.email "actions@users.noreply.github.com"
          # Stage each candidate path separately. Passing both to one
          # `git add` makes git abort with "pathspec did not match any files"
          # when one of them doesn't exist — staging NOTHING and silently
          # discarding the sync.
          for f in data/trips.json trips.json; do
            if [ -f "$f" ]; then git add -- "$f"; fi
          done
          if git diff --cached --quiet; then
            echo "No change in trips."
          else
            echo "Changes staged:"
            git diff --cached --stat
            git commit -m "Sync trips from TripIt"
            git push
          fi
