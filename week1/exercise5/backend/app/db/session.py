from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from .accounts import seed_default_account
from .models import Base

SessionFactory = sessionmaker[Session]


def create_session_factory(settings: Settings) -> SessionFactory:
    """Create the engine and tables, seed the default account, and make sure the data folders exist."""
    url = make_url(settings.database_url)
    connect_args = {}
    if url.get_backend_name() == "sqlite":
        # The streaming endpoint saves from a different thread than the one that opened the connection.
        connect_args["check_same_thread"] = False
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, connect_args=connect_args)
    if url.get_backend_name() == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        seed_default_account(session)
    return factory


def _enable_sqlite_foreign_keys(connection, _record) -> None:
    # SQLite ignores ON DELETE CASCADE unless foreign keys are switched on for each connection.
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_session_factory(request: Request) -> SessionFactory:
    return request.app.state.db


def get_db(factory: Annotated[SessionFactory, Depends(get_session_factory)]) -> Iterator[Session]:
    with factory() as session:
        yield session


def dispose(factory: SessionFactory) -> None:
    engine = factory.kw["bind"]
    if isinstance(engine, Engine):
        engine.dispose()
