import { formatNumber, h, s } from "./dom.js";
import { dataUrlInfo, imagePlaceholder } from "./images.js";

const WARN_RATIO = 0.8;

const WARN_ICON = `<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
  <path d="M10 2 L19 18 H1 Z" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linejoin="round"/>
  <path d="M10 8 v4" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>
  <circle cx="10" cy="15" r="1.2" fill="currentColor"/>
</svg>`;

/**
 * Renders the context viewer from the conversation state:
 *   { config, turns: [{ usage, stopped, images }], lastRequest }
 * Every render rebuilds each section from scratch; the data is small.
 */
export class ContextViewer {
  constructor(root) {
    this.root = root;
    this.chartView = "bars";
    this.showRaw = false;
    this.state = null;

    for (const button of root.querySelectorAll("[data-chart]")) {
      button.addEventListener("click", () => {
        this.chartView = button.dataset.chart;
        for (const other of root.querySelectorAll("[data-chart]")) {
          other.setAttribute("aria-pressed", String(other === button));
        }
        this.#renderChart();
      });
    }

    const rawToggle = root.querySelector("#raw-toggle");
    rawToggle.addEventListener("click", () => {
      this.showRaw = !this.showRaw;
      rawToggle.setAttribute("aria-pressed", String(this.showRaw));
      this.#renderMessages();
    });
  }

  render(state) {
    this.state = state;
    this.#renderTotals();
    this.#renderContextWindow();
    this.#renderLastTurn();
    this.#renderChart();
    this.#renderMessages();
  }

  #renderTotals() {
    const { turns, lastRequest } = this.state;
    const totalTokens = turns.reduce((sum, turn) => sum + (turn.usage?.total_tokens ?? 0), 0);
    const unreported = turns.filter((turn) => !turn.usage).length;

    const container = this.root.querySelector("#totals");
    container.replaceChildren(
      stat("Turns", formatNumber(turns.length)),
      stat("Total tokens", formatNumber(totalTokens), { highlight: true }),
      stat("Messages sent", formatNumber(lastRequest?.messages.length ?? 0)),
    );
    this.root.querySelector("#totals-note")?.remove();
    if (unreported) {
      container.after(note(`${unreported} turn(s) have no usage (stopped early or not reported) and are not counted.`, "totals-note"));
    }
  }

  #renderContextWindow() {
    const container = this.root.querySelector("#context-window");
    const windowSize = this.state.config?.context_window;
    if (!windowSize) {
      container.replaceChildren(note("Set LLM_CONTEXT_WINDOW in .env to see how full the context is."));
      return;
    }

    const last = this.state.turns.at(-1);
    if (last && !last.usage) {
      container.replaceChildren(note(last.stopped
        ? "The last reply was stopped before usage was reported, so the context size is unknown."
        : "The last turn did not report usage, so the context size is unknown."));
      return;
    }

    const used = last?.usage?.prompt_tokens ?? 0;
    const ratio = used / windowSize;
    const level = ratio >= 1 ? "over" : ratio >= WARN_RATIO ? "warn" : "ok";

    const fill = h("div", {
      class: `meter-fill ${level === "ok" ? "" : level} ${used === 0 ? "empty" : ""}`,
      style: `width: ${Math.min(ratio, 1) * 100}%`,
    });
    const meter = h("div", {
      class: "meter",
      role: "meter",
      "aria-label": "Prompt tokens of the last turn compared with the context window",
      "aria-valuemin": 0,
      "aria-valuemax": windowSize,
      "aria-valuenow": Math.min(used, windowSize),
    }, fill);

    const caption = h("div", { class: "meter-caption" },
      h("span", {}, `${formatNumber(used)} / ${formatNumber(windowSize)} tokens`),
      h("strong", {}, `${(ratio * 100).toFixed(ratio > 0 && ratio < 0.01 ? 1 : 0)}%`),
    );

    const children = [meter, caption];
    if (level === "warn") {
      children.push(banner("warn", "The conversation is getting close to the model's context limit."));
    } else if (level === "over") {
      children.push(banner("error",
        "The conversation is larger than the model's context window. Older messages may be ignored or the request may fail."));
    }
    container.replaceChildren(...children);
  }

  #renderLastTurn() {
    const container = this.root.querySelector("#last-turn");
    const last = this.state.turns.at(-1);
    if (!last) {
      container.replaceChildren(note("No turns yet."));
      return;
    }
    if (!last.usage) {
      container.replaceChildren(note(last.stopped
        ? "This reply was stopped. Usage is only reported when a reply finishes."
        : "Usage not reported by the provider for this turn."));
      return;
    }
    const { prompt_tokens, completion_tokens, total_tokens } = last.usage;
    container.replaceChildren(h("dl", { class: "stat-row" },
      stat("Prompt", formatNumber(prompt_tokens), { swatch: "var(--tokens-prompt)" }),
      stat("Completion", formatNumber(completion_tokens), { swatch: "var(--tokens-completion)" }),
      stat("Total", formatNumber(total_tokens), { highlight: true }),
    ));
  }

  #renderChart() {
    const container = this.root.querySelector("#growth-chart");
    const turns = this.state?.turns ?? [];
    if (turns.length === 0) {
      container.replaceChildren(note("Send a message to see the tokens grow with each turn."));
      return;
    }
    const chart = this.chartView === "bars" ? perTurnBars(turns) : runningTotalLine(turns);
    container.replaceChildren(chart, turnsTable(turns));
  }

  #renderMessages() {
    const container = this.root.querySelector("#messages-sent");
    const request = this.state?.lastRequest;
    if (!request) {
      container.replaceChildren(note("The exact payload sent to the model appears here after you send a message."));
      return;
    }
    if (this.showRaw) {
      container.replaceChildren(h("pre", { class: "raw-json" }, JSON.stringify(request, shortenImageData, 2)));
      return;
    }

    const lastIndex = request.messages.length - 1;
    const cards = request.messages.map((message, index) => {
      const parts = typeof message.content === "string" ? [{ type: "text", text: message.content }] : message.content;
      const preview = parts.map((part) => (part.type === "text" ? part.text : imageLabel(part))).join(" ");
      return h("details", { class: `msg-card role-${message.role}`, open: index === lastIndex },
        h("summary", {},
          h("span", { class: "role-chip" }, message.role),
          h("span", { class: "msg-preview" }, preview.replace(/\s+/g, " ")),
          h("span", { class: "msg-index" }, `#${index}`),
        ),
        ...parts.map(partContent),
      );
    });
    container.replaceChildren(h("div", { class: "msg-list" }, ...cards));
  }
}

function imageLabel(part) {
  const url = part.image_url?.url ?? "";
  const info = dataUrlInfo(url);
  return info ? imagePlaceholder(info.mimeType, info.sizeBytes) : `[image · ${url.slice(0, 40)}]`;
}

function partContent(part) {
  if (part.type === "text") return h("pre", { class: "msg-content" }, part.text);
  if (part.type === "image_url") {
    const url = part.image_url?.url ?? "";
    return h("figure", { class: "msg-image" },
      url.startsWith("data:image/") ? h("img", { src: url, alt: "" }) : null,
      h("figcaption", {}, imageLabel(part)),
    );
  }
  return h("pre", { class: "msg-content" }, JSON.stringify(part, null, 2));
}

/** JSON.stringify replacer: base64 image data would be megabytes of noise in the raw view. */
function shortenImageData(key, value) {
  if (key === "url" && typeof value === "string") {
    const info = dataUrlInfo(value);
    if (info) return imagePlaceholder(info.mimeType, info.sizeBytes);
  }
  return value;
}

function stat(label, value, { highlight = false, swatch = null } = {}) {
  return h("div", { class: `stat ${highlight ? "stat-highlight" : ""}` },
    h("dt", {}, swatch ? h("span", { class: "swatch", style: `background: ${swatch}` }) : null, label),
    h("dd", {}, value),
  );
}

function note(text, id = null) {
  return h("p", { class: "muted", id }, text);
}

function banner(kind, text) {
  const element = h("div", { class: `banner banner-${kind}`, role: kind === "error" ? "alert" : "status" });
  element.innerHTML = WARN_ICON;
  element.append(h("span", {}, text));
  return element;
}

/* ---------- Charts (plain SVG) ---------- */

const W = 360;
const H = 190;
const PAD = { top: 14, right: 10, bottom: 24, left: 42 };
const PLOT_W = W - PAD.left - PAD.right;
const PLOT_H = H - PAD.top - PAD.bottom;
const BASELINE = PAD.top + PLOT_H;

/** Round up to 1, 2, 2.5 or 5 times a power of ten so the axis label is a tidy number. */
function niceMax(value) {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].find((m) => m * power >= value);
  return step * power;
}

function compact(value) {
  if (value >= 1_000_000) return `${+(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${+(value / 1_000).toFixed(1)}k`;
  return String(value);
}

function chartFrame(label, maxValue) {
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": label });
  for (const value of [0, maxValue / 2, maxValue]) {
    const y = BASELINE - (value / maxValue) * PLOT_H;
    svg.append(s("text", { class: "axis-label", x: PAD.left - 8, y: y + 4, "text-anchor": "end" }, compact(value)));
  }
  svg.append(s("line", {
    x1: PAD.left, x2: W - PAD.right, y1: BASELINE, y2: BASELINE,
    stroke: "var(--ink)", "stroke-width": 2, "stroke-linecap": "round",
  }));
  return svg;
}

/** Turn numbers under the x axis, thinned out when there are many turns. */
function xLabels(svg, count, xAt) {
  const every = Math.ceil(count / 12);
  for (let i = 0; i < count; i++) {
    const isLast = i === count - 1;
    if (i % every !== 0 && !isLast) continue;
    if (!isLast && count - 1 - i < every / 2) continue; // keep clear of the last label
    svg.append(s("text", {
      class: `axis-label ${isLast ? "current" : ""}`,
      x: xAt(i), y: BASELINE + 16, "text-anchor": "middle",
    }, String(i + 1)));
  }
}

/** Path for a rectangle with only its top corners rounded, so bars sit flat on the baseline. */
function topRoundedRect(x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height);
  return `M${x},${y + height} V${y + r} Q${x},${y} ${x + r},${y} H${x + width - r} Q${x + width},${y} ${x + width},${y + r} V${y + height} Z`;
}

function tooltipLayer(wrap) {
  const tip = h("div", { class: "chart-tooltip", hidden: true });
  wrap.append(tip);
  return {
    show(svg, x, y, ...rows) {
      tip.replaceChildren(...rows);
      tip.hidden = false;
      // SVG coordinates scale with the rendered width; convert them to pixels in the wrapper.
      const scale = svg.getBoundingClientRect().width / W;
      const left = Math.min(Math.max(x * scale, 80), wrap.clientWidth - 80);
      // Near the top of the plot the tooltip would cover the controls above, so it flips below.
      const flip = y * scale < 90;
      tip.classList.toggle("below", flip);
      tip.style.left = `${left}px`;
      tip.style.top = `${flip ? y * scale + 24 : y * scale}px`;
    },
    hide() {
      tip.hidden = true;
    },
  };
}

function missingUsage(turn) {
  return turn.stopped ? "Stopped before usage was reported" : "Usage not reported";
}

function tipRow(label, value, swatch = null) {
  return h("div", { class: "row" },
    h("span", {}, swatch ? h("span", { class: "swatch", style: `background: ${swatch}` }) : null, label),
    h("strong", {}, value),
  );
}

function legend(items) {
  return h("ul", { class: "chart-legend" }, ...items.map(([label, color]) =>
    h("li", {}, color ? h("span", { class: "swatch", style: `background: ${color}` }) : imageIcon(), label)));
}

/** A small picture glyph marking the turns whose message carried an image. */
function imageMarker(cx, bottom) {
  const width = 14;
  const height = 11;
  const x = cx - width / 2;
  const y = bottom - height;
  return s("g", { class: "image-marker", "aria-hidden": "true" },
    s("rect", { x, y, width, height, rx: 2.5, fill: "var(--paper)", stroke: "var(--ink)", "stroke-width": 1.5 }),
    s("path", {
      d: `M${x + 2.5},${y + height - 2.5} L${x + 5.5},${y + 5} L${x + 8},${y + 7.5} L${x + 9.5},${y + 6} L${x + width - 2.5},${y + height - 2.5} Z`,
      fill: "var(--teal-500)", stroke: "var(--ink)", "stroke-width": 1, "stroke-linejoin": "round",
    }),
    s("circle", { cx: x + width - 4, cy: y + 3.5, r: 1.6, fill: "var(--sun-500)" }),
  );
}

function imageIcon() {
  const svg = s("svg", { viewBox: "0 0 16 13", width: 16, height: 13, "aria-hidden": "true" });
  svg.append(imageMarker(8, 12.5));
  return svg;
}

function imagesRow(turn) {
  return turn.images ? [tipRow("Images", formatNumber(turn.images))] : [];
}

function legendItems(turns, items) {
  return turns.some((turn) => turn.images) ? [...items, ["Has an image", null]] : items;
}

function perTurnBars(turns) {
  const maxValue = niceMax(Math.max(...turns.map((t) => t.usage?.total_tokens ?? 0)));
  const svg = chartFrame("Prompt and completion tokens for each turn, stacked", maxValue);
  const slot = PLOT_W / turns.length;
  const barWidth = Math.max(3, Math.min(30, slot * 0.62));
  const xAt = (i) => PAD.left + slot * (i + 0.5);
  const yScale = (value) => (value / maxValue) * PLOT_H;
  const wrap = h("div", { class: "chart-wrap" });
  const tooltip = tooltipLayer(wrap);
  const lastIndex = turns.length - 1;

  // Sun highlight behind the current turn.
  svg.append(s("rect", {
    x: xAt(lastIndex) - Math.min(slot, barWidth + 12) / 2, y: PAD.top - 4,
    width: Math.min(slot, barWidth + 12), height: PLOT_H + 4,
    rx: 6, fill: "var(--sun-200)",
  }));

  turns.forEach((turn, i) => {
    const x = xAt(i) - barWidth / 2;
    if (!turn.usage) {
      svg.append(s("rect", {
        x, y: BASELINE - 14, width: barWidth, height: 14, rx: 3,
        fill: "none", stroke: "var(--ink-muted)", "stroke-width": 1.5, "stroke-dasharray": "3 2",
      }));
    } else {
      const { prompt_tokens: prompt, completion_tokens: completion } = turn.usage;
      const promptH = yScale(prompt);
      const completionH = yScale(completion);
      const gap = completionH > 0 && promptH > 0 ? 2 : 0;
      const hasCompletion = completionH > 0;
      svg.append(s("path", {
        d: hasCompletion
          ? `M${x},${BASELINE} V${BASELINE - promptH} H${x + barWidth} V${BASELINE} Z`
          : topRoundedRect(x, BASELINE - promptH, barWidth, promptH, 4),
        fill: "var(--tokens-prompt)", stroke: "var(--ink)", "stroke-width": 1.5, "stroke-linejoin": "round",
      }));
      if (hasCompletion) {
        svg.append(s("path", {
          d: topRoundedRect(x, BASELINE - promptH - gap - completionH, barWidth, completionH, 4),
          fill: "var(--tokens-completion)", stroke: "var(--ink)", "stroke-width": 1.5, "stroke-linejoin": "round",
        }));
      }
    }
    if (turn.images) {
      const top = turn.usage ? BASELINE - yScale(turn.usage.total_tokens) : BASELINE - 14;
      svg.append(imageMarker(xAt(i), top - 4));
    }

    const hit = s("rect", { class: "chart-hit", x: xAt(i) - slot / 2, y: PAD.top, width: slot, height: PLOT_H });
    hit.addEventListener("pointerenter", () => {
      const top = (turn.usage ? BASELINE - yScale(turn.usage.total_tokens) - 2 : BASELINE - 16) - (turn.images ? 16 : 0);
      tooltip.show(svg, xAt(i), top, h("strong", {}, `Turn ${i + 1}`),
        ...(turn.usage
          ? [
            tipRow("Prompt", formatNumber(turn.usage.prompt_tokens), "var(--tokens-prompt)"),
            tipRow("Completion", formatNumber(turn.usage.completion_tokens), "var(--tokens-completion)"),
            tipRow("Total", formatNumber(turn.usage.total_tokens)),
            ...imagesRow(turn),
          ]
          : [h("div", {}, missingUsage(turn)), ...imagesRow(turn)]));
    });
    hit.addEventListener("pointerleave", () => tooltip.hide());
    svg.append(hit);
  });

  xLabels(svg, turns.length, xAt);
  wrap.prepend(legend(legendItems(turns, [["Prompt", "var(--tokens-prompt)"], ["Completion", "var(--tokens-completion)"]])), svg);
  return wrap;
}

function runningTotals(turns) {
  let sum = 0;
  return turns.map((turn) => {
    sum += turn.usage?.total_tokens ?? 0;
    return sum;
  });
}

function runningTotalLine(turns) {
  const totals = runningTotals(turns);
  const maxValue = niceMax(totals.at(-1));
  const svg = chartFrame("Running total of tokens used in the conversation, one point per turn", maxValue);
  const n = turns.length;
  const inset = 12;
  const xAt = (i) => (n === 1 ? PAD.left + PLOT_W / 2 : PAD.left + inset + ((PLOT_W - inset * 2) * i) / (n - 1));
  const yAt = (value) => BASELINE - (value / maxValue) * PLOT_H;
  const wrap = h("div", { class: "chart-wrap" });
  const tooltip = tooltipLayer(wrap);

  const crosshair = s("line", {
    y1: PAD.top, y2: BASELINE, stroke: "var(--ink-muted)", "stroke-width": 1.5,
    "stroke-dasharray": "4 3", visibility: "hidden",
  });
  svg.append(crosshair);

  if (n > 1) {
    svg.append(s("polyline", {
      points: totals.map((value, i) => `${xAt(i)},${yAt(value)}`).join(" "),
      fill: "none", stroke: "var(--ink)", "stroke-width": 3, "stroke-linejoin": "round", "stroke-linecap": "round",
    }));
  }

  const slot = n === 1 ? PLOT_W : (PLOT_W - inset * 2) / (n - 1);
  totals.forEach((value, i) => {
    const isLast = i === n - 1;
    const reported = Boolean(turns[i].usage);
    svg.append(s("circle", {
      cx: xAt(i), cy: yAt(value), r: isLast ? 6.5 : 5,
      fill: !reported ? "var(--paper)" : isLast ? "var(--sun-500)" : "var(--teal-500)",
      stroke: "var(--ink)", "stroke-width": 2,
    }));
    if (turns[i].images) {
      // Below the point, unless that would run into the x axis.
      const below = yAt(value) + 20;
      svg.append(imageMarker(xAt(i), below <= BASELINE - 2 ? below : yAt(value) - 9));
    }

    const hit = s("rect", { class: "chart-hit", x: xAt(i) - slot / 2, y: PAD.top, width: slot, height: PLOT_H });
    hit.addEventListener("pointerenter", () => {
      crosshair.setAttribute("x1", xAt(i));
      crosshair.setAttribute("x2", xAt(i));
      crosshair.setAttribute("visibility", "visible");
      tooltip.show(svg, xAt(i), yAt(value) - 8, h("strong", {}, `Turn ${i + 1}`),
        tipRow("Running total", formatNumber(value)),
        reported
          ? tipRow("This turn", `+${formatNumber(turns[i].usage.total_tokens)}`)
          : h("div", {}, missingUsage(turns[i])),
        ...imagesRow(turns[i]));
    });
    hit.addEventListener("pointerleave", () => {
      crosshair.setAttribute("visibility", "hidden");
      tooltip.hide();
    });
    svg.append(hit);
  });

  // Direct label on the current point.
  const lastX = xAt(n - 1);
  svg.append(s("text", {
    class: "axis-label current", x: lastX - 10, y: yAt(totals.at(-1)) - 12, "text-anchor": n === 1 ? "middle" : "end",
  }, formatNumber(totals.at(-1))));

  xLabels(svg, n, xAt);
  wrap.prepend(svg);
  if (turns.some((turn) => turn.images)) wrap.prepend(legend([["Has an image", null]]));
  return wrap;
}

function turnsTable(turns) {
  const totals = runningTotals(turns);
  const cell = (value) => h("td", {}, value === undefined ? "—" : formatNumber(value));
  const rows = turns.map((turn, i) => h("tr", {},
    h("td", {}, turn.images ? `${i + 1} (image)` : String(i + 1)),
    cell(turn.usage?.prompt_tokens),
    cell(turn.usage?.completion_tokens),
    cell(turn.usage?.total_tokens),
    cell(totals[i]),
  ));
  return h("details", { class: "chart-table" },
    h("summary", {}, "Show as table"),
    h("table", {},
      h("thead", {}, h("tr", {}, ...["Turn", "Prompt", "Completion", "Total", "Running"].map((t) => h("th", { scope: "col" }, t)))),
      h("tbody", {}, ...rows),
    ),
  );
}
