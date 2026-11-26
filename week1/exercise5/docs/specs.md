# Chatbot Proxy — Specification

A chat app built in stages. The browser talks only to a FastAPI backend, and the backend talks to an LLM through an OpenAI-compatible API. The frontend is plain HTML, CSS and JavaScript. It renders replies as markdown and has a **context viewer** that shows exactly what is sent to the model and how many tokens come back.

Status: **Stages 1–4 built.**

---

## Table of contents

1. [Goals and non-goals](#1-goals-and-non-goals)
2. [Architecture](#2-architecture)
3. [Configuration (.env)](#3-configuration-env)
4. [Running: ports, local and Docker Compose](#4-running-ports-local-and-docker-compose)
5. [Backend](#5-backend)
6. [Frontend](#6-frontend)
7. [Stages](#7-stages)
8. [Data model (Stage 4)](#8-data-model-stage-4)
9. [Design system](#9-design-system)
10. [Repository layout](#10-repository-layout)
11. [README requirements](#11-readme-requirements)
12. [Security notes](#12-security-notes)

---

## 1. Goals and non-goals

### Goals
- **The backend is the only path to the model.** The browser never sees the API key and never calls the LLM directly.
- **Switch providers by config only.** OpenAI, OpenRouter, Groq, or a local server such as Ollama, LM Studio, llama.cpp or vLLM all work by editing `.env`. No code changes.
- **Make the context visible.** The user can watch the messages array grow and the token counts go up with each turn.
- **Easy to run.** Someone clones the repo, copies `.env.example` to `.env`, fills it in, runs `docker compose up`, and gets a working chat. Running locally without Docker works too.
- **Built in stages.** Each stage is a working app that builds on the one before.

### Non-goals (for now)
- User accounts and authentication. The data model leaves room for them (see §8).
- Trimming or summarizing the context when the conversation exceeds the model's context window. For now the UI only warns (see §6).
- Tool calling, RAG, or multiple models in one conversation.
- A frontend build step, framework, or bundler.

---

## 2. Architecture

```
┌──────────────────────┐      HTTP / SSE       ┌──────────────────────┐   OpenAI-compatible API   ┌──────────────────┐
│  Browser             │ ───────────────────▶  │  FastAPI backend     │ ───────────────────────▶  │  LLM provider    │
│  vanilla HTML/CSS/JS │ ◀───────────────────  │  (proxy) :6662       │ ◀───────────────────────  │  cloud or local  │
│  served on :6661     │                       │  holds the API key   │                           │                  │
└──────────────────────┘                       └──────────┬───────────┘                           └──────────────────┘
                                                          │ Stage 4
                                                          ▼
                                                   SQLite (volume)
```

- **Frontend**: static files. In Docker they are served by nginx on port **6661**. Locally they are served by any static server on 6661.
- **Backend**: FastAPI on port **6662**. It calls the OpenAI-compatible HTTP API directly with `httpx` (`POST {LLM_BASE_URL}/chat/completions`), with no vendor SDK. Any server that implements this API works.
- **Where conversation state lives**:
  - Stages 1–3: the **client** keeps the conversation and sends the full `messages` array on every request. The backend keeps no state.
  - Stage 4: the **backend** keeps the conversation in SQLite. The client sends only the conversation id and the new message.
- The frontend calls the backend directly at `http://localhost:6662`, set in `frontend/config.js`, and the backend allows that origin through CORS. This works the same way in local and Docker modes.

---

## 3. Configuration (.env)

All configuration lives in `.env` at the project root. **`.env` is gitignored and is never committed.** A committed `.env.example` documents every variable with safe placeholder values.

| Variable | Example | Required | Purpose |
|---|---|---|---|
| `LLM_BASE_URL` | `https://api.openai.com/v1` | yes | Base URL of the OpenAI-compatible API |
| `LLM_API_KEY` | `sk-...` | yes* | API key. *Local servers usually accept any non-empty value, e.g. `local` |
| `LLM_MODEL` | `gpt-4o-mini` | yes | Model name sent in each request |
| `LLM_SUPPORTS_VISION` | `false` | no | Turns on image attachments (Stage 3). There is no reliable way to detect this automatically, so it is set by hand |
| `LLM_SYSTEM_PROMPT` | _(empty)_ | no | Custom system prompt. When empty, the built-in default is used (see [§5](#system-prompt)) |
| `LLM_TEMPERATURE` | `0.7` | no | Sampling temperature |
| `LLM_TIMEOUT_SECONDS` | `120` | no | How long to wait for the model before giving up |
| `LLM_CONTEXT_WINDOW` | `128000` | no | The model's context window in tokens. Used only for the context-size warning. If empty, the warning is turned off |
| `BACKEND_PORT` | `6662` | no | FastAPI port |
| `FRONTEND_PORT` | `6661` | no | Port for the static frontend |
| `CORS_ORIGINS` | `http://localhost:6661` | no | Comma-separated list of allowed browser origins |
| `MAX_IMAGE_MB` | `5` | no | Largest image upload allowed (Stage 3) |
| `DATABASE_URL` | `sqlite:///./data/chat.db` | no | Database location (Stage 4). Relative paths start from the backend's working directory (`backend/` locally, `/app` in Docker). Images go in `uploads/` next to the SQLite file |

Example presets to document in `.env.example` and the README:

```dotenv
# OpenAI
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini

# Ollama on the host machine (local run)
# LLM_BASE_URL=http://localhost:11434/v1
# LLM_API_KEY=ollama
# LLM_MODEL=llama3.2

# Ollama on the host machine, backend running in Docker
# LLM_BASE_URL=http://host.docker.internal:11434/v1

# LM Studio
# LLM_BASE_URL=http://localhost:1234/v1
```

Settings are loaded with `pydantic-settings`. If a required value is missing, the backend refuses to start and prints a clear error.

---

## 4. Running: ports, local and Docker Compose

| Service | Port | URL |
|---|---|---|
| Frontend | 6661 | http://localhost:6661 |
| Backend | 6662 | http://localhost:6662 (OpenAPI docs at `/docs`) |

### Docker Compose
- `docker-compose.yml` defines two services:
  - `backend`: built from `backend/Dockerfile` (python-slim + uvicorn), using `env_file: .env`.
  - `frontend`: nginx serving `frontend/`.
- `extra_hosts: ["host.docker.internal:host-gateway"]` on `backend`, so a model running on the host machine can be reached on Linux too. Docker Desktop on macOS and Windows provides this name already.
- Stage 4 adds a named volume mounted at `/app/data`, so the SQLite file survives container restarts.
- Run with: `cp .env.example .env` → edit `.env` → `docker compose up --build`.

### Local (no Docker)
- Backend: create a venv, `pip install -r backend/requirements.txt`, then `uvicorn app.main:app --port 6662 --reload` from `backend/`.
- Frontend: `python -m http.server 6661 -d frontend`.
- Both modes read the same `.env`.

---

## 5. Backend

### Stack
FastAPI, uvicorn, `httpx` (async client for the OpenAI-compatible API), `pydantic-settings`. From Stage 4: SQLAlchemy 2.x with SQLite. SQLAlchemy is used rather than raw `sqlite3` so a later move to Postgres is easy.

### Endpoints that apply to every stage
| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Liveness check. Also reports whether the LLM settings were loaded |
| GET | `/api/config` | Public settings: `model`, `supports_vision`, `max_image_mb`, `context_window`. **Never includes the key or the base URL** |

### Behavior rules
- Upstream errors are mapped to clear JSON errors: 401/403 from the provider becomes "invalid API key", a timeout becomes 504, a connection error becomes 502. The key is never included in any error message.
- Every chat response includes a **context echo**: the exact payload sent to the model (`model`, `messages`, parameters). The context viewer displays this.
- Token usage comes from the provider's `usage` field. If the provider doesn't return one, the response says `usage: null` and the UI shows "usage not reported". Token counts are never estimated silently.
- Every request uses the model currently set in `LLM_MODEL`, including requests in saved conversations (Stage 4). Changing `LLM_MODEL` and restarting switches all conversations to the new model.

### System prompt
The backend always adds a system message at the start of the messages it sends. The client never sends one.

**Default** (used when `LLM_SYSTEM_PROMPT` is empty or not set):
```text
You are a friendly and helpful assistant. Answer clearly and concisely.
Format your replies in Markdown: use headings, lists, and tables where they help,
and put code in fenced code blocks with a language tag.
If you are not sure about something, say so instead of guessing.
```
The default asks for Markdown because the chat window renders it.

**Custom**: set `LLM_SYSTEM_PROMPT` in `.env` and restart the backend. The custom value replaces the default completely. For several lines, use a double-quoted value with `\n`:
```dotenv
LLM_SYSTEM_PROMPT="You are a pirate tutor who explains Python.\nAlways reply in Markdown and keep answers short."
```
The system message is shown as the first card in the context viewer, so it is easy to check which prompt is active.

The endpoints for each stage are listed in §7.

---

## 6. Frontend

### Stack
Plain HTML, CSS and ES modules. No framework and no build step.

Markdown is rendered with **marked**, and the resulting HTML is sanitized with **DOMPurify**. Both libraries are stored in `frontend/vendor/` (no CDN), so the app works offline and with local models. Syntax highlighting with highlight.js is optional. These are small libraries, not a framework.

### Layout
```
┌──────────────────────────────────────────────────────────────────────┐
│  Header: app name · model badge · [Context] toggle · (S4) [New chat] │
├───────────────┬──────────────────────────────────┬───────────────────┤
│ (S4) History  │  Chat transcript                 │  Context viewer   │
│ sidebar       │  - user bubbles (right)          │  (collapsible)    │
│ conversation  │  - assistant bubbles (left),     │  - messages sent  │
│ list          │    rendered as markdown          │  - token meter    │
│               │                                  │  - per-turn chart │
│               ├──────────────────────────────────┤                   │
│               │ [📎 S3] [ message input... ] [Send]│                  │
└───────────────┴──────────────────────────────────┴───────────────────┘
```
On mobile, the sidebar and context viewer collapse into slide-over panels.

### Chat window
- Enter sends the message and Shift+Enter adds a new line. The input grows with its content, up to a limit.
- Assistant replies are rendered as markdown: headings, lists, tables, inline code and fenced code blocks. Each code block has a **Copy** button.
- While waiting for a reply, a "thinking" indicator is shown. Stage 2 replaces it with live streamed text.
- Errors appear inline as an error bubble with a **Retry** button.

### Context viewer
This panel shows what the model actually receives.
- **Messages sent**: the `messages` array from the last request, one collapsible card per message, color-coded by role (system / user / assistant). Base64 image data is shortened to a placeholder such as `[image/png · 182 KB]`. A **Raw JSON** toggle shows the full request payload.
- **Token meter for the last turn**: `prompt_tokens`, `completion_tokens` and `total_tokens`.
- **Growth charts**: both views are built, and a toggle switches between them. One will be chosen later and the other removed. Both are drawn with plain SVG, with no chart library.
  - **Per-turn bars**: one stacked bar per turn (prompt vs completion), so the user can see the prompt grow as the history gets longer.
  - **Running-total line**: a line for the total tokens used so far in the conversation, with one point per turn.
- **Context-size warning**: shown when `context_window` is set. A meter shows the last turn's `prompt_tokens` against the window. At 80% or more, a warning banner appears in sun ("The conversation is getting close to the model's context limit"). At 100% or more, it switches to the error style ("The conversation is larger than the model's context window. Older messages may be ignored or the request may fail"). Nothing is trimmed automatically.
- **Running totals**: number of turns, total tokens, and message count.

---

## 7. Stages

Each stage keeps everything from the stages before it.

### Stage 1 — Baseline (no streaming)
The backend calls the model, waits for the full answer, and returns it. The chat window shows the reply once it arrives.

**API**
```
POST /api/chat
Request:  { "messages": [ { "role": "user" | "assistant" | "system", "content": "..." } ] }
Response: {
  "message": { "role": "assistant", "content": "..." },
  "usage":   { "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0 } | null,
  "model":   "gpt-4o-mini",
  "finish_reason": "stop",
  "request": { "model": "...", "messages": [ ... ], "temperature": 0.7 }
}
```
The backend adds the system prompt (see [§5](#system-prompt)) to the start of the messages. That is why the echoed `request` can differ from what the client sent. If the client sends a `system` message, the backend drops it.

**Done when**
- [ ] Chat works against a cloud provider and against a local model, switched only by editing `.env`.
- [x] Replies render as markdown, and HTML in model output is sanitized.
- [x] The context viewer shows the messages sent and the token usage for each turn. Both chart views grow with each turn, and the toggle switches between them.
- [x] The context-size warning appears at 80% and 100% of `LLM_CONTEXT_WINDOW`.
- [x] Runs locally and with `docker compose up`.
- [x] `.env` is gitignored and `.env.example` is committed. The README covers setup.

### Stage 2 — Advanced (streaming)
The backend streams from the model and relays that stream to the browser as Server-Sent Events, so the answer appears token by token.

**API**
```
POST /api/chat/stream          (same request body as /api/chat)
Content-Type: text/event-stream

event: context   data: { "request": { ...exact payload sent... } }
event: delta     data: { "content": "Hel" }
event: delta     data: { "content": "lo" }
event: usage     data: { "prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17 }
event: done      data: { "finish_reason": "stop" }
event: error     data: { "message": "..." }        (instead of done, on failure)
```

**Implementation notes**
- Backend: `StreamingResponse` with an async generator. It sends `POST /chat/completions` with `"stream": true` and `"stream_options": {"include_usage": true}` through `httpx` streaming, reads the upstream SSE lines (`data: {...}` chunks, ending with `data: [DONE]`), and turns each `choices[0].delta.content` into a `delta` event. Some providers reject `stream_options`. In that case, retry without it and send `usage: null`.
- Each chunk is forwarded as soon as it arrives. Nothing is collected and sent at the end.
- Send headers `Cache-Control: no-cache` and `X-Accel-Buffering: no`, so proxies don't hold back the stream.
- When the client disconnects, the backend closes the upstream stream so it stops spending tokens.
- Frontend: `EventSource` only supports GET, so the client uses `fetch()` with a `ReadableStream` reader and a small SSE parser.
- The markdown re-render is throttled with `requestAnimationFrame` so long replies stay smooth. A code fence that hasn't closed yet must still render reasonably.
- A **Stop** button replaces Send while streaming and aborts the request through `AbortController`.
- Stage 1's `/api/chat` stays available. The UI uses streaming by default; `STREAMING = false` in `frontend/config.js` switches it back to `/api/chat`.

**Behavior decided while building**
- Validation errors (empty `messages`, only system messages) are still plain JSON errors with a 4xx status. Once the stream has started the status is already 200, so every upstream failure, including one before the first token, arrives as an `error` event: `{ "code", "message", "status" }` with the same codes and messages as Stage 1.
- `context` is sent once the provider accepts the request, so it always echoes the payload that was really sent (with or without `stream_options`).
- `usage` is always sent before `done`; its data is `null` when the provider reported none.
- A server that ignores `"stream": true` and answers with one JSON body is relayed as a single `delta`.
- `LLM_TIMEOUT_SECONDS` applies to each wait for the next chunk, not to the whole reply.
- The backend races every upstream read against the browser's disconnect, so Stop closes the upstream request even while the model is still thinking and no chunk is being written.
- Stop keeps the partial reply in the chat and in the history, marked "Stopped" and without usage. Stopping before any text arrives shows a note with **Retry**. A failure mid-stream drops the partial text and shows the error bubble with **Retry**.

**Done when**
- [x] Text appears progressively, and the backend relays chunks without buffering them.
- [x] Stop cancels both the browser request and the upstream request.
- [x] Token usage arrives at the end of the stream and updates the context viewer.

### Stage 3 — Best (vision)
If `LLM_SUPPORTS_VISION=true`, the user can attach an image to a message.

**Behavior**
- An attach button (📎) and drag-and-drop or paste onto the input. It is hidden when `/api/config` reports `supports_vision: false`.
- Accepted types: PNG, JPEG, WebP, GIF. Images larger than `MAX_IMAGE_MB` are rejected.
- Before sending, the client shrinks large images (longest side ≤ 1568 px) using a canvas. This keeps payloads and tokens down.
- A thumbnail preview with a remove (✕) button appears before sending. Sent images appear inside the user's bubble.

**Message format** (OpenAI content parts)
```json
{ "role": "user", "content": [
  { "type": "text", "text": "What is in this picture?" },
  { "type": "image_url", "image_url": { "url": "data:image/png;base64,..." } }
] }
```
- The backend checks the MIME type and size, and returns a 400 with a clear message if vision is turned off.
- The context viewer shows image parts as shortened placeholders and a small thumbnail.

**Behavior decided while building**
- Up to `MAX_IMAGES_PER_MESSAGE` images per message (4, set in `frontend/config.js`). A message can be an image with no text.
- `MAX_IMAGE_MB` is checked twice in the browser (on the original file, and again after shrinking) and once in the backend (on the decoded bytes).
- The canvas cannot encode GIF, so a GIF larger than 1568 px is sent as a PNG of its first frame. Images that are already small enough are sent unchanged.
- The backend accepts only `data:` URLs, so it can check every image. It compares the declared MIME type with the file's first bytes and rejects a mismatch. Images are only allowed in `user` messages.
- Error codes: `vision_disabled` (vision is off), `invalid_image` (wrong type, not base64, not a data URL, contents don't match the type) and `image_too_large`, all with status 400.
- The token charts mark turns whose message carried an image with a small picture icon, and the tooltip lists the image count. **Raw JSON** also shortens base64 data to the placeholder.

**Done when**
- [ ] Image + question works with a vision-capable model, streamed. (Verified end to end against a mock OpenAI-compatible server, not yet against a real vision model.)
- [x] The attach button is hidden when vision is off, and the backend rejects images in that case.
- [x] The image shows up in the context viewer, and the token jump from the image is visible.

### Stage 4 — Chat history (SQLite)
Conversations are saved in SQLite and listed in a sidebar. There are no logins yet, but every record belongs to an **account**, so authentication can be added later without a schema migration.

**API** (all scoped to the current account)
| Method | Path | Description |
|---|---|---|
| GET | `/api/conversations` | List conversations (id, title, updated_at), newest first |
| POST | `/api/conversations` | Create an empty conversation |
| GET | `/api/conversations/{id}` | Conversation with its messages and per-message usage |
| PATCH | `/api/conversations/{id}` | Rename |
| DELETE | `/api/conversations/{id}` | Delete, including its messages and attachments |
| POST | `/api/conversations/{id}/messages` | Send a new user message. Streams the reply over SSE like Stage 2, then saves both messages |
| GET | `/api/attachments/{id}` | An image saved with a message, so the chat can show it after a reload |

**Account scoping**
- A FastAPI dependency `get_current_account()` returns the seeded default account (`local`) for now. Every query filters by `account_id`.
- Adding auth later means changing only this dependency so it reads the account from a session or token. Routes and queries stay the same.
- Ids are UUIDs, so they can't be guessed once there are multiple users.

**Behavior**
- The backend builds the context from the database (system prompt + saved history + new message). The context viewer still shows exactly that payload.
- Conversations don't store a model or a system prompt. Every request uses the current `LLM_MODEL` and system prompt, so an old conversation continues with whatever is configured now.
- The title is created automatically from the first user message (truncated). It can be renamed.
- Images are saved as files under `data/uploads/` and referenced from the database. They are converted back to data URLs when the context is rebuilt.
- The stateless `/api/chat` and `/api/chat/stream` endpoints remain available.

**Behavior decided while building**
- `POST /api/conversations/{id}/messages` takes `{ "content": "..." | [content parts] }`. Its events are those of Stage 2 plus `saved` (`{ "conversation": { id, title, created_at, updated_at } }`), sent after `usage` and just before `done`.
- The UI creates a conversation only when its first message is sent, so **New chat** never leaves empty conversations behind. The open conversation is kept in the URL hash (`#/c/<id>`), so a reload reopens it and the browser's Back button works.
- Saving follows the Stage 2 rules. A finished reply saves both messages. A reply stopped after some text saves the partial reply with `finish_reason: "stopped"` and no usage. A failed reply, or one stopped before any text, saves nothing, and the UI marks the user message **Not sent** so **Retry** sends it again.
- `title` is `null` until the first message is saved. It is then the first 60 characters of that message (or "Image" for an image-only message). A conversation renamed before its first message keeps its name.
- Switching conversations while a reply streams stops that reply; the backend keeps the partial text as above.
- After a reload, the context viewer rebuilds the turns and token charts from the saved usage. **Messages sent** stays empty until the next message, because the payload itself is not stored.
- Images are stored as `data/uploads/<conversation id>/<attachment id>.<ext>`. Deleting a conversation removes that folder.
- The default account has a fixed id (a UUID derived from the name `local`), so it is the same in every fresh database.
- `HISTORY = false` in `frontend/config.js` switches the UI back to the stateless Stage 1–3 behavior (no sidebar). `STREAMING` only applies in that mode.
- Below 1240 px the chat list becomes a slide-over, and below 960 px the context viewer does too.

**Done when**
- [ ] Conversations survive a page reload and a container restart (volume). (Reload verified in the browser and a restart simulated in the tests; not yet checked with `docker compose restart`.)
- [x] The sidebar lists, switches, renames and deletes conversations.
- [x] All data access goes through the account dependency.

---

## 8. Data model (Stage 4)

```
accounts
  id            TEXT  PK (uuid)
  name          TEXT
  created_at    TIMESTAMP

conversations
  id            TEXT  PK (uuid)
  account_id    TEXT  FK → accounts.id   (indexed)
  title         TEXT
  created_at    TIMESTAMP
  updated_at    TIMESTAMP

messages
  id                 TEXT  PK (uuid)
  conversation_id    TEXT  FK → conversations.id  ON DELETE CASCADE  (indexed)
  role               TEXT  -- system | user | assistant
  content            TEXT  -- JSON: string or list of content parts (image parts reference attachments)
  prompt_tokens      INTEGER NULL
  completion_tokens  INTEGER NULL
  finish_reason      TEXT NULL
  created_at         TIMESTAMP

attachments
  id            TEXT  PK (uuid)
  message_id    TEXT  FK → messages.id  ON DELETE CASCADE
  mime_type     TEXT
  size_bytes    INTEGER
  path          TEXT  -- relative to data/uploads/
  created_at    TIMESTAMP
```
Later: a `users` table linked to `accounts` (one or many), plus sessions. Nothing above needs to change for that.

---

## 9. Design system

### Look and feel
Playful, flat, like a **2D vector illustration**:
- **Solid colors only. No gradients and no blurred shadows.**
- Thick dark outlines (2.5px ink), large rounded corners, chunky shapes.
- **Flat 3D depth**: interactive elements sit on a solid, offset "ledge" in a darker shade of their own color, made with a hard `box-shadow` (no blur). When pressed, the element moves down onto the ledge.
- Optional decoration: a few simple solid shapes in the background (blobs, dots, squiggles) in light tints. Never behind text that needs to stay readable.

### Brand colors
| Name | Hex | Role |
|---|---|---|
| **Teal** | `#008790` | Primary: main actions, links, focus, brand |
| **Coral** | `#FF7A5B` | Secondary: secondary actions, user messages, highlights that need attention |
| **Sun** | `#FCCA67` | Accent: badges, token counters, selection, small touches of joy |

### Shade scales
Lighter shades (50–400) are mixed toward the paper color `#FFFDF8`. Darker shades (600–900) are mixed toward a deep color of the same hue (teal → `#062A30`, coral → `#6E1A10`, sun → `#6B3D05`) instead of black. Mixing toward black makes warm colors look muddy, and this keeps them rich. Contrast ratios below are WCAG 2.x and were computed for this palette.

| Step | Teal | Coral | Sun |
|---|---|---|---|
| 50  | `#EBF4F0` | `#FFF3EB` | `#FFF9EC` |
| 100 | `#CCE5E3` | `#FFE3D9` | `#FEF3DB` |
| 200 | `#9ED0D0` | `#FFCBBC` | `#FEEAC1` |
| 300 | `#6BB9BC` | `#FFB19D` | `#FDDFA4` |
| 400 | `#339FA5` | `#FF947A` | `#FDD484` |
| **500** | **`#008790`** | **`#FF7A5B`** | **`#FCCA67`** |
| 600 | `#01737B` | `#DF654A` | `#DCAB51` |
| 700 | `#036068` | `#C2523C` | `#BF8F3E` |
| 800 | `#044D54` | `#A53E2C` | `#A2732A` |
| 900 | `#053D43` | `#8B2D1F` | `#885919` |

**Neutrals**
| Token | Hex | Use |
|---|---|---|
| `--ink` | `#13262B` | Body text, outlines, ledges of neutral elements (15.7:1 on white) |
| `--paper` | `#FFFDF8` | Cards and bubbles |
| `--canvas` | `#FFF9EC` (sun-50) | Page background |
| `--ink-muted` | to be chosen at build time | Secondary text. Must reach ≥ 4.5:1 on paper and canvas |

**Status** (kept separate from coral so errors don't look like a secondary action)
| Token | Hex | Notes |
|---|---|---|
| `--error` | `#B42318` | 6.6:1 with white. Always shown with an icon and text |
| `--success` | teal-600 `#01737B` | |
| `--warning` | sun-500 bg + ink text | |

### Text on each color (measured contrast)
| Background | Text | Ratio | Verdict |
|---|---|---|---|
| teal-500 | white | 4.31 | OK only for **large/bold** text (≥ 18.66px bold). Use teal-600 for normal-size text |
| teal-600 | white | 5.65 | AA for all text |
| teal-700 | white | 7.31 | AAA. Good for links on paper |
| coral-500 | ink | 6.11 | AA. **Never put white text on coral-500 (2.57)** |
| sun-500 | ink | 10.29 | AAA. **Never put white text on sun (1.52)** |
| paper / canvas | ink | ~15 | AAA |
| teal-900 (code blocks) | paper | ~12 | AAA |

### Color usage rules
- **60-30-10**: about 60% neutral surfaces (canvas/paper), 30% teal (structure, primary UI), 10% coral and sun (accents, attention).
- **One primary (teal) button per view.** Coral is for secondary actions and sun is for highlights. Too many loud colors make the screen hard to scan.
- **How to use each scale:** 50–200 for surfaces and tinted backgrounds, 300–500 for fills and illustrations, 600–900 for text on light backgrounds, hover/pressed states, ledges, and borders.
- Hover goes **one step darker** (500 → 600). Pressed goes another step and moves down onto the ledge.
- Never use color as the only signal. Pair it with an icon, a label, or a shape.
- Role colors used everywhere (chat, context viewer and chart):
  - **system** = teal
  - **user** = coral
  - **assistant** = sun
  - prompt tokens = teal
  - completion tokens = coral

### Components

**Buttons (flat 3D)**
| Variant | Fill | Text | Border | Ledge (hard shadow) |
|---|---|---|---|---|
| Primary | teal-500 | white, bold ≥ 18px | ink 2.5px | teal-800, `0 4px 0` |
| Secondary | coral-500 | ink, bold | ink 2.5px | coral-800, `0 4px 0` |
| Accent | sun-500 | ink, bold | ink 2.5px | sun-800, `0 4px 0` |
| Ghost | paper | ink | ink 2.5px | ink, `0 4px 0` |
| Disabled | teal-100 | teal-700 | teal-300 | none (flat) |

States: hover moves up 1px with a 5px ledge and darkens one step. Active moves down 4px and the ledge becomes 0. `:focus-visible` gets a 3px teal-700 outline with a 3px offset. All movement is turned off under `prefers-reduced-motion`.

```css
/* reference sketch */
.btn {
  border: 2.5px solid var(--ink);
  border-radius: 14px;
  box-shadow: 0 4px 0 0 var(--ledge);
  transition: transform 80ms, box-shadow 80ms;
}
.btn:hover  { transform: translateY(-1px); box-shadow: 0 5px 0 0 var(--ledge); }
.btn:active { transform: translateY(4px);  box-shadow: 0 0 0 0 var(--ledge); }
```

**Chat bubbles**
- User: coral-100 fill, ink outline, ink text, aligned right, small coral-800 ledge.
- Assistant: paper fill, ink outline, ink text, aligned left, small ink ledge, with a small sun-colored avatar shape.
- Error: coral-50 fill, `--error` border, icon + message + Retry button.

**Code blocks**: teal-900 background, paper text, monospace, radius 10px, a Copy button in sun.

**Input bar**: paper background with a thick ink outline. On focus, it gets a teal ring and a teal-800 ledge.

**Context viewer cards**: a tinted background for each role (teal-50 / coral-50 / sun-50) with a colored left stripe (500) and an ink outline.

**Token charts**: stacked bars with solid teal and coral fills, ink outlines, sun highlight on the current turn. The running-total line is a 3px solid ink line with teal dots and the current point in sun. No gridline clutter.

### Typography
- Headings: **Fredoka** (rounded, playful). Body: **Nunito**. Code: **JetBrains Mono**, falling back to `ui-monospace`.
- Font files are stored in `frontend/vendor/fonts/` so no request goes to a third party. System fonts are the fallback.
- Base size 16px, line-height 1.55. Headings use rounded, heavy weights (600–700).

### Shape and spacing tokens
- Radius: `--r-sm 8px`, `--r-md 14px`, `--r-lg 20px`, `--r-pill 999px`.
- Spacing scale: 4, 8, 12, 16, 24, 32, 48.
- Border: `--stroke 2.5px` ink. Ledge offset: `4px` for buttons, `3px` for cards and bubbles.

All colors, radii and spacing are CSS custom properties in `frontend/css/tokens.css`.

---

## 10. Repository layout

```
exercise5/
├── docs/
│   └── specs.md
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app, CORS, routers
│   │   ├── config.py          # pydantic-settings
│   │   ├── errors.py          # AppError: status + code + message for the browser
│   │   ├── llm.py             # httpx client for /chat/completions: call + stream helpers
│   │   ├── images.py          # Stage 3: data URL decoding, type and size checks
│   │   ├── attachments.py     # Stage 4: image files under data/uploads/
│   │   ├── schemas.py         # request/response models
│   │   ├── routers/
│   │   │   ├── chat.py        # /api/chat, /api/chat/stream
│   │   │   └── conversations.py   # Stage 4
│   │   └── db/                # Stage 4: models, session, account dependency
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── index.html
│   ├── config.js              # API_BASE_URL
│   ├── css/  (tokens.css, base.css, components.css)
│   ├── js/   (main.js, api.js, sse.js, chat.js, markdown.js, context-viewer.js, dom.js,
│   │          images.js, attachments.js, history.js, panels.js)
│   └── vendor/ (marked, dompurify, fonts)
├── data/                      # SQLite and uploads (gitignored, Docker volume)
├── docker-compose.yml
├── .env.example
├── .gitignore
└── README.md
```

---

## 11. README requirements

The README is written for someone who has just cloned the repo:
1. What the app is, with a screenshot.
2. Requirements: Docker (or Python 3.11+ for local runs).
3. Quick start: `cp .env.example .env` → edit it → `docker compose up --build` → open http://localhost:6661.
4. Provider presets: OpenAI, OpenRouter, Ollama and LM Studio, including the `host.docker.internal` note for local models when the backend runs in Docker.
5. Running locally without Docker.
6. Description of each `.env` variable (or a link to `.env.example`).
7. A short tour of the context viewer.
8. Troubleshooting: invalid key, model not found, CORS, a local model the container can't reach, a vision model that isn't vision-capable.
9. Which stages are finished.

---

## 12. Security notes

- `.env` is in `.gitignore` from the very first commit. Only `.env.example` is committed.
- The API key exists only in the backend process. It is never sent to the browser, logged, or included in errors.
- Model output is untrusted HTML. It is always passed through DOMPurify before going into the DOM, and links get `rel="noopener noreferrer"`.
- CORS is limited to `CORS_ORIGINS`. It is never `*`.
- Image uploads are checked for MIME type and size on the backend, not only in the client.
- Stage 4: every query is scoped by `account_id`, ready for auth later.

