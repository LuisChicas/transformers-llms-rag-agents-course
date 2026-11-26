import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..config import Settings, get_settings
from ..images import check_message_images
from ..llm import LLMClient, LLMError, StreamEvent, build_payload
from ..schemas import AssistantMessage, ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    # Tells nginx and similar proxies not to buffer the response.
    "X-Accel-Buffering": "no",
}


def get_llm_client(request: Request) -> LLMClient:
    return request.app.state.llm


def check_request(body: ChatRequest, settings: Settings) -> None:
    if all(message.role == "system" for message in body.messages):
        raise HTTPException(400, "Send at least one user or assistant message. System messages are added by the server.")
    check_message_images(settings, body.messages)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    llm: Annotated[LLMClient, Depends(get_llm_client)],
) -> ChatResponse:
    check_request(body, settings)
    payload = build_payload(settings, body.messages)
    completion = await llm.complete(payload)
    return ChatResponse(
        message=AssistantMessage(content=completion.content),
        usage=completion.usage,
        model=completion.model,
        finish_reason=completion.finish_reason,
        request=payload,
    )


@router.post(
    "/chat/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Server-Sent Events"}},
)
async def chat_stream(
    body: ChatRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    llm: Annotated[LLMClient, Depends(get_llm_client)],
) -> StreamingResponse:
    """Stream the reply as SSE: `context`, `delta`*, `usage`, then `done` (or `error` on failure)."""
    check_request(body, settings)
    payload = build_payload(settings, body.messages)
    events = until_disconnected(request, llm.stream(payload))
    return StreamingResponse(relay(events), media_type="text/event-stream", headers=SSE_HEADERS)


def format_sse(event: str, data: Any) -> str:
    # json.dumps escapes newlines, so the data always fits on a single `data:` line.
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def relay(events: AsyncIterator[StreamEvent]) -> AsyncIterator[str]:
    """Format events as SSE. Once streaming has started the status is already 200, so errors become events."""
    async with aclosing(events):
        try:
            async for event, data in events:
                yield format_sse(event, data)
        except LLMError as error:
            yield format_sse("error", {"code": error.code, "message": error.message, "status": error.status_code})
        except Exception:
            logger.exception("Unexpected failure while streaming a reply")
            yield format_sse("error", {"code": "internal_error", "message": "The backend failed while streaming the reply.", "status": 500})


async def until_disconnected(request: Request, events: AsyncIterator[StreamEvent]) -> AsyncIterator[StreamEvent]:
    """Pass events through, but stop as soon as the browser disconnects.

    The server only notices a closed connection when it next writes to it, which can be a
    long time while the model is still thinking. Racing every upstream read against the
    disconnect message closes the upstream request immediately instead.
    """
    disconnected = asyncio.ensure_future(_wait_for_disconnect(request))
    next_event: asyncio.Future | None = None
    try:
        while True:
            next_event = asyncio.ensure_future(anext(events))
            await asyncio.wait({next_event, disconnected}, return_when=asyncio.FIRST_COMPLETED)
            if not next_event.done():
                logger.info("Browser disconnected; closing the upstream stream")
                return
            try:
                item = next_event.result()
            except StopAsyncIteration:
                return
            yield item
    finally:
        disconnected.cancel()
        # Starlette may also stop this generator by cancelling its task scope. The shield lets
        # the cleanup finish anyway, so the upstream request is never left running.
        with anyio.CancelScope(shield=True):
            if next_event is not None and not next_event.done():
                # Cancelling the pending read runs the generator's cleanup, closing the upstream response.
                next_event.cancel()
                await asyncio.gather(next_event, return_exceptions=True)
            await events.aclose()


async def _wait_for_disconnect(request: Request) -> None:
    while (await request.receive())["type"] != "http.disconnect":
        pass
