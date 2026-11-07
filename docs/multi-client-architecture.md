# Multi-client EcoLogits architecture

This document describes how the current OpenCode-only implementation stores
session-level CO₂ totals, and how the same proxy can be extended to support
other clients such as Claude Code, Cursor, Continue, or any OpenAI-compatible
consumer.

## Current design

The proxy (`proxy.py`) is deliberately dumb:

- It forwards every `/v1/chat/completions` request to the upstream.
- After each response it calls the EcoLogits API and records the per-request
  estimate in `ecologits.jsonl` and `ecologits-last.json`.
- It exposes `GET /ecologits/last` so consumers can read the most recent
  estimate.

The OpenCode TUI plugin (`~/.config/opencode/plugins/ecologits-toast/tui.js`)
holds all session-aware logic:

- It listens to OpenCode lifecycle events (`session.created`,
  `session.execution.succeeded`, `session.deleted`).
- It maps each OpenCode `sessionID` to a running `{ min, max }` CO₂ total.
- It persists those totals to `~/.local/share/opencode/ecologits-sessions.json`.

This keeps the proxy generic and makes the plugin the only client-specific
component.

## The hard problem: session attribution

The proxy sees individual HTTP requests. It does **not** know which chat
session a request belongs to, because the OpenAI chat-completions protocol
does not carry a session identifier.

Therefore any multi-client design must answer: *who owns this request?*

The only reliable answer is: **the client that originated the request must
say so.**

## Future design

To support multiple clients, the proxy should become the central datastore, but 
each client still needs a tiny integration to attribute its requests to a 
session.

### Proposed protocol

Standardize on a request header:

```http
X-Ecologits-Session-ID: <opaque-session-id>
```

When the proxy sees this header, it stores the estimate under that session
ID in addition to the global log. It can then expose:

- `GET /ecologits/sessions/{sessionID}` — total impact for that session.
- `GET /ecologits/lifetime` — cumulative impact for the current API key / user.
- `GET /ecologits/requests?session=...&since=...` — raw request history.

Clients that cannot send a custom header can still be supported through a
small wrapper or local proxy that injects the header.

### Client-specific notes

| Client | Session attribution strategy |
|---|---|
| **OpenCode** | TUI plugin knows `sessionID`; either inject the header before the request leaves the client, or keep the current event-based plugin and have it call a proxy `POST /ecologits/sessions/{id}/event` endpoint. |
| **Claude Code** | Can often be configured to send custom HTTP headers, or can be wrapped in a thin local proxy that adds `X-Ecologits-Session-ID` based on the current Claude Code project / conversation. |
| **Cursor** | Does not expose custom headers easily. Most practical approach is a local transparent proxy that derives a session ID from the active workspace or conversation window, or accept request-level tracking only. |
| **Generic OpenAI-compatible clients** | Add the header in the client config if supported; otherwise run a small sidecar proxy. |

### Migration path from current design

1. Keep the OpenCode plugin as the reference client integration.
2. Add an SQLite backend to the proxy and a session header parser.
3. Change the OpenCode plugin to send `X-Ecologits-Session-ID` (or call a new
   proxy endpoint) instead of maintaining its own JSON file.
4. Migrate `ecologits-sessions.json` into the SQLite database on first run.
5. Add client integrations for Claude Code, Cursor, etc., one at a time.

## Guiding principle

Keep the proxy **client-agnostic** and let each client own the small amount of
logic needed to identify its sessions. That way adding a new client is a
matter of writing a thin integration, not changing the core proxy.
