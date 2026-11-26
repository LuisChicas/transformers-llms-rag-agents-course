import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .db.session import create_session_factory, dispose
from .errors import AppError
from .llm import LLMClient
from .routers import chat, conversations
from .schemas import Health, PublicConfig

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.llm = LLMClient(settings)
    app.state.db = create_session_factory(settings)
    yield
    await app.state.llm.aclose()
    dispose(app.state.db)


app = FastAPI(title="Chatbot Proxy", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)


def error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


@app.exception_handler(AppError)
async def handle_app_error(_: Request, error: AppError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content=error_body(error.code, error.message))


@app.exception_handler(HTTPException)
async def handle_http_error(_: Request, error: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content=error_body("bad_request", str(error.detail)))


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
    first = error.errors()[0] if error.errors() else {}
    where = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    message = f"Invalid request: {where} {first.get('msg', '')}".strip()
    return JSONResponse(status_code=422, content=error_body("invalid_request", message))


@app.get("/api/health", response_model=Health, tags=["meta"])
async def health(current: Annotated[Settings, Depends(get_settings)]) -> Health:
    return Health(llm_configured=bool(current.llm_base_url and current.llm_api_key and current.llm_model))


@app.get("/api/config", response_model=PublicConfig, tags=["meta"])
async def public_config(current: Annotated[Settings, Depends(get_settings)]) -> PublicConfig:
    return PublicConfig(
        model=current.llm_model,
        supports_vision=current.llm_supports_vision,
        max_image_mb=current.max_image_mb,
        context_window=current.llm_context_window,
    )


app.include_router(chat.router)
app.include_router(conversations.router)
