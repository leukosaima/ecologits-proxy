#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python="$repo/.venv/bin/python"
test_last="$HOME/.local/share/opencode/ecologits-last.json"

if [ ! -x "$python" ]; then
  echo "Virtual environment not found. Run ./start-proxy.sh first to create it."
  exit 1
fi

rm -f "$test_last"

echo "Starting mock upstream on :9999 ..."
"$python" -m uvicorn mock_upstream:app --app-dir "$repo" --host 127.0.0.1 --port 9999 \
  >"$repo/mock-upstream.log" 2>&1 &
mock_pid=$!

echo "Starting EcoLogits proxy on :8787 ..."
UPSTREAM_BASE_URL="http://127.0.0.1:9999" \
ECOLOGITS_ZONE="WOR" \
ECOLOGITS_FALLBACK_PROVIDER="openai" \
ECOLOGITS_FALLBACK_MODEL="gpt-4o-mini" \
"$python" -m uvicorn proxy:app --app-dir "$repo" --host 127.0.0.1 --port 8787 \
  >"$repo/proxy.log" 2>&1 &
proxy_pid=$!

cleanup() {
  echo
  echo "Stopping test servers..."
  kill "$mock_pid" "$proxy_pid" 2>/dev/null || true
}
trap cleanup EXIT

echo "Waiting for servers to start..."
sleep 4

echo
echo ">>> Testing /v1/models"
curl -s http://127.0.0.1:8787/v1/models | "$python" -m json.tool

echo
echo ">>> Testing non-streaming chat completion"
curl -s -X POST http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"glm-5.3-flash","messages":[{"role":"user","content":"hello"}]}' | "$python" -m json.tool

sleep 2

echo
echo ">>> Testing streaming chat completion"
curl -s -N -X POST http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"openai/gpt-4o-mini","messages":[{"role":"user","content":"hello"}],"stream":true}'

sleep 2

echo
echo ">>> Testing streaming chat completion without upstream finish_reason"
curl -s -N -X POST http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-test-no-finish: 1" \
  -d '{"model":"openai/gpt-4o-mini","messages":[{"role":"user","content":"hello"}],"stream":true}'

sleep 2

echo
echo ">>> Last EcoLogits estimate"
if [ -f "$test_last" ]; then
  "$python" -m json.tool "$test_last"
else
  echo "No estimate file written yet."
fi
