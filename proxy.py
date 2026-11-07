"""
EcoLogits-aware OpenAI-compatible proxy for OpenCode.

Sits between OpenCode and your LLM gateway, records the environmental impact of
every chat completion by calling https://api.ecologits.ai/v1beta/estimations.

Environment variables:
  UPSTREAM_BASE_URL          LLM upstream (required; set in config.local.*)
  ECOLOGITS_ZONE             Electricity mix zone (default: WOR)
  ECOLOGITS_FALLBACK_PROVIDER  Provider to use when the request model is not mapped
  ECOLOGITS_FALLBACK_MODEL     Model to use when the request model is not mapped
  ECOLOGITS_LOG_PATH         JSONL log file
  ECOLOGITS_LAST_PATH        Single-file snapshot of the last estimate
  ECOLOGITS_MAPPING_PATH     JSON file mapping request model -> {"provider","model_name"}
  ECOLOGITS_SYNTHESIZE_FINISH_REASON  Synthesize finish_reason on [DONE] (default: true)
  ECOLOGITS_STREAM_LOG_PATH  Debug log of every SSE line forwarded to the client
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv

# Load optional local .env file (gitignored) for output paths and other settings.
load_dotenv(Path(__file__).with_name(".env"))

app = FastAPI(title="EcoLogits OpenCode Proxy")

UPSTREAM = os.environ.get("UPSTREAM_BASE_URL", "http://localhost:8000").rstrip("/")
ECOLOGITS_URL = "https://api.ecologits.ai/v1beta/estimations"
ZONE = os.environ.get("ECOLOGITS_ZONE", "WOR")
FALLBACK_PROVIDER = os.environ.get("ECOLOGITS_FALLBACK_PROVIDER", "")
FALLBACK_MODEL = os.environ.get("ECOLOGITS_FALLBACK_MODEL", "")

LOG_PATH = Path(
    os.environ.get(
        "ECOLOGITS_LOG_PATH",
        Path.home() / ".local" / "share" / "opencode" / "ecologits.jsonl",
    )
)
LAST_PATH = Path(
    os.environ.get(
        "ECOLOGITS_LAST_PATH",
        Path.home() / ".local" / "share" / "opencode" / "ecologits-last.json",
    )
)
MAPPING_PATH = Path(
    os.environ.get(
        "ECOLOGITS_MAPPING_PATH",
        Path(__file__).with_name("ecologits-mapping.json"),
    )
)
STREAM_LOG_PATH = os.environ.get("ECOLOGITS_STREAM_LOG_PATH")
SYNTHESIZE_FINISH_REASON = os.environ.get(
    "ECOLOGITS_SYNTHESIZE_FINISH_REASON", "true"
).lower() in ("1", "true", "yes", "on")

HTTP_CLIENT = httpx.AsyncClient(
    timeout=httpx.Timeout(300.0),
    follow_redirects=True,
)

SUPPORTED_PROVIDERS = {
    "openai",
    "anthropic",
    "mistralai",
    "google_genai",
    "cohere",
    "huggingface_hub",
}


def _load_mapping() -> dict[str, dict[str, str]]:
    if MAPPING_PATH.exists():
        try:
            data = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as exc:
            print(f"[ecologits-proxy] failed to load mapping: {exc}")
    return {}


MODEL_MAPPING = _load_mapping()


def _resolve_ecologits(model: str) -> dict[str, str] | None:
    """Map a request model id to an EcoLogits provider/model_name."""
    if model in MODEL_MAPPING:
        mapped = MODEL_MAPPING[model]
        return {"provider": mapped["provider"], "model_name": mapped["model_name"]}

    # Support provider/model or provider:model syntax directly.
    for delim in ("/", ":"):
        if delim in model:
            prov, name = model.split(delim, 1)
            if prov in SUPPORTED_PROVIDERS:
                return {"provider": prov, "model_name": name}

    if FALLBACK_PROVIDER and FALLBACK_MODEL:
        return {"provider": FALLBACK_PROVIDER, "model_name": FALLBACK_MODEL}

    return None


def _filter_headers(request_headers: httpx.Headers) -> dict[str, str]:
    """Forward headers except those that httpx should recompute."""
    drop = {"host", "content-length", "content-encoding", "transfer-encoding"}
    return {
        str(k): str(v)
        for k, v in request_headers.items()
        if k.lower() not in drop
    }


def _parse_stream_usage(text: str) -> int | None:
    """Extract completion_tokens from the final usage chunk of an SSE stream."""
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            continue
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        usage = chunk.get("usage")
        if isinstance(usage, dict) and usage.get("completion_tokens") is not None:
            return int(usage["completion_tokens"])
    return None


def _parse_non_stream_usage(body: bytes) -> int | None:
    try:
        data = json.loads(body)
    except Exception:
        return None
    usage = data.get("usage") or {}
    if usage.get("completion_tokens") is not None:
        return int(usage["completion_tokens"])
    return None


def _normalize_value(value: Any) -> dict[str, float]:
    """EcoLogits returns either a scalar or {min,max} for impact values."""
    if isinstance(value, (int, float)):
        return {"min": float(value), "max": float(value)}
    if isinstance(value, dict):
        return {
            "min": float(value.get("min", 0)),
            "max": float(value.get("max", 0)),
        }
    return {"min": 0.0, "max": 0.0}


def _format_impact_text(impacts: dict[str, Any]) -> str:
    gwp = _normalize_value(impacts.get("gwp", {}).get("value", 0))
    energy = _normalize_value(impacts.get("energy", {}).get("value", 0))
    gwp_min, gwp_max = gwp["min"], gwp["max"]
    energy_min, energy_max = energy["min"], energy["max"]
    if gwp_min == gwp_max:
        gwp_text = f"{gwp_min:.2e} kgCO₂eq"
    else:
        gwp_text = f"{gwp_min:.2e}–{gwp_max:.2e} kgCO₂eq"
    if energy_min == energy_max:
        energy_text = f"{energy_min:.2e} kWh"
    else:
        energy_text = f"{energy_min:.2e}–{energy_max:.2e} kWh"
    return f"{gwp_text} · {energy_text}"


async def _record_impact(
    model: str,
    output_tokens: int | None,
    latency: float,
) -> None:
    if output_tokens is None:
        return

    ecologits = _resolve_ecologits(model)
    if ecologits is None:
        print(f"[ecologits-proxy] no EcoLogits mapping for model '{model}', skipping")
        return

    payload = {
        **ecologits,
        "output_token_count": output_tokens,
        "request_latency": latency,
        "electricity_mix_zone": ZONE,
    }

    try:
        resp = await HTTP_CLIENT.post(ECOLOGITS_URL, json=payload, timeout=30.0)
        resp.raise_for_status()
        impacts = resp.json().get("impacts")
    except Exception as exc:
        print(f"[ecologits-proxy] EcoLogits API error: {exc}")
        return

    entry = {
        "time": time.time(),
        "model": model,
        "output_tokens": output_tokens,
        "latency_seconds": latency,
        "zone": ZONE,
        "ecologits_request": payload,
        "impacts": impacts,
    }

    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        LAST_PATH.write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[ecologits-proxy] {model}: {_format_impact_text(impacts)}")
    except Exception as exc:
        print(f"[ecologits-proxy] failed to write log: {exc}")


async def _stream_generator(
    upstream: httpx.Response,
    model: str,
    started_at: float,
):
    """Yields SSE chunks and records impact once the stream ends.

    Parses the upstream stream line-by-line so that SSE event boundaries are
    preserved and line endings are normalized to LF. This avoids problems with
    mixed LF/CRLF keepalive frames and gives us a place to detect a missing
    terminal ``finish_reason``.
    """
    collected: list[str] = []
    seen_finish_reason = False
    last_chunk_id = "ecologits-synthetic"
    stream_log = _open_stream_log()

    try:
        async for raw_line in upstream.aiter_lines():
            # Normalize any line ending (CRLF or LF) to LF for consistent SSE framing.
            line = raw_line.rstrip("\r\n")

            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload == "[DONE]":
                    # OpenAI clients expect the terminal finish chunk *before* [DONE].
                    # If the upstream never sent one, synthesize it now.
                    if not seen_finish_reason and SYNTHESIZE_FINISH_REASON:
                        finish_line = _synthetic_finish_line(model, last_chunk_id)
                        collected.append(finish_line)
                        _write_stream_log(stream_log, finish_line)
                        yield finish_line
                        print(
                            "[ecologits-proxy] upstream stream ended with [DONE] but no "
                            f"finish_reason; synthesized one for model={model}"
                        )
                    done_line = line + "\n\n"
                    collected.append(done_line)
                    _write_stream_log(stream_log, done_line)
                    yield done_line
                    break

            sse_line = line + "\n"
            collected.append(sse_line)
            _write_stream_log(stream_log, sse_line)
            yield sse_line

            if not line.startswith("data:"):
                continue

            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if chunk.get("id"):
                last_chunk_id = chunk["id"]
            for choice in chunk.get("choices") or []:
                if choice.get("finish_reason"):
                    seen_finish_reason = True
    except httpx.RemoteProtocolError:
        # Upstream closed without a clean chunked terminator; treat as end of stream.
        pass
    except Exception as exc:
        print(f"[ecologits-proxy] stream read error: {exc}")
    finally:
        _close_stream_log(stream_log)
        try:
            await upstream.aclose()
        except Exception:
            pass

    text = "".join(collected)
    output_tokens = _parse_stream_usage(text)
    latency = time.time() - started_at
    asyncio.get_event_loop().create_task(_record_impact(model, output_tokens, latency))


def _open_stream_log() -> Any:
    if not STREAM_LOG_PATH:
        return None
    try:
        Path(STREAM_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
        return Path(STREAM_LOG_PATH).open("a", encoding="utf-8")
    except Exception as exc:
        print(f"[ecologits-proxy] failed to open stream log: {exc}")
        return None


def _write_stream_log(stream_log: Any, text: str) -> None:
    if stream_log is None:
        return
    try:
        stream_log.write(text)
        stream_log.flush()
    except Exception:
        pass


def _close_stream_log(stream_log: Any) -> None:
    if stream_log is None:
        return
    try:
        stream_log.close()
    except Exception:
        pass


def _synthetic_finish_line(model: str, chunk_id: str) -> str:
    """Return an OpenAI-format SSE line with finish_reason: stop."""
    finish_chunk = json.dumps(
        {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
    )
    return f"data: {finish_chunk}\n\n"


@app.get("/ecologits/last")
async def ecologits_last():
    """Return the most recent estimate as JSON for local consumers (e.g. the TUI plugin)."""
    if not LAST_PATH.exists():
        return Response(status_code=404, content='{"error":"no estimate yet"}', media_type="application/json")
    return Response(content=LAST_PATH.read_text(encoding="utf-8"), media_type="application/json")


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"])
async def catch_all(path: str, request: Request):
    """Forward every request upstream; special-case chat completions for impact tracking."""
    if path == "v1/chat/completions" and request.method == "POST":
        return await _chat_completions(request)

    target = f"{UPSTREAM}/{path}"
    headers = _filter_headers(request.headers)
    body = await request.body() if request.method != "GET" else b""

    upstream = await HTTP_CLIENT.request(
        request.method,
        target,
        content=body or None,
        headers=headers,
        params=request.query_params,
    )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=dict(upstream.headers),
    )


async def _chat_completions(request: Request):
    body_bytes = await request.body()
    try:
        body_json = json.loads(body_bytes)
    except Exception:
        body_json = {}

    model = body_json.get("model", "")
    stream = body_json.get("stream", False)
    print(f"[ecologits-proxy] chat request: model={model} stream={stream}")

    headers = _filter_headers(request.headers)
    target = f"{UPSTREAM}/v1/chat/completions"
    started_at = time.time()

    if stream:
        upstream = await HTTP_CLIENT.send(
            HTTP_CLIENT.build_request("POST", target, content=body_bytes, headers=headers),
            stream=True,
        )
        print(f"[ecologits-proxy] upstream status: {upstream.status_code}")
        return StreamingResponse(
            _stream_generator(upstream, model, started_at),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "text/event-stream"),
            headers={k: v for k, v in upstream.headers.items() if k.lower() not in {"content-length", "transfer-encoding"}},
        )

    upstream = await HTTP_CLIENT.post(target, content=body_bytes, headers=headers)
    print(f"[ecologits-proxy] upstream status: {upstream.status_code}")
    output_tokens = _parse_non_stream_usage(upstream.content)
    latency = time.time() - started_at
    asyncio.get_event_loop().create_task(_record_impact(model, output_tokens, latency))

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=dict(upstream.headers),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8787")))
