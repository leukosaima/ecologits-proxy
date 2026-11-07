# Copy this file to config.local.sh and adjust for your setup.
# config.local.sh is gitignored.

# Required: the OpenAI-compatible upstream the proxy forwards to.
export UPSTREAM_BASE_URL="https://your-llm-gateway.example.com"

# Optional: electricity mix zone (ISO 3166-1 alpha-3, or WOR for world average).
export ECOLOGITS_ZONE="WOR"

# Optional: used when a request model is not present in ecologits-mapping.json.
export ECOLOGITS_FALLBACK_PROVIDER="openai"
export ECOLOGITS_FALLBACK_MODEL="gpt-4o-mini"

# Optional: path to your model mapping file.
export ECOLOGITS_MAPPING_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ecologits-mapping.json"

# Optional: synthesize a terminal finish_reason: stop when the upstream ends the
# stream with [DONE] but never emitted one. Default is true; set to false to
# forward the upstream stream exactly as-is.
# export ECOLOGITS_SYNTHESIZE_FINISH_REASON="true"

# Optional: append every forwarded SSE line to this file for debugging.
# export ECOLOGITS_STREAM_LOG_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ecologits-stream.log"
