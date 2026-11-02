"""
Run our tracked searches, tell people what changed, then fetch what is new
"""

from __future__ import annotations

import argparse
import logging
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from esmporium.db import download_summary
from esmporium.downloads import totals
from esmporium.query import Query
from sqlmodel import Session

from local.database import open_database
from local.download_formatting import (
    DownloadGroup,
    download_subject,
    format_download_intent,
    format_download_report,
)
from local.downloads import (
    IDENTITY_FACETS,
    DownloadSettings,
    describe_plan,
    describe_results,
    plan_only,
    run_downloads,
)
from local.endpoints import (
    Endpoint,
    EndpointResult,
    TrackedRequirement,
    requirement_for,
    search_endpoint,
)
from local.notifications import EmailSettings, send_email
from local.summary_formatting import (
    PATTERN_EFFECT_FACETS,
    ReportGroup,
    format_report,
    report_subject,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.engine import Engine

    from local.downloads import DownloadIntent

# ---------------------------------------------------------------------------------
# Configuration. Everything you might want to change is in this block.
# ---------------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
"""
Root of this repository

Worked out from this file rather than from the working directory, so that cron can
run the job from wherever it likes and still find the database.
"""

DB_VERSION_ID = "002"
"""
Which database this job reads and writes

Bump this manually to start a fresh database, e.g. because a search's definition
changed enough that its history is no longer comparable. The old database stays
on disk under its own version, so nothing is lost by moving on.

Note that bumping it now costs more than it used to: the database is what remembers
which files we already have, so a fresh one does not know about what is already
under `DOWNLOAD_ROOT` and the next download would fetch all of it again.
"""

DB_PATH = REPO_ROOT / "dbs" / f"{DB_VERSION_ID}_db.sqlite"
"""Database holding every result we have ever seen, and every run we have ever done"""

SEARCH_STEM = "pattern-effect-tas-mon-historical"
"""
What this search is called in the database, before the project and endpoint

See [TrackedRequirement.stem][local.endpoints.TrackedRequirement.stem]: this is the
key the whole history hangs off, so changing it starts again from scratch.
"""

REQUIREMENTS = (
    TrackedRequirement(
        label="CMIP7",
        stem=SEARCH_STEM,
        role_stem="tas",
        query=Query(
            project=("CMIP7",),
            variable=("tas",),
            experiment=("historical",),
            reporting_interval=("mon",),
            processing_id=("tavg-h2m-hxy-u",),
        ),
        endpoints=(
            Endpoint("CMIP7", "search.east.esgf.io", "east"),
            Endpoint("CMIP7", "search.west.esgf.io", "west"),
        ),
        download=True,
        detailed=True,
    ),
    TrackedRequirement(
        label="CMIP6",
        stem=SEARCH_STEM,
        role_stem="tas",
        query=Query(
            project=("CMIP6",),
            variable=("tas",),
            experiment=("historical",),
            reporting_interval=("mon",),
            processing_id=("Amon",),
        ),
        endpoints=(
            Endpoint("CMIP6", "esg-dn1.nsc.liu.se", "nsc"),
            Endpoint("CMIP6", "esgf-node.ornl.gov", "ornl"),
            Endpoint("CMIP6", "esgf.nci.org.au", "nci"),
            Endpoint("CMIP6", "esgf-data.dkrz.de", "dkrz"),
            Endpoint("CMIP6", "esgf.ceda.ac.uk", "ceda"),
            Endpoint("CMIP6", "search.east.esgf.io", "east"),
            Endpoint("CMIP6", "search.west.esgf.io", "west"),
        ),
        # Watched, not fetched. We want to know what CMIP6 holds; we do not want all
        # of it on this disk.
        #
        # Turning this on would also be a trap today, and not the one you might
        # expect. Five of these seven endpoints are Solr, which publishes no
        # per-file URLs on a dataset document, so a search against one records a
        # dataset with no files. `east` and `west` are ESGF-NG, whose STAC assets
        # *are* their files. So roughly 460 of CMIP6's ~1140 dataset versions have
        # files recorded and the other ~680 have none -- and a download plans from
        # the files, so it would quietly fetch the 460 and say nothing at all about
        # the rest. A partial download that reads as a complete one is worse than
        # one that refuses, which is why this stays off until
        # `record_solr_file_access` is wired into esmporium's tracked search.
        download=False,
        detailed=False,
    ),
)
"""
Every question we track, and what to do with its answers

The unit this job is organised around. Adding another question -- another variable,
another frequency, another project -- is adding another of these, and nothing else
has to learn about it: the search loop, the download loop, both emails and both
reports all iterate this list.

The two here are the same scientific question asked of two projects, which is why
they share a `stem`. The database files them apart because the project and endpoint
are appended to it.
"""

RECIPIENTS = (
    "azehrung@student.unimelb.edu.au",
    "zebedee.nicholls@climate-energy-college.org",
)
"""Who gets told what changed, and what was downloaded. Add addresses here."""

SEND_EMAIL = True
"""
Whether to email either report at all
"""

EMAIL_WHEN_NOTHING_CHANGED = True
"""
Whether to send the search report on a morning when nothing changed

On, deliberately: without it, "nothing changed" and "the server is on fire" both
look like an empty inbox, and this is the mail being watched for silence.
"""

EMAIL_WHEN_NOTHING_DOWNLOADED = False
"""
Whether to send the download report on a morning when nothing new arrived

Off, because the search report has already gone out an instant earlier and has
already said there was nothing to fetch. A second "nothing happened" email every day
would only train you to ignore both.
"""

MAX_ROWS = 50
"""How many rows of each table to put in the reports before folding the rest away"""


# ---------------------------------------------------------------------------------
# The job itself.
# ---------------------------------------------------------------------------------

logger = logging.getLogger("cmip-output-tracker")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """
    Read the command line

    Parameters
    ----------
    argv
        Arguments to parse. `None`, the default, reads `sys.argv`.

    Returns
    -------
    :
        The parsed arguments
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--search-only",
        action="store_true",
        help="search and report, and do not download anything",
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help=(
            "download what the last search found, without searching again. For "
            "retrying files which failed, without waiting for tomorrow."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "say what would be downloaded and fetch nothing, sending no mail. How "
            "to size a first run before starting it, and how to check a widened "
            "search before it is turned loose on a disk."
        ),
    )
    args = parser.parse_args(argv)

    if args.search_only and args.download_only:
        parser.error("--search-only and --download-only ask for opposite things")

    return args


def downloading() -> tuple[TrackedRequirement, ...]:
    """
    Get the requirements whose bytes we fetch

    Returns
    -------
    :
        Those of [REQUIREMENTS][] with `download` set, in the same order
    """
    return tuple(each for each in REQUIREMENTS if each.download)


def search_everything(engine: Engine) -> tuple[ReportGroup, ...]:
    """
    Search every endpoint of every requirement, one at a time

    Parameters
    ----------
    engine
        The database to record the runs in

    Returns
    -------
    :
        One group per requirement, carrying one result per endpoint in
        [REQUIREMENTS][] order, including the endpoints which failed
    """
    groups = []
    for tracked in REQUIREMENTS:
        results: list[EndpointResult] = []
        for endpoint in tracked.endpoints:
            logger.info("Searching %s for %s", endpoint.label, tracked.label)
            result = search_endpoint(
                engine, endpoint, requirement_for(tracked, endpoint)
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

        groups.append(
            ReportGroup(
                label=tracked.label,
                detailed=tracked.detailed,
                results=tuple(results),
            )
        )

    return tuple(groups)


def plan_everything(engine: Engine, root: Path) -> tuple[DownloadIntent, ...]:
    """
    Work out what every downloading requirement is about to fetch

    A database read and a `stat` per file, no network, so this is cheap enough to
    run before the first email rather than after -- which is the whole point of it.

    Parameters
    ----------
    engine
        The database to plan from

    root
        The directory the files would go into

    Returns
    -------
    :
        One intent per requirement which downloads, in [REQUIREMENTS][] order
    """
    intents = []
    for tracked in downloading():
        # One pass, not two. `skip_done=False` returns every file we know of with
        # `already_here` set on each, so this yields both "what is outstanding" and
        # "how much exists at all" -- and the second is what lets an empty plan say
        # whether we have everything or have simply never searched.
        everything = plan_only(engine, tracked.query, root, skip_done=False)
        outstanding = [item for item in everything if not item.already_here]
        intents.append(
            describe_plan(engine, tracked.label, outstanding, n_known=len(everything))
        )

    return tuple(intents)


def with_download_blocks(
    groups: Sequence[ReportGroup],
    intents: Sequence[DownloadIntent],
    *,
    root: Path | None,
) -> tuple[ReportGroup, ...]:
    """
    Put each requirement's download plan inside its own section of the report

    Matched by label, so a group with nothing to download is returned untouched and
    a question which is only ever watched never grows a heading about downloading.

    Parameters
    ----------
    groups
        The search results, one group per requirement

    intents
        What each downloading requirement is about to fetch

    root
        Where the files would go. `None` when nothing downloads, in which case the
        groups come back as they went in.

    Returns
    -------
    :
        The groups, with an appendix on the ones that have a plan
    """
    if root is None or not intents:
        return tuple(groups)

    by_label = {intent.label: intent for intent in intents}

    return tuple(
        replace(
            group,
            appendix=format_download_intent(
                [by_label[group.label]],
                root=root,
                facets=IDENTITY_FACETS,
                max_rows=MAX_ROWS,
            ),
        )
        if group.label in by_label
        else group
        for group in groups
    )


def download_everything(engine: Engine, root: Path) -> tuple[DownloadGroup, ...]:
    """
    Fetch what every downloading requirement asks for and we have not got

    Parameters
    ----------
    engine
        The database to plan from and record into

    root
        The directory to download into

    Returns
    -------
    :
        One group per requirement which downloads, in [REQUIREMENTS][] order
    """
    groups = []
    for tracked in downloading():
        logger.info("Downloading %s", tracked.label)
        results = run_downloads(engine, tracked.query, root)
        logger.info("%s: %s", tracked.label, totals(results))

        groups.append(
            DownloadGroup(
                # The requirement, not just its project: once two questions share a
                # project, the project stops identifying which asked for the bytes.
                label=f"{tracked.stem} \u00b7 {tracked.label}",
                records=describe_results(engine, results),
                totals=totals(results),
            )
        )

    return tuple(groups)


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


def try_to_notify(subject: str, body: str) -> None:
    """
    Email one message, and carry on if the mail server will not take it

    Used for the *search* report only. A mail server being down is not a reason to
    miss a day of data, and the download writes its own records whether or not
    anybody was told. The failure is logged and goes to stderr, which is where
    cron's `MAILTO` picks it up, so it is not silent.

    Parameters
    ----------
    subject
        Subject line

    body
        Body of the message, as plain text
    """
    try:
        notify(subject, body)
    except Exception:
        logger.exception("Could not send the search report; carrying on anyway")


def report_failure(what: str) -> None:
    """
    Try to email a traceback, and never let that failure replace the original

    Parameters
    ----------
    what
        Which half of the job died, for the subject line
    """
    try:
        notify(
            f"[FAILED] {SEARCH_STEM} {what}",
            f"The {what} did not finish. The traceback was:\n\n"
            f"{traceback.format_exc()}",
        )
    except Exception:
        logger.exception("Could not email the failure either")


def main(argv: list[str] | None = None) -> None:
    """
    Search, report, download, report

    In that order, and in one process, so that "the download sees what this morning's
    search found" is a fact about the code rather than a convention in the crontab.
    The search report is emailed *before* the download starts, so a long download
    cannot make the morning mail late.

    Parameters
    ----------
    argv
        Arguments to parse. `None`, the default, reads `sys.argv`.

    Raises
    ------
    Exception
        Anything the database or the migration raises, and anything the download
        raises which is not one file failing to arrive. One endpoint failing to
        answer is not one of these, and neither is one file failing to download:
        both are reported in a body and the job carries on.
    """
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    # Secrets and machine-specific paths come from the .env file beside this
    # repository, never from a tracked file. Without `override`, anything already
    # exported wins, which is what you want when debugging by hand.
    load_dotenv(REPO_ROOT / ".env")

    will_download = bool(downloading()) and not args.search_only
    # Read before anything touches the network, so a missing DOWNLOAD_ROOT fails in
    # the first second rather than after a seven-endpoint search.
    root = DownloadSettings.from_env().root if will_download else None

    engine = open_database(DB_PATH)
    logger.info("Using database %s", DB_PATH)

    if args.dry_run:
        if root is None:
            logger.info("Nothing is configured to download")

            return

        print(
            format_download_intent(
                plan_everything(engine, root),
                root=root,
                facets=IDENTITY_FACETS,
                max_rows=MAX_ROWS,
            )
        )

        return

    # --- search, and report it -------------------------------------------------
    if not args.download_only:
        try:
            groups = search_everything(engine)
        except Exception:
            # Getting here means something broke which is not one endpoint being
            # down -- the database, the migration, a requirement we cannot even
            # build. Without this, the only symptom is mail that never arrives.
            logger.exception("The search failed")
            report_failure("search")

            raise

        intents = plan_everything(engine, root) if root is not None else ()
        rendered = format_report(
            with_download_blocks(groups, intents, root=root),
            max_rows=MAX_ROWS,
            facets=PATTERN_EFFECT_FACETS,
        )

        print(rendered)

        if not SEND_EMAIL:
            logger.info("SEND_EMAIL is off, so no mail")
        else:
            anything_to_say = any(
                result.summary is None
                or result.summary.any_changes
                or not result.summary.run.complete
                for group in groups
                for result in group.results
            )
            if anything_to_say or EMAIL_WHEN_NOTHING_CHANGED:
                try_to_notify(report_subject(groups, stem=SEARCH_STEM), rendered)
            else:
                logger.info(
                    "Nothing changed and EMAIL_WHEN_NOTHING_CHANGED is off, no mail"
                )

    if root is None:
        return

    # --- download, and report that ---------------------------------------------
    try:
        download_groups = download_everything(engine, root)
    except Exception:
        logger.exception("The download failed")
        report_failure("downloads")

        raise

    with Session(engine) as session:
        held = download_summary(session)

    rendered = format_download_report(
        download_groups,
        held,
        root=root,
        facets=IDENTITY_FACETS,
        max_rows=MAX_ROWS,
    )
    print(rendered)

    if not SEND_EMAIL:
        logger.info("SEND_EMAIL is off, so no mail")

        return

    nothing_happened = all(
        not group.totals.n_fetched and not group.totals.n_failed
        for group in download_groups
    )
    if nothing_happened and not EMAIL_WHEN_NOTHING_DOWNLOADED:
        logger.info("Nothing new and EMAIL_WHEN_NOTHING_DOWNLOADED is off, so no mail")

        return

    notify(download_subject(download_groups, stem=SEARCH_STEM), rendered)


if __name__ == "__main__":
    main()
