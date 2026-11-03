"""
Summarise how the ESGF data nodes have behaved for us, and print it

A read-only report over what the download half has already been recording. Nothing
here searches, downloads, emails or writes: it is the thing to run when somebody asks
"which nodes are actually serving us" and you would rather answer with the database
than with a feeling.

    uv run python scripts/data-node-health.py              # everything we know
    uv run python scripts/data-node-health.py --days 1     # just last night's run
    uv run python scripts/data-node-health.py --days 7     # how they are doing lately
    uv run python scripts/data-node-health.py --db /other/path.sqlite

The numbers come from `datanodecallrecord`, which gets one row per *HTTP attempt* --
not per file -- because `local.downloads.run_downloads` passes
`record_data_node_calls` as its observer. So a file fetched on the third try from the
second mirror contributed four rows, and `attempts` is larger than any file count.

**Read the `IN PARALLEL` block before reading the `MB/s` column.** Since 2026-11-03
the daily job fetches eight files at once, and that does not make any one transfer
faster -- sharing an uplink makes each slightly slower, if anything. `MB/s` is
esmporium's median *per attempt*, so on its own a parallel run looks like a
marginally worse sequential one. The figures that parallelism actually improves are
in that block: how many transfers were in flight, and the aggregate rate.

The time at the foot is transfer time summed over every attempt, including the failed
ones. It leaves out retry backoff and anything that is not a fetch, and it is now
*worker* time rather than wall-clock: with eight workers it can be several times
longer than anybody waited. Where it and the first-to-last span differ by more than a
minute both are reported, and the report says which way round it came out -- a longer
span is idle time between runs, a shorter one is transfers overlapping.

**This has to be run on the machine that did the downloading.** The database is
git-ignored, so the copy in a clone has the table and none of the rows.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

from esmporium.db import aggregate_data_node_health, configure_sqlite_for_concurrency
from sqlalchemy.exc import OperationalError
from sqlmodel import create_engine

from local.node_health import (
    fetch_time,
    format_node_health,
    measure_parallelism,
    read_transfers,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
"""
Root of this repository

Worked out from this file rather than from the working directory, for the same reason
`scripts/drive-daily.py` does it: so this can be run from anywhere.
"""

DB_VERSION_ID = "002"
"""
Which database to read

**Must match `DB_VERSION_ID` in `scripts/drive-daily.py`**, which is the script that
writes the rows. Duplicated rather than imported because that file's name is not a
Python identifier, so importing it needs `importlib` and a comment longer than this
one. If the daily job is bumped to `003` and this is not, the symptom is a report
saying nothing has ever been downloaded -- which is why that case is spelled out in
the output rather than left as an empty table.
"""

DEFAULT_DB_PATH = REPO_ROOT / "dbs" / f"{DB_VERSION_ID}_db.sqlite"
"""Where the daily job keeps its records"""


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
        "--days",
        type=int,
        default=None,
        metavar="N",
        help=(
            "only count attempts from the last N days. The default is all of "
            "history, which is what you want to describe a bulk download that has "
            "already happened; a window is what you want to ask how the nodes are "
            "behaving now. Note this is *not* esmporium's own default of 7 days -- "
            "a report should not quietly leave rows out."
        ),
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        metavar="PATH",
        help=f"the database to read (default: {DEFAULT_DB_PATH})",
    )

    args = parser.parse_args(argv)

    if args.days is not None and args.days < 1:
        parser.error("--days has to be at least 1; leave it out for all of history")

    return args


def window_phrase(days: int | None) -> str:
    """
    Say how far back the numbers go, for the report's heading

    Parameters
    ----------
    days
        How many days back, or `None` for all of history

    Returns
    -------
    :
        The phrase, e.g. `'all history'` or `'the last 7 days, since 2026-10-27'`

        The absolute date is included because a report gets forwarded, and "the last
        7 days" stops being true the moment it is read rather than run.
    """
    if days is None:
        return "all history"

    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    plural = "day" if days == 1 else "days"

    return f"the last {days} {plural}, since {since:%Y-%m-%d}"


def main(argv: list[str] | None = None) -> int:
    """
    Read the health out of the database and print it

    Parameters
    ----------
    argv
        Arguments to parse. `None`, the default, reads `sys.argv`.

    Returns
    -------
    :
        An exit status: `0` if the report was printed, `1` if it could not be
    """
    args = parse_args(argv)

    # Checked by hand, because SQLite would otherwise *create* an empty database at a
    # mistyped path and this would print a confident report about a file it had just
    # invented.
    if not args.db.is_file():
        print(
            f"No database at {args.db}.\n"
            "The records live on the machine that did the downloading, and `dbs/` is "
            "git-ignored, so a fresh clone has none of them. Pass --db if it is "
            "somewhere else.",
            file=sys.stderr,
        )

        return 1

    # Deliberately *not* `local.database.open_database`: that migrates to head, and a
    # read-only report has no business altering the database it is reporting on -- all
    # the more so when the obvious thing to point it at is a copy of the production
    # one. `configure_sqlite_for_concurrency` is still wanted, for its `busy_timeout`:
    # without it, running this while the nightly download holds the write lock fails
    # immediately with "database is locked".
    engine = configure_sqlite_for_concurrency(create_engine(f"sqlite:///{args.db}"))

    since = None if args.days is None else datetime.timedelta(days=args.days)
    try:
        health = aggregate_data_node_health(engine, since=since)
        # The same `since`, deliberately: two reads of one table, so the timings
        # cannot end up describing a different window than the table above them. A
        # second read rather than one, because classifying outcomes is esmporium's
        # job and only the timing is missing from what it hands back.
        #
        # One read for both timing figures, though: the parallelism and the total are
        # the same rows looked at two ways, and reading them twice would be a way for
        # the two to disagree.
        transfers = read_transfers(engine, since=since)
        took = fetch_time(transfers)
        parallelism = measure_parallelism(transfers)
    except OperationalError as exc:
        # The likeliest cause by far is an older database: `datanodecallrecord`
        # arrived with the download tables, so `001_db.sqlite` has search history and
        # nothing else.
        print(
            f"Could not read download health out of {args.db}: {exc.orig}\n"
            "If that is a missing table, this database predates the download half. "
            f"The daily job writes to {DEFAULT_DB_PATH.name}.",
            file=sys.stderr,
        )

        return 1

    print(f"Database: {args.db}")
    print()
    print(
        format_node_health(
            health,
            took,
            window=window_phrase(args.days),
            parallelism=parallelism,
        ),
        end="",
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
