"""
How a download run is laid out for reading

The same division of labour [local.summary_formatting][] has, and for the same
reason: esmporium knows the database, we know the layout. Everything here is a pure
function of what [local.downloads.describe_results][] hands back, so the email can be
rewritten as often as we like without touching the library.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from local.summary_formatting import _SHORT_HEADERS, _capped, render_table

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from esmporium.datasets import DownloadOutcome
    from esmporium.db import DownloadSummary
    from esmporium.downloads import RunTotals

    from local.downloads import DownloadIntent, DownloadRecord


@dataclass(frozen=True)
class DownloadGroup:
    """
    What one tracked requirement's download run came to

    The download twin of `local.summary_formatting.ReportGroup`, and there for the
    same reason: with two requirements downloading, one undifferentiated table of
    files could not say which question asked for what.
    """

    label: str
    """Which tracked requirement this is the run for"""

    records: tuple[DownloadRecord, ...]
    """Every file it tried, from `local.downloads.describe_results`"""

    totals: RunTotals
    """What its run added up to, from [esmporium.downloads.totals][]"""


_BYTES_PER_STEP = 1024.0
"""What each step of the unit ladder is worth"""

_UNITS = ("B", "KB", "MB", "GB", "TB")
"""
The unit ladder, powers of 1024

Binary powers under decimal names, which is what `df` and every file manager a
climate scientist uses already do. Being correct about `KiB` here would make our
numbers disagree with theirs.
"""


def readable_bytes(n: int) -> str:
    """
    Render a byte count the way a person would say it

    Parameters
    ----------
    n
        The number of bytes

    Returns
    -------
    :
        The count, to one decimal place above a kilobyte

    Examples
    --------
    >>> readable_bytes(0)
    '0 B'
    >>> readable_bytes(52593)
    '51.4 KB'
    >>> readable_bytes(3 * 1024**3)
    '3.0 GB'
    """
    size = float(n)
    for unit in _UNITS:
        if size < _BYTES_PER_STEP or unit == _UNITS[-1]:
            # Whole bytes read oddly to one decimal place: `51.4 KB` is a size, but
            # `512.0 B` is a number of bytes pretending to be one.
            if unit == "B":
                return f"{int(size)} {unit}"

            return f"{size:.1f} {unit}"

        size /= _BYTES_PER_STEP

    raise AssertionError  # pragma: no cover - the loop always returns


def readable_outcome(outcome: DownloadOutcome) -> str:
    """
    Render how an attempt ended, in words

    `str()` on the enum gives `DownloadOutcome.NEEDS_CREDENTIALS`, which is the name
    of a Python object and not the name of anything that happened. The value is
    already the word we want; it only needs its underscores taking out.

    Parameters
    ----------
    outcome
        How the attempt ended

    Returns
    -------
    :
        The outcome, as a person would say it

    Examples
    --------
    >>> from esmporium.datasets import DownloadOutcome
    >>> readable_outcome(DownloadOutcome.NEEDS_CREDENTIALS)
    'needs credentials'
    >>> readable_outcome(DownloadOutcome.MISSING)
    'missing'
    """
    return outcome.value.replace("_", " ")


def _headers(facets: Sequence[str], *extra: str) -> list[str]:
    """
    Build a row of column headings, abbreviating the wide facet names

    Parameters
    ----------
    facets
        The facet names to put first

    extra
        Headings to append after them

    Returns
    -------
    :
        The headings
    """
    return [_SHORT_HEADERS.get(facet, facet) for facet in facets] + list(extra)


def _identity(record: DownloadRecord, facets: Sequence[str]) -> list[str]:
    """
    Render the cells which say which dataset a record belongs to

    Parameters
    ----------
    record
        The record

    facets
        The facet names to render, in order

    Returns
    -------
    :
        One cell per facet, then the version
    """
    return [record.facets.get(facet, "-") for facet in facets] + [record.version]


def _fetched_section(
    records: Sequence[DownloadRecord], *, facets: Sequence[str], max_rows: int
) -> list[str]:
    """
    Render the files this run actually moved

    Parameters
    ----------
    records
        Every record of the run

    facets
        Which facets to show

    max_rows
        How many rows to show before folding the rest away

    Returns
    -------
    :
        The lines, or an empty list if nothing was fetched
    """
    rows = []
    for record in records:
        outcome = record.result.fetch
        if outcome is None or record.result.skipped:
            continue

        rows.append(
            [
                *_identity(record, facets),
                record.filename,
                readable_bytes(outcome.size_on_disk),
                outcome.route.data_node,
                # "we got it, but only from the fourth mirror" is worth seeing.
                str(len(record.result.failures)) if record.result.failures else "-",
            ]
        )

    if not rows:
        return []

    kept, hidden = _capped(rows, max_rows)
    headers = _headers(facets, "version", "file", "size", "from", "after")
    lines = [f"Fetched ({len(rows)})", render_table(headers, kept, right=("size",))]
    if hidden:
        lines.append(f"  ... and {hidden} more")

    return lines


def _failed_section(
    records: Sequence[DownloadRecord], *, facets: Sequence[str], max_rows: int
) -> list[str]:
    """
    Render the files this run could not get

    Parameters
    ----------
    records
        Every record of the run

    facets
        Which facets to show

    max_rows
        How many rows to show before folding the rest away

    Returns
    -------
    :
        The lines, or an empty list if nothing failed
    """
    failed = [record for record in records if not record.result.succeeded]
    if not failed:
        return []

    rows = []
    for record in failed:
        last = record.result.failures[-1] if record.result.failures else None
        rows.append(
            [
                *_identity(record, facets),
                record.filename,
                str(len(record.result.failures)),
                # The outcome rather than the exception's message: the message is as
                # long as whatever the node said, and a table is not the place for it.
                readable_outcome(last.outcome)
                if last is not None
                else "no route offered",
            ]
        )

    kept, hidden = _capped(rows, max_rows)
    headers = _headers(facets, "version", "file", "tried", "ended as")
    lines = [f"Failed ({len(failed)})", render_table(headers, kept, right=("tried",))]
    if hidden:
        lines.append(f"  ... and {hidden} more")

    return lines


def _headline(group: DownloadGroup, *, root: Path) -> list[str]:
    """
    Render the lines which say what one requirement's run came to

    `RunTotals.__str__` would nearly do, but it finishes with a raw byte count, and a
    report which says `112387382 bytes` on one line and `107.2 MB` on the next has
    said the same thing twice and been harder to read for it. Counting in esmporium,
    rendering here.

    Parameters
    ----------
    group
        The requirement's run

    root
        Where the bytes went

    Returns
    -------
    :
        The lines
    """
    totals = group.totals
    counts = [
        f"{totals.n_fetched} fetched",
        f"{totals.n_skipped} already here",
        f"{totals.n_failed} failed",
    ]
    if totals.n_given_up:
        # Worth its own clause: these will not arrive by waiting, so they are the
        # ones that need somebody to look.
        counts.append(f"{totals.n_given_up} for good")

    return [
        f"{group.label} this run",
        f"  {totals.n_files} file(s): {', '.join(counts)}",
        f"  {readable_bytes(totals.bytes_fetched)} moved into {root}",
    ]


def _held_lines(summary: DownloadSummary) -> list[str]:
    """
    Render what the database holds across every run so far

    `DownloadSummary.__str__` would nearly do, but it prints the status keys as they
    are stored, so a report says `given_up` where a person says `given up`. Counting
    in esmporium, rendering here -- the same division as [_headline][(m).].

    Parameters
    ----------
    summary
        What the database holds

    Returns
    -------
    :
        The lines
    """
    by_status = ", ".join(
        f"{count} {status.replace('_', ' ')}"
        for status, count in sorted(summary.n_by_status.items())
    )
    held = f"  {summary.n_total} file(s) ({by_status or 'none yet'})"

    outstanding = (
        f"  {summary.n_outstanding} still to get, so a further run would try again"
        if summary.n_outstanding
        else "  nothing outstanding"
    )

    return ["In the database", held, outstanding]


def format_download_report(
    groups: Sequence[DownloadGroup],
    summary: DownloadSummary,
    *,
    root: Path,
    facets: Sequence[str],
    max_rows: int = 50,
) -> str:
    """
    Render everything a download run did, a block per tracked requirement

    Parameters
    ----------
    groups
        One [DownloadGroup][(m).] per requirement which downloads, in the order to
        show them

    summary
        What the database holds overall, from [esmporium.db.download_summary][].

        Database-wide, and labelled as such: it counts every file we have ever
        downloaded, not just the ones in `groups`.

    root
        Where the files were written, so a reader knows which disk filled up

    facets
        Which facets to identify a file by, e.g.
        [local.downloads.IDENTITY_FACETS][]

    max_rows
        How many rows of each table to show before folding the rest away

    Returns
    -------
    :
        The report, ending in exactly one newline, with no trailing whitespace on
        any line

        Both the same as [local.summary_formatting.format_report][], and for a
        duller reason than symmetry: our own `end-of-file-fixer` adds the final
        newline to any checked-in copy, so a renderer which omits it makes every
        regression file fail the moment it is committed.
    """
    blocks: list[str] = []

    for group in groups:
        blocks.append("\n".join(_headline(group, root=root)))

        sections = [
            _fetched_section(group.records, facets=facets, max_rows=max_rows),
            _failed_section(group.records, facets=facets, max_rows=max_rows),
        ]
        blocks.extend("\n".join(lines) for lines in sections if lines)

    if all(not group.records for group in groups):
        # Said out loud, because an empty report and a broken job look identical.
        blocks.append(
            "Nothing to do: everything the search has found is already on disk."
        )

    blocks.append("\n".join(_held_lines(summary)))

    rendered = "\n\n".join(blocks)

    return "\n".join(line.rstrip() for line in rendered.splitlines()) + "\n"


def download_subject(groups: Sequence[DownloadGroup], *, stem: str) -> str:
    """
    Render one subject line for a download run

    What a subject line has to carry is whether this is a morning to open the mail:
    whether anything arrived, and whether anything is stuck. Summed across every
    requirement, because there is one email however many of them downloaded.

    Parameters
    ----------
    groups
        One per requirement which downloads

    stem
        What to call the search the download follows, e.g.
        `'pattern-effect-tas-mon-historical'`

    Returns
    -------
    :
        The subject line
    """
    n_files = sum(group.totals.n_files for group in groups)
    if not n_files:
        return f"{stem} downloads: nothing new"

    n_fetched = sum(group.totals.n_fetched for group in groups)
    n_failed = sum(group.totals.n_failed for group in groups)
    moved = sum(group.totals.bytes_fetched for group in groups)

    subject = f"{stem} downloads: {n_fetched} fetched, {readable_bytes(moved)}"
    if n_failed:
        # In front, because this is the half of the subject worth reacting to.
        subject = f"[{n_failed} FAILED] {subject}"

    return subject


def format_download_intent(intents: Sequence[DownloadIntent], *, root: Path) -> str:
    """
    Render what is about to be downloaded, for the email which goes out first

    Sent *before* the fetching starts, which is why it exists as its own block
    rather than as part of the download report: between them, the two emails say
    "this much is coming" and then "this much arrived", and a gap between the two
    is a thing you can notice.

    Parameters
    ----------
    intents
        One per tracked requirement which downloads, in the order to show them

    root
        Where the files are going, so a reader knows which disk is about to fill

    Returns
    -------
    :
        The block, or `''` when no requirement downloads at all

        Empty rather than a sentence, because a report for requirements that are
        only ever watched should not carry a heading about downloading.
    """
    if not intents:
        return ""

    if all(intent.nothing_known for intent in intents):
        # Not the same as having everything, though both have nothing to fetch.
        # A database which has been created or migrated but not searched since has
        # no file records at all, and a download planned against one finds nothing
        # to do for a reason which has nothing to do with downloading.
        return (
            "About to download\n"
            "  nothing: no files are recorded yet, so a search has not run since "
            "this\n  database was created or migrated. Run the search first."
        )

    if all(intent.nothing_to_do for intent in intents):
        return "About to download\n  nothing new to fetch; it is all already here"

    lines = ["About to download"]
    for intent in intents:
        if intent.nothing_known:
            lines.append(f"  {intent.label}: nothing recorded yet -- has a search run?")
            continue

        if intent.nothing_to_do:
            lines.append(
                f"  {intent.label}: nothing new ({intent.n_known} already here)"
            )
            continue

        line = (
            f"  {intent.label}: {intent.n_files} file(s), "
            f"{readable_bytes(intent.n_bytes)}"
        )
        if intent.n_unsized:
            line += f" (+{intent.n_unsized} of unpublished size)"

        lines.append(line)

    lines.append(f"  into {root}")

    return "\n".join(lines)
