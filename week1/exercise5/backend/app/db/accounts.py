import uuid

from sqlalchemy.orm import Session

from .models import Account

DEFAULT_ACCOUNT_NAME = "local"
# Fixed so the default account keeps the same id across restarts and fresh databases.
DEFAULT_ACCOUNT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "chatbot-proxy:account:local"))


def seed_default_account(session: Session) -> None:
    if session.get(Account, DEFAULT_ACCOUNT_ID) is None:
        session.add(Account(id=DEFAULT_ACCOUNT_ID, name=DEFAULT_ACCOUNT_NAME))
        session.commit()
