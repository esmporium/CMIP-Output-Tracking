"""
Rendering an esmporium `SearchSummary` as text
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from esmporium.datasets import DATASET_FACET_COLUMNS
from esmporium.db import DatasetChangeType, describe_moved

from local.coverage import Coverage, coverage_of, newest_usable_records
from local.models import ModelSupport, model_support

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from esmporium.db import ChangeRecord, SearchSummary, VersionRecord

    from local.endpoints import EndpointResult


_SECTION_HEADINGS: dict[DatasetChangeType, str] = {
    DatasetChangeType.ADDED: "ADDED",
    DatasetChangeType.REMOVED: "REMOVED",
    DatasetChangeType.MODIFIED: "MODIFIED",
}
"""What we call each change type in front of a user"""

DEFAULT_FACETS: tuple[str, ...] = (
    "project",
    "model",
    "experiment",
    "variant_label",
    "variable",
    "reporting_interval",
)
"""
The facets every table shows unless it is told otherwise

This is a readability default, not a correctness one: rows are told apart by the `id`
column, which is always shown, so narrowing this can never make two datasets
indistinguishable. Widen it with the `facets` argument to [format_summary][(m).]
when a search does vary in something else:

    format_summary(summary, facets=(*DEFAULT_FACETS, "grid_label"))

Everything is in `summary.changes_frame()` regardless, whatever is shown here.
"""

PATTERN_EFFECT_FACETS: tuple[str, ...] = (
    "model",
    "variant_label",
    "grid_label",
)
"""
The facets the pattern-effect report shows

The `id`, `version` and `retracted` columns are added by `_row_columns` regardless, so
this is only what names a row, not what tells two rows apart.
"""

_SHORT_HEADERS: dict[str, str] = {
    "variant_label": "variant",
    "reporting_interval": "interval",
    "grid_label": "grid",
    "processing_id": "table",
    "id_project_specific": "native id",
}
"""
Shorter headings for the columns whose canonical names are too wide to tabulate
"""


def _readable_list(values: Sequence[str]) -> str:
    """
    Render values as a list a person can read, e.g. `'a', 'b' and 'c'`

    A copy of esmporium's own `readable_list`.

    Parameters
    ----------
    values
        The values to render, in the order they should be read

    Returns
    -------
    :
        The values, rendered for reading.

        An empty string if there are no values, so a caller can check for one
        before building a sentence around it.
    """
    if not values:
        return ""

    if len(values) == 1:
        return repr(values[0])

    comma_separated = ", ".join(repr(value) for value in values[:-1])

    return f"{comma_separated} and {values[-1]!r}"


class MisalignedRowError(ValueError):
    """Raised when a table's rows do not match its headings"""

    def __init__(self, *, n_headers: int, row_lengths: Sequence[int]) -> None:
        """
        Initialise

        Parameters
        ----------
        n_headers
            How many headings the table has

        row_lengths
            The lengths of the rows which do not match
        """
        self.n_headers = n_headers
        """How many headings the table has"""

        self.row_lengths = tuple(row_lengths)
        """The lengths of the rows which do not match"""

        msg = (
            f"A table with {n_headers} headings was given "
            f"{len(self.row_lengths)} rows which do not match it, of lengths "
            f"{sorted(set(self.row_lengths))}. "
            "Build the headings and the cells from the same column list, "
            "so that a value cannot end up under the wrong heading."
        )
        super().__init__(msg)


class UnknownFacetError(ValueError):
    """Raised when a summary is asked to show a facet no dataset records"""

    def __init__(self, unknown: Sequence[str]) -> None:
        """
        Initialise

        Parameters
        ----------
        unknown
            The facet names which are not dataset facets
        """
        self.unknown = tuple(unknown)
        """The facet names which are not dataset facets"""

        msg = (
            f"Cannot show {_readable_list(self.unknown)}: "
            "a summary can only show the facets a dataset records, which are "
            f"{_readable_list(DATASET_FACET_COLUMNS)}. "
            "Facets only a search document knows are not available here; read them "
            "off the raw documents instead."
        )
        super().__init__(msg)


def check_facets(facets: Sequence[str] | None) -> tuple[str, ...]:
    """
    Resolve and check the facets a table should show

    Parameters
    ----------
    facets
        The facets asked for, or `None` for [DEFAULT_FACETS][(m).]

    Returns
    -------
    :
        The facets to show, in esmporium's `DATASET_FACET_COLUMNS` order, so
        that two tables of the same summary line up

    Raises
    ------
    UnknownFacetError
        One of the names is not a dataset facet
    """
    if facets is None:
        return DEFAULT_FACETS

    asked = tuple(facets)
    unknown = [name for name in asked if name not in DATASET_FACET_COLUMNS]
    if unknown:
        raise UnknownFacetError(unknown)

    return tuple(name for name in DATASET_FACET_COLUMNS if name in asked)


def _row_columns(
    facets: Sequence[str],
) -> tuple[tuple[str, Callable[[VersionRecord], str]], ...]:
    """
    Get the columns which name a version, as `(heading, how to read it)` pairs

    One list feeding both the heading row and every cell, so that a value cannot end
    up under the wrong heading: if the headings and the cells were built from the
    facet list separately, the two could be ordered differently and nothing would
    say so.
    """
    columns: list[tuple[str, Callable[[VersionRecord], str]]] = [
        ("id", lambda version: str(version.dataset_version_id))
    ]
    columns.extend(
        (
            _SHORT_HEADERS.get(facet, facet),
            lambda version, facet=facet: version.facets.get(facet) or "-",  # type: ignore[misc]
        )
        for facet in facets
    )
    columns.append(("version", lambda version: version.version))
    columns.append(("retracted", lambda version: "yes" if version.retracted else "no"))

    return tuple(columns)


def render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    indent: str = "  ",
    right: Sequence[str] = (),
) -> str:
    """
    Render a fixed-width table

    Parameters
    ----------
    headers
        The column headings

    rows
        The rows, each with one cell per heading

    indent
        What to put in front of every line

    right
        The headings whose columns to right-justify, for columns of numbers meant to
        be compared down the column rather than read across the row. Everything is
        left-justified by default, which is what reads best for names.

    Returns
    -------
    :
        The table, with no trailing whitespace on any line.

    Examples
    --------
    >>> print(render_table(["model", "n"], [["CESM3", "1"], ["ACCESS-ESM1-6", "12"]]))
      model          n
      CESM3          1
      ACCESS-ESM1-6  12

    Raises
    ------
    MisalignedRowError
        A row does not have one cell per heading
    """
    ragged = [len(row) for row in rows if len(row) != len(headers)]
    if ragged:
        raise MisalignedRowError(n_headers=len(headers), row_lengths=ragged)

    widths = [
        max(len(str(headers[column])), *(len(str(row[column])) for row in rows))
        if rows
        else len(str(headers[column]))
        for column in range(len(headers))
    ]

    justify = [
        str.rjust if headers[column] in right else str.ljust
        for column in range(len(headers))
    ]

    lines = []
    for cells in [headers, *rows]:
        line = indent + "  ".join(
            justify[column](str(cell), widths[column])
            for column, cell in enumerate(cells)
        )
        lines.append(line.rstrip())

    return "\n".join(lines)


def _capped(
    rows: Sequence[Sequence[str]], max_rows: int
) -> tuple[list[list[str]], int]:
    """Cut a table down to `max_rows` rows, and say how many were left out."""
    kept = [list(row) for row in rows[:max_rows]]

    return kept, len(rows) - len(kept)


def _change_section(
    change_type: DatasetChangeType,
    changes: Sequence[ChangeRecord],
    *,
    max_rows: int,
    facets: Sequence[str],
) -> list[str]:
    """Render one change type's table, or just its count when there are none."""
    heading = _SECTION_HEADINGS[change_type]
    if not changes:
        return [f"{heading} (0)"]

    columns = _row_columns(facets)
    headers = [header for header, _ in columns]
    rows = [[read(change.version) for _, read in columns] for change in changes]
    kept, left_out = _capped(rows, max_rows)

    # `what moved` goes under its row rather than in a column of its own: it is the
    # one free-text field here, and as a column it set the table's width on its own.
    moved_under = {
        index: describe_moved(change.detail)
        for index, change in enumerate(changes[: len(kept)])
        if describe_moved(change.detail)
    }

    lines = [f"{heading} ({len(changes)})"]
    if moved_under:
        table = render_table(headers, kept).splitlines()
        lines.append(table[0])
        for index, row in enumerate(table[1:]):
            lines.append(row)
            if index in moved_under:
                lines.append(f"         {moved_under[index]}")
    else:
        lines.append(render_table(headers, kept))

    if left_out:
        lines.append(f"  ... (+{left_out} more)")

    # "There is new data" and "there is new data you must not use" are different
    # pieces of news, so an arrival which is already retracted gets said out loud
    # rather than only showing up as a `yes` in a column.
    already_retracted = sum(1 for change in changes if change.added_but_retracted)
    if already_retracted:
        lines.append(
            f"  {already_retracted} of these arrived already retracted, "
            "so they are not data you can use"
        )

    return lines


def _window_lines(summary: SearchSummary) -> list[str]:
    """Say which runs this summary covers, and what it is measured against."""
    runs = summary.window_runs
    if len(runs) == 1:
        covered = f"run {runs[0]}"
    else:
        covered = f"runs {runs[0]}-{runs[-1]} ({len(runs)} runs)"

    started = summary.run.started_at.strftime("%Y-%m-%d %H:%M UTC")
    if summary.baseline_run_id is not None:
        measured = f"compared against run {summary.baseline_run_id}"
    elif _is_first_run(summary):
        measured = "compared against nothing (this is the first run)"
    else:
        # Neither of the above, and we cannot tell which it is. esmporium reads the
        # baseline off the recorded changes, so a run which changed nothing recorded
        # no baseline -- whether it was the first run of its search or the hundredth.
        # This line used to say "this is the first run" here, which is false from the
        # second quiet morning onwards, so it states the fact instead of guessing.
        measured = "nothing changed, so no baseline was recorded"

    return [f"{covered}, newest started {started}", measured]


def _health_lines(summary: SearchSummary) -> list[str]:
    """Say whether the newest run's answer can be believed."""
    health = summary.run
    lines = ["RUN HEALTH"]

    if not health.leaves:
        lines.append("  no sub-searches recorded for this run")

        return lines

    lines.append(
        f"  {health.n_answered}/{health.n_leaves} sub-searches answered"
        + (f"   host: {', '.join(health.hosts)}" if health.hosts else "")
    )

    if health.n_matches is None:
        silent = sum(1 for leaf in health.leaves if leaf.n_matches is None)
        lines.append(
            f"  {health.n_documents} documents collected; "
            f"{silent} of {health.n_leaves} sub-searches reported no total"
        )
    elif health.n_documents < health.n_matches:
        lines.append(
            f"  {health.n_documents}/{health.n_matches} documents collected "
            "-- TRUNCATED, so a removal here may not be real"
        )
    else:
        lines.append(
            f"  {health.n_documents}/{health.n_matches} documents collected "
            "(nothing truncated)"
        )

    for leaf in health.failures:
        why = leaf.failure or "no endpoint answered"
        lines.append(f"  FAILED  {leaf.label}: {why}")

    for leaf in health.truncated:
        lines.append(
            f"  TRUNCATED  {leaf.label}: "
            f"{leaf.n_documents} of {leaf.n_matches} collected"
        )

    if not health.complete:
        lines.extend(
            [
                "  this run is incomplete, so it will not be used as a baseline:",
                "  the next run will compare itself against an earlier one",
            ]
        )

    return lines


def _counted(summary: SearchSummary) -> str:
    """
    Say how much the newest run returned, counting datasets rather than documents

    Datasets, because a replica of a dataset is the same dataset: the hosts' own
    document counts for one of our searches differ by a factor of four, almost
    entirely through replicas and how far each host's federated search reached. Those
    numbers are not comparable across endpoints and this one is.
    """
    current = summary.current
    n_datasets = len({version.dataset_id for version in current})

    if len(current) == n_datasets:
        return f"{len(current)} datasets"

    return f"{len(current)} versions of {n_datasets} datasets"


def _current_lines(
    summary: SearchSummary,
    *,
    max_rows: int,
    facets: Sequence[str],
    show_table: bool = True,
) -> list[str]:
    """Render what the newest run returns now, or just say how much of it there is."""
    current = summary.current
    if not current:
        return ["CURRENT STATE", "  the newest run returned nothing"]

    if not show_table:
        # The count without the rows. `max_rows=0` cannot do this: `_capped` keeps no
        # rows but `render_table` still draws the heading, so the table comes out as a
        # bare header followed by `... (+N more)`, which reads like a bug.
        return [f"CURRENT STATE ({_counted(summary)})"]

    columns = (*_row_columns(facets), ("nodes", lambda v: str(len(v.data_nodes))))
    headers = [header for header, _ in columns]
    rows = [[read(version) for _, read in columns] for version in current]
    kept, left_out = _capped(rows, max_rows)

    lines = [
        f"CURRENT STATE ({_counted(summary)})",
        render_table(headers, kept),
    ]
    if left_out:
        lines.append(f"  ... (+{left_out} more)")

    return lines


def format_summary(
    summary: SearchSummary,
    *,
    max_rows: int = 20,
    facets: Sequence[str] | None = None,
    show_current_table: bool = True,
) -> str:
    """
    Render a summary as text

    Written for somewhere a person is looking: a terminal, a script's log, a notebook
    cell. Every line is free of trailing whitespace, and the whole thing ends in
    exactly one newline, so it can be written to a file our pre-commit will not then
    want to change.

    Parameters
    ----------
    summary
        What to render

    max_rows
        How many rows of each table to show. The rest are summarised as
        `... (+N more)`, because a watched search can return thousands of datasets and
        the point of a summary is to be read.

    facets
        Which dataset facets to put in the tables. `None` uses
        [DEFAULT_FACETS][(m).].

        The `id`, `version` and `retracted` columns are always shown, so narrowing
        this never makes two rows indistinguishable; it only makes them less
        self-describing.

    show_current_table
        Whether to list every dataset the newest run returned, or only count them.

        A watched search returns the same hundreds of rows every morning, so the list
        is the least informative part of the report and the part which pushes the
        changes -- the bit there is news in -- off the top of the screen. The count is
        kept either way, because "47 datasets" going quiet is itself worth seeing.

    Returns
    -------
    :
        The rendered summary

    Raises
    ------
    UnknownFacetError
        `facets` names something which is not a dataset facet
    """
    shown = check_facets(facets)
    blocks: list[list[str]] = [
        [summary.headline],
        _window_lines(summary),
    ]

    if summary.any_changes:
        for change_type in _SECTION_HEADINGS:
            blocks.append(
                _change_section(
                    change_type,
                    [
                        change
                        for change in summary.changes
                        if change.change_type is change_type
                    ],
                    max_rows=max_rows,
                    facets=shown,
                )
            )
    else:
        blocks.append(["NO CHANGES"])

    if summary.net_unchanged:
        blocks.append(
            [
                f"{summary.net_unchanged} versions moved inside the window "
                "and ended up back where they started"
            ]
        )

    blocks.append(_health_lines(summary))
    blocks.append(
        _current_lines(
            summary,
            max_rows=max_rows,
            facets=shown,
            show_table=show_current_table,
        )
    )

    rendered = "\n\n".join("\n".join(block) for block in blocks)

    return "\n".join(line.rstrip() for line in rendered.splitlines()) + "\n"


def _is_first_run(summary: SearchSummary) -> bool:
    """
    Work out whether this is the first run of its search
    """
    return summary.baseline_run_id is None and summary.any_changes


def _change_counts(summary: SearchSummary) -> str:
    """Say what changed in one short phrase, for a column rather than a heading."""
    if not summary.any_changes:
        return "no changes"

    if _is_first_run(summary):
        # Everything is an addition on a first run, which is news about us starting
        # to watch rather than news about ESGF.
        return f"first run ({summary.counts[DatasetChangeType.ADDED]})"

    counts = summary.counts

    return (
        f"+{counts[DatasetChangeType.ADDED]} "
        f"-{counts[DatasetChangeType.REMOVED]} "
        f"~{counts[DatasetChangeType.MODIFIED]}"
    )


def _datasets(n: int) -> str:
    """Count datasets, agreeing with itself about whether there is one of them."""
    return f"{n} dataset" if n == 1 else f"{n} datasets"


def _cover_lines(coverage: Coverage) -> list[str]:
    """Say what it takes to get the newest version of everything."""
    cover = coverage.smallest_cover
    if not cover:
        return []

    if len(cover) == 1:
        return [
            f"  {cover[0]} alone holds the newest version of all {coverage.n_datasets}"
        ]

    said = " + ".join(cover)
    hedge = "" if coverage.cover_is_exact else " (a good set, not provably smallest)"

    return [
        f"  no endpoint holds the newest version of all {coverage.n_datasets}",
        f"  fewest that do: {said}{hedge}",
    ]


def format_endpoint_overview(
    results: Sequence[EndpointResult],
    coverage: Coverage,
    *,
    title: str,
) -> str:
    """
    Render several endpoints' answers as one row each

    For the projects we watch but do not want a changes table of. The three count
    columns are a version-level comparison across the endpoints, not three facts
    about each one on its own: `current` means this endpoint has the newest version
    any endpoint reported, `stale` that it has an older one, `absent` that it has no
    usable version at all. They sum to the same total on every row, which is what
    makes the table readable down a column as well as across a row.

    `stale` and `absent` are kept apart because they fail differently. An absent
    dataset gives you nothing and you notice; a stale one gives you old data and you
    do not.

    Parameters
    ----------
    results
        The endpoints' results, rendered in the order given

    coverage
        What [coverage_of][local.coverage.coverage_of] made of those same results

    title
        What to call this block, e.g. the project name

    Returns
    -------
    :
        The block, with no trailing newline
    """
    counted = f"{coverage.n_answered} of {coverage.n_endpoints} answered"
    if coverage.n_datasets:
        counted = f"{counted}; {coverage.n_datasets} datasets at newest usable version"

    by_tag = {endpoint.tag: endpoint for endpoint in coverage.endpoints}

    headers = ["endpoint", "host", "current", "stale", "absent", "vs last run"]
    rows = []
    for result in results:
        seen = by_tag.get(result.endpoint.tag)
        rows.append(
            [
                result.endpoint.tag,
                result.endpoint.host,
                "-" if seen is None else str(seen.current),
                "-" if seen is None else str(seen.stale),
                "-" if seen is None else str(seen.absent),
                "FAILED" if result.summary is None else _change_counts(result.summary),
            ]
        )

    lines = [
        f"{title} ({counted})",
        render_table(headers, rows, right=("current", "stale", "absent")),
        *_cover_lines(coverage),
    ]

    if coverage.contested:
        leader = max(coverage.endpoints, key=lambda e: e.ahead_on, default=None)
        said = f"  {_datasets(coverage.contested)} served at different versions"
        if leader is not None and leader.ahead_on:
            said = f"{said}; {leader.tag} has the newest of {leader.ahead_on}"
        lines.append(said)

    # Said out loud rather than left to make the totals quietly not add up. Both
    # lines are kept short enough to survive a mail client at a sensible width: the
    # body goes out as fixed-width text and a line which wraps takes the table's
    # columns with it.
    if coverage.unordered:
        lines.append(
            f"  {_datasets(coverage.unordered)} with an unorderable version, "
            "not judged above"
        )

    if coverage.unusable:
        lines.append(
            f"  {_datasets(coverage.unusable)} retracted everywhere, "
            "so not counted above"
        )

    # Said under the table rather than in a column, for the same reason `what moved`
    # is: a reason is free text and would set the table's width on its own.
    for result in results:
        if result.summary is None:
            lines.append(f"  FAILED  {result.endpoint.label}: {result.failure}")

    for result in results:
        if result.summary is not None and not result.summary.run.complete:
            # Kept short enough to survive a mail client at a sensible width: the
            # body goes out as fixed-width text and a line which wraps takes the
            # table's columns with it.
            lines.append(
                f"  INCOMPLETE  {result.endpoint.label}: "
                "count may be short, and not a usable baseline"
            )

    return "\n".join(line.rstrip() for line in lines)


def compare_endpoint_summaries(
    first: EndpointResult,
    second: EndpointResult,
    *,
    max_rows: int = 10,
    facets: Sequence[str] | None = None,
) -> list[str]:
    """
    Say whether two endpoints returned the same thing

    Compared at version level, on the newest usable version each endpoint reported,
    which is the same rule
    [newest_usable_records][local.coverage.newest_usable_records] applies for the
    coverage table. Two endpoints both holding a dataset at two different versions is
    a difference, not agreement -- one node lagging another by a version is precisely
    the drift worth seeing -- so it is reported as its own category rather than folded
    into either side's "only in".

    Parameters
    ----------
    first, second
        The two endpoints to compare

    max_rows
        How many differing rows to show per category

    facets
        Which dataset facets to name the differing rows by. `None` uses
        [DEFAULT_FACETS][(m).].

    Returns
    -------
    :
        The lines of the comparison
    """
    shown = check_facets(facets)
    one, other = first.endpoint.tag, second.endpoint.tag
    heading = f"{one.upper()} vs {other.upper()}"

    if first.summary is None or second.summary is None:
        silent = [
            result.endpoint.tag for result in (first, second) if result.summary is None
        ]

        return [heading, f"  cannot compare: {_readable_list(silent)} did not answer"]

    here = newest_usable_records(first)
    there = newest_usable_records(second)

    only_here = sorted(set(here) - set(there))
    only_there = sorted(set(there) - set(here))
    differing = sorted(
        dataset
        for dataset in set(here) & set(there)
        if here[dataset].version != there[dataset].version
    )

    if not (only_here or only_there or differing):
        return [
            heading,
            f"  {one} and {other} agree, on all {len(here)} datasets "
            "at the same versions",
        ]

    lines = [
        heading,
        f"  {one} has {len(here)}, {other} has {len(there)} datasets -- THEY DIVERGE",
        f"  {len(only_here)} only in {one}, {len(only_there)} only in {other}, "
        f"{len(differing)} at different versions",
    ]

    columns = _row_columns(shown)
    headers = [header for header, _ in columns]
    for tag, holdings, only in ((one, here, only_here), (other, there, only_there)):
        if not only:
            continue

        rows = [[read(holdings[dataset]) for _, read in columns] for dataset in only]
        kept, left_out = _capped(rows, max_rows)
        lines.append(f"  only in {tag} ({len(only)})")
        lines.append(render_table(headers, kept, indent="    "))
        if left_out:
            lines.append(f"    ... (+{left_out} more)")

    if differing:
        # Both versions side by side, because which of the two is behind is the whole
        # point and a single `version` column could not say.
        naming = _row_columns(shown)[:-2]
        version_headers = [header for header, _ in naming] + [one, other]
        rows = [
            [read(here[dataset]) for _, read in naming]
            + [here[dataset].version, there[dataset].version]
            for dataset in differing
        ]
        kept, left_out = _capped(rows, max_rows)
        lines.append(f"  at different versions ({len(differing)})")
        lines.append(render_table(version_headers, kept, indent="    "))
        if left_out:
            lines.append(f"    ... (+{left_out} more)")

    return lines


def format_model_support(support: ModelSupport, *, title: str) -> str:
    """
    Render which models each endpoint can serve, one row per model

    Parameters
    ----------
    support
        What [model_support][local.models.model_support] made of one project's results

    title
        What to call this block, e.g. the project name

    Returns
    -------
    :
        The block, with no trailing newline
    """
    heading = f"{title}  models with at least one satisfying variant"
    if not support.endpoints:
        return f"{heading}\n  no endpoint answered, so there is nothing to list"

    if not support.models:
        return f"{heading}\n  no endpoint returned a usable dataset"

    headers = ["model", *support.endpoints]
    rows = [
        [
            model,
            *(str(support.variants[model].get(tag, "-")) for tag in support.endpoints),
        ]
        for model in support.models
    ]
    totals = [
        "models satisfied",
        *(str(support.satisfied_at(tag)) for tag in support.endpoints),
    ]

    drawn = render_table(
        headers, [*rows, totals], right=tuple(support.endpoints)
    ).splitlines()
    # A rule above the totals, so the one row which counts rows is not mistaken for
    # another model. `render_table` draws cells, not rules, so it is spliced in here
    # rather than taught to the table.
    rule = "  " + "-" * (max(len(line) for line in drawn) - 2)

    lines = [heading, *drawn[:-1], rule, drawn[-1]]
    lines.append(
        f"  {support.n_models} models have at least one variant "
        "which satisfies the requirement"
    )
    for tag in support.silent:
        lines.append(f"  {tag} is not shown: it did not answer")

    return "\n".join(line.rstrip() for line in lines)


_A_PAIR = 2
"""
How many endpoints a project needs before a direct comparison makes sense

Two endpoints have one comparison between them. Three have three, and nobody wants
to read three pairwise tables when the overview's column of counts says the same
thing, so only a pair gets compared.
"""


def _projects_in_order(results: Sequence[EndpointResult]) -> tuple[str, ...]:
    """Get the projects these results cover, in the order they first appear."""
    return tuple(dict.fromkeys(result.endpoint.project for result in results))


def format_report(
    results: Sequence[EndpointResult],
    *,
    detailed: Sequence[str] = (),
    max_rows: int = 20,
    facets: Sequence[str] | None = None,
    show_current_table: bool = False,
) -> str:
    """
    Render every endpoint we asked, as one report

    Parameters
    ----------
    results
        What each endpoint said. Grouped by project in the order the projects first
        appear, so the order of this sequence is the order of the report.

    detailed
        The projects to give a full changes table to, one block per endpoint, plus a
        comparison when the project has exactly two. Every other project gets one
        [format_endpoint_overview][(m).] table instead.

        This is the knob to reach for when a new search wants more or less than the
        others: it is a caller's choice, so a search which needs a table and a search
        which needs a line can live in the same report without either one teaching
        this module about itself.

    max_rows
        How many rows of each table to show before summarising the rest

    facets
        Which dataset facets the tables name rows by. `None` uses
        [DEFAULT_FACETS][(m).].

    show_current_table
        Whether the detailed blocks list every dataset or only count them. Defaults to
        counting, which is the opposite of [format_summary][(m).]'s default: a report
        of nine endpoints cannot afford nine full listings.

    Returns
    -------
    :
        The report, ending in exactly one newline
    """
    blocks: list[list[str]] = []

    for project in _projects_in_order(results):
        of_project = [
            result for result in results if result.endpoint.project == project
        ]

        # One coverage per project, because a tag only identifies an endpoint within
        # a project: `east` is two different searches across CMIP6 and CMIP7.
        coverage = coverage_of(of_project)

        models = format_model_support(
            model_support(of_project), title=project
        ).splitlines()

        if project not in detailed:
            blocks.append(
                format_endpoint_overview(
                    of_project, coverage, title=project
                ).splitlines()
            )
            blocks.append(models)
            continue

        for result in of_project:
            # The title and the summary are one block, so the endpoint's name sits
            # directly above its own headline rather than floating a blank line away
            # from it.
            title = f"{project}  {result.endpoint.label}"
            if result.summary is None:
                blocks.append([title, f"  FAILED: {result.failure}"])
                continue

            rendered = format_summary(
                result.summary,
                max_rows=max_rows,
                facets=facets,
                show_current_table=show_current_table,
            )
            blocks.append([title, *rendered.rstrip("\n").splitlines()])

        # See `_A_PAIR`.
        if len(of_project) == _A_PAIR:
            blocks.append(
                compare_endpoint_summaries(
                    of_project[0],
                    of_project[1],
                    max_rows=max_rows,
                    facets=facets,
                )
            )

        blocks.append(models)

    rendered = "\n\n".join("\n".join(block) for block in blocks)

    return "\n".join(line.rstrip() for line in rendered.splitlines()) + "\n"


def report_subject(results: Sequence[EndpointResult], *, stem: str) -> str:
    """
    Render one subject line for a report covering several endpoints

    `SearchSummary.headline` covers one search, and we have nine. What a subject line
    has to carry is whether this is a morning to open the mail: how many endpoints
    moved, per project, and whether any of them failed.

    Parameters
    ----------
    results
        What each endpoint said

    stem
        What to call the search, e.g. `'pattern-effect'`

    Returns
    -------
    :
        The subject line
    """
    parts = []
    for project in _projects_in_order(results):
        of_project = [
            result for result in results if result.endpoint.project == project
        ]
        changed = [
            result
            for result in of_project
            if result.summary is not None and result.summary.any_changes
        ]
        parts.append(f"{project} {len(changed)}/{len(of_project)} changed")

    subject = f"{stem}: {', '.join(parts)}" if parts else f"{stem}: nothing asked"

    n_failed = sum(1 for result in results if result.summary is None)
    if n_failed:
        subject = f"[{n_failed} FAILED] {subject}"

    return subject
