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

USER="${1:-}"
REPO="${2:-travel-dashboard}"

if [ -z "$USER" ]; then
  echo "Usage: ./push-to-github.sh <your-github-username> [repo-name]" >&2
  exit 1
fi

# Safety: refuse to push if .env somehow got tracked.
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "ERROR: .env is tracked by git — refusing to push so your API key stays private." >&2
  echo "Run: git rm --cached .env" >&2
  exit 1
fi

if command -v gh >/dev/null 2>&1; then
  echo "Creating repo with the GitHub CLI..."
  gh repo create "$REPO" --public --source=. --remote=origin --push
else
  echo "GitHub CLI not found — create the repo first at:"
  echo "   https://github.com/new   (name it: $REPO, Public, no README/gitignore/license)"
  echo
  read -r -p "Press Enter once the empty repo exists..."
  git remote remove origin 2>/dev/null || true
  git remote add origin "https://github.com/$USER/$REPO.git"
  git branch -M main
  git push -u origin main
fi

echo
echo "Pushed. Next steps:"
echo "  1. Settings -> Pages -> Source: 'GitHub Actions'"
echo "  2. Settings -> Secrets and variables -> Actions -> New repository secret:"
echo "       AVIATIONSTACK_KEY = (your key, it's in your local .env)"
echo "       TRIPIT_ICAL_URL   = (your TripIt calendar feed URL)"
echo "  3. Actions tab -> run 'Sync trips from TripIt calendar feed' -> Run workflow"
echo
echo "Your dashboard will be live at: https://$USER.github.io/$REPO/"
