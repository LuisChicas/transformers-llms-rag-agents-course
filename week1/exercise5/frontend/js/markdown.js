import { marked } from "../vendor/marked.esm.js";
import DOMPurify from "../vendor/purify.es.js";

marked.setOptions({ gfm: true, breaks: false });

// Links from model output open in a new tab without giving that tab access to this page.
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A" && node.hasAttribute("href")) {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

/**
 * Render untrusted Markdown into `element`. The HTML from marked is always sanitized
 * before it touches the DOM.
 */
export function renderMarkdown(element, text) {
  element.innerHTML = DOMPurify.sanitize(marked.parse(text));
  addCodeBlockChrome(element);
}

// The Copy buttons are added after sanitizing so they never pass through model-controlled HTML.
function addCodeBlockChrome(root) {
  for (const pre of root.querySelectorAll("pre")) {
    const code = pre.querySelector("code");
    const language = [...(code?.classList ?? [])]
      .find((name) => name.startsWith("language-"))
      ?.slice("language-".length);

    const wrapper = document.createElement("div");
    wrapper.className = "code-block";

    const head = document.createElement("div");
    head.className = "code-block-head";

    const label = document.createElement("span");
    label.className = "code-lang";
    label.textContent = language || "code";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-accent btn-xs";
    button.textContent = "Copy";
    button.addEventListener("click", () => copyText((code ?? pre).textContent, button));

    head.append(label, button);
    pre.replaceWith(wrapper);
    wrapper.append(head, pre);
  }
}

async function copyText(text, button) {
  let copied = false;
  try {
    await navigator.clipboard.writeText(text);
    copied = true;
  } catch {
    // The Clipboard API needs a secure context; fall back to a hidden textarea.
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    copied = document.execCommand("copy");
    area.remove();
  }
  button.textContent = copied ? "Copied!" : "Copy failed";
  setTimeout(() => {
    button.textContent = "Copy";
  }, 1500);
}
