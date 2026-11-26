import asyncio
import json

import httpx
import pytest

from app.db.accounts import DEFAULT_ACCOUNT_ID
from app.db.models import Account, Attachment, Conversation, Message, utcnow
from app.db.session import create_session_factory, get_session_factory
from app.main import app
from app.routers.chat import until_disconnected
from app.routers.conversations import PendingTurn

from .test_stream import (
    ControlledStream,
    FakeRequest,
    controlled_client,
    parse_events,
    sse_body,
    sse_chunk,
    sse_response,
)
from .test_vision import PNG, data_url

USAGE = {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24}


def reply(*pieces: str, usage: dict | None = USAGE):
    """An upstream handler that streams `pieces` and records each request body."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        chunks = [sse_chunk(piece) for piece in pieces] + [sse_chunk(finish_reason="stop")]
        if usage:
            chunks.append({"id": "chatcmpl-1", "choices": [], "usage": usage})
        return sse_response(sse_body(*chunks))

    handler.seen = seen
    return handler


def new_conversation(client) -> str:
    response = client.post("/api/conversations")
    assert response.status_code == 201
    return response.json()["id"]


def send(client, conversation_id: str, content):
    return client.post(f"/api/conversations/{conversation_id}/messages", json={"content": content})


def session_factory():
    return app.dependency_overrides[get_session_factory]()


def test_create_list_rename_and_delete(client_factory):
    client = client_factory(lambda _: pytest.fail("not called"))
    first = new_conversation(client)
    second = new_conversation(client)

    listed = client.get("/api/conversations").json()
    assert [c["id"] for c in listed] == [second, first]  # newest first
    assert listed[0]["title"] is None

    renamed = client.patch(f"/api/conversations/{first}", json={"title": "  Trip plans  "})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Trip plans"
    assert client.patch(f"/api/conversations/{first}", json={"title": "   "}).status_code == 400

    assert client.delete(f"/api/conversations/{first}").status_code == 204
    assert [c["id"] for c in client.get("/api/conversations").json()] == [second]
    assert client.get(f"/api/conversations/{first}").status_code == 404


def test_unknown_conversation_is_404(client_factory):
    client = client_factory(lambda _: pytest.fail("not called"))
    assert client.get("/api/conversations/nope").json()["error"]["code"] == "not_found"
    assert send(client, "nope", "Hi").status_code == 404


def test_send_streams_then_saves_both_messages_with_usage(client_factory):
    handler = reply("Hel", "lo!")
    client = client_factory(handler)
    conversation_id = new_conversation(client)

    response = send(client, conversation_id, "Hello there, how are you?")

    events = parse_events(response.text)
    assert [name for name, _ in events] == ["context", "delta", "delta", "usage", "saved", "done"]
    saved = events[4][1]["conversation"]
    assert saved["id"] == conversation_id
    assert saved["title"] == "Hello there, how are you?"
    assert events[0][1]["request"] == handler.seen[0]

    detail = client.get(f"/api/conversations/{conversation_id}").json()
    assert detail["title"] == "Hello there, how are you?"
    user, assistant = detail["messages"]
    assert (user["role"], user["content"]) == ("user", "Hello there, how are you?")
    assert (assistant["role"], assistant["content"]) == ("assistant", "Hello!")
    assert assistant["prompt_tokens"] == 20
    assert assistant["completion_tokens"] == 4
    assert assistant["finish_reason"] == "stop"


def test_context_is_rebuilt_from_saved_history(client_factory):
    handler = reply("Answer")
    client = client_factory(handler)
    conversation_id = new_conversation(client)
    send(client, conversation_id, "First question")
    send(client, conversation_id, "Second question")

    messages = handler.seen[1]["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert [m["content"] for m in messages[1:]] == ["First question", "Answer", "Second question"]


def test_title_is_truncated_and_kept_after_rename(client_factory):
    client = client_factory(reply("ok"))
    conversation_id = new_conversation(client)
    send(client, conversation_id, "word " * 40)
    title = client.get(f"/api/conversations/{conversation_id}").json()["title"]
    assert len(title) <= 60 and title.endswith("…")

    client.patch(f"/api/conversations/{conversation_id}", json={"title": "Mine"})
    send(client, conversation_id, "Another message")
    assert client.get(f"/api/conversations/{conversation_id}").json()["title"] == "Mine"


def test_sending_bumps_the_conversation_to_the_top(client_factory):
    client = client_factory(reply("ok"))
    older = new_conversation(client)
    newer = new_conversation(client)
    send(client, older, "Hi")
    assert [c["id"] for c in client.get("/api/conversations").json()] == [older, newer]


def test_failed_reply_saves_nothing(client_factory):
    body = sse_body(sse_chunk("Par"), {"error": {"message": "overloaded"}}, done=False)
    client = client_factory(lambda _: sse_response(body))
    conversation_id = new_conversation(client)

    events = parse_events(send(client, conversation_id, "Hi").text)

    assert events[-1][0] == "error"
    detail = client.get(f"/api/conversations/{conversation_id}").json()
    assert detail["messages"] == []
    assert detail["title"] is None


def test_empty_message_is_rejected(client_factory):
    client = client_factory(lambda _: pytest.fail("upstream must not be called"))
    conversation_id = new_conversation(client)
    assert send(client, conversation_id, "   ").status_code == 400
    assert send(client, conversation_id, [{"type": "text", "text": " "}]).status_code == 400


def test_images_are_saved_as_files_and_sent_again_as_data_urls(client_factory, settings):
    handler = reply("A cat.")
    client = client_factory(handler, llm_supports_vision=True)
    conversation_id = new_conversation(client)
    url = data_url(PNG)
    content = [{"type": "text", "text": "What is this?"}, {"type": "image_url", "image_url": {"url": url}}]

    send(client, conversation_id, content)
    send(client, conversation_id, "Are you sure?")

    # The image file is on disk, and the history sent on the next turn carries it as a data URL again.
    files = list((settings.uploads_dir / conversation_id).iterdir())
    assert [f.read_bytes() for f in files] == [PNG]
    assert handler.seen[1]["messages"][1]["content"] == content

    user = client.get(f"/api/conversations/{conversation_id}").json()["messages"][0]
    text_part, image_part = user["content"]
    assert text_part == {"type": "text", "text": "What is this?"}
    assert image_part["type"] == "image"
    assert image_part["mime_type"] == "image/png"
    assert image_part["size_bytes"] == len(PNG)

    image = client.get(image_part["url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content == PNG

    client.delete(f"/api/conversations/{conversation_id}")
    assert not (settings.uploads_dir / conversation_id).exists()
    assert client.get(image_part["url"]).status_code == 404


def test_image_only_message_gets_a_title(client_factory):
    client = client_factory(reply("A cat."), llm_supports_vision=True)
    conversation_id = new_conversation(client)
    send(client, conversation_id, [{"type": "image_url", "image_url": {"url": data_url(PNG)}}])
    assert client.get(f"/api/conversations/{conversation_id}").json()["title"] == "Image"


def test_images_are_rejected_when_vision_is_off(client_factory):
    client = client_factory(lambda _: pytest.fail("upstream must not be called"))
    conversation_id = new_conversation(client)
    response = send(client, conversation_id, [{"type": "image_url", "image_url": {"url": data_url(PNG)}}])
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "vision_disabled"


def test_other_accounts_data_is_invisible(client_factory):
    client = client_factory(lambda _: pytest.fail("not called"))
    with session_factory()() as db:
        other = Account(name="someone else")
        db.add(other)
        db.flush()
        conversation = Conversation(account_id=other.id, title="Private")
        message = Message(role="user", content=json.dumps("secret"))
        message.attachments.append(Attachment(mime_type="image/png", size_bytes=1, path="x/y.png"))
        conversation.messages.append(message)
        db.add(conversation)
        db.commit()
        conversation_id = conversation.id
        attachment_id = message.attachments[0].id

    assert client.get("/api/conversations").json() == []
    assert client.get(f"/api/conversations/{conversation_id}").status_code == 404
    assert client.patch(f"/api/conversations/{conversation_id}", json={"title": "x"}).status_code == 404
    assert client.delete(f"/api/conversations/{conversation_id}").status_code == 404
    assert send(client, conversation_id, "Hi").status_code == 404
    assert client.get(f"/api/attachments/{attachment_id}").status_code == 404


def test_conversations_survive_a_restart(client_factory, settings):
    client = client_factory(reply("Saved"))
    conversation_id = new_conversation(client)
    send(client, conversation_id, "Remember me")

    # A new engine on the same file, as after a container restart.
    factory = create_session_factory(settings)
    with factory() as db:
        conversation = db.get(Conversation, conversation_id)
        assert [json.loads(m.content) for m in conversation.messages] == ["Remember me", "Saved"]


def stop_scenario(settings, upstream: ControlledStream, *, deltas: int) -> list[Message]:
    """Run a reply through PendingTurn and disconnect the browser after `deltas` deltas."""
    factory = create_session_factory(settings)
    with factory() as db:
        conversation = Conversation(account_id=DEFAULT_ACCOUNT_ID)
        db.add(conversation)
        db.commit()
        conversation_id = conversation.id

    async def scenario():
        llm = controlled_client(settings, upstream)
        request = FakeRequest()
        turn = PendingTurn(
            factory=factory,
            uploads_dir=settings.uploads_dir,
            account_id=DEFAULT_ACCOUNT_ID,
            conversation_id=conversation_id,
            content="Tell me a story",
            images=[],
            started_at=utcnow(),
        )
        events = turn.save_when_finished(until_disconnected(request, llm.stream({"model": "m", "messages": []})))
        assert (await anext(events))[0] == "context"
        for _ in range(deltas):
            assert (await anext(events))[0] == "delta"
        pending = asyncio.ensure_future(anext(events))
        await asyncio.sleep(0)
        request.disconnect.set()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(pending, timeout=1)
        await llm.aclose()

    asyncio.run(scenario())
    with factory() as db:
        return list(db.get(Conversation, conversation_id).messages)


def test_stop_after_some_text_saves_the_partial_reply(settings):
    upstream = ControlledStream(sse_body(sse_chunk("Once upon"), done=False), sse_body(sse_chunk(" a time")))
    user, assistant = stop_scenario(settings, upstream, deltas=1)
    assert json.loads(user.content) == "Tell me a story"
    assert json.loads(assistant.content) == "Once upon"
    assert assistant.finish_reason == "stopped"
    assert assistant.prompt_tokens is None


def test_stop_before_any_text_saves_nothing(settings):
    upstream = ControlledStream(b": waiting\n\n", sse_body(sse_chunk("Too late")))
    assert stop_scenario(settings, upstream, deltas=0) == []
