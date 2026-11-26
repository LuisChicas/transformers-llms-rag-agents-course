import { HISTORY, MAX_IMAGES_PER_MESSAGE, STREAMING } from "../config.js";
import {
  backendUrl,
  chatAsEvents,
  createConversation,
  deleteConversation,
  getConfig,
  getConversation,
  listConversations,
  renameConversation,
  streamChat,
  streamMessage,
} from "./api.js";
import { AttachmentTray } from "./attachments.js";
import { ChatView, setupComposer } from "./chat.js";
import { ContextViewer } from "./context-viewer.js";
import { HistorySidebar, conversationHash, conversationIdFromHash } from "./history.js";
import { createPanelGroup } from "./panels.js";

const state = {
  config: null,
  // Stage 4: the saved conversation on screen, or null for a new chat that is created on its first message.
  conversationId: null,
  // Without HISTORY the client owns the conversation and sends the whole history each turn.
  messages: [],
  turns: [], // one entry per reply: { usage, stopped, images }
  lastRequest: null,
  pending: null, // the AbortController of the reply in progress
  loading: false, // a saved conversation is being fetched
  // Bumped whenever the conversation on screen changes, so work still finishing for the
  // previous one (a reply, a fetch) knows to leave the screen alone.
  epoch: 0,
};

const chat = new ChatView(document.querySelector("#transcript"));
const viewer = new ContextViewer(document.querySelector("#context-panel"));
const sendButton = document.querySelector("#send-button");
const input = document.querySelector("#message-input");
const modelBadge = document.querySelector("#model-badge");

const attachments = new AttachmentTray({
  button: document.querySelector("#attach-button"),
  fileInput: document.querySelector("#attach-input"),
  tray: document.querySelector("#attachment-tray"),
  dropZone: document.querySelector(".chat"),
  composer: document.querySelector("#composer"),
  textarea: input,
  errorBox: document.querySelector("#composer-error"),
  maxImages: MAX_IMAGES_PER_MESSAGE,
});

const sidebar = new HistorySidebar(document.querySelector("#conversation-list"), {
  async onRename(id, title) {
    sidebar.upsert(await renameConversation(id, title));
  },
  async onDelete(id) {
    await deleteConversation(id);
    sidebar.remove(id);
    if (id === state.conversationId) {
      clearHash();
      startNewChat();
    }
  },
});

setupComposer(document.querySelector("#composer"), input, (text) => {
  if (state.pending || state.loading || attachments.busy) return false;
  if (!text && attachments.count === 0) return false;

  const images = attachments.take();
  // A message with images uses OpenAI content parts; a text-only message stays a plain string.
  const content = images.length
    ? [
      ...(text ? [{ type: "text", text }] : []),
      ...images.map((image) => ({ type: "image_url", image_url: { url: image.url } })),
    ]
    : text;

  const turn = chat.addUser({ text, images: images.map((image) => ({ src: image.url, alt: image.name })) });
  if (!HISTORY) state.messages.push({ role: "user", content });
  requestReply({ content, turn, images: images.length });
  return true;
});

// While a reply is streaming, the Send button becomes Stop.
sendButton.addEventListener("click", (event) => {
  if (!state.pending) return;
  event.preventDefault();
  state.pending.abort();
});

document.querySelector("#new-chat").addEventListener("click", () => {
  clearHash();
  startNewChat();
});

/** @param {{content: string | object[], turn: HTMLElement, images: number}} message */
async function requestReply(message) {
  const epoch = state.epoch;
  const controller = new AbortController();
  setPending(controller);
  const reply = chat.startReply();

  let content = "";
  let usage = null;
  let finishReason = null;
  let done = false;
  try {
    let events;
    if (HISTORY) {
      if (!state.conversationId) {
        const conversation = await createConversation();
        if (epoch !== state.epoch) return;
        state.conversationId = conversation.id;
        sidebar.upsert(conversation);
        sidebar.setActive(conversation.id);
        window.history.replaceState(null, "", conversationHash(conversation.id));
      }
      events = streamMessage(state.conversationId, message.content, { signal: controller.signal });
    } else {
      events = (STREAMING ? streamChat : chatAsEvents)(state.messages, { signal: controller.signal });
    }

    for await (const { event, data } of events) {
      if (epoch !== state.epoch) return;
      if (event === "context") {
        // Shown right away, so the payload is visible while the reply is still arriving.
        state.lastRequest = data.request;
        viewer.render(state);
      } else if (event === "delta") {
        content += data.content;
        reply.update(content);
      } else if (event === "usage") {
        usage = data;
      } else if (event === "saved") {
        sidebar.upsert(data.conversation);
      } else if (event === "done") {
        finishReason = data.finish_reason;
        done = true;
      }
    }
    if (!done) throw new Error("The connection to the backend closed before the reply finished.");
    completeTurn(reply, content, usage, { finishReason, images: message.images });
    if (HISTORY) chat.setUnsent(message.turn, false);
  } catch (error) {
    if (epoch !== state.epoch) return;
    const retry = () => requestReply(message);
    if (controller.signal.aborted && content) {
      // A stopped reply keeps the text that already arrived and stays in the history.
      completeTurn(reply, content, null, { stopped: true, images: message.images });
      if (HISTORY) {
        chat.setUnsent(message.turn, false);
        refreshSidebarSoon();
      }
      return;
    }
    // The partial text is dropped and the user message stays last, so Retry resends it.
    // With HISTORY the backend saved nothing, so the message is flagged as not sent.
    reply.remove();
    if (HISTORY) chat.setUnsent(message.turn, true);
    if (controller.signal.aborted) {
      chat.addNotice("Stopped before the model replied.", retry);
    } else {
      chat.addError(error.message, retry);
    }
  } finally {
    if (epoch === state.epoch) setPending(null);
  }
}

function completeTurn(reply, content, usage, { images = 0, ...options }) {
  reply.finish(content, usage, options);
  if (!HISTORY) state.messages.push({ role: "assistant", content });
  state.turns.push({ usage, stopped: Boolean(options.stopped), images });
  viewer.render(state);
}

function setPending(controller) {
  state.pending = controller;
  const streaming = Boolean(controller);
  sendButton.classList.toggle("btn-primary", !streaming);
  sendButton.classList.toggle("btn-stop", streaming);
  sendButton.innerHTML = streaming ? `${STOP_ICON}Stop` : "Send";
  if (!streaming) input.focus({ preventScroll: true });
}

const STOP_ICON = `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
  <rect x="2" y="2" width="12" height="12" rx="2.5" fill="currentColor"/>
</svg>`;

/* ---------- Switching conversations ---------- */

/** Leave the conversation on screen: stop its reply and forget its state. */
function resetConversation() {
  state.epoch += 1;
  if (state.pending) {
    state.pending.abort();
    setPending(null);
    // The backend keeps a reply stopped this way; the sidebar picks up its new timestamp.
    if (HISTORY) refreshSidebarSoon();
  }
  state.conversationId = null;
  state.messages = [];
  state.turns = [];
  state.lastRequest = null;
  state.loading = false;
  chat.clear();
}

function startNewChat() {
  resetConversation();
  sidebar.setActive(null);
  viewer.render(state);
  input.focus();
}

async function openConversation(id) {
  resetConversation();
  const epoch = state.epoch;
  state.conversationId = id;
  state.loading = true;
  sidebar.setActive(id);
  viewer.render(state);
  if (historyPanel?.isSlideOver()) historyPanel.setVisible(false);

  try {
    const conversation = await getConversation(id);
    if (epoch !== state.epoch) return;
    showSavedConversation(conversation);
  } catch (error) {
    if (epoch !== state.epoch) return;
    if (error.status === 404) {
      sidebar.remove(id);
      clearHash();
      startNewChat();
      chat.addError("That conversation no longer exists.", null);
    } else {
      chat.addError(error.message, () => openConversation(id));
    }
  } finally {
    if (epoch === state.epoch) state.loading = false;
  }
}

function showSavedConversation(conversation) {
  chat.clear();
  let imagesInLastMessage = 0;
  for (const message of conversation.messages) {
    if (message.role === "user") {
      const display = displayContent(message.content);
      chat.addUser(display);
      imagesInLastMessage = display.images.length;
      continue;
    }
    const usage = message.prompt_tokens !== null && message.completion_tokens !== null
      ? {
        prompt_tokens: message.prompt_tokens,
        completion_tokens: message.completion_tokens,
        total_tokens: message.prompt_tokens + message.completion_tokens,
      }
      : null;
    const stopped = message.finish_reason === "stopped";
    chat.addReply(message.content, usage, { stopped, finishReason: message.finish_reason });
    state.turns.push({ usage, stopped, images: imagesInLastMessage });
  }
  viewer.render(state);
}

/** Saved message content as text plus image sources for a chat bubble. */
function displayContent(content) {
  if (typeof content === "string") return { text: content, images: [] };
  const text = content.filter((part) => part.type === "text").map((part) => part.text).join("\n");
  const images = content
    .filter((part) => part.type === "image")
    .map((part) => ({ src: backendUrl(part.url), alt: "Attached image" }));
  return { text, images };
}

function clearHash() {
  if (window.location.hash) window.history.pushState(null, "", window.location.pathname + window.location.search);
}

window.addEventListener("hashchange", () => {
  if (!HISTORY) return;
  const id = conversationIdFromHash(window.location.hash);
  if (id && id !== state.conversationId) openConversation(id);
  else if (!id && state.conversationId) startNewChat();
});

async function loadSidebar() {
  sidebar.setStatus("Loading chats…");
  try {
    sidebar.setConversations(await listConversations());
  } catch (error) {
    sidebar.setStatus(error.message, loadSidebar);
  }
}

// The backend saves a stopped reply when it notices the disconnect, a moment after the
// browser aborts, so the list is fetched again shortly afterwards.
function refreshSidebarSoon() {
  setTimeout(async () => {
    try {
      sidebar.setConversations(await listConversations());
    } catch {
      // The next successful action refreshes the list anyway.
    }
  }, 600);
}

async function loadConfig() {
  try {
    state.config = await getConfig();
    modelBadge.textContent = state.config.model;
    modelBadge.classList.remove("offline");
    attachments.setEnabled(state.config.supports_vision, state.config.max_image_mb);
  } catch (error) {
    modelBadge.textContent = "backend offline";
    modelBadge.classList.add("offline");
    chat.addError(error.message, loadConfig);
  }
  viewer.render(state);
}

/* ---------- Side panels ---------- */

const panels = createPanelGroup(document.querySelector("#panel-backdrop"));
panels.add({
  name: "context",
  toggle: document.querySelector("#context-toggle"),
  closeButton: document.querySelector("#context-close"),
  query: "(max-width: 960px)",
});
const historyPanel = HISTORY
  ? panels.add({
    name: "history",
    toggle: document.querySelector("#history-toggle"),
    closeButton: document.querySelector("#history-close"),
    query: "(max-width: 1240px)",
  })
  : null;

document.querySelector("#conversation-list").addEventListener("click", (event) => {
  if (event.target.closest(".conv-link") && historyPanel?.isSlideOver()) historyPanel.setVisible(false);
});

/* ---------- Start ---------- */

document.body.classList.toggle("no-history", !HISTORY);
viewer.render(state);
await loadConfig();
if (HISTORY) {
  loadSidebar();
  const id = conversationIdFromHash(window.location.hash);
  if (id) openConversation(id);
}
input.focus();
