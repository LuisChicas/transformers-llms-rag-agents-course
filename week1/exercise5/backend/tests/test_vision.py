import base64
import json

import httpx
import pytest

from .conftest import completion_response

# Only the signature matters: the backend checks the first bytes, not the whole image.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def data_url(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def image_message(url: str, text: str = "What is in this picture?") -> dict:
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": url}},
        ],
    }


def post(client, *messages):
    return client.post("/api/chat", json={"messages": list(messages)})


def test_image_parts_are_sent_to_the_model_unchanged(client_factory):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return completion_response("A cat.")

    message = image_message(data_url(PNG))
    response = post(client_factory(handler, llm_supports_vision=True), message)

    assert response.status_code == 200
    sent = seen[0]["messages"][-1]
    # `detail` was not sent by the client, so it must not appear as null.
    assert sent == message
    assert response.json()["request"] == seen[0]


def test_images_are_rejected_when_vision_is_off(client_factory):
    client = client_factory(lambda _: pytest.fail("upstream must not be called"))
    response = post(client, image_message(data_url(PNG)))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "vision_disabled"
    assert "LLM_SUPPORTS_VISION" in response.json()["error"]["message"]


def test_stream_rejects_images_before_streaming_when_vision_is_off(client_factory):
    client = client_factory(lambda _: pytest.fail("upstream must not be called"))
    response = client.post("/api/chat/stream", json={"messages": [image_message(data_url(PNG))]})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "vision_disabled"


def test_text_only_messages_work_when_vision_is_off(client_factory):
    client = client_factory(lambda _: completion_response())
    text_parts = {"role": "user", "content": [{"type": "text", "text": "Hi"}]}
    assert post(client, text_parts).status_code == 200


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("https://example.com/cat.png", "invalid_image"),
        (data_url(b"<svg/>", "image/svg+xml"), "invalid_image"),
        (data_url(b"not an image at all"), "invalid_image"),  # says PNG, isn't
        (data_url(JPEG, "image/png"), "invalid_image"),  # JPEG bytes labelled PNG
        ("data:image/png;base64,***not-base64***", "invalid_image"),
    ],
)
def test_bad_images_are_rejected(client_factory, url, code):
    client = client_factory(lambda _: pytest.fail("upstream must not be called"), llm_supports_vision=True)
    response = post(client, image_message(url))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == code


def test_images_over_the_size_limit_are_rejected(client_factory):
    client = client_factory(
        lambda _: pytest.fail("upstream must not be called"), llm_supports_vision=True, max_image_mb=0.001
    )
    response = post(client, image_message(data_url(PNG + b"\x00" * 2000)))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "image_too_large"


def test_every_accepted_type_passes(client_factory):
    client = client_factory(lambda _: completion_response(), llm_supports_vision=True)
    samples = {
        "image/png": PNG,
        "image/jpeg": JPEG,
        "image/gif": b"GIF89a" + b"\x00" * 16,
        "image/webp": b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 16,
    }
    for mime, data in samples.items():
        assert post(client, image_message(data_url(data, mime))).status_code == 200, mime


def test_images_in_assistant_messages_are_rejected(client_factory):
    client = client_factory(lambda _: pytest.fail("upstream must not be called"), llm_supports_vision=True)
    assistant = {**image_message(data_url(PNG)), "role": "assistant"}
    response = post(client, {"role": "user", "content": "Hi"}, assistant, {"role": "user", "content": "And?"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_image"
