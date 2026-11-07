"""Tiny mock OpenAI-compatible upstream for testing the proxy."""
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()


@app.get("/v1/models")
def models():
    return {"data": [{"id": "glm-5.3-flash"}, {"id": "openai/gpt-4o-mini"}]}


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    no_finish = req.headers.get("x-test-no-finish") == "1"

    if body.get("stream"):
        async def gen():
            yield (
                "data: "
                + json.dumps(
                    {
                        "id": "chatcmpl-test",
                        "object": "chat.completion.chunk",
                        "choices": [{"delta": {"role": "assistant", "content": "hi "}}],
                    }
                )
                + "\n\n"
            )
            yield (
                "data: "
                + json.dumps(
                    {
                        "id": "chatcmpl-test",
                        "object": "chat.completion.chunk",
                        "choices": [{"delta": {"content": "there"}}],
                    }
                )
                + "\n\n"
            )
            if not no_finish:
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "id": "chatcmpl-test",
                            "object": "chat.completion.chunk",
                            "choices": [{"delta": {}, "finish_reason": "stop", "index": 0}],
                        }
                    )
                    + "\n\n"
                )
            yield (
                "data: "
                + json.dumps(
                    {
                        "id": "chatcmpl-test",
                        "object": "chat.completion.chunk",
                        "choices": [],
                        "usage": {"completion_tokens": 7},
                    }
                )
                + "\n\n"
            )
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return JSONResponse(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "choices": [
                {"message": {"role": "assistant", "content": "hi there"}}
            ],
            "usage": {"completion_tokens": 5},
        }
    )
