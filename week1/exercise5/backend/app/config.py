import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

# The .env file lives at the project root, two levels above this package.
# In Docker it does not exist inside the image; Compose injects the variables instead.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_SYSTEM_PROMPT = (
    "You are a friendly and helpful assistant. Answer clearly and concisely.\n"
    "Format your replies in Markdown: use headings, lists, and tables where they help,\n"
    "and put code in fenced code blocks with a language tag.\n"
    "If you are not sure about something, say so instead of guessing."
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        # Treat `LLM_CONTEXT_WINDOW=` and similar blank values as "not set".
        env_ignore_empty=True,
        extra="ignore",
    )

    llm_base_url: str
    llm_api_key: str = Field(repr=False)
    llm_model: str
    llm_supports_vision: bool = False
    llm_system_prompt: str | None = None
    llm_temperature: float = 0.7
    llm_timeout_seconds: float = Field(default=120, gt=0)
    llm_context_window: int | None = Field(default=None, gt=0)

    backend_port: int = 6662
    frontend_port: int = 6661
    cors_origins: str = "http://localhost:6661"
    max_image_mb: float = Field(default=5, gt=0)
    # Relative paths are resolved from the working directory: backend/ locally, /app in Docker.
    database_url: str = "sqlite:///./data/chat.db"

    @field_validator("llm_base_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("cors_origins")
    @classmethod
    def reject_wildcard(cls, value: str) -> str:
        if "*" in value:
            raise ValueError("a wildcard origin is not allowed; list the exact origins instead")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def uploads_dir(self) -> Path:
        """Image attachments live in `uploads/` next to the SQLite file (or in ./data/uploads)."""
        url = make_url(self.database_url)
        if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
            return Path(url.database).parent / "uploads"
        return Path("data") / "uploads"

    @property
    def system_prompt(self) -> str:
        if not self.llm_system_prompt or not self.llm_system_prompt.strip():
            return DEFAULT_SYSTEM_PROMPT
        # python-dotenv expands "\n" inside double quotes, but other loaders (or a
        # single-quoted value) leave it literal. Expanding here makes both behave the same.
        return self.llm_system_prompt.replace("\\n", "\n")


def _format_validation_error(error: ValidationError) -> str:
    lines = ["Backend configuration is invalid. Check your .env file (see .env.example):"]
    for item in error.errors():
        name = str(item["loc"][0]).upper() if item["loc"] else "?"
        reason = "is required but not set" if item["type"] == "missing" else item["msg"]
        lines.append(f"  - {name} {reason}")
    return "\n".join(lines)


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as error:
        print(_format_validation_error(error), file=sys.stderr)
        raise SystemExit(1) from None
