#!/usr/bin/env bash
# One-command push of this dashboard to a new GitHub repo.
#
#   ./push-to-github.sh <your-github-username> [repo-name]
#
# Example:
#   ./push-to-github.sh cbeaumoore travel-dashboard
#
# The git history is already committed in this folder, so this only sets the
# remote and pushes. Your .env (with the AviationStack key) is git-ignored
# and will NOT be uploaded.

set -euo pipefail

USER="${1:-LCEMA36}"
REPO="${2:-travel-dashboard}"

# Safety: refuse to push if .env somehow got tracked.
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "ERROR: .env is tracked by git — refusing to push so your API key stays private." >&2
  echo "Run: git rm --cached .env" >&2
  exit 1
fi

# The remote is already set to https://github.com/LCEMA36/travel-dashboard.git
# and both commits are made — this just points it at the right place and pushes.
git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/$USER/$REPO.git"
git branch -M main

echo "Pushing to https://github.com/$USER/$REPO.git ..."
git push -u origin main

echo
echo "Pushed. Next steps:"
echo "  1. Settings -> Pages -> Source: 'GitHub Actions'"
echo "  2. Settings -> Secrets and variables -> Actions -> New repository secret:"
echo "       AVIATIONSTACK_KEY = (your key, it's in your local .env)"
echo "       TRIPIT_ICAL_URL   = (your TripIt calendar feed URL)"
echo "  3. Actions tab -> run 'Sync trips from TripIt calendar feed' -> Run workflow"
echo
echo "Your dashboard will be live at: https://$USER.github.io/$REPO/"
