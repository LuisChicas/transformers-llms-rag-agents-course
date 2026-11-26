"""Checks for images sent as `data:` URLs inside OpenAI content parts (Stage 3)."""

import base64
import binascii
import re
from dataclasses import dataclass

from .config import Settings
from .errors import AppError
from .schemas import ChatMessage, ImagePart, MessageContent

ALLOWED_IMAGE_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}

DATA_URL = re.compile(r"^data:(?P<mime>[\w.+-]+/[\w.+-]+);base64,(?P<data>.*)$", re.DOTALL)


@dataclass
class DecodedImage:
    mime_type: str
    data: bytes

    @property
    def extension(self) -> str:
        return ALLOWED_IMAGE_TYPES[self.mime_type]

    def data_url(self) -> str:
        return to_data_url(self.mime_type, self.data)


def to_data_url(mime_type: str, data: bytes) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def sniff_image_type(data: bytes) -> str | None:
    """The real image type from the file's first bytes, whatever the client claimed."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def decode_image(url: str, max_bytes: int) -> DecodedImage:
    match = DATA_URL.match(url)
    if not match:
        raise AppError(400, "invalid_image", "Images must be sent as base64 data URLs (data:image/...;base64,...).")

    declared = match["mime"].lower()
    if declared not in ALLOWED_IMAGE_TYPES:
        raise AppError(400, "invalid_image", f"Images of type {declared} are not accepted. Use PNG, JPEG, WebP or GIF.")

    encoded = match["data"]
    # Checked before decoding so an oversized upload is never held in memory twice.
    if len(encoded) * 3 // 4 > max_bytes + 3:
        raise _too_large(max_bytes)
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise AppError(400, "invalid_image", "An attached image is not valid base64.") from None
    if len(data) > max_bytes:
        raise _too_large(max_bytes)

    actual = sniff_image_type(data)
    if actual != declared:
        raise AppError(400, "invalid_image", f"An attached image says it is {declared}, but its contents are not.")
    return DecodedImage(mime_type=declared, data=data)


def _too_large(max_bytes: int) -> AppError:
    return AppError(400, "image_too_large", f"An attached image is larger than the {max_bytes / 1024 / 1024:g} MB limit (MAX_IMAGE_MB).")


def image_parts(content: MessageContent) -> list[ImagePart]:
    if isinstance(content, str):
        return []
    return [part for part in content if isinstance(part, ImagePart)]


def require_vision(settings: Settings) -> None:
    if not settings.llm_supports_vision:
        raise AppError(
            400,
            "vision_disabled",
            "Image attachments are turned off for this model. If the model supports vision, "
            "set LLM_SUPPORTS_VISION=true in .env and restart the backend.",
        )


def check_content_images(settings: Settings, content: MessageContent) -> list[DecodedImage]:
    """Validate every image in one user message's content and return them decoded."""
    parts = image_parts(content)
    if not parts:
        return []
    require_vision(settings)
    max_bytes = int(settings.max_image_mb * 1024 * 1024)
    return [decode_image(part.image_url.url, max_bytes) for part in parts]


def check_message_images(settings: Settings, messages: list[ChatMessage]) -> None:
    for message in messages:
        if message.role != "user" and image_parts(message.content):
            raise AppError(400, "invalid_image", "Only user messages can contain images.")
        check_content_images(settings, message.content)
