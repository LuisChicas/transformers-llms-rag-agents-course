import asyncio
import json
from collections.abc import AsyncIterator

import anyio
import httpx
import pytest

from app.llm import LLMClient
from app.routers.chat import until_disconnected

from .conftest import API_KEY, completion_response

USAGE = {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}
HELLO = [{"role": "user", "content": "Hello"}]


def sse_chunk(content: str | None = None, finish_reason: str | None = None, **extra) -> dict:
    delta = {} if content is None else {"content": content}
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        **extra,
    }


def sse_body(*chunks: dict | str, done: bool = True) -> bytes:
    lines = [f"data: {c if isinstance(c, str) else json.dumps(c)}\n\n" for c in chunks]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def sse_response(body: bytes) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def parse_events(text: str) -> list[tuple[str, object]]:
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def stream(client, messages=HELLO):
    return client.post("/api/chat/stream", json={"messages": messages})


def test_stream_relays_deltas_usage_and_done(client_factory):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return sse_response(
            sse_body(
                sse_chunk("Hel"),
                sse_chunk("lo"),
                sse_chunk(finish_reason="stop"),
                {"id": "chatcmpl-1", "choices": [], "usage": USAGE},
            )
        )

    response = stream(client_factory(handler))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"

    events = parse_events(response.text)
    assert [name for name, _ in events] == ["context", "delta", "delta", "usage", "done"]
    assert events[1][1] == {"content": "Hel"}
    assert events[2][1] == {"content": "lo"}
    assert events[3][1] == USAGE
    assert events[4][1] == {"finish_reason": "stop"}

    sent = json.loads(seen[0].content)
    assert sent["stream"] is True
    assert sent["stream_options"] == {"include_usage": True}
    # The context event echoes exactly what the model received, system prompt included.
    assert events[0][1]["request"] == sent
    assert sent["messages"][0]["role"] == "system"


def test_stream_without_usage_reports_null(client_factory):
    handler = lambda _: sse_response(sse_body(sse_chunk("Hi"), sse_chunk(finish_reason="stop")))
    events = parse_events(stream(client_factory(handler)).text)
    assert ("usage", None) in events
    assert events[-1] == ("done", {"finish_reason": "stop"})


def test_stream_options_rejected_retries_without_it(client_factory):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if "stream_options" in body:
            return httpx.Response(400, json={"error": {"message": "Unrecognized argument: stream_options"}})
        return sse_response(sse_body(sse_chunk("Hi"), sse_chunk(finish_reason="stop")))

    events = parse_events(stream(client_factory(handler)).text)

    assert len(seen) == 2
    assert "stream_options" not in seen[1]
    assert events[0][1]["request"] == seen[1]
    assert ("delta", {"content": "Hi"}) in events
    assert ("usage", None) in events
    assert events[-1][0] == "done"


def test_stream_auth_error_becomes_error_event_without_the_key(client_factory):
    handler = lambda _: httpx.Response(401, json={"error": {"message": f"Bad key {API_KEY}"}})
    response = stream(client_factory(handler))

    events = parse_events(response.text)
    assert [name for name, _ in events] == ["error"]
    assert events[0][1]["code"] == "invalid_api_key"
    assert API_KEY not in response.text


def test_stream_mid_stream_provider_error_becomes_error_event(client_factory):
    body = sse_body(sse_chunk("Par"), {"error": {"message": f"overloaded {API_KEY}"}}, done=False)
    response = stream(client_factory(lambda _: sse_response(body)))

    events = parse_events(response.text)
    assert [name for name, _ in events] == ["context", "delta", "error"]
    assert events[-1][1]["code"] == "upstream_error"
    assert API_KEY not in response.text


def test_stream_timeout_becomes_error_event(client_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    events = parse_events(stream(client_factory(handler)).text)
    assert events == [("error", {"code": "upstream_timeout", "message": events[0][1]["message"], "status": 504})]


def test_stream_unreadable_chunk_becomes_error_event(client_factory):
    body = sse_body(sse_chunk("Hi"), "not json", done=False)
    events = parse_events(stream(client_factory(lambda _: sse_response(body))).text)
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "bad_upstream_response"


def test_stream_tolerates_comments_and_missing_final_blank_line(client_factory):
    body = b": keep-alive\n\ndata: " + json.dumps(sse_chunk("Hi", finish_reason="stop")).encode()
    events = parse_events(stream(client_factory(lambda _: sse_response(body))).text)
    assert [name for name, _ in events] == ["context", "delta", "usage", "done"]


def test_server_that_ignores_stream_flag_is_relayed_as_one_delta(client_factory):
    handler = lambda _: completion_response("Whole answer", usage=USAGE)
    events = parse_events(stream(client_factory(handler)).text)
    assert [name for name, _ in events] == ["context", "delta", "usage", "done"]
    assert events[1][1] == {"content": "Whole answer"}
    assert events[2][1] == USAGE


def test_stream_only_system_messages_is_rejected_before_streaming(client_factory):
    client = client_factory(lambda _: pytest.fail("upstream must not be called"))
    response = stream(client, [{"role": "system", "content": "x"}])
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


class ControlledStream(httpx.AsyncByteStream):
    """An upstream body that sends one chunk, then waits until the test releases it."""

    def __init__(self, first: bytes, rest: bytes):
        self.first = first
        self.rest = rest
        self.release = asyncio.Event()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.first
        await self.release.wait()
        yield self.rest

    async def aclose(self) -> None:
        self.closed = True


def controlled_client(settings, upstream: ControlledStream) -> LLMClient:
    handler = lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=upstream)
    return LLMClient(settings, transport=httpx.MockTransport(handler))


PAYLOAD = {"model": "test-model", "messages": HELLO}


def test_chunks_are_forwarded_before_the_upstream_finishes(settings):
    upstream = ControlledStream(sse_body(sse_chunk("Hel"), done=False), sse_body(sse_chunk("lo")))

    async def scenario():
        llm = controlled_client(settings, upstream)
        events = llm.stream(PAYLOAD)
        assert (await anext(events))[0] == "context"
        # If chunks were buffered this would hang, because the upstream is still waiting.
        first = await asyncio.wait_for(anext(events), timeout=1)
        assert first == ("delta", {"content": "Hel"})
        upstream.release.set()
        assert await anext(events) == ("delta", {"content": "lo"})
        await events.aclose()
        await llm.aclose()

    asyncio.run(scenario())


class FakeRequest:
    """Just enough of a Starlette request for until_disconnected()."""

    def __init__(self):
        self.disconnect = asyncio.Event()

    async def receive(self) -> dict:
        await self.disconnect.wait()
        return {"type": "http.disconnect"}


def test_disconnect_closes_the_upstream_stream_while_waiting_for_the_model(settings):
    upstream = ControlledStream(sse_body(sse_chunk("Hel"), done=False), sse_body(sse_chunk("lo")))

    async def scenario():
        llm = controlled_client(settings, upstream)
        request = FakeRequest()
        events = until_disconnected(request, llm.stream(PAYLOAD))
        assert (await anext(events))[0] == "context"
        assert (await anext(events))[0] == "delta"

        # The model is now "thinking": no chunk is coming. The browser presses Stop.
        pending = asyncio.ensure_future(anext(events))
        await asyncio.sleep(0)
        request.disconnect.set()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(pending, timeout=1)
        assert upstream.closed
        await llm.aclose()

    asyncio.run(scenario())


def test_closing_the_stream_early_closes_the_upstream(settings):
    upstream = ControlledStream(sse_body(sse_chunk("Hel"), done=False), sse_body(sse_chunk("lo")))

    async def scenario():
        llm = controlled_client(settings, upstream)
        events = llm.stream(PAYLOAD)
        await anext(events)
        await anext(events)
        await events.aclose()
        assert upstream.closed
        await llm.aclose()

    asyncio.run(scenario())


def test_cancelling_the_response_task_closes_the_upstream_stream(settings):
    """Starlette stops a response by cancelling its task scope when it sees the disconnect first."""
    upstream = ControlledStream(sse_body(sse_chunk("Hel"), done=False), sse_body(sse_chunk("lo")))

    async def scenario():
        llm = controlled_client(settings, upstream)
        events = until_disconnected(FakeRequest(), llm.stream(PAYLOAD))
        with anyio.CancelScope() as scope:
            await anext(events)
            await anext(events)
            asyncio.get_running_loop().call_later(0.05, scope.cancel)
            await anext(events)  # waits for the model until the scope is cancelled
        assert scope.cancelled_caught
        assert upstream.closed
        await llm.aclose()

    asyncio.run(scenario())
