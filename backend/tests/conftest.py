import pytest
from sqlalchemy.orm import Session

from app.db.base import engine


@pytest.fixture()
def db() -> Session:
    """A real Postgres session bound to a connection-level transaction that is always
    rolled back at teardown. Code under test (e.g. the orchestrator) is free to call
    session.commit() -- with join_transaction_mode="create_savepoint" that only
    releases a SAVEPOINT, it never touches the outer transaction we roll back here."""
    connection = engine.connect()
    outer_transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()
