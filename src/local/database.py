"""
Opening the database both halves of the job share
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from esmporium.db import configure_sqlite_for_concurrency
from esmporium.db.migrate import upgrade_to_head
from sqlmodel import create_engine

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine


def open_database(db_path: Path) -> Engine:
    """
    Get an engine for our database, creating and migrating it if need be

    Parameters
    ----------
    db_path
        Where the database is. Its directory is created if it is not there yet.

    Returns
    -------
    :
        The engine, configured the way every esmporium function which writes to
        SQLite requires
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = configure_sqlite_for_concurrency(create_engine(f"sqlite:///{db_path}"))
    # Safe on a database which is new, and on one which is already up to date.
    upgrade_to_head(engine)

    return engine
