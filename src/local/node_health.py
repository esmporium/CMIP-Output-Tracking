"""
How the ESGF data nodes have behaved, laid out for reading

Mostly the division of labour [local.download_formatting][] has: esmporium records an
attempt and aggregates the attempts
([esmporium.db.aggregate_data_node_health][]), and we arrange the result. The
rendering is a pure function of a mapping of
[DataNodeHealth][esmporium.db.DataNodeHealth], which is a plain frozen dataclass, so
the layout can be tested without a database.

[read_fetch_time][(m).] is the exception, and the reason is simply that esmporium
throws the number away: `DataNodeHealth` carries `median_throughput_mb_per_s` and
`total_bytes` but no time at all, so "how long did it take" has to be read off
[DataNodeCallRecord][esmporium.db.schema.DataNodeCallRecord] directly. Reading a row
ourselves is not new here -- `local.downloads` does it to put a model's name beside a
downloaded file -- but the aggregation stays upstream, so the outcome classification
has one home.

Worth knowing before reading the numbers: a data node only appears here once we have
actually *fetched* from it. The search endpoints are different machines doing a
different job and are measured separately
(see [esmporium.db.aggregate_host_health][]), so nothing in this report says anything
about whether `search.east.esgf.io` is answering.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING

from esmporium.db import DataNodeCallRecord
from sqlmodel import Session, col, select

from local.download_formatting import readable_bytes
from local.summary_formatting import render_table

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from esmporium.db import DataNodeHealth
    from sqlalchemy.engine import Engine

_HEADERS = ("node", "attempts", "ok", "failed", "ok%", "served", "MB/s")
"""
The columns, in reading order

There is deliberately no per-node time column: how long each node took is only
interesting divided by what it served, which is `MB/s`, and the one time figure worth
having is the total -- see [_span_lines][(m).]. `failed` is narrower than it sounds
and `ok%` is ours rather than esmporium's; both are explained on
[attributable_failures][(m).] and [ok_rate][(m).].
"""

_RIGHT = ("attempts", "ok", "failed", "ok%", "served", "MB/s")
"""
Which columns are right-justified

Everything but the node's name: these are numbers meant to be compared down the
column, and the name is the only thing read across the row.
"""


@dataclass(frozen=True)
class FetchTime:
    """
    How long the fetching took, across every data node

    One figure for the whole window rather than one per node, because per node it is
    only interesting divided by what was served -- which is `MB/s`, and esmporium
    already works that out.

    Separate from [DataNodeHealth][esmporium.db.DataNodeHealth] because esmporium
    keeps none of it: see this module's docstring.
    """

    seconds: float
    """
    How long was spent fetching, summed over every attempt

    **Transfer time, not elapsed time.** esmporium measures each attempt from just
    before the request to just after the checksum, so this covers connecting,
    streaming and verifying, and leaves out the backoff waits between retries, the
    planning, and everything written to the database. Summing is honest today because
    `esmporium.downloads.run` is sequential on purpose; if downloads are ever
    parallelised, this becomes worker-time rather than wall-clock and
    [_span_lines][(m).] has to change its wording with it.

    Failed attempts are included. A node that burns thirty seconds timing out has
    cost us thirty seconds, and leaving that out would make a bad night look quick.
    """

    first: datetime.datetime
    """
    When the earliest attempt in the window was recorded, in UTC

    Naive, because SQLite hands `created_at` back without its timezone even though it
    was stored with one. Treated and labelled as UTC throughout, never localised.
    """

    last: datetime.datetime
    """When the latest attempt in the window was recorded, in UTC. Naive, as above."""

    @property
    def elapsed(self) -> datetime.timedelta:
        """
        How long between the first attempt and the last

        Not the same as [seconds][(c).seconds] and, over more than one run, very much
        larger -- it includes the retry backoff, the rest of each run, and the whole
        day between runs. See [_span_lines][(m).] for why both are reported and
        neither is averaged into the other.
        """
        return self.last - self.first


def _cutoff(
    since: datetime.timedelta | datetime.datetime | None,
) -> datetime.datetime | None:
    """
    Turn a `since` argument into an absolute cut-off

    Mirrors `esmporium.db.download_health._cutoff` rather than importing it, which is
    private. Worth keeping identical: a timing read over a different window than the
    health read beside it would put two inconsistent figures in one report.

    Parameters
    ----------
    since
        How far back to look: a `timedelta`, an absolute `datetime`, or `None` for all
        of history

    Returns
    -------
    :
        The cut-off, or `None` to look at everything
    """
    if since is None:
        return None

    if isinstance(since, datetime.timedelta):
        return datetime.datetime.now(datetime.timezone.utc) - since

    return since


def read_fetch_time(
    engine: Engine,
    *,
    since: datetime.timedelta | datetime.datetime | None = None,
) -> FetchTime | None:
    """
    Read how long the fetching has taken us

    Here rather than upstream only because esmporium throws the number away:
    `DataNodeHealth` carries `median_throughput_mb_per_s` and `total_bytes` but no
    time at all, so this goes to
    [DataNodeCallRecord][esmporium.db.schema.DataNodeCallRecord] itself.

    Parameters
    ----------
    engine
        The database to read from

    since
        How far back to look, with the same meaning as esmporium's own `since`: a
        `timedelta` is "this long ago", a `datetime` an absolute cut-off, `None` all
        of history.

        **Pass whatever was passed to
        [esmporium.db.aggregate_data_node_health][]**, or the time will be describing
        a different window than the table above it.

    Returns
    -------
    :
        The time spent, or `None` if there are no attempts in the window at all

        `None` rather than a zeroed record, so that "nothing has been fetched" cannot
        be printed as "it took no time".
    """
    statement = select(DataNodeCallRecord.seconds, DataNodeCallRecord.created_at)

    cutoff = _cutoff(since)
    if cutoff is not None:
        # Filtered in SQL rather than in Python for the reason esmporium gives at the
        # same point: `created_at` is stored timezone-aware and comes back naive, so
        # comparing it to an aware cutoff in Python raises `TypeError`. Binding the
        # parameter has SQLAlchemy serialise both sides the same way.
        statement = statement.where(col(DataNodeCallRecord.created_at) >= cutoff)

    with Session(engine) as session:
        rows = session.exec(statement).all()

    if not rows:
        return None

    # Summed here rather than with `func.sum` and `func.min` in SQL. The row count is
    # one per fetch attempt -- a few hundred for the CMIP7 archive, and still only
    # tens of thousands after a year of nightly runs -- so pulling two columns costs
    # nothing, and it keeps the `created_at` values plain Python datetimes instead of
    # depending on what SQLite's `min()` hands back through SQLAlchemy.
    return FetchTime(
        seconds=sum(took for took, _ in rows),
        first=min(created_at for _, created_at in rows),
        last=max(created_at for _, created_at in rows),
    )


_SECONDS_PER_MINUTE = 60
"""How many seconds a minute is worth"""

_SECONDS_PER_HOUR = 60 * 60
"""How many seconds an hour is worth"""

_SECONDS_PER_DAY = 24 * 60 * 60
"""How many seconds a day is worth"""

_SPAN_WORTH_SAYING_SECONDS = 60
"""
How much idle time there has to be before the span is reported as well

Below a minute the span and the summed fetching time are the same story told twice:
a sequential run records each attempt as the one before it finishes, so the two agree
to within the first attempt's own length. Above it there is a gap -- retry backoff, or
a whole day between nightly runs -- and a reader who is told only the span would read
it as the download time.
"""

_PRECISE_BELOW_SECONDS = 10
"""
Below how many seconds a duration gets a decimal place

A single attempt against a nearby node is over in a fraction of a second, and `0s`
would read as "no time at all" rather than as "fast".
"""


def readable_duration(seconds: float) -> str:
    """
    Render a duration the way a person would say it

    Two units at most, and never a unit that is zero: `4h 44m` rather than
    `4h 44m 3s`, because the seconds in a four-hour download are noise, and
    `3h` rather than `3h 0m`.

    Parameters
    ----------
    seconds
        How many seconds

    Returns
    -------
    :
        The duration, in words

    Examples
    --------
    >>> readable_duration(0.42)
    '0.4s'
    >>> readable_duration(18)
    '18s'
    >>> readable_duration(252)
    '4m 12s'
    >>> readable_duration(11520)
    '3h 12m'
    >>> readable_duration(10800)
    '3h'
    >>> readable_duration(183600)
    '2d 3h'
    """
    if seconds < _PRECISE_BELOW_SECONDS:
        return f"{seconds:.1f}s"

    if seconds < _SECONDS_PER_MINUTE:
        return f"{round(seconds)}s"

    if seconds < _SECONDS_PER_HOUR:
        whole = round(seconds)
        rest = whole % _SECONDS_PER_MINUTE

        return f"{whole // _SECONDS_PER_MINUTE}m" + (f" {rest}s" if rest else "")

    if seconds < _SECONDS_PER_DAY:
        minutes = round(seconds / _SECONDS_PER_MINUTE)
        rest = minutes % _SECONDS_PER_MINUTE

        return f"{minutes // _SECONDS_PER_MINUTE}h" + (f" {rest}m" if rest else "")

    hours = round(seconds / _SECONDS_PER_HOUR)
    rest = hours % 24

    return f"{hours // 24}d" + (f" {rest}h" if rest else "")


def attributable_failures(health: DataNodeHealth) -> int:
    """
    Count the failures which are the data node's fault

    Which is not every failure it returned. A 404 means a route we recorded has gone
    away, a 401 means we did not present credentials, and a `SIZE_MISMATCH` means the
    search index published the wrong size -- in all three the node answered us
    correctly, and esmporium excludes them deliberately (see
    [DownloadOutcome.counts_against_data_node][esmporium.datasets.DownloadOutcome.counts_against_data_node]).

    Parameters
    ----------
    health
        The node's health

    Returns
    -------
    :
        How many attempts failed in a way that counts against the node

        Read off `n_judged`, which today holds exactly this. See [ok_rate][(m).] for
        why that is worth a function of its own rather than being inlined.
    """
    return health.n_judged


def ok_rate(health: DataNodeHealth) -> float | None:
    """
    Work out the share of attempts the data node answered properly

    **Deliberately computed here rather than read off
    [DataNodeHealth.failure_rate][esmporium.db.DataNodeHealth.failure_rate].** That
    field does not currently mean what its docstring says it means: `n_judged` leaves
    successes out (`DownloadOutcome.SUCCESS.counts_against_data_node` is `False`), and
    `_summarise` then divides by it, so `n_failed == len(judged)` by construction and
    `failure_rate` can only ever come out as exactly `0.0` or `1.0`. A node which
    succeeded nineteen times out of twenty reports `1.0`, which in a report to a
    colleague would be a straightforwardly false statement about a working node.

    `n_success` and `n_judged` are each sound on their own, so the rate is built from
    those two instead. `tests/unit/test_node_health_formatting.py` pins the upstream
    behaviour this relies on, so an upstream fix fails a test by name rather than
    silently halving every number here.

    Parameters
    ----------
    health
        The node's health

    Returns
    -------
    :
        The share of *attributable* attempts which succeeded, between 0 and 1

        `None` when there is nothing to judge -- a node every one of whose attempts
        was a 404 has told us nothing about itself. That reads as "no evidence"
        rather than as either a perfect or a broken node, which is the distinction
        esmporium is careful about everywhere else and would be a shame to lose in
        the last step.
    """
    failures = attributable_failures(health)
    judged = health.n_success + failures
    if not judged:
        return None

    return health.n_success / judged


_FAILURE_WORDS: tuple[tuple[str, str], ...] = (
    ("n_timeout", "timed out"),
    ("n_blocked", "refused us (403, 429 or 503)"),
    ("n_host_fault", "never answered"),
    ("n_checksum_failed", "served bytes that failed their checksum"),
    ("n_missing", "had nothing there (404), which is not the node's fault"),
    ("n_needs_credentials", "wanted credentials (401), which is not the node's fault"),
)
"""
How to say each counted failure, in the order to say them

Worst first, and the two which are not the node's fault last and labelled as such --
they are in `n_calls` but not in [attributable_failures][(m).], so a reader adding
the columns up needs telling where they went.
"""


def describe_failures(health: DataNodeHealth) -> list[str]:
    """
    Say how a data node's failed attempts failed, in words

    One phrase per kind of failure that actually happened, so a node which only ever
    timed out gets one clause rather than six zeros.

    Every non-success attempt is accounted for, including the ones esmporium does not
    count separately: `SHORT_READ`, `SIZE_MISMATCH` and `TRANSPORT_ERROR` have no
    field of their own on
    [DataNodeHealth][esmporium.db.DataNodeHealth], so whatever is left over after the
    six that do is reported as `other`. Without that, a node whose every transfer was
    cut off part way would show a dismal `ok%` beside an empty explanation.

    Parameters
    ----------
    health
        The node's health

    Returns
    -------
    :
        The phrases, in [_FAILURE_WORDS][(m).] order, with any remainder last

        Empty when nothing failed.
    """
    phrases = [
        f"{count} {words}"
        for attribute, words in _FAILURE_WORDS
        if (count := getattr(health, attribute))
    ]

    counted = sum(getattr(health, attribute) for attribute, _ in _FAILURE_WORDS)
    other = health.n_calls - health.n_success - counted
    if other > 0:
        phrases.append(f"{other} other (a short read, a bad size, or a reset)")

    return phrases


def _row(health: DataNodeHealth) -> list[str]:
    """
    Render one data node's line of the table

    Parameters
    ----------
    health
        The node's health

    Returns
    -------
    :
        One cell per heading in [_HEADERS][(m).]
    """
    rate = ok_rate(health)
    throughput = health.median_throughput_mb_per_s

    return [
        health.data_node,
        str(health.n_calls),
        str(health.n_success),
        str(attributable_failures(health)),
        "-" if rate is None else f"{rate:.0%}",
        readable_bytes(health.total_bytes),
        "-" if throughput is None else f"{throughput:.1f}",
    ]


def _in_reading_order(
    health_by_node: Mapping[str, DataNodeHealth],
) -> list[DataNodeHealth]:
    """
    Sort the nodes the way somebody reading for trouble would want them

    Worst first, rather than alphabetically as the rest of this repository's tables
    are: the reason to open this report is to find out whether a node has gone bad,
    and in a list of nine the one that has should not be eighth. Ties -- which on a
    clean run is every row -- break on how much we asked of the node, so the one the
    download actually leaned on is at the top.

    Parameters
    ----------
    health_by_node
        What we know, keyed by node name

    Returns
    -------
    :
        The healths, worst first

        A node with nothing to judge sorts among the healthy ones, because no
        evidence is not evidence of a problem.
    """
    return sorted(
        health_by_node.values(),
        key=lambda health: (
            ok_rate(health) if ok_rate(health) is not None else 1.0,
            -health.n_calls,
            health.data_node,
        ),
    )


def _footer(healths: Sequence[DataNodeHealth], took: FetchTime | None) -> list[str]:
    """
    Render the lines which say what the whole table comes to

    Parameters
    ----------
    healths
        Every node's health

    took
        How long the fetching took, or `None` if we were not given it

    Returns
    -------
    :
        The lines: what was served, and -- when there is a timing -- how long it took
    """
    n_attempts = sum(health.n_calls for health in healths)
    nodes = f"{len(healths)} node{'' if len(healths) == 1 else 's'}"
    attempts = f"{n_attempts} attempt{'' if n_attempts == 1 else 's'}"
    served = readable_bytes(sum(health.total_bytes for health in healths))

    lines = [f"  {nodes}, {attempts}, {served} served"]
    if took is not None:
        lines.extend(_span_lines(took))

    return lines


def _span_lines(took: FetchTime) -> list[str]:
    """
    Say how long the fetching took, both ways, and never conflate them

    There are two honest answers to "how long did the download take" and they can be
    days apart, so both are given and each is labelled:

    - **time spent fetching**, the sum of every attempt. Additive and meaningful over
      any window, and what to quote for "21 GB took us this long". It leaves out the
      retry backoff and everything that is not a transfer, and it is wall-clock only
      because `esmporium.downloads.run` is sequential today.
    - **the span**, from the first attempt to the last. Over one run this is the
      honest end-to-end figure. Over a window covering several nights it is mostly
      the idle days between them, which is why it is given as two dates and not as a
      single duration pretending to be a download time.

    Parameters
    ----------
    took
        How long the fetching took

    Returns
    -------
    :
        The lines: always the fetching time, and the span once there is a gap to
        explain
    """
    span = took.elapsed.total_seconds()
    lines = [
        f"  {readable_duration(took.seconds)} spent fetching, summed over every attempt"
    ]

    # Only worth a second line once there is a gap to explain. See
    # `_SPAN_WORTH_SAYING_SECONDS`.
    if span - took.seconds > _SPAN_WORTH_SAYING_SECONDS:
        lines.append(
            f"  first attempt {took.first:%Y-%m-%d %H:%M} UTC, "
            f"last {took.last:%Y-%m-%d %H:%M} UTC ({readable_duration(span)} apart, "
            "which includes any time between runs)"
        )

    return lines


def format_node_health(
    health_by_node: Mapping[str, DataNodeHealth],
    took: FetchTime | None = None,
    *,
    window: str,
) -> str:
    """
    Render what we know about the data nodes we have fetched from

    Parameters
    ----------
    health_by_node
        What we know, keyed by node name, from
        [esmporium.db.aggregate_data_node_health][]

    took
        How long the fetching took, from [read_fetch_time][(m).], read over **the
        same window** as the health.

        `None` leaves the timing lines out altogether, which is what a caller holding
        only a health mapping should get rather than a figure it has not measured.

    window
        How far back these numbers go, as a phrase to put in the heading, e.g.
        `'all history'` or `'the last 7 days'`

        Passed in rather than worked out here because the caller is the one that
        chose it, and a heading which says `all history` over a week's worth of rows
        is worse than no heading.

    Returns
    -------
    :
        The report, ending in exactly one newline, with no trailing whitespace on
        any line

        The same shape as [local.download_formatting.format_download_report][], so
        this can be dropped into either email later without being reformatted.
    """
    heading = f"DATA NODE HEALTH ({window})"
    if not health_by_node:
        # Said out loud, because an empty table and a database that nobody has
        # downloaded against yet look identical -- and on a repository where the
        # downloads happen on another machine, the second is the likelier of the two.
        return (
            f"{heading}\n"
            "  No fetch attempts recorded in this window.\n"
            "  Either nothing has been downloaded yet, or this is not the database "
            "the download wrote to.\n"
        )

    healths = _in_reading_order(health_by_node)

    # No count line under the heading: the footer already gives the node count, and
    # saying it twice in a table this short is noise.
    lines = [heading]
    table = render_table(_HEADERS, [_row(health) for health in healths], right=_RIGHT)

    # The failure breakdown goes *under* each row rather than in columns of its own.
    # Six more columns would be six columns of zeros on every good morning, and the
    # phrases are long enough to set the table's whole width on their own -- the same
    # call `local.summary_formatting` makes for `what moved`.
    rows = table.splitlines()
    lines.append(rows[0])
    for health, row in zip(healths, rows[1:], strict=True):
        lines.append(row)
        lines.extend(f"      {phrase}" for phrase in describe_failures(health))

    lines.append("")
    lines.extend(_footer(healths, took))

    rendered = "\n".join(lines)

    return "\n".join(line.rstrip() for line in rendered.splitlines()) + "\n"
