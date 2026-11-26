import { API_BASE_URL } from "../config.js";
import { readServerSentEvents } from "./sse.js";

export class ApiError extends Error {
  constructor(message, { status = null, code = null } = {}) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

/** fetch() that turns network failures and non-2xx responses into ApiError. Aborts pass through. */
async function send(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, options);
  } catch (error) {
    if (error.name === "AbortError") throw error;
    // fetch() rejects without detail for both "server down" and "blocked by CORS".
    throw new ApiError(
      `Could not reach the backend at ${API_BASE_URL}. Check that it is running and that CORS_ORIGINS includes ${window.location.origin}.`,
      { code: "network" },
    );
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const message = body?.error?.message ?? `The backend returned HTTP ${response.status}.`;
    throw new ApiError(message, { status: response.status, code: body?.error?.code ?? null });
  }
  return response;
}

async function request(path, options = {}) {
  const response = await send(path, options);
  if (response.status === 204) return null;
  try {
    return await response.json();
  } catch {
    throw new ApiError("The backend returned a response that could not be read.", { status: response.status });
  }
}

export function getConfig() {
  return request("/api/config");
}

function json(method, body, signal) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  };
}

/** A path from the backend (such as an attachment URL) as an absolute URL. */
export function backendUrl(path) {
  return `${API_BASE_URL}${path}`;
}

/**
 * @param {{role: string, content: string | object[]}[]} messages content is a string, or
 *   OpenAI content parts when the message has images
 */
export function sendChat(messages, { signal } = {}) {
  return request("/api/chat", json("POST", { messages }, signal));
}

/**
 * Stream a reply from /api/chat/stream. Yields { event, data } with `data` already parsed:
 * context, delta (many), usage, then done. An `error` event is thrown as ApiError.
 */
export function streamChat(messages, { signal } = {}) {
  return streamEvents("/api/chat/stream", { messages }, signal);
}

async function* streamEvents(path, body, signal) {
  const response = await send(path, json("POST", body, signal));
  for await (const { event, data } of readServerSentEvents(response.body)) {
    let parsed;
    try {
      parsed = JSON.parse(data);
    } catch {
      throw new ApiError("The backend sent a stream event that could not be read.");
    }
    if (event === "error") {
      throw new ApiError(parsed?.message ?? "The stream failed.", { status: parsed?.status, code: parsed?.code });
    }
    yield { event, data: parsed };
  }
}

/** Stage 1 (no streaming), adapted to the same events so the UI has a single code path. */
export async function* chatAsEvents(messages, { signal } = {}) {
  const response = await sendChat(messages, { signal });
  yield { event: "context", data: { request: response.request } };
  yield { event: "delta", data: { content: response.message.content } };
  yield { event: "usage", data: response.usage };
  yield { event: "done", data: { finish_reason: response.finish_reason } };
}

/* ---------- Stage 4: saved conversations ---------- */

export function listConversations() {
  return request("/api/conversations");
}

export function createConversation() {
  return request("/api/conversations", { method: "POST" });
}

export function getConversation(id) {
  return request(`/api/conversations/${encodeURIComponent(id)}`);
}

export function renameConversation(id, title) {
  return request(`/api/conversations/${encodeURIComponent(id)}`, json("PATCH", { title }));
}

export function deleteConversation(id) {
  return request(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/**
 * Send a message to a saved conversation and stream the reply. Same events as streamChat,
 * plus `saved` ({ conversation }) once both messages are stored, just before `done`.
 */
export function streamMessage(conversationId, content, { signal } = {}) {
  return streamEvents(`/api/conversations/${encodeURIComponent(conversationId)}/messages`, { content }, signal);
}
