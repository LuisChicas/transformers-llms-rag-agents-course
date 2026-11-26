"""Thin client for any server that implements the OpenAI `/chat/completions` API."""

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings
from .errors import AppError
from .schemas import ChatMessage, Usage

logger = logging.getLogger(__name__)


class LLMError(AppError):
    """An upstream failure, already translated into what the browser should see."""


@dataclass
class Completion:
    content: str
    usage: Usage | None
    model: str
    finish_reason: str | None


# (event name, JSON-serializable data), relayed to the browser as one SSE event.
StreamEvent = tuple[str, Any]

# Statuses a provider may return when it rejects `stream_options`; the request is retried without it.
STREAM_OPTIONS_REJECTED = (400, 422)


def build_payload(settings: Settings, messages: list[ChatMessage]) -> dict[str, Any]:
    """System prompt first, then the client's messages without any system message of its own."""
    # exclude_none drops optional fields the client left out, such as an image's `detail`.
    conversation = [m.model_dump(exclude_none=True) for m in messages if m.role != "system"]
    return {
        "model": settings.llm_model,
        "messages": [{"role": "system", "content": settings.system_prompt}, *conversation],
        "temperature": settings.llm_temperature,
    }


def parse_usage(raw: Any) -> Usage | None:
    if not isinstance(raw, dict):
        return None
    prompt = raw.get("prompt_tokens")
    completion = raw.get("completion_tokens")
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return None
    total = raw.get("total_tokens")
    if not isinstance(total, int):
        total = prompt + completion
    return Usage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)


class LLMClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.llm_base_url,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            timeout=httpx.Timeout(settings.llm_timeout_seconds),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def complete(self, payload: dict[str, Any]) -> Completion:
        try:
            response = await self._http.post("/chat/completions", json=payload)
        except httpx.TransportError as error:
            raise self._transport_error(error) from None

        if response.status_code >= 400:
            raise self._error_from_response(response)
        return self._parse_completion(response, payload)

    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[StreamEvent]:
        """Stream a reply as `context`, `delta`*, `usage`, `done` events, forwarding each chunk on arrival.

        Failures raise LLMError, before or during the stream. Closing the generator early
        (the browser disconnected) closes the upstream response so the model stops generating.
        """
        sent = {**payload, "stream": True, "stream_options": {"include_usage": True}}
        response = await self._open_stream(sent)
        try:
            if response.status_code in STREAM_OPTIONS_REJECTED:
                await response.aread()
                logger.info(
                    "LLM server returned HTTP %s with stream_options; retrying without it: %s",
                    response.status_code,
                    self._scrub(self._upstream_message(response)),
                )
                await response.aclose()
                sent = {**payload, "stream": True}
                response = await self._open_stream(sent)

            if response.status_code >= 400:
                await response.aread()
                raise self._error_from_response(response)

            yield "context", {"request": sent}

            # A server that ignores `stream: true` answers with one JSON body; relay it as a single delta.
            if not response.headers.get("content-type", "").startswith("text/event-stream"):
                await response.aread()
                completion = self._parse_completion(response, payload)
                if completion.content:
                    yield "delta", {"content": completion.content}
                yield "usage", completion.usage.model_dump() if completion.usage else None
                yield "done", {"finish_reason": completion.finish_reason}
                return

            usage: Usage | None = None
            finish_reason: str | None = None
            async for data in self._sse_data(response):
                if data == "[DONE]":
                    break
                chunk = self._parse_chunk(data)
                usage = parse_usage(chunk.get("usage")) or usage
                choices = chunk.get("choices")
                if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                    continue  # e.g. the final usage-only chunk, which has `choices: []`
                choice = choices[0]
                finish_reason = choice.get("finish_reason") or finish_reason
                delta = choice.get("delta")
                content = delta.get("content") if isinstance(delta, dict) else None
                if isinstance(content, str) and content:
                    yield "delta", {"content": content}

            yield "usage", usage.model_dump() if usage else None
            yield "done", {"finish_reason": finish_reason}
        finally:
            await response.aclose()

    async def _open_stream(self, payload: dict[str, Any]) -> httpx.Response:
        request = self._http.build_request("POST", "/chat/completions", json=payload)
        try:
            return await self._http.send(request, stream=True)
        except httpx.TransportError as error:
            raise self._transport_error(error) from None

    async def _sse_data(self, response: httpx.Response) -> AsyncIterator[str]:
        """Yield the `data` of each upstream SSE event; comments and other fields are skipped."""
        lines: list[str] = []
        try:
            async for line in response.aiter_lines():
                if line == "":
                    if lines:
                        yield "\n".join(lines)
                        lines = []
                elif line.startswith("data:"):
                    value = line[5:]
                    lines.append(value[1:] if value.startswith(" ") else value)
        except httpx.TransportError as error:
            raise self._transport_error(error) from None
        # Some servers end the body without the blank line that closes the last event.
        if lines:
            yield "\n".join(lines)

    def _parse_chunk(self, data: str) -> dict[str, Any]:
        try:
            chunk = json.loads(data)
        except ValueError:
            chunk = None
        if not isinstance(chunk, dict):
            logger.warning("Unreadable chunk in LLM stream: %s", self._scrub(data[:300]))
            raise LLMError(502, "bad_upstream_response", "The model server sent a stream chunk that could not be read.")

        # Some providers (OpenRouter, for one) report failures inside the stream after a 200 status.
        error = chunk.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else error
            detail = self._scrub(str(message or "unknown error"))
            logger.warning("LLM server reported an error mid-stream: %s", detail)
            raise LLMError(502, "upstream_error", f"The model provider reported an error: {detail}")
        return chunk

    def _parse_completion(self, response: httpx.Response, payload: dict[str, Any]) -> Completion:
        try:
            data = response.json()
            choice = data["choices"][0]
            content = choice["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            logger.warning("Unexpected LLM response body: %s", self._scrub(response.text[:500]))
            raise LLMError(
                502, "bad_upstream_response", "The model server returned a response that could not be read."
            ) from None

        return Completion(
            content=content,
            usage=parse_usage(data.get("usage")),
            model=data.get("model") or payload["model"],
            finish_reason=choice.get("finish_reason"),
        )

    def _transport_error(self, error: httpx.TransportError) -> LLMError:
        if isinstance(error, httpx.TimeoutException):
            logger.warning("LLM request timed out after %ss", self._settings.llm_timeout_seconds)
            return LLMError(
                504,
                "upstream_timeout",
                f"The model did not answer within {self._settings.llm_timeout_seconds:g} seconds.",
            )
        if isinstance(error, httpx.ConnectError):
            logger.warning("Could not reach the LLM server: %s", type(error).__name__)
            return LLMError(
                502,
                "upstream_unreachable",
                "Could not connect to the model server. Check LLM_BASE_URL and that the server is running.",
            )
        logger.warning("Connection to the LLM server failed: %s", type(error).__name__)
        return LLMError(502, "upstream_unreachable", "The connection to the model server was lost.")

    def _error_from_response(self, response: httpx.Response) -> LLMError:
        status = response.status_code
        detail = self._scrub(self._upstream_message(response))
        logger.warning("LLM server returned HTTP %s: %s", status, detail)

        # Auth errors get a fixed message: some providers echo part of the key back.
        if status in (401, 403):
            return LLMError(
                502, "invalid_api_key", "The model provider rejected the API key. Check LLM_API_KEY in .env."
            )
        if status == 404:
            return LLMError(
                502,
                "model_not_found",
                f"The model provider returned 404 for model '{self._settings.llm_model}'. "
                "Check LLM_MODEL, and that LLM_BASE_URL includes the API path (usually ending in /v1).",
            )
        if status == 429:
            return LLMError(429, "rate_limited", f"The model provider is rate limiting requests: {detail}")
        return LLMError(502, "upstream_error", f"The model provider returned HTTP {status}: {detail}")

    @staticmethod
    def _upstream_message(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text[:300] or response.reason_phrase
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str):
            return error
        return response.text[:300]

    def _scrub(self, text: str) -> str:
        return text.replace(self._settings.llm_api_key, "***")
