/**
 * Minimal Server-Sent Events parser for a fetch() response body.
 * EventSource only supports GET, and the chat endpoint is a POST.
 *
 * Yields { event, data } with `data` as the raw string; `event` defaults to "message".
 */
export async function* readServerSentEvents(body) {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  let event = "message";
  let data = [];

  /** Feed one line; returns a finished event when the line is the blank line that ends it. */
  const takeLine = (line) => {
    if (line === "") {
      const finished = data.length ? { event, data: data.join("\n") } : null;
      event = "message";
      data = [];
      return finished;
    }
    if (line.startsWith(":")) return null; // comment / keep-alive

    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    if (field === "event") event = value;
    else if (field === "data") data.push(value);
    return null;
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;

      // A chunk can end in the middle of a line, so only complete lines are parsed. A trailing
      // "\r" is held back too: it may be the first half of a "\r\n" split across two chunks.
      const holdCR = buffer.endsWith("\r");
      const lines = (holdCR ? buffer.slice(0, -1) : buffer).split(/\r\n|\r|\n/);
      buffer = lines.pop() + (holdCR ? "\r" : "");

      for (const line of lines) {
        const finished = takeLine(line);
        if (finished) yield finished;
      }
    }
    // Be lenient with servers that end the body without the final blank line.
    takeLine(buffer.replace(/\r$/, ""));
    const last = takeLine("");
    if (last) yield last;
  } finally {
    // Closes the connection if the caller stopped reading early; harmless once the stream ended.
    reader.cancel().catch(() => {});
  }
}
