import { h } from "./dom.js";

export const UNTITLED = "New chat";

const PENCIL_ICON = `<svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
  <path d="M4 16 l1-4 L13.5 3.5 a1.6 1.6 0 0 1 2.3 0 l0.7 0.7 a1.6 1.6 0 0 1 0 2.3 L8 15 Z" fill="var(--sun-500)" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
</svg>`;

const TRASH_ICON = `<svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
  <path d="M5 6 h10 l-1 11 H6 Z" fill="var(--coral-200)" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
  <path d="M3.5 6 h13 M8 3.5 h4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
</svg>`;

/** Path of a conversation in the page's URL hash, so the open chat survives a reload. */
export function conversationHash(id) {
  return `#/c/${encodeURIComponent(id)}`;
}

export function conversationIdFromHash(hash) {
  const match = /^#\/c\/([^/]+)$/.exec(hash);
  return match ? decodeURIComponent(match[1]) : null;
}

/**
 * The list of saved conversations. Opening one is a plain link to its hash; renaming and
 * deleting are handed to the callbacks, which return promises.
 */
export class HistorySidebar {
  constructor(list, { onRename, onDelete }) {
    this.list = list;
    this.onRename = onRename;
    this.onDelete = onDelete;
    this.conversations = [];
    this.activeId = null;
    this.status = null; // a message shown instead of the list, e.g. while loading
  }

  setConversations(conversations) {
    this.conversations = conversations;
    this.status = null;
    this.render();
  }

  setStatus(message, onRetry = null) {
    this.status = { message, onRetry };
    this.render();
  }

  /** Add a conversation or update it, keeping the list sorted newest first. */
  upsert(conversation) {
    this.conversations = [conversation, ...this.conversations.filter((c) => c.id !== conversation.id)]
      .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
    this.render();
  }

  remove(id) {
    this.conversations = this.conversations.filter((c) => c.id !== id);
    this.render();
  }

  setActive(id) {
    this.activeId = id;
    for (const item of this.list.querySelectorAll(".conv-item")) {
      const active = item.dataset.id === id;
      item.classList.toggle("active", active);
      const link = item.querySelector(".conv-link");
      if (active) link?.setAttribute("aria-current", "page");
      else link?.removeAttribute("aria-current");
    }
  }

  render() {
    if (this.status) {
      const { message, onRetry } = this.status;
      const retry = onRetry ? h("button", { type: "button", class: "btn btn-ghost btn-xs" }, "Retry") : null;
      retry?.addEventListener("click", onRetry);
      this.list.replaceChildren(h("li", { class: "conv-empty" }, h("p", { class: "muted" }, message), retry));
      return;
    }
    if (this.conversations.length === 0) {
      this.list.replaceChildren(h("li", { class: "conv-empty" },
        h("p", { class: "muted" }, "No saved chats yet. Send a message to start one.")));
      return;
    }
    this.list.replaceChildren(...this.conversations.map((conversation) => this.#item(conversation)));
    this.setActive(this.activeId);
  }

  #item(conversation) {
    const title = conversation.title || UNTITLED;
    const rename = iconButton(PENCIL_ICON, `Rename “${title}”`);
    const remove = iconButton(TRASH_ICON, `Delete “${title}”`);
    const item = h("li", { class: "conv-item", "data-id": conversation.id },
      h("a", { class: "conv-link", href: conversationHash(conversation.id) },
        h("span", { class: `conv-title ${conversation.title ? "" : "untitled"}` }, title),
        h("time", { class: "conv-time", datetime: conversation.updated_at }, relativeTime(conversation.updated_at)),
      ),
      h("div", { class: "conv-actions" }, rename, remove),
    );
    rename.addEventListener("click", () => this.#startRename(item, conversation));
    remove.addEventListener("click", async () => {
      if (!window.confirm(`Delete “${title}”? Its messages and images are removed for good.`)) return;
      remove.disabled = true;
      try {
        await this.onDelete(conversation.id);
      } catch (error) {
        remove.disabled = false;
        window.alert(error.message);
      }
    });
    return item;
  }

  #startRename(item, conversation) {
    const input = h("input", {
      type: "text",
      class: "conv-rename",
      value: conversation.title ?? "",
      maxlength: 200,
      placeholder: UNTITLED,
      "aria-label": "Conversation title",
    });
    item.replaceChildren(input);
    input.focus();
    input.select();

    let finished = false;
    const finish = async (save) => {
      if (finished) return;
      finished = true;
      const title = input.value.trim();
      if (save && title && title !== conversation.title) {
        input.disabled = true;
        try {
          await this.onRename(conversation.id, title);
          return; // the callback updates the list
        } catch (error) {
          window.alert(error.message);
        }
      }
      this.render();
    };
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        finish(true);
      } else if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        finish(false);
      }
    });
    input.addEventListener("blur", () => finish(true));
  }
}

function iconButton(svg, label) {
  const button = h("button", { type: "button", class: "conv-action", "aria-label": label, title: label });
  button.innerHTML = svg;
  return button;
}

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
const dateFormat = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

function relativeTime(iso) {
  const date = new Date(iso);
  const seconds = (date.getTime() - Date.now()) / 1000;
  const abs = Math.abs(seconds);
  if (abs < 60) return "just now";
  if (abs < 3600) return relative.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return relative.format(Math.round(seconds / 3600), "hour");
  if (abs < 7 * 86400) return relative.format(Math.round(seconds / 86400), "day");
  return dateFormat.format(date);
}
