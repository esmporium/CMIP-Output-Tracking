"""
Run our tracked searches, record what changed, and tell people about it
"""

from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv
from esmporium.db import configure_sqlite_for_concurrency
from esmporium.db.migrate import upgrade_to_head
from esmporium.query import Query
from sqlalchemy.engine import Engine
from sqlmodel import create_engine

from local.endpoints import (
    Endpoint,
    EndpointResult,
    endpoint_requirement,
    search_endpoint,
)
from local.notifications import EmailSettings, send_email
from local.summary_formatting import (
    PATTERN_EFFECT_FACETS,
    format_report,
    report_subject,
)

# ---------------------------------------------------------------------------------
# Configuration. Everything you might want to change is in this block.
# ---------------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
"""
Root of this repository

Worked out from this file rather than from the working directory, so that cron can
run the script from wherever it likes and still find the database.
"""

DB_VERSION_ID = "002"
"""
Which database this job reads and writes

Bump this manually to start a fresh database, e.g. because a search's definition
changed enough that its history is no longer comparable. The old database stays
on disk under its own version, so nothing is lost by moving on.
"""

DB_PATH = REPO_ROOT / "dbs" / f"{DB_VERSION_ID}_db.sqlite"
"""Database holding every result we have ever seen, and every search we have run"""

SEARCH_STEM = "pattern-effect-tas-mon-historical"
"""
What this search is called, before the project and endpoint are appended

The stem of the key the whole history hangs off: one requirement per endpoint is
stored as `<stem>-<project>-<tag>`. Changing the query without changing this is
refused by esmporium, so if you edit a `Query` below, either change the stem or bump
[DB_VERSION_ID][].
"""

PROCESSING_IDS = {
    "CMIP6": ("Amon",),
    "CMIP7": ("tavg-h2m-hxy-u",),
}
"""
The `processing_id` to pin, per project. These are *not* interchangeable.

This is specific to the current SEARCH_STEM requirement.
"""

QUERIES = {
    project: Query(
        project=(project,),
        variable=("tas",),
        experiment=("historical",),
        reporting_interval=("mon",),
        processing_id=processing_id,
    )
    for project, processing_id in PROCESSING_IDS.items()
}
"""
The question, per project

Each facet takes a tuple of accepted values, so widening a search later is a matter
of adding to these, e.g. `variable=("tas", "pr")`. esmporium also accepts a bare
string and wraps it, but spelling the tuple keeps the type checkers happy and makes
the "several values" case the obvious one.
"""

ENDPOINTS = (
    # CMIP7
    Endpoint("CMIP7", "search.east.esgf.io", "east"),
    Endpoint("CMIP7", "search.west.esgf.io", "west"),
    # CMIP6
    Endpoint("CMIP6", "esg-dn1.nsc.liu.se", "nsc"),
    Endpoint("CMIP6", "esgf-node.ornl.gov", "ornl"),
    Endpoint("CMIP6", "esgf.nci.org.au", "nci"),
    Endpoint("CMIP6", "esgf-data.dkrz.de", "dkrz"),
    Endpoint("CMIP6", "esgf.ceda.ac.uk", "ceda"),
    Endpoint("CMIP6", "search.east.esgf.io", "east"),
    Endpoint("CMIP6", "search.west.esgf.io", "west"),
)
"""
Every endpoint we ask, and what we call it

One tracked search each, so each has its own history and its own baseline. Adding a
host here adds a row to the report and a requirement to the database; it does not
change what any other endpoint reports.

The tag has to be stable, because it is part of the requirement name that endpoint's
whole history is stored under. Renaming a tag starts that endpoint again from
scratch.
"""

DETAILED_PROJECTS = ("CMIP7",)
"""
Which projects get a full changes table, one block per endpoint

Everything else gets a single row per endpoint: how much it returned, and whether
that differs from its own previous run.
"""

RECIPIENTS = (
    "azehrung@student.unimelb.edu.au",
    "zebedee.nicholls@climate-energy-college.org",
)
"""Who gets told what changed. Add addresses here."""

SEND_EMAIL = True
"""
Whether to email the report at all
"""

EMAIL_WHEN_NOTHING_CHANGED = True
"""
Whether to send mail on a morning when nothing changed
"""

MAX_ROWS = 50
"""How many rows of each table to put in the report before folding the rest away"""


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


def search_everything(engine: Engine) -> tuple[EndpointResult, ...]:
    """
    Search every endpoint in [ENDPOINTS][], one at a time

    Parameters
    ----------
    engine
        The database to record the runs in

    Returns
    -------
    :
        One result per endpoint, in [ENDPOINTS][] order, including the endpoints
        which failed
    """
    results = []
    for endpoint in ENDPOINTS:
        logger.info("Searching %s for %s", endpoint.label, endpoint.project)
        result = search_endpoint(
            engine,
            endpoint,
            endpoint_requirement(
                endpoint,
                QUERIES[endpoint.project],
                name_stem=SEARCH_STEM,
                role_stem="tas",
            ),
        )

        if result.summary is None:
            logger.warning("%s failed: %s", endpoint.label, result.failure)
        else:
            logger.info(
                "%s returned %s datasets (%s)",
                endpoint.label,
                result.n_datasets,
                result.summary.headline,
            )

        results.append(result)

    return tuple(results)


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
    Run every search, report what changed, and email it

    Raises
    ------
    Exception
        Anything the mail server, the database or the migration raises. One endpoint
        failing to answer is not one of these: that is reported in the body and the
        job carries on, because eight endpoints' news is still news.
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
        results = search_everything(engine)
    except Exception:
        # Getting here means something broke which is not one endpoint being down --
        # the database, the migration, a requirement we cannot even build. Without
        # this, the only symptom of a broken job is mail that never arrives.
        logger.exception("The job failed")
        try:
            notify(
                f"[FAILED] {SEARCH_STEM}",
                "The job did not finish. The traceback was:\n\n"
                f"{traceback.format_exc()}",
            )
        except Exception:
            # Swallowed so that the original failure is what propagates: being
            # unable to report a problem must not replace the problem.
            logger.exception("Could not email the failure either")

        raise

    rendered = format_report(
        results,
        detailed=DETAILED_PROJECTS,
        max_rows=MAX_ROWS,
        facets=PATTERN_EFFECT_FACETS,
    )
    subject = report_subject(results, stem=SEARCH_STEM)

    print(rendered)

    if not SEND_EMAIL:
        logger.info("SEND_EMAIL is off, so no mail")

        return

    anything_to_say = any(
        result.summary is None
        or result.summary.any_changes
        or not result.summary.run.complete
        for result in results
    )
    if anything_to_say or EMAIL_WHEN_NOTHING_CHANGED:
        notify(subject, rendered)
    else:
        logger.info("Nothing changed and EMAIL_WHEN_NOTHING_CHANGED is off, so no mail")


if __name__ == "__main__":
    main()
