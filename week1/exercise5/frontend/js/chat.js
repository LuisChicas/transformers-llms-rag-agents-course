import { renderMarkdown } from "./markdown.js";
import { formatNumber, h } from "./dom.js";

const AVATAR_SVG = `<svg class="avatar" viewBox="0 0 34 34" aria-hidden="true">
  <path d="M17 3 C 26 3, 31 9, 31 17 S 25 31, 17 31 S 3 26, 3 17 S 8 3, 17 3 Z" fill="var(--sun-500)" stroke="var(--ink)" stroke-width="2.5"/>
  <circle cx="12.5" cy="15" r="2" fill="var(--ink)"/><circle cx="21.5" cy="15" r="2" fill="var(--ink)"/>
  <path d="M12 21 q 5 4 10 0" fill="none" stroke="var(--ink)" stroke-width="2.2" stroke-linecap="round"/>
</svg>`;

const ERROR_ICON_SVG = `<svg class="error-icon" viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
  <path d="M12 3 L22 20 H2 Z" fill="currentColor" stroke="var(--ink)" stroke-width="2" stroke-linejoin="round"/>
  <path d="M12 9 v5" stroke="var(--white)" stroke-width="2.5" stroke-linecap="round"/>
  <circle cx="12" cy="17" r="1.4" fill="var(--white)"/>
</svg>`;

/** Owns the transcript DOM: bubbles, streaming replies, and error bubbles. */
export class ChatView {
  constructor(transcript) {
    this.transcript = transcript;
    this.welcome = transcript.querySelector("#welcome").cloneNode(true);
  }

  /** Empty the transcript and show the welcome card again. */
  clear() {
    this.transcript.replaceChildren(this.welcome.cloneNode(true));
  }

  /**
   * @param {{text: string, images: {src: string, alt: string}[]}} message
   * @returns {HTMLElement} the turn, so it can be marked as not sent later
   */
  addUser({ text, images = [] }) {
    this.#removeWelcome();
    // A new message makes any earlier Retry obsolete.
    for (const error of this.transcript.querySelectorAll(".turn-error")) error.remove();
    const bubble = h("div", { class: "bubble bubble-user" },
      images.length
        ? h("div", { class: "bubble-images" }, ...images.map(({ src, alt }) => h("img", { src, alt, loading: "lazy" })))
        : null,
      text ? h("div", { class: "bubble-text" }, text) : null,
    );
    const turn = h("div", { class: "turn turn-user" }, bubble);
    this.#append(turn);
    return turn;
  }

  /** Flag a user message that the backend did not save, e.g. because its reply failed. */
  setUnsent(turn, unsent) {
    turn.classList.toggle("turn-unsent", unsent);
    turn.querySelector(".unsent-flag")?.remove();
    if (unsent) turn.querySelector(".bubble").append(h("span", { class: "meta-flag unsent-flag" }, "Not sent"));
  }

  /** Show a finished reply at once, e.g. when opening a saved conversation. */
  addReply(text, usage, options) {
    this.startReply().finish(text, usage, options);
  }

  /**
   * Add an assistant reply that fills in as text arrives. The thinking indicator shows until
   * the first text; after that the Markdown is re-rendered at most once per animation frame,
   * so long replies stay smooth however fast the deltas come.
   */
  startReply() {
    const bubble = h("div", { class: "bubble bubble-assistant streaming" }, thinkingDots());
    const content = h("div", { class: "markdown" });
    const turn = this.#assistantTurn(bubble);
    this.#append(turn);

    let text = "";
    let frame = 0;
    const render = () => {
      frame = 0;
      // Follow the reply only if the reader hasn't scrolled up to read something else.
      const follow = this.#isNearBottom();
      if (!content.isConnected) bubble.replaceChildren(content);
      renderMarkdown(content, text);
      if (follow) this.#scrollToBottom();
    };

    return {
      /** @param {string} fullText the whole reply so far */
      update(fullText) {
        text = fullText;
        if (!frame) frame = requestAnimationFrame(render);
      },
      finish(fullText, usage, { stopped = false, finishReason = null } = {}) {
        cancelAnimationFrame(frame);
        text = fullText;
        render();
        bubble.classList.remove("streaming");
        bubble.append(usageFooter(usage, { stopped, finishReason }));
      },
      remove() {
        cancelAnimationFrame(frame);
        turn.remove();
      },
    };
  }

  addError(message, onRetry) {
    const bubble = h("div", { class: "bubble bubble-error", role: "alert" });
    bubble.innerHTML = ERROR_ICON_SVG;
    bubble.append(h("div", { class: "error-text" }, h("strong", {}, "Something went wrong. "), message));
    this.#addRetryTurn(bubble, onRetry, "btn-secondary");
  }

  /** A neutral note with a Retry button, e.g. when the user stopped a reply before any text arrived. */
  addNotice(message, onRetry) {
    const bubble = h("div", { class: "bubble bubble-notice", role: "status" },
      h("div", { class: "error-text" }, message));
    this.#addRetryTurn(bubble, onRetry, "btn-ghost");
  }

  /** `onRetry` may be null when there is nothing to retry; the bubble then has no button. */
  #addRetryTurn(bubble, onRetry, buttonClass) {
    const turn = this.#assistantTurn(bubble);
    turn.classList.add("turn-error");
    this.#append(turn);
    if (!onRetry) return;
    const retry = h("button", { type: "button", class: `btn ${buttonClass} btn-sm` }, "Retry");
    bubble.append(retry);
    retry.addEventListener("click", () => {
      turn.remove();
      onRetry();
    });
    retry.focus({ preventScroll: true });
  }

  #assistantTurn(bubble) {
    const turn = h("div", { class: "turn turn-assistant" });
    turn.innerHTML = AVATAR_SVG;
    turn.append(bubble);
    return turn;
  }

  #append(node) {
    this.transcript.append(node);
    this.#scrollToBottom();
  }

  #scrollToBottom() {
    this.transcript.scrollTop = this.transcript.scrollHeight;
  }

  #isNearBottom() {
    const { scrollTop, scrollHeight, clientHeight } = this.transcript;
    return scrollHeight - scrollTop - clientHeight < 80;
  }

  #removeWelcome() {
    this.transcript.querySelector("#welcome")?.remove();
  }
}

function thinkingDots() {
  return h("div", { class: "thinking", role: "status" },
    h("span"), h("span"), h("span"),
    h("span", { class: "visually-hidden" }, "The model is thinking…"),
  );
}

function usageFooter(usage, { stopped = false, finishReason = null } = {}) {
  const footer = h("div", { class: "bubble-meta" });
  if (stopped) {
    footer.append(h("span", { class: "meta-flag" }, "Stopped"));
  } else if (finishReason === "length") {
    footer.append(h("span", { class: "meta-flag" }, "Cut off: the model reached its output limit"));
  }

  if (usage) {
    footer.append(
      h("span", { class: "token-chip prompt" }, `${formatNumber(usage.prompt_tokens)} prompt`),
      h("span", { class: "token-chip completion" }, `${formatNumber(usage.completion_tokens)} completion`),
      h("span", {}, `${formatNumber(usage.total_tokens)} total tokens`),
    );
  } else if (stopped) {
    footer.append(h("span", {}, "Usage is only reported when a reply finishes"));
  } else {
    footer.append(h("span", {}, "Usage not reported by the provider"));
  }
  return footer;
}

/**
 * Enter sends, Shift+Enter adds a line, and the textarea grows with its content.
 * `onSubmit(text)` is called even with empty text, since a message may be only an image;
 * it returns false to keep the text in the input.
 */
export function setupComposer(form, textarea, onSubmit) {
  const resize = () => {
    textarea.style.height = "auto";
    textarea.style.height = `${textarea.scrollHeight + 5}px`;
  };

  textarea.addEventListener("input", resize);
  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = textarea.value.trim();
    if (onSubmit(text) !== false) {
      textarea.value = "";
      resize();
    }
  });
}
