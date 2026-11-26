"""Stage 4: conversations saved in the database. Every query is scoped to the current account."""

import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import attachments as files
from ..config import Settings, get_settings
from ..db.dependencies import get_current_account
from ..db.models import Account, Attachment, Conversation, Message, new_id, utcnow
from ..db.session import SessionFactory, get_db, get_session_factory
from ..errors import AppError
from ..images import DecodedImage, check_content_images, to_data_url
from ..llm import LLMClient, StreamEvent, build_payload
from ..schemas import (
    ChatMessage,
    ConversationDetail,
    ConversationSummary,
    CreateConversation,
    ImagePart,
    MessageContent,
    NewMessage,
    RenameConversation,
    StoredMessage,
)
from .chat import SSE_HEADERS, get_llm_client, relay, until_disconnected

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["conversations"])

Db = Annotated[Session, Depends(get_db)]
CurrentAccount = Annotated[Account, Depends(get_current_account)]
CurrentSettings = Annotated[Settings, Depends(get_settings)]

TITLE_LENGTH = 60
# Saved as the finish reason of a reply the user stopped before it finished.
STOPPED = "stopped"
MISSING_IMAGE_TEXT = "[An image was attached here, but its file is missing.]"


def find_conversation(db: Session, account: Account, conversation_id: str) -> Conversation:
    conversation = db.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.account_id == account.id)
    )
    if conversation is None:
        raise AppError(404, "not_found", "Conversation not found. It may have been deleted.")
    return conversation


def summary(conversation: Conversation) -> ConversationSummary:
    return ConversationSummary.model_validate(conversation, from_attributes=True)


@router.get("/conversations", response_model=list[ConversationSummary])
def list_conversations(db: Db, account: CurrentAccount) -> list[ConversationSummary]:
    rows = db.scalars(
        select(Conversation).where(Conversation.account_id == account.id).order_by(Conversation.updated_at.desc())
    )
    return [summary(conversation) for conversation in rows]


@router.post("/conversations", response_model=ConversationSummary, status_code=201)
def create_conversation(
    db: Db, account: CurrentAccount, body: Annotated[CreateConversation | None, Body()] = None
) -> ConversationSummary:
    title = body.title.strip() if body and body.title else None
    now = utcnow()
    conversation = Conversation(account_id=account.id, title=title or None, created_at=now, updated_at=now)
    db.add(conversation)
    db.commit()
    return summary(conversation)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: str, db: Db, account: CurrentAccount) -> ConversationDetail:
    conversation = find_conversation(db, account, conversation_id)
    messages = [
        StoredMessage(
            id=message.id,
            role=message.role,
            content=api_content(message),
            prompt_tokens=message.prompt_tokens,
            completion_tokens=message.completion_tokens,
            finish_reason=message.finish_reason,
            created_at=message.created_at,
        )
        for message in conversation.messages
    ]
    return ConversationDetail(**summary(conversation).model_dump(), messages=messages)


@router.patch("/conversations/{conversation_id}", response_model=ConversationSummary)
def rename_conversation(
    conversation_id: str, body: RenameConversation, db: Db, account: CurrentAccount
) -> ConversationSummary:
    title = body.title.strip()
    if not title:
        raise AppError(400, "invalid_request", "The title cannot be empty.")
    conversation = find_conversation(db, account, conversation_id)
    conversation.title = title
    db.commit()
    return summary(conversation)


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: str, db: Db, account: CurrentAccount, settings: CurrentSettings
) -> Response:
    conversation = find_conversation(db, account, conversation_id)
    db.delete(conversation)  # messages and attachments rows go with it (ON DELETE CASCADE)
    db.commit()
    files.delete_conversation_files(settings.uploads_dir, conversation_id)
    return Response(status_code=204)


@router.get(
    "/attachments/{attachment_id}",
    response_class=FileResponse,
    responses={200: {"content": {"image/*": {}}, "description": "The image file"}},
)
def get_attachment(attachment_id: str, db: Db, account: CurrentAccount, settings: CurrentSettings) -> FileResponse:
    attachment = db.scalar(
        select(Attachment)
        .join(Message, Attachment.message_id == Message.id)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Attachment.id == attachment_id, Conversation.account_id == account.id)
    )
    path = files.resolve(settings.uploads_dir, attachment.path) if attachment else None
    if attachment is None or path is None:
        raise AppError(404, "not_found", "Image not found.")
    return FileResponse(
        path,
        media_type=attachment.mime_type,
        # Attachments never change once saved.
        headers={"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Server-Sent Events"}},
)
async def send_message(
    conversation_id: str,
    body: NewMessage,
    request: Request,
    db: Db,
    account: CurrentAccount,
    settings: CurrentSettings,
    llm: Annotated[LLMClient, Depends(get_llm_client)],
    factory: Annotated[SessionFactory, Depends(get_session_factory)],
) -> StreamingResponse:
    """Stream the reply like `/api/chat/stream`, then save the user message and the reply.

    Events: `context`, `delta`*, `usage`, `saved`, `done` (or `error`). The context is built
    from the database: system prompt, saved history, then the new message.
    """
    require_text_or_image(body.content)
    images = check_content_images(settings, body.content)
    conversation = find_conversation(db, account, conversation_id)

    history = [
        ChatMessage(role=message.role, content=model_content(message, settings.uploads_dir))
        for message in conversation.messages
    ]
    payload = build_payload(settings, [*history, ChatMessage(role="user", content=body.content)])

    turn = PendingTurn(
        factory=factory,
        uploads_dir=settings.uploads_dir,
        account_id=account.id,
        conversation_id=conversation.id,
        content=body.content,
        images=images,
        started_at=utcnow(),
    )
    events = turn.save_when_finished(until_disconnected(request, llm.stream(payload)))
    return StreamingResponse(relay(events), media_type="text/event-stream", headers=SSE_HEADERS)


def require_text_or_image(content: MessageContent) -> None:
    if isinstance(content, str):
        has_content = bool(content.strip())
    else:
        has_content = any(isinstance(part, ImagePart) or part.text.strip() for part in content)
    if not has_content:
        raise AppError(400, "invalid_request", "The message is empty. Type some text or attach an image.")


# ---------- Converting between stored and sent content ----------


def model_content(message: Message, uploads_dir: Path) -> str | list[dict[str, Any]]:
    """Stored content as the model expects it: image references become data URLs again."""
    content = json.loads(message.content)
    if isinstance(content, str):
        return content
    by_id = {attachment.id: attachment for attachment in message.attachments}
    parts: list[dict[str, Any]] = []
    for part in content:
        if part.get("type") != "image":
            parts.append(part)
            continue
        attachment = by_id.get(part.get("attachment_id"))
        path = files.resolve(uploads_dir, attachment.path) if attachment else None
        if attachment is None or path is None:
            logger.warning("Attachment %s of message %s is missing; sending a note instead", part.get("attachment_id"), message.id)
            parts.append({"type": "text", "text": MISSING_IMAGE_TEXT})
            continue
        parts.append({"type": "image_url", "image_url": {"url": to_data_url(attachment.mime_type, path.read_bytes())}})
    return parts


def api_content(message: Message) -> str | list[dict[str, Any]]:
    """Stored content for the browser: image references point at the attachment endpoint."""
    content = json.loads(message.content)
    if isinstance(content, str):
        return content
    by_id = {attachment.id: attachment for attachment in message.attachments}
    parts: list[dict[str, Any]] = []
    for part in content:
        attachment = by_id.get(part.get("attachment_id")) if part.get("type") == "image" else None
        if attachment is None:
            parts.append(part)
            continue
        parts.append({
            "type": "image",
            "attachment_id": attachment.id,
            "mime_type": attachment.mime_type,
            "size_bytes": attachment.size_bytes,
            "url": f"/api/attachments/{attachment.id}",
        })
    return parts


def make_title(content: MessageContent) -> str:
    if isinstance(content, str):
        text = content
    else:
        text = " ".join(part.text for part in content if not isinstance(part, ImagePart))
    text = " ".join(text.split())
    if not text:
        return "Image"
    if len(text) <= TITLE_LENGTH:
        return text
    return text[: TITLE_LENGTH - 1].rstrip() + "…"


class PendingTurn:
    """A user message and its reply, saved together once the reply has finished or been stopped.

    Nothing is saved when the reply fails, or when it is stopped before any text arrived, so
    the browser can retry by sending the same message again.
    """

    def __init__(
        self,
        *,
        factory: SessionFactory,
        uploads_dir: Path,
        account_id: str,
        conversation_id: str,
        content: MessageContent,
        images: list[DecodedImage],
        started_at: datetime,
    ):
        self.factory = factory
        self.uploads_dir = uploads_dir
        self.account_id = account_id
        self.conversation_id = conversation_id
        self.content = content
        self.images = images
        self.started_at = started_at

    async def save_when_finished(self, events: AsyncIterator[StreamEvent]) -> AsyncIterator[StreamEvent]:
        text = ""
        usage: dict[str, int] | None = None
        saved = False
        failed = False
        try:
            async for event, data in events:
                if event == "delta":
                    text += data["content"]
                elif event == "usage":
                    usage = data
                elif event == "done":
                    conversation = self.save(text, usage, data.get("finish_reason"))
                    saved = True
                    if conversation is not None:
                        yield "saved", {"conversation": conversation.model_dump(mode="json")}
                yield event, data
        except Exception:
            failed = True
            raise
        finally:
            # The browser disconnected (Stop) after some text arrived: keep the partial reply,
            # as the stateless UI does. This runs while the generator is being closed, so it
            # must not await anything; the save is synchronous.
            if not saved and not failed and text:
                self.save(text, None, STOPPED)

    def save(self, text: str, usage: dict[str, int] | None, finish_reason: str | None) -> ConversationSummary | None:
        with self.factory() as db:
            conversation = db.scalar(
                select(Conversation).where(
                    Conversation.id == self.conversation_id, Conversation.account_id == self.account_id
                )
            )
            if conversation is None:
                logger.info("Conversation %s was deleted while a reply streamed; not saving it", self.conversation_id)
                return None

            written: list[str] = []
            try:
                user = Message(role="user", created_at=self.started_at)
                user.content = json.dumps(self.stored_content(user, written), ensure_ascii=False)
                assistant = Message(
                    role="assistant",
                    content=json.dumps(text, ensure_ascii=False),
                    prompt_tokens=usage["prompt_tokens"] if usage else None,
                    completion_tokens=usage["completion_tokens"] if usage else None,
                    finish_reason=finish_reason,
                    created_at=utcnow(),
                )
                conversation.messages.extend([user, assistant])
                if conversation.title is None:
                    conversation.title = make_title(self.content)
                conversation.updated_at = utcnow()
                db.commit()
            except Exception:
                db.rollback()
                files.delete_files(self.uploads_dir, written)
                raise
            return summary(conversation)

    def stored_content(self, user: Message, written: list[str]) -> str | list[dict[str, Any]]:
        """Write the images to disk and replace them with references to their attachment rows."""
        if isinstance(self.content, str):
            return self.content
        images = iter(self.images)
        parts: list[dict[str, Any]] = []
        for part in self.content:
            if not isinstance(part, ImagePart):
                parts.append(part.model_dump(exclude_none=True))
                continue
            image = next(images)
            attachment_id = new_id()
            path = files.save_image(self.uploads_dir, self.conversation_id, attachment_id, image)
            written.append(path)
            user.attachments.append(
                Attachment(id=attachment_id, mime_type=image.mime_type, size_bytes=len(image.data), path=path)
            )
            parts.append({"type": "image", "attachment_id": attachment_id})
        return parts
