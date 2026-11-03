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

from esmporium.db import DataNode, DataNodeCallRecord
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

`at once` is appended to this when the caller has measured the parallelism, and left
off when it has not -- a column of dashes would read as "we never asked this node for
more than one thing at a time", which is a claim rather than an absence.
"""

_PEAK_HEADER = "at once"
"""
What the per-node peak concurrency column is called

Short, because the node names already set the table's width. `peak` on its own was
the first choice and reads as a peak *rate*, which is the one thing in this table it
is not.
"""

_RIGHT = ("attempts", "ok", "failed", "ok%", "served", "MB/s", _PEAK_HEADER)
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

    **Transfer time, and since 2026-11-03 it is worker time, not elapsed time.**
    esmporium measures each attempt from just before the request to just after the
    checksum, so this covers connecting, streaming and verifying, and leaves out the
    backoff waits between retries, the planning, and everything written to the
    database.

    It used to double as the wall clock, because the download loop was sequential and
    so each attempt began as the one before it ended. The daily job now fetches eight
    files at a time, so this sum can be several times the time anybody waited. That
    makes it a different and still useful number -- roughly what the same transfers
    would have cost one after another -- but it is no longer the answer to "how long
    did the download take". [Concurrency.active_seconds][(m).Concurrency] is.

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


BYTES_PER_MB = 1_000_000
"""
How many bytes esmporium counts to a megabyte when it reports throughput

Decimal, not binary, and restated here because our own
[local.download_formatting.readable_bytes][] steps by 1024. So the `served` column
and the `MB/s` column beside it are in different units already, upstream of us. Our
aggregate figure uses this one, because the number it has to be comparable with is
the `MB/s` column, not the `served` one.
"""


@dataclass(frozen=True)
class Transfer:
    """
    One recorded fetch attempt, as an interval in time

    The unit [measure_parallelism][(m).] works in. A
    [DataNodeHealth][esmporium.db.DataNodeHealth] is a per-node total and so cannot
    say whether two transfers overlapped; this can, which is the whole reason it
    exists.

    **The interval is honest, and that took checking.** `seconds` is measured inside
    `esmporium.downloads.fetch_to_path`, which starts its clock *after* the per-node
    limiter has handed out a slot -- so it is transfer time with no queue wait folded
    into it. And `created_at` is set when the record is constructed, which is as soon
    as the fetch returns and before the row is committed, so a run with eight workers
    taking the SQLite write lock in turn does not smear the timestamps. Either of
    those being otherwise would make every overlap below fiction.
    """

    data_node: str
    """Which node served it"""

    finished: datetime.datetime
    """
    When the attempt finished, in UTC

    Naive, because SQLite hands `created_at` back without its timezone even though it
    was stored with one. Treated and labelled as UTC throughout, never localised.
    """

    seconds: float
    """How long the attempt itself took"""

    n_bytes: int
    """How many bytes it moved. Zero for an attempt which failed before any arrived."""

    @property
    def started(self) -> datetime.datetime:
        """
        When the attempt began, worked back from when it finished

        Derived rather than stored because esmporium records one timestamp and one
        duration, not two timestamps.
        """
        return self.finished - datetime.timedelta(seconds=self.seconds)


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


def read_transfers(
    engine: Engine,
    *,
    since: datetime.timedelta | datetime.datetime | None = None,
) -> tuple[Transfer, ...]:
    """
    Read every recorded fetch attempt in the window, as intervals

    The one read this module makes, and everything else here is a pure function of
    what it returns. Joined to [DataNode][esmporium.db.schema.DataNode] because
    `DataNodeCallRecord` keeps a foreign key and the report needs the host name.

    Parameters
    ----------
    engine
        The database to read from

    since
        How far back to look, with the same meaning as esmporium's own `since`: a
        `timedelta` is "this long ago", a `datetime` an absolute cut-off, `None` all
        of history.

        **Pass whatever was passed to
        [esmporium.db.aggregate_data_node_health][]**, or these figures will be
        describing a different window than the table above them.

    Returns
    -------
    :
        One per attempt, in no particular order

        Empty when nothing was fetched in the window.
    """
    statement = select(
        DataNode.data_node,
        DataNodeCallRecord.created_at,
        DataNodeCallRecord.seconds,
        DataNodeCallRecord.bytes_transferred,
    ).join(DataNode, col(DataNodeCallRecord.data_node_id) == col(DataNode.id))

    cutoff = _cutoff(since)
    if cutoff is not None:
        # Filtered in SQL rather than in Python for the reason esmporium gives at the
        # same point: `created_at` is stored timezone-aware and comes back naive, so
        # comparing it to an aware cutoff in Python raises `TypeError`. Binding the
        # parameter has SQLAlchemy serialise both sides the same way.
        statement = statement.where(col(DataNodeCallRecord.created_at) >= cutoff)

    with Session(engine) as session:
        rows = session.exec(statement).all()

    # Pulled into Python rather than aggregated in SQL. The row count is one per fetch
    # attempt -- a few hundred for the CMIP7 archive, and still only tens of thousands
    # after a year of nightly runs -- so this costs nothing, and the overlap sweep in
    # [measure_parallelism][(m).] cannot be expressed in SQLite anyway.
    return tuple(
        Transfer(
            data_node=data_node,
            finished=created_at,
            seconds=seconds,
            n_bytes=n_bytes,
        )
        for data_node, created_at, seconds, n_bytes in rows
    )


def fetch_time(transfers: Sequence[Transfer]) -> FetchTime | None:
    """
    Total up how long the fetching took

    Parameters
    ----------
    transfers
        Every attempt in the window, from [read_transfers][(m).]

    Returns
    -------
    :
        The time spent, or `None` if there are no attempts in the window at all

        `None` rather than a zeroed record, so that "nothing has been fetched" cannot
        be printed as "it took no time".
    """
    if not transfers:
        return None

    return FetchTime(
        seconds=sum(transfer.seconds for transfer in transfers),
        first=min(transfer.started for transfer in transfers),
        last=max(transfer.finished for transfer in transfers),
    )


def read_fetch_time(
    engine: Engine,
    *,
    since: datetime.timedelta | datetime.datetime | None = None,
) -> FetchTime | None:
    """
    Read how long the fetching has taken us

    A convenience over [read_transfers][(m).] and [fetch_time][(m).], kept because
    "how long did it take" is a question worth being able to ask on its own. A caller
    which also wants the parallelism figures should read the transfers once and pass
    them to both, rather than reading the table twice.

    Parameters
    ----------
    engine
        The database to read from

    since
        How far back to look. See [read_transfers][(m).].

    Returns
    -------
    :
        The time spent, or `None` if there are no attempts in the window at all
    """
    return fetch_time(read_transfers(engine, since=since))


@dataclass(frozen=True)
class Concurrency:
    """
    How much was in flight at once, over one window

    The measurement the `MB/s` column cannot give you. That column is esmporium's
    median *per attempt*, and running eight transfers at once does not make any one
    of them faster -- if anything it makes each slightly slower, because they share
    the same uplink. What parallelism buys is more of them at the same time, and this
    is where that shows up.
    """

    n_transfers: int
    """How many attempts this covers"""

    n_bytes: int
    """How many bytes they moved between them"""

    worker_seconds: float
    """
    Every attempt's own time, summed

    What the same transfers would have cost one after another, *if* each had run at
    the speed it managed here. See [mean][(c).mean] for why that "if" flatters us.
    """

    active_seconds: float
    """
    How long at least one transfer was in flight

    The union of the intervals, so idle gaps are excluded -- which is what makes
    every figure here survive a window covering several nightly runs. A span from the
    first attempt to the last would be mostly the days between them.
    """

    span_seconds: float
    """
    From the first attempt starting to the last one finishing, idle time included

    Reported separately from [active_seconds][(c).active_seconds], and never divided
    into anything, for the reason given there.
    """

    peak: int
    """
    The most transfers in flight at any one instant

    Against one node this should come out at or under the run's `per_data_node`; over
    every node at or under its `max_workers`. Those two caps are the whole of how a
    parallel run is paced, so this is the number that says whether they engaged.
    """

    @property
    def mean(self) -> float | None:
        """
        How many transfers were in flight on average, while anything was

        Also the honest speedup over fetching them one at a time, because the two are
        the same division: `worker_seconds / active_seconds`.

        **It flatters us slightly**, and knowing by how much is not possible from the
        records alone. It assumes each transfer would have run at the same speed on
        its own, and a lone transfer usually runs a little quicker for having the
        uplink to itself. So read it as an upper bound on what parallelism bought.

        `None` when nothing was in flight for any measurable time, which happens only
        if every attempt was recorded as instantaneous.
        """
        if not self.active_seconds:
            return None

        return self.worker_seconds / self.active_seconds

    @property
    def aggregate_mb_per_s(self) -> float | None:
        """
        How fast the bytes arrived overall, while anything was being fetched

        The figure to compare against the `MB/s` column: that one is per transfer,
        this one is everything at once. In [BYTES_PER_MB][(m).], to match it.

        `None` under the same condition as [mean][(c).mean].
        """
        if not self.active_seconds:
            return None

        return self.n_bytes / BYTES_PER_MB / self.active_seconds


@dataclass(frozen=True)
class Parallelism:
    """
    How much was in flight at once, overall and per node

    Both, because they answer different questions: the overall figure says whether
    the worker pool was being used, and the per-node ones say whether any single
    archive was being leant on harder than we meant to lean on it.
    """

    overall: Concurrency
    """Across every node at once"""

    by_node: Mapping[str, Concurrency]
    """Per node, keyed by host name"""


def _concurrency(transfers: Sequence[Transfer]) -> Concurrency:
    """
    Sweep a set of intervals for how many overlapped

    Parameters
    ----------
    transfers
        The attempts to measure. Must not be empty.

    Returns
    -------
    :
        What was in flight across them
    """
    # One event per interval end, swept in time order.
    #
    # At a tie, an *end* is taken before a start, so one transfer finishing as another
    # begins is a handoff rather than an overlap. That matters more than it sounds:
    # a worker pool hands its slot straight on, so with files of equal size the ties
    # are exact and frequent, and taking starts first reported a peak of 11 against a
    # pool of 8. It costs the union nothing, because the stretch between two events
    # at the same instant is zero seconds long either way.
    #
    # `-1` sorting before `1` is what gives that ordering, so the tuples sort as they
    # are.
    events: list[tuple[datetime.datetime, int]] = []
    for transfer in transfers:
        events.append((transfer.started, 1))
        events.append((transfer.finished, -1))
    events.sort()

    in_flight = 0
    peak = 0
    active = 0.0
    previous: datetime.datetime | None = None
    for when, delta in events:
        # Credited before the depth changes, because `in_flight` describes the
        # stretch which has just ended rather than the one about to start.
        if previous is not None and in_flight > 0:
            active += (when - previous).total_seconds()

        in_flight += delta
        peak = max(peak, in_flight)
        previous = when

    first = min(transfer.started for transfer in transfers)
    last = max(transfer.finished for transfer in transfers)

    return Concurrency(
        n_transfers=len(transfers),
        n_bytes=sum(transfer.n_bytes for transfer in transfers),
        worker_seconds=sum(transfer.seconds for transfer in transfers),
        active_seconds=active,
        span_seconds=(last - first).total_seconds(),
        peak=peak,
    )


def measure_parallelism(transfers: Sequence[Transfer]) -> Parallelism | None:
    """
    Work out how much of the fetching happened at the same time

    A pure function of [read_transfers][(m).]'s output, so this is testable without a
    database -- the same division the rest of this module has.

    Parameters
    ----------
    transfers
        Every attempt in the window

    Returns
    -------
    :
        What was in flight, overall and per node, or `None` if there were no attempts
    """
    if not transfers:
        return None

    by_node: dict[str, list[Transfer]] = {}
    for transfer in transfers:
        by_node.setdefault(transfer.data_node, []).append(transfer)

    return Parallelism(
        overall=_concurrency(transfers),
        by_node={
            node: _concurrency(of_node) for node, of_node in sorted(by_node.items())
        },
    )


_SECONDS_PER_MINUTE = 60
"""How many seconds a minute is worth"""

_SECONDS_PER_HOUR = 60 * 60
"""How many seconds an hour is worth"""

_SECONDS_PER_DAY = 24 * 60 * 60
"""How many seconds a day is worth"""

_SPAN_WORTH_SAYING_SECONDS = 60
"""
How far the span and the summed fetching time have to diverge before both are given

Below a minute apart they are the same story told twice, which is what a *sequential*
run produces: each attempt begins as the one before it ends, so the two agree to
within the first attempt's own length.

They can now diverge in either direction, and the two mean opposite things:

- **span longer** -- idle time, from retry backoff or from a window covering several
  nightly runs. A reader told only the span would read a week as the download time.
- **span shorter** -- transfers overlapping, i.e. the parallel fetching working. A
  reader told only the sum would think the download took eight times as long as it
  did.

So the comparison is on the absolute difference, and the wording follows whichever
way round it came out.
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


def _row(health: DataNodeHealth, peak: int | None = None) -> list[str]:
    """
    Render one data node's line of the table

    Parameters
    ----------
    health
        The node's health

    peak
        The most transfers in flight against this node at once, or `None` to leave
        the column off

    Returns
    -------
    :
        One cell per heading in [_HEADERS][(m).], plus the peak when given
    """
    rate = ok_rate(health)
    throughput = health.median_throughput_mb_per_s

    cells = [
        health.data_node,
        str(health.n_calls),
        str(health.n_success),
        str(attributable_failures(health)),
        "-" if rate is None else f"{rate:.0%}",
        readable_bytes(health.total_bytes),
        "-" if throughput is None else f"{throughput:.1f}",
    ]
    if peak is not None:
        cells.append(str(peak))

    return cells


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
    days apart or a factor of eight apart, so both are given and each is labelled:

    - **time spent fetching**, the sum of every attempt. Additive over any window,
      and since the job went parallel this is worker time: roughly what the same
      transfers would have cost one after another. It leaves out retry backoff and
      everything that is not a transfer.
    - **the span**, from the first attempt starting to the last one finishing. Over
      one run this is the end-to-end figure somebody actually waited through. Over a
      window covering several nights it is mostly the idle days between them, which
      is why it is given as two dates and a duration rather than as a download time.

    Parameters
    ----------
    took
        How long the fetching took

    Returns
    -------
    :
        The lines: always the fetching time, and the span once the two diverge
    """
    span = took.elapsed.total_seconds()
    lines = [
        f"  {readable_duration(took.seconds)} spent fetching, summed over every attempt"
    ]

    # Only worth a second line once the two differ, and the reason they differ is
    # worth saying because it can be either of two opposite things. See
    # `_SPAN_WORTH_SAYING_SECONDS`.
    if abs(span - took.seconds) > _SPAN_WORTH_SAYING_SECONDS:
        why = (
            "which includes any time between runs"
            if span > took.seconds
            else "less than the sum above, because transfers overlapped"
        )
        lines.append(
            f"  first attempt {took.first:%Y-%m-%d %H:%M} UTC, "
            f"last {took.last:%Y-%m-%d %H:%M} UTC ({readable_duration(span)} apart, "
            f"{why})"
        )

    return lines


def _busiest(
    by_node: Mapping[str, Concurrency],
) -> tuple[str, Concurrency] | None:
    """
    Find the node we had the most going against at once

    Parameters
    ----------
    by_node
        Each node's concurrency

    Returns
    -------
    :
        The node's name and its concurrency, or `None` if there are no nodes

        Ties break on the node's name, so the line this feeds does not change from
        run to run for no reason.
    """
    if not by_node:
        return None

    return max(by_node.items(), key=lambda item: (item[1].peak, item[0]))


def _parallel_lines(parallelism: Parallelism) -> list[str]:
    """
    Say how much was in flight at once, and what it bought

    The block the `MB/s` column cannot be read as. That column is a median per
    attempt, and fetching eight files at once does not make any one of them quicker;
    it is the number of them that changes. Without this block a parallel run looks
    like a slower sequential one, because per-transfer throughput under contention is
    if anything a little worse.

    Parameters
    ----------
    parallelism
        What was in flight, from [measure_parallelism][(m).]

    Returns
    -------
    :
        The lines, headed `IN PARALLEL` when transfers really did overlap and
        `ONE AT A TIME` when none of them did
    """
    overall = parallelism.overall
    mean = overall.mean
    aggregate = overall.aggregate_mb_per_s

    if mean is None or aggregate is None:
        # Only reachable if every attempt was recorded as instantaneous, which means
        # there is no interval to overlap and nothing here to say.
        return ["IN PARALLEL", "  too little transfer time recorded to measure"]

    if overall.peak <= 1:
        # A sequential run, which is every run this repository did before 2026-11-03
        # and any run since in which the files happened never to coincide. Reported
        # under its own heading and without the comparison lines: "one at a time
        # would have taken 1.0x longer" is true and absurd, and a per-node cap that
        # was never approached is not news.
        #
        # Worth printing rather than skipping, because this is the baseline. A `tas`
        # bulk run and a `tos` one in the same format is how the two get compared.
        return [
            "ONE AT A TIME",
            f"  nothing overlapped, so {aggregate:.1f} MB/s was all of it",
            f"  {readable_duration(overall.active_seconds)} of transfers, back to back",
        ]

    plural = "" if overall.peak == 1 else "s"
    lines = [
        "IN PARALLEL",
        f"  {overall.peak} transfer{plural} in flight at peak, "
        f"{mean:.1f} on average while anything was running",
        f"  {aggregate:.1f} MB/s all together, against the per-transfer median "
        "in the table above",
        f"  {readable_duration(overall.active_seconds)} with something in flight, "
        f"versus {readable_duration(overall.worker_seconds)} of transfer time summed",
    ]

    busiest = _busiest(parallelism.by_node)
    if busiest is not None:
        node, concurrency = busiest
        # Named rather than given as a bare number, because the question it answers
        # is "is one archive taking more of this than we meant" and the answer is
        # only actionable with the host attached. The configured cap lives in
        # `scripts/drive-daily.py` and is deliberately not imported here, so this
        # reports what happened and leaves the comparison to the reader.
        lines.append(
            f"  at most {concurrency.peak} at once against a single node ({node}), "
            "which is what the per-node cap limits"
        )

    # The speedup and the mean are the same division, so it is stated once and the
    # caveat is attached to it rather than repeated.
    lines.append(
        f"  so fetching them one at a time would have taken about "
        f"{mean:.1f}x longer -- an upper bound, since a lone transfer has the "
        "uplink to itself and runs a little quicker"
    )

    return lines


def format_node_health(
    health_by_node: Mapping[str, DataNodeHealth],
    took: FetchTime | None = None,
    *,
    window: str,
    parallelism: Parallelism | None = None,
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

    parallelism
        What was in flight at once, from [measure_parallelism][(m).], measured over
        **the same window** as the health.

        `None` leaves out both the `at once` column and the `IN PARALLEL` block, which
        is what a caller holding only a health mapping should get. Worth passing
        whenever the download being reported on was a parallel one: without it the
        report has no figure that parallelism improves, so a run which went several
        times faster reads as one which went very slightly slower.

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

    peaks = {} if parallelism is None else parallelism.by_node

    def peak_of(health: DataNodeHealth) -> int | None:
        if parallelism is None:
            return None

        # A node in the health table and not in the transfers cannot happen -- both
        # are read from the same rows over the same window -- but zero is the honest
        # answer if it ever did, rather than a crash in a read-only report.
        measured = peaks.get(health.data_node)

        return 0 if measured is None else measured.peak

    headers = _HEADERS if parallelism is None else (*_HEADERS, _PEAK_HEADER)

    # No count line under the heading: the footer already gives the node count, and
    # saying it twice in a table this short is noise.
    lines = [heading]
    table = render_table(
        headers,
        [_row(health, peak_of(health)) for health in healths],
        right=_RIGHT,
    )

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

    if parallelism is not None:
        lines.append("")
        lines.extend(_parallel_lines(parallelism))

    rendered = "\n".join(lines)

    return "\n".join(line.rstrip() for line in rendered.splitlines()) + "\n"
