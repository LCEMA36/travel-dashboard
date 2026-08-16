#!/usr/bin/env bash
# Push this dashboard to GitHub, replacing the flattened web-UI upload
# with the proper folder structure.
#
#   ./push-to-github.sh
#
# The git history is already committed here and the remote is already set to
# https://github.com/LCEMA36/travel-dashboard.git — this just pushes.
#
# Your .env (with the AviationStack key) is git-ignored and will NOT upload.

set -euo pipefail

USER="${1:-LCEMA36}"
REPO="${2:-travel-dashboard}"
REMOTE="https://github.com/$USER/$REPO.git"

cd "$(dirname "$0")"

# Safety: refuse to push if .env somehow got tracked.
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "ERROR: .env is tracked by git — refusing to push so your API key stays private." >&2
  echo "Fix with: git rm --cached .env" >&2
  exit 1
fi

git remote remove origin 2>/dev/null || true
git remote add origin "$REMOTE"
git branch -M main

echo "Pushing to $REMOTE ..."
echo

if git push -u origin main 2>/dev/null; then
  echo "Pushed."
else
  cat <<'EOF'

The normal push was rejected. That's expected here: the repo already has an
"Add files via upload" commit whose history is unrelated to this one, so git
won't fast-forward.

That upload is the flattened copy we're replacing, so overwriting it is the
intent. To do that:

    git push -u origin main --force

EOF
  read -r -p "Replace the uploaded files with this proper structure? [y/N] " ans
  case "$ans" in
    [yY]*)
      git push -u origin main --force
      echo "Pushed (forced)."
      ;;
    *)
      echo "Left the remote untouched. Nothing was changed on GitHub."
      exit 0
      ;;
  esac
fi

cat <<EOF

Next steps on github.com/$USER/$REPO:

  1. Settings -> Pages
       Source: "GitHub Actions"      (pages.yml is now in the repo)
       ...or "Deploy from a branch" -> main -> / (root). Either works.

  2. Settings -> Secrets and variables -> Actions -> New repository secret
       AVIATIONSTACK_KEY  = (it's in your local .env in this folder)
       TRIPIT_ICAL_URL    = (your TripIt calendar feed URL)

  3. Actions tab -> "Sync trips from TripIt calendar feed" -> Run workflow

Your dashboard: https://$(echo "$USER" | tr '[:upper:]' '[:lower:]').github.io/$REPO/
EOF
