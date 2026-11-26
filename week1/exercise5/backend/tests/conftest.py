import os
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

# app.main loads settings at import time, so required values must exist before the import.
os.environ.setdefault("LLM_BASE_URL", "http://upstream.test/v1")
os.environ.setdefault("LLM_API_KEY", "sk-test-secret-key")
os.environ.setdefault("LLM_MODEL", "test-model")

from app.config import Settings, get_settings  # noqa: E402
from app.db.session import create_session_factory, dispose, get_session_factory  # noqa: E402
from app.llm import LLMClient  # noqa: E402
from app.main import app  # noqa: E402
from app.routers.chat import get_llm_client  # noqa: E402

API_KEY = "sk-test-secret-key"
BASE_URL = "http://upstream.test/v1"

Handler = Callable[[httpx.Request], httpx.Response]


def make_settings(**overrides) -> Settings:
    values = {
        "llm_base_url": BASE_URL,
        "llm_api_key": API_KEY,
        "llm_model": "test-model",
        "llm_context_window": 8000,
        **overrides,
    }
    # _env_file=None keeps a developer's real .env out of the tests.
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(database_url=f"sqlite:///{tmp_path / 'data' / 'chat.db'}")


@pytest.fixture
def client_factory(settings: Settings) -> Iterator[Callable[..., TestClient]]:
    """Build a TestClient whose upstream LLM is replaced by `handler`.

    Keyword arguments override settings, e.g. `client_factory(handler, llm_supports_vision=True)`.
    Each test gets its own SQLite database in a temporary folder.
    """
    session_factories = []

    def factory(handler: Handler, **overrides) -> TestClient:
        current = settings.model_copy(update=overrides)
        llm = LLMClient(current, transport=httpx.MockTransport(handler))
        session_factory = create_session_factory(current)
        session_factories.append(session_factory)
        app.dependency_overrides[get_settings] = lambda: current
        app.dependency_overrides[get_llm_client] = lambda: llm
        app.dependency_overrides[get_session_factory] = lambda: session_factory
        return TestClient(app)

    yield factory
    app.dependency_overrides.clear()
    for session_factory in session_factories:
        dispose(session_factory)


def completion_response(content: str = "Hello!", usage: dict | None = None, **extra) -> httpx.Response:
    body = {
        "id": "chatcmpl-1",
        "model": "test-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        **extra,
    }
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)
