#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$ROOT_DIR"

# Fail if no changes
if git diff --quiet && git diff --cached --quiet; then
  echo "ERROR: No changes to commit. Edit the code first."
  exit 1
fi

# Commit message from argument or default
MESSAGE="${1:-app: update}"

git add -A
git commit -m "$MESSAGE"
git push origin main

echo ""
echo "Deploying SHA: $(git rev-parse --short HEAD)"
"$SCRIPT_DIR/deploy.sh"
