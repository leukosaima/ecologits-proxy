# Copy this file to config.local.ps1 and adjust for your setup.
# config.local.ps1 is gitignored.

# Required: the OpenAI-compatible upstream the proxy forwards to.
$env:UPSTREAM_BASE_URL = "https://your-llm-gateway.example.com"

# Optional: electricity mix zone (ISO 3166-1 alpha-3, or WOR for world average).
$env:ECOLOGITS_ZONE = "WOR"

# Optional: used when a request model is not present in ecologits-mapping.json.
$env:ECOLOGITS_FALLBACK_PROVIDER = "openai"
$env:ECOLOGITS_FALLBACK_MODEL = "gpt-4o-mini"

# Optional: path to your model mapping file.
$env:ECOLOGITS_MAPPING_PATH = "$PSScriptRoot\ecologits-mapping.json"

# Optional: synthesize a terminal finish_reason: stop when the upstream ends the
# stream with [DONE] but never emitted one. Default is true; set to false to
# forward the upstream stream exactly as-is.
# $env:ECOLOGITS_SYNTHESIZE_FINISH_REASON = "true"

# Optional: append every forwarded SSE line to this file for debugging.
# $env:ECOLOGITS_STREAM_LOG_PATH = "$PSScriptRoot\ecologits-stream.log"
