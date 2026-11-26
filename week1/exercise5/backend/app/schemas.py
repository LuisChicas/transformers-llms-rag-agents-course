from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class TextPart(BaseModel):
    type: Literal["text"]
    text: str


class ImageURL(BaseModel):
    # Only `data:` URLs are accepted, so the backend can check the type and size of every image.
    url: str
    detail: Literal["auto", "low", "high"] | None = None


class ImagePart(BaseModel):
    type: Literal["image_url"]
    image_url: ImageURL


ContentPart = Annotated[TextPart | ImagePart, Field(discriminator="type")]

# A plain string, or OpenAI content parts (text and images) for messages with attachments.
MessageContent = str | Annotated[list[ContentPart], Field(min_length=1)]


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: MessageContent


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str


class ChatResponse(BaseModel):
    message: AssistantMessage
    usage: Usage | None
    model: str
    finish_reason: str | None
    # The exact payload sent to the model, shown by the context viewer.
    request: dict[str, Any]


class PublicConfig(BaseModel):
    model: str
    supports_vision: bool
    max_image_mb: float
    context_window: int | None


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    llm_configured: bool


# ---------- Stage 4: conversations ----------


class ConversationSummary(BaseModel):
    id: str
    # None until the first message is saved; the title is then taken from that message.
    title: str | None
    created_at: datetime
    updated_at: datetime


class StoredMessage(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    # Image parts come back as {"type": "image", "attachment_id", "mime_type", "size_bytes", "url"}.
    content: str | list[dict[str, Any]]
    prompt_tokens: int | None
    completion_tokens: int | None
    # The provider's finish reason, or "stopped" when the user stopped the reply.
    finish_reason: str | None
    created_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[StoredMessage]


class CreateConversation(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class RenameConversation(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class NewMessage(BaseModel):
    content: MessageContent
