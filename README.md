# EcoLogits Proxy

A tiny local OpenAI-compatible proxy that estimates the environmental impact of
LLM calls with the public [EcoLogits API](https://api.ecologits.ai/v1beta/estimations).

It sits between an OpenAI-compatible client (for example OpenCode) and any
OpenAI-compatible upstream, and records energy, CO₂-equivalent, water, and other
impacts for each chat completion.

## Why a proxy?

Most clients do not expose a hook for "after the LLM responds" in the provider
path, and the EcoLogits API needs two things clients usually do not surface to
plugins:

1. the **output token count** of the generation, and
2. the **request latency**.

A local proxy can see both in the upstream response, so it is the smallest place
to add EcoLogits without forking the client.

## What it does

- Forwards `/v1/models` and `/v1/chat/completions` to your upstream.
- After each chat completion, calls `POST https://api.ecologits.ai/v1beta/estimations`.
- Writes estimates to:
  - `~/.local/share/opencode/ecologits.jsonl` (rolling log)
  - `~/.local/share/opencode/ecologits-last.json` (last result only)
- The included OpenCode TUI plugin shows the last estimate as a toast when
  OpenCode emits `session.execution.succeeded`.
- The toast converts the abstract numbers into friendly units:
  micrograms/milligrams of CO₂, milliWatt-hours, and "≈ driving a car X km"
  (using ~200 g CO₂/km as a rough global average passenger-car emission factor).
- Handles upstream SSE streams that close after `data: [DONE]` without a clean
  chunked-transfer terminator.
- Normalizes SSE line endings to LF, which avoids parser issues with mixed
  LF/CRLF keepalive frames from some gateways.
- Synthesizes a terminal `finish_reason: "stop"` chunk when an upstream ends the
  stream with `data: [DONE]` but never emitted a non-null finish reason. This
  prevents strict OpenAI-compatible clients from entering a retry loop.

## Important caveat about model mapping

The EcoLogits API only knows about a fixed catalog of providers/models
(`openai`, `anthropic`, `mistralai`, `google_genai`, `cohere`, `huggingface_hub`).
If your upstream uses other model ids, the proxy maps them to the closest
supported equivalent in `ecologits-mapping.json`.

The estimate is therefore a rough order-of-magnitude number for unknown models,
not a precise measurement. If you use a supported provider/model directly, the
estimate becomes exact within EcoLogits' own methodology.

## Configure

Copy the example config for your platform and edit it:

**Windows:**

```powershell
Copy-Item config.example.ps1 config.local.ps1
Copy-Item ecologits-mapping.example.json ecologits-mapping.json
```

**macOS / Linux:**

```bash
cp config.example.sh config.local.sh
cp ecologits-mapping.example.json ecologits-mapping.json
```

Then set at least:

- `UPSTREAM_BASE_URL` — your OpenAI-compatible upstream, e.g. `https://api.openai.com`
  or `https://your-gateway.example.com/v1`.

Optional values:

- `ECOLOGITS_ZONE` — electricity mix zone (default `WOR`).
- `ECOLOGITS_FALLBACK_PROVIDER` / `ECOLOGITS_FALLBACK_MODEL` — used when a model
  is not in `ecologits-mapping.json`.
- `ECOLOGITS_MAPPING_PATH` — path to your mapping file.
- `ECOLOGITS_SYNTHESIZE_FINISH_REASON` — when `true` (default), synthesize a
  terminal `finish_reason: "stop"` for streaming responses that reach
  `data: [DONE]` without a non-null finish reason. Set to `false` if you want
  the proxy to forward exactly what the upstream sends.
- `ECOLOGITS_STREAM_LOG_PATH` — if set, every SSE line forwarded to the client
  is appended to this file. Useful for diagnosing "stream ended without
  finish_reason" errors.

`config.local.*` and `ecologits-mapping.json` are gitignored, so your local
endpoints and model mappings are not committed.

### Output paths

By default, estimates are written to `~/.local/share/opencode/`. To keep them in
the project directory instead, create a `.env` file (also gitignored):

```dotenv
ECOLOGITS_LOG_PATH=/path/to/ecologits-proxy/ecologits.jsonl
ECOLOGITS_LAST_PATH=/path/to/ecologits-proxy/ecologits-last.json
```

The proxy loads `.env` automatically via `python-dotenv`. The TUI plugin fetches
the latest estimate from `http://127.0.0.1:8787/ecologits/last`, so no plugin
changes are needed when you move the output files.

## Run it

**Windows (PowerShell):**

```powershell
.\start-proxy.ps1
```

**macOS / Linux (bash):**

```bash
chmod +x start-proxy.sh test-proxy.sh   # once
./start-proxy.sh
```

The proxy listens on `http://127.0.0.1:8787/v1` and logs each chat request and
upstream status code to the console.

## Configure OpenCode

Add a custom provider in `~/.config/opencode/opencode.jsonc` (V2 format):

```jsonc
{
  "providers": {
    "ecologits-local": {
      "name": "EcoLogits Local Proxy",
      "package": "@opencode/ai/providers/openai-compatible",
      "settings": {
        "baseURL": "http://127.0.0.1:8787/v1",
        "apiKey": "not-used" // replace if your upstream needs a real key
      },
      "models": {
        "your-model-id": { "name": "Your Model" }
      }
    }
  }
}
```

The toast plugin is TUI-only, so also add it to `~/.config/opencode/cli.json`:

```json
{
  "plugins": ["./plugins/ecologits-toast"]
}
```

The plugin itself lives at:

```
~/.config/opencode/plugins/ecologits-toast/tui.js
```

### Use it

1. Start the proxy.
2. Restart OpenCode.
3. In the TUI, run `/models`.
4. Pick a model under **EcoLogits Local Proxy**.
5. Chat normally.

After the assistant finishes, OpenCode emits `session.execution.succeeded` and
the plugin shows a toast like:

```
177–221 µg CO₂ · 0.38–0.48 mWh
≈ driving a car 0.9 mm–1.1 mm (session total ≈ 1.0 mm)
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Provider not in `/models` | Config is in V1 format (`npm`/`options`). Use the V2 `package`/`settings`/`models` format above. |
| No toast on startup | TUI plugin is not listed in `cli.json`, or the path is wrong. |
| No toast after a reply | Check that the proxy wrote `~/.local/share/opencode/ecologits-last.json` and that it is fresh. |
| `RemoteProtocolError: incomplete chunked read` | Update to the latest `proxy.py`; it stops at `data: [DONE]`. |
| `OpenAI Chat stream ended without finish_reason` / retries | The upstream may omit the terminal `finish_reason`. The proxy synthesizes one by default when the stream reaches `data: [DONE]`. If it still fails, set `ECOLOGITS_STREAM_LOG_PATH` and inspect the last SSE lines; a stream that closes *before* `[DONE]` is a genuine upstream truncation. |
| Compaction fails or bypasses the proxy | Compaction/title generation may use a different small model/provider. Point that model at the proxy too, or disable compaction while testing. |

## Adapting it to a shared gateway

If you want this for every consumer of a shared gateway, deploy the same proxy
next to the gateway:

1. Build a container image from `proxy.py` + `requirements.txt`.
2. Set `UPSTREAM_BASE_URL` to the existing upstream backend.
3. Route gateway traffic through the proxy instead of directly to the backend.
4. The proxy will continue to call `api.ecologits.ai` and can additionally
   expose metrics, write to a database, or add response headers such as
   `X-Ecologits-GWP`.

## Files

| File | Purpose |
|------|---------|
| `proxy.py` | FastAPI proxy with EcoLogits integration |
| `ecologits-mapping.example.json` | Template for model mapping |
| `config.example.ps1` / `config.example.sh` | Template for local configuration |
| `start-proxy.ps1` / `start-proxy.sh` | Create venv, install deps, and start the proxy |
| `requirements.txt` | Python dependencies |
| `mock_upstream.py` | Local mock upstream for testing |
| `test-proxy.ps1` / `test-proxy.sh` | End-to-end test against the mock upstream |
| `~/.config/opencode/plugins/ecologits-toast/tui.js` | TUI plugin that toasts on `session.execution.succeeded` |
