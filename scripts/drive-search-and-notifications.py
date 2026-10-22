"""
Run our tracked searches, record what changed, and tell people about it

This is the entry point for the whole job, and the only place configuration lives.
It takes no arguments: a job which runs itself from cron every morning has nobody
there to pass any, so everything it needs is either written down below or, if it is
a secret, read from the `.env` file beside it (see `.env.example`).

What it does, in order:

1. migrates (or creates) the database at [DB_PATH][]
2. searches ESGF for [REQUIREMENT][], saving every result into that database and
   recording the search itself
3. works out what changed since the last complete run
4. prints that summary
5. emails it to [RECIPIENTS][]

Run it with `make run-search-and-notifications`.
"""

from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv
from esmporium.db import (
    configure_sqlite_for_concurrency,
    search_and_track,
    summarise_search,
)
from esmporium.db.migrate import upgrade_to_head
from esmporium.query import Query
from esmporium.requirements import leaf, requirement
from sqlalchemy.engine import Engine
from sqlmodel import create_engine

from cmip_output_tracker.notifications import EmailSettings, send_email

# ---------------------------------------------------------------------------------
# Configuration. Everything you might want to change is in this block.
# ---------------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
"""
Root of this repository

Worked out from this file rather than from the working directory, so that cron can
run the script from wherever it likes and still find the database.
"""

DB_VERSION_ID = "001"
"""
Which database this job reads and writes

Bump this to start a fresh database, e.g. because a search's definition changed
enough that its history is no longer comparable. The old database stays on disk
under its own version, so nothing is lost by moving on.

Note that this is *our* version, not the schema's: esmporium migrates the schema
itself, on every run, via `upgrade_to_head`.
"""

DB_PATH = REPO_ROOT / "dbs" / f"{DB_VERSION_ID}_db.sqlite"
"""Database holding every result we have ever seen, and every search we have run"""

REQUIREMENT = requirement(
    name="pattern-effect-cmip7-tas-mon-historical",
    tree=leaf(
        # Each facet takes a tuple of accepted values, so widening the search later
        # is a matter of adding to these, e.g. `variable=("tas", "pr")`. esmporium
        # also accepts a bare string and wraps it, but spelling the tuple keeps the
        # type checkers happy and makes the "several values" case the obvious one.
        Query(
            project=("CMIP7",),
            variable=("tas",),
            experiment=("historical",),
            reporting_interval=("mon",),
        ),
        "tas",
    ),
    group_by=("model", "variant_label"),
)
"""
The search we are tracking: the pattern effect use case, for CMIP7

Monthly near-surface air temperature from the historical experiment, grouped one
model and variant at a time. This is the simplest requirement esmporium can
express -- one dataset, in one role, nothing optional -- which is why it is the one
we are shaking the job out on.

The name is the key the whole history hangs off: it is what the database stores the
search under, and what the summary is read back out by. Changing the query without
changing the name is refused, so if you edit the `Query` above, either rename the
requirement or bump [DB_VERSION_ID][].
"""

RECIPIENTS = (
    "azehrung@student.unimelb.edu.au",
    "zebedee.nicholls@climate-energy-college.org",
)
"""Who gets told what changed. Add addresses here."""

EMAIL_WHEN_NOTHING_CHANGED = True
"""
Whether to send mail on a morning when nothing changed

Left on deliberately. A quiet email is how you know the job is still running at
all: silence from a cron job means "nothing changed" and "the server is broken"
at the same time, and those need telling apart. Turn it off once the job has
earned enough trust to be boring.
"""

MAX_ROWS = 50
"""How many rows of each table to put in the summary before folding the rest away"""


# ---------------------------------------------------------------------------------
# The job itself.
# ---------------------------------------------------------------------------------

logger = logging.getLogger("cmip-output-tracker")


def open_database() -> Engine:
    """
    Get an engine for our database, creating and migrating it if need be

    Returns
    -------
    :
        The engine, configured the way every esmporium function which writes to
        SQLite requires
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    engine = configure_sqlite_for_concurrency(create_engine(f"sqlite:///{DB_PATH}"))
    # Safe on a database which is new, and on one which is already up to date.
    upgrade_to_head(engine)

    return engine


def notify(subject: str, body: str) -> None:
    """
    Email one message to everyone on the list

    Parameters
    ----------
    subject
        Subject line

    body
        Body of the message, as plain text
    """
    settings = EmailSettings.from_env()
    send_email(settings, recipients=RECIPIENTS, subject=subject, body=body)
    logger.info("Emailed %s", ", ".join(RECIPIENTS))


def main() -> None:
    """
    Run the search, report what changed, and email it

    Raises
    ------
    Exception
        Anything the search or the mail server raises. Re-raised after the failure
        has been emailed, so that cron sees the job fail too.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    # Secrets come from the .env file beside this repository, never from a tracked
    # file. Without `override`, anything already exported wins, which is what you
    # want when debugging by hand.
    load_dotenv(REPO_ROOT / ".env")

    engine = open_database()
    logger.info("Using database %s", DB_PATH)

    try:
        result = search_and_track(engine, REQUIREMENT)
    except Exception:
        # A search which fell over is news in its own right: without this, the only
        # symptom of a broken job is mail that never arrives.
        logger.exception("Search failed")
        try:
            notify(
                f"[FAILED] {REQUIREMENT.name}",
                "The tracked search did not finish. The traceback was:\n\n"
                f"{traceback.format_exc()}",
            )
        except Exception:
            # Swallowed so that the search's own failure is what propagates: being
            # unable to report a problem must not replace the problem.
            logger.exception("Could not email the failure either")

        raise

    logger.info("Run %s finished (complete=%s)", result.search_run_id, result.complete)

    # Read the summary back out of the database rather than off `result`, so that
    # what gets emailed is what was actually recorded.
    summary = summarise_search(engine, REQUIREMENT.name)
    rendered = summary.render(max_rows=MAX_ROWS)

    print(rendered)

    if summary.any_changes or not summary.run.complete or EMAIL_WHEN_NOTHING_CHANGED:
        notify(summary.headline, rendered)
    else:
        logger.info("Nothing changed and EMAIL_WHEN_NOTHING_CHANGED is off, so no mail")


if __name__ == "__main__":
    main()
