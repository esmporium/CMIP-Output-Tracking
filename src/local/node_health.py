"""
How the ESGF data nodes have behaved, laid out for reading

The same division of labour [local.download_formatting][] has: esmporium records an
attempt and aggregates the attempts
([esmporium.db.aggregate_data_node_health][]), and we arrange the result. Everything
here is a pure function of a mapping of
[DataNodeHealth][esmporium.db.DataNodeHealth], which is a plain frozen dataclass, so
none of it needs a database.

Worth knowing before reading the numbers: a data node only appears here once we have
actually *fetched* from it. The search endpoints are different machines doing a
different job and are measured separately
(see [esmporium.db.aggregate_host_health][]), so nothing in this report says anything
about whether `search.east.esgf.io` is answering.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from local.download_formatting import readable_bytes
from local.summary_formatting import render_table

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from esmporium.db import DataNodeHealth

_HEADERS = ("node", "attempts", "ok", "failed", "ok%", "MB/s", "served")
"""
The columns, in reading order

`failed` is narrower than it sounds and `ok%` is ours rather than esmporium's; both
are explained on [attributable_failures][(m).] and [ok_rate][(m).].
"""

_RIGHT = ("attempts", "ok", "failed", "ok%", "MB/s", "served")
"""
Which columns are right-justified

Everything but the node's name: these are numbers meant to be compared down the
column, and the name is the only thing read across the row.
"""


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
        "-" if throughput is None else f"{throughput:.1f}",
        readable_bytes(health.total_bytes),
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


def _footer(healths: Sequence[DataNodeHealth]) -> str:
    """
    Render the line which says what the whole table comes to

    Parameters
    ----------
    healths
        Every node's health

    Returns
    -------
    :
        The line
    """
    n_attempts = sum(health.n_calls for health in healths)
    nodes = f"{len(healths)} node{'' if len(healths) == 1 else 's'}"
    attempts = f"{n_attempts} attempt{'' if n_attempts == 1 else 's'}"
    served = readable_bytes(sum(health.total_bytes for health in healths))

    return f"  {nodes}, {attempts}, {served} served"


def format_node_health(
    health_by_node: Mapping[str, DataNodeHealth], *, window: str
) -> str:
    """
    Render what we know about the data nodes we have fetched from

    Parameters
    ----------
    health_by_node
        What we know, keyed by node name, from
        [esmporium.db.aggregate_data_node_health][]

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
    lines.append(_footer(healths))

    rendered = "\n".join(lines)

    return "\n".join(line.rstrip() for line in rendered.splitlines()) + "\n"
