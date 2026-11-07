#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venv="$repo/.venv"

# Load local, untracked configuration if present.
if [ -f "$repo/config.local.sh" ]; then
  # shellcheck disable=SC1091
  source "$repo/config.local.sh"
fi

if [ -z "${UPSTREAM_BASE_URL:-}" ]; then
  echo "UPSTREAM_BASE_URL is not set. Copy config.example.sh to config.local.sh and configure it." >&2
  exit 1
fi

if [ ! -d "$venv" ]; then
  echo "Creating Python virtual environment..."
  python3 -m venv "$venv"
fi

echo "Installing / upgrading dependencies..."
"$venv/bin/python" -m pip install -q -r "$repo/requirements.txt"

echo "Starting EcoLogits proxy on http://127.0.0.1:8787 (upstream: $UPSTREAM_BASE_URL)"
exec "$venv/bin/python" -m uvicorn proxy:app --app-dir "$repo" --host 127.0.0.1 --port 8787
