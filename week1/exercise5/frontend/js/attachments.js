import { h } from "./dom.js";
import { ImageError, formatBytes, prepareImage } from "./images.js";

const ERROR_TIMEOUT_MS = 6000;

/**
 * Images waiting to be sent with the next message: the 📎 button, drag-and-drop onto the
 * chat, and pasting into the input all add to it. Hidden entirely while vision is off.
 */
export class AttachmentTray {
  constructor({ button, fileInput, tray, dropZone, composer, textarea, errorBox, maxImages }) {
    this.button = button;
    this.fileInput = fileInput;
    this.tray = tray;
    this.errorBox = errorBox;
    this.maxImages = maxImages;
    this.enabled = false;
    this.maxBytes = 0;
    this.items = [];
    this.processing = 0;
    this.errorTimer = 0;
    this.nextId = 1;

    button.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
      this.addFiles([...fileInput.files]);
      fileInput.value = "";
    });

    textarea.addEventListener("paste", (event) => {
      if (!this.enabled) return;
      const images = [...(event.clipboardData?.files ?? [])].filter((file) => file.type.startsWith("image/"));
      if (images.length === 0) return; // plain text pastes as usual
      event.preventDefault();
      this.addFiles(images);
    });

    // dragenter/dragleave fire for every child element, so a counter tracks whether the
    // pointer is still somewhere over the drop zone.
    let depth = 0;
    const hasFiles = (event) => this.enabled && event.dataTransfer?.types.includes("Files");
    dropZone.addEventListener("dragenter", (event) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depth += 1;
      composer.classList.add("drag-over");
    });
    dropZone.addEventListener("dragover", (event) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    });
    dropZone.addEventListener("dragleave", () => {
      depth = Math.max(0, depth - 1);
      if (depth === 0) composer.classList.remove("drag-over");
    });
    dropZone.addEventListener("drop", (event) => {
      depth = 0;
      composer.classList.remove("drag-over");
      if (!hasFiles(event)) return;
      event.preventDefault();
      this.addFiles([...event.dataTransfer.files]);
    });
  }

  setEnabled(enabled, maxImageMb) {
    this.enabled = enabled;
    this.maxBytes = Math.floor(maxImageMb * 1024 * 1024);
    this.button.hidden = !enabled;
    if (!enabled) this.clear();
  }

  /** True while an image is still being resized; sending then would leave it behind. */
  get busy() {
    return this.processing > 0;
  }

  get count() {
    return this.items.length;
  }

  /** Remove and return the images to send. */
  take() {
    const items = this.items;
    this.items = [];
    this.#render();
    return items;
  }

  clear() {
    this.items = [];
    this.#render();
    this.#hideError();
  }

  async addFiles(files) {
    if (!this.enabled || files.length === 0) return;
    const room = this.maxImages - this.items.length - this.processing;
    if (room <= 0) {
      this.#showError(`You can attach up to ${this.maxImages} images to one message.`);
      return;
    }
    if (files.length > room) {
      this.#showError(`Only ${room} more image(s) fit in this message; the rest were skipped.`);
    }

    this.processing += Math.min(files.length, room);
    this.#render();
    await Promise.all(files.slice(0, room).map(async (file) => {
      try {
        const image = await prepareImage(file, this.maxBytes);
        this.items.push({ id: this.nextId++, ...image });
      } catch (error) {
        this.#showError(error instanceof ImageError ? error.message : `"${file.name}" could not be attached.`);
      } finally {
        this.processing -= 1;
        this.#render();
      }
    }));
  }

  #render() {
    const thumbs = this.items.map((item) => {
      const remove = h("button", {
        type: "button",
        class: "attachment-remove",
        "aria-label": `Remove ${item.name}`,
        title: "Remove",
      }, "✕");
      remove.addEventListener("click", () => {
        this.items = this.items.filter((other) => other.id !== item.id);
        this.#render();
      });
      return h("figure", { class: "attachment", title: `${item.name} · ${formatBytes(item.sizeBytes)}` },
        h("img", { src: item.url, alt: item.name }),
        remove,
      );
    });
    for (let i = 0; i < this.processing; i++) {
      thumbs.push(h("div", { class: "attachment attachment-loading", role: "status" },
        h("span", { class: "visually-hidden" }, "Preparing image…")));
    }
    this.tray.replaceChildren(...thumbs);
    this.tray.hidden = thumbs.length === 0;
  }

  #showError(message) {
    clearTimeout(this.errorTimer);
    this.errorBox.textContent = message;
    this.errorBox.hidden = false;
    this.errorTimer = setTimeout(() => this.#hideError(), ERROR_TIMEOUT_MS);
  }

  #hideError() {
    clearTimeout(this.errorTimer);
    this.errorBox.hidden = true;
  }
}
