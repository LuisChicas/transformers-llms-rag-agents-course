from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from .accounts import DEFAULT_ACCOUNT_ID
from .models import Account
from .session import get_db


def get_current_account(db: Annotated[Session, Depends(get_db)]) -> Account:
    """The account every request acts as. All data access is scoped by its id.

    There are no logins yet, so this is always the seeded `local` account. Adding auth
    means changing only this function to read the account from a session or token.
    """
    account = db.get(Account, DEFAULT_ACCOUNT_ID)
    if account is None:
        raise RuntimeError("The default account is missing; the database was not initialized.")
    return account
