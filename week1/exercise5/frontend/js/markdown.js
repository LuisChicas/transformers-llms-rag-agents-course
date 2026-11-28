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
  wrapTables(element);
  fitTables(element);
}

// The wrapper scrolls wide tables sideways and draws the border, so the table itself can keep table layout.
function wrapTables(root) {
  for (const table of root.querySelectorAll("table")) {
    const wrapper = document.createElement("div");
    wrapper.className = "table-scroll";
    table.replaceWith(wrapper);
    wrapper.append(table);
  }
}

/**
 * Stretch every table under `root` that fits at its natural width to the full width of its
 * bubble, with the last column taking the spare room. CSS alone cannot decide this: a 100%
 * width on the last column squeezes the other columns to their narrowest width even when
 * there is room for them, so the stretch is applied only after measuring that the table fits.
 * Call it again whenever the bubbles change width.
 */
export function fitTables(root) {
  const wrappers = [...root.querySelectorAll(".table-scroll")];
  // Remove every stretch first and measure afterwards, so the page is laid out only once.
  for (const wrapper of wrappers) wrapper.classList.remove("table-fill");
  // The 1px slack keeps a table that already spans the bubble, give or take rounding, unstretched.
  const fits = wrappers.map(
    (wrapper) => wrapper.firstElementChild.getBoundingClientRect().width < wrapper.clientWidth - 1,
  );
  wrappers.forEach((wrapper, i) => wrapper.classList.toggle("table-fill", fits[i]));
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
