// Where the browser reaches the FastAPI backend. Change it if you change BACKEND_PORT.
export const API_BASE_URL = "http://localhost:6662";

// true: conversations are saved in the backend database and listed in the sidebar (Stage 4).
// They always stream. false: the client keeps the conversation in memory, as in Stages 1–3.
export const HISTORY = true;

// Only used when HISTORY is false.
// true: replies stream token by token from /api/chat/stream.
// false: the Stage 1 behavior, the full reply arrives at once from /api/chat.
export const STREAMING = true;

// Most images that can be attached to one message (Stage 3).
export const MAX_IMAGES_PER_MESSAGE = 4;
