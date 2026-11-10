"""
Fetching the bytes for what a tracked search found

The division of labour mirrors [local.endpoints][]: esmporium knows how to plan, fetch
and record a download, and this module knows the two things it cannot know for us --
where on *this* machine the data goes, and that we want every attempt written down.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from esmporium.db import (
    Dataset,
    DatasetVersion,
    File,
    download,
    plan_downloads,
    record_data_node_calls,
)
from esmporium.downloads import DEFAULT_PER_DATA_NODE
from sqlmodel import Session, col, select

if TYPE_CHECKING:
    from collections.abc import Iterable

    from esmporium.db import DownloadPlanItem
    from esmporium.downloads import DownloadResult
    from esmporium.query import QueryProtocol
    from sqlalchemy.engine import Engine

IDENTITY_FACETS = ("model", "variant_label", "grid_label")
"""
Which facets of a dataset a download is reported under **by default**

Enough to tell one CMIP7 pattern-effect download from another, which is what the
report is for: the project, experiment, variable and frequency are the same for every
row by construction, so printing them would be nine identical columns.

Only the default. A question which spans several experiments or variables -- the
Gregory question spans two and four -- has to name its own columns, or every one of
its rows is described the same way. That is
[local.endpoints.TrackedRequirement.report_facets][], which rides on
[DownloadIntent.facets][(m).DownloadIntent.facets] and
[local.download_formatting.DownloadGroup.facets][] to the renderers.
"""

RECORDED_FACETS = ("model", "variant_label", "grid_label", "experiment", "variable")
"""
Which facets are looked up and carried, whether or not they are printed

A superset of [IDENTITY_FACETS][(m).] and of anything a question asks to be shown,
deliberately: the lookup happens once, in [_identities][(m).], against rows we are
already reading, so carrying two more strings per dataset costs nothing -- while
*not* carrying them would mean a question could only be shown the columns someone
thought of when the plan was made.

So this is the set a renderer may choose from, and `IDENTITY_FACETS` is the set it
chooses by default. Adding a facet here is free; a renderer asked for one that is
not here gets `'-'`.
"""


class MissingDownloadSettingError(ValueError):
    """
    Raised when a required download setting is not in the environment
    """

    def __init__(self, name: str) -> None:
        """
        Initialise the error

        Parameters
        ----------
        name
            Name of the environment variable which is missing
        """
        self.name = name
        """Name of the environment variable which is missing"""

        super().__init__(
            f"{name} is not set. "
            "Put it in the .env file at the root of this repository "
            "(see .env.example), or export it before running."
        )


@dataclass(frozen=True)
class DownloadSettings:
    """
    Where this machine puts downloaded data

    Built by [from_env][(c).from_env]. Not a secret, unlike
    [local.notifications.EmailSettings][], but machine-specific in exactly the same
    way: a laptop and a shared server do not have the same data volume, and a path
    committed to the repository would be wrong on one of them.
    """

    root: Path
    """
    The directory to download into

    Files go underneath it in esmporium's own layout, which is DRS-shaped but is not
    any project's real DRS -- see [esmporium.downloads.paths][]. Does not have to
    exist yet; whatever writes the bytes creates the directories it needs.
    """

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DownloadSettings:
        """
        Read the settings from the environment

        Parameters
        ----------
        env
            Environment to read from.
            `None`, the default, reads the process's own environment,
            which is where
            [python-dotenv](https://pypi.org/project/python-dotenv)
            puts the contents of a `.env` file.

        Returns
        -------
        :
            The settings

        Raises
        ------
        MissingDownloadSettingError
            `DOWNLOAD_ROOT` is not set

            Deliberately required, with no default. A default would be somewhere
            inside this repository, and the first anyone would know of it is a git
            status full of netCDF, or a full disk on whichever volume the repository
            happens to sit on.
        """
        source = os.environ if env is None else env

        root = source.get("DOWNLOAD_ROOT", "").strip()
        if not root:
            raise MissingDownloadSettingError("DOWNLOAD_ROOT")

        return cls(root=Path(root).expanduser())


@dataclass(frozen=True)
class DatasetTally:
    """
    One dataset version's worth of files, counted

    The unit both emails report in. A dataset version is what a person thinks in --
    "CanESM6-0-MR r26i1p1f1" -- while a file is an arbitrary slice of one: CMIP7
    publishes ACCESS-ESM1-6 as eighteen files per variant and CanESM6-0-MR as three,
    which says something about the publisher and nothing about the science.

    Carries [dataset_version_id][(c).dataset_version_id] so a reader can follow one
    dataset across the morning: the number in the `ADDED` table of the search report
    is the same number here.
    """

    dataset_version_id: int
    """Which dataset version this is, as the search report's `id` column shows it"""

    facets: Mapping[str, str]
    """
    Which dataset it is, keyed by facet name

    The keys are [RECORDED_FACETS][(m).]; which of them a report *prints* is a
    separate choice. A facet the database holds as `NULL`, which `grid_label` is
    allowed to be, reads as `'-'`.
    """

    version: str
    """The dataset version, as ESGF reports it"""

    n_files: int
    """How many files of it this tally covers"""

    n_bytes: int
    """
    How many bytes those files come to

    What the API published, for a tally of a plan; what landed on disk, for a tally
    of a finished run. Files published with no size at all contribute nothing, which
    is why [n_unsized][(c).n_unsized] is counted separately.
    """

    n_unsized: int = 0
    """
    How many of the files were published with no size

    Named rather than folded into zero, so that a small total cannot read as a small
    download when it is only an unmeasured one.
    """

    nodes: tuple[str, ...] = ()
    """
    Which data nodes served these files, deduplicated

    Empty for a plan, which has not asked anybody for anything yet.
    """


@dataclass(frozen=True)
class DownloadIntent:
    """
    What one tracked requirement is about to fetch, before it fetches any of it

    Worked out from a plan, which is a database read and some `stat` calls, so this
    can be put in front of a person *before* the download starts rather than after.
    That is the whole point of it: an email which says "2.1 GB starting now" means
    that a box which dies mid-download still told somebody it was busy.
    """

    label: str
    """Which tracked requirement this is the plan for"""

    tallies: tuple[DatasetTally, ...]
    """
    One per dataset version with something still to fetch, in reading order

    Empty when there is nothing to do, which [n_known][(c).n_known] is needed to
    interpret.
    """

    n_known: int
    """
    How many files we know about for this requirement at all, fetched or not

    The difference between "you already have everything" and "nobody has told us
    what exists yet", which are opposite situations that otherwise read identically:
    both have nothing to fetch. A freshly migrated database has no file records
    until a search writes them, and a download planned against one finds nothing to
    do for a reason that has nothing to do with the download.
    """

    facets: tuple[str, ...] | None = None
    """
    Which facets this requirement's table names a row by, or `None` for the default

    Carried on the intent rather than passed to the renderer, because it is a fact
    about the *question* -- how many experiments and variables it spans -- and the
    renderer is handed several questions at once. See [IDENTITY_FACETS][(m).].
    """

    @property
    def n_datasets(self) -> int:
        """How many dataset versions have something to fetch"""
        return len(self.tallies)

    @property
    def n_files(self) -> int:
        """How many files are to be fetched"""
        return sum(tally.n_files for tally in self.tallies)

    @property
    def n_bytes(self) -> int:
        """How many bytes those files were published as"""
        return sum(tally.n_bytes for tally in self.tallies)

    @property
    def n_unsized(self) -> int:
        """How many of them were published with no size"""
        return sum(tally.n_unsized for tally in self.tallies)

    @property
    def nothing_to_do(self) -> bool:
        """Whether there is nothing to fetch for this requirement"""
        return not self.tallies

    @property
    def nothing_known(self) -> bool:
        """
        Whether we have no file records for this requirement at all

        Nearly always means a search has not run since the database was created or
        migrated. See [n_known][(c).n_known].
        """
        return not self.n_known


IdentityLookup = Callable[
    [Session, Sequence[int]], dict[int, tuple[Mapping[str, str], str]]
]
"""
How [describe_plan][(m).] finds out which dataset each version belongs to

Named so that it can be injected. See [_identities][(m).] for the real one.
"""


def _identities(
    session: Session, dataset_version_ids: Sequence[int]
) -> dict[int, tuple[Mapping[str, str], str]]:
    """
    Look up which dataset each version belongs to

    One query for the whole run rather than one per version: a bulk run is dozens of
    versions and a round trip each to answer "which model was that" would cost more
    than the tallying does.

    Parameters
    ----------
    session
        The session to read from

    dataset_version_ids
        The versions to describe

    Returns
    -------
    :
        The facets and the version string, keyed by dataset version id

        A version we find no row for is simply absent, which the callers read as
        "describe it as unknown rather than dropping it".
    """
    if not dataset_version_ids:
        return {}

    rows = session.exec(
        select(DatasetVersion, Dataset)
        .join(Dataset, col(DatasetVersion.dataset_id) == col(Dataset.id))
        .where(col(DatasetVersion.id).in_(list(dataset_version_ids)))
    ).all()

    return {
        version.id: (
            {facet: getattr(dataset, facet) or "-" for facet in RECORDED_FACETS},
            version.version,
        )
        for version, dataset in rows
        if version.id is not None
    }


UNKNOWN_FACETS: Mapping[str, str] = dict.fromkeys(RECORDED_FACETS, "-")
"""
What to show for a dataset we could not find a row for

Should not happen -- a plan and a result both came from those rows -- but printing a
row bare is better than dropping it, because a download nobody can account for is
exactly the one worth seeing.
"""


def describe_plan(  # noqa: PLR0913 - one argument per thing a plan has to be told
    engine: Engine,
    label: str,
    plan: Sequence[DownloadPlanItem],
    *,
    n_known: int,
    facets: tuple[str, ...] | None = None,
    identities: IdentityLookup = _identities,
) -> DownloadIntent:
    """
    Tally a plan by dataset version, ready to put in front of a person

    Parameters
    ----------
    engine
        The database to read the dataset identities from

    label
        Which tracked requirement this is the plan for

    plan
        The files still to fetch. See [plan_only][(m).].

    n_known
        How many files we know of for this requirement at all, fetched or not.
        See [DownloadIntent.n_known][(m).DownloadIntent.n_known].

    facets
        Which facets this requirement's table should name a row by, or `None` for
        the renderer's default. See
        [DownloadIntent.facets][(m).DownloadIntent.facets].

    identities
        How to look up which dataset each version belongs to. Injected so that this
        function's own behaviour -- the grouping, the tallying, and that each
        requirement's choice of columns survives the trip -- can be tested without a
        database, which is how `local.summary_formatting` and `local.endpoints` are
        tested too.

    Returns
    -------
    :
        The intent, with one tally per dataset version, in dataset order
    """
    grouped: dict[int, list[DownloadPlanItem]] = {}
    for item in plan:
        grouped.setdefault(item.dataset_version_id, []).append(item)

    with Session(engine) as session:
        known = identities(session, tuple(grouped))

    tallies = []
    for dataset_version_id, items in grouped.items():
        # `identity` rather than `facets`: the parameter of that name is the tuple of
        # column *names* this requirement wants printed, and a loop variable called
        # `facets` shadowed it -- so every intent came back carrying the last
        # dataset's facet mapping where its column choice should have been, and every
        # table rendered the same five columns.
        identity, version = known.get(dataset_version_id, (UNKNOWN_FACETS, "-"))
        sized = [
            item.request.expected_size
            for item in items
            if item.request.expected_size is not None
        ]
        tallies.append(
            DatasetTally(
                dataset_version_id=dataset_version_id,
                facets=identity,
                version=version,
                n_files=len(items),
                n_bytes=sum(sized),
                n_unsized=len(items) - len(sized),
            )
        )

    return DownloadIntent(
        label=label,
        tallies=tuple(_in_reading_order(tallies)),
        n_known=n_known,
        facets=facets,
    )


def tally_records(records: Iterable[DownloadRecord]) -> tuple[DatasetTally, ...]:
    """
    Tally finished results by dataset version

    A pure function of what [describe_results][(m).] handed back, so the renderer's
    tests need no database. Counts only the files which actually arrived: bytes on
    disk, not bytes promised, and the nodes which really served them.

    Parameters
    ----------
    records
        The run's records. Failures and already-here files are ignored, because this
        is the tally of what *moved*; failures are reported per file, where the
        particular file is the point.

    Returns
    -------
    :
        One tally per dataset version which got at least one file, in reading order
    """
    grouped: dict[int, list[DownloadRecord]] = {}
    for record in records:
        if record.result.fetch is None or record.result.skipped:
            continue

        grouped.setdefault(record.dataset_version_id, []).append(record)

    tallies = []
    for dataset_version_id, of_dataset in grouped.items():
        fetches = [
            record.result.fetch
            for record in of_dataset
            if record.result.fetch is not None
        ]
        tallies.append(
            DatasetTally(
                dataset_version_id=dataset_version_id,
                facets=of_dataset[0].facets,
                version=of_dataset[0].version,
                n_files=len(fetches),
                n_bytes=sum(fetch.size_on_disk for fetch in fetches),
                nodes=tuple(fetch.route.data_node for fetch in fetches),
            )
        )

    return tuple(_in_reading_order(tallies))


def _in_reading_order(tallies: Iterable[DatasetTally]) -> list[DatasetTally]:
    """
    Sort tallies the way a person scans them

    By model then variant, case-insensitively, so that `CanESM6-0-MR` sits beside
    `CanESM6-0-LR` instead of being thrown elsewhere by where ASCII puts a
    lower-case letter -- the same ordering `local.models` uses.

    Parameters
    ----------
    tallies
        The tallies to sort

    Returns
    -------
    :
        Them, sorted
    """
    return sorted(
        tallies,
        key=lambda tally: (
            str(tally.facets.get("model", "")).lower(),
            str(tally.facets.get("variant_label", "")).lower(),
            tally.version,
        ),
    )


def plan_only(
    engine: Engine, query: QueryProtocol, root: Path, *, skip_done: bool = True
) -> tuple[DownloadPlanItem, ...]:
    """
    Work out what a download would fetch, without fetching any of it

    What `--dry-run` is: the way to find out how much a first run is about to move
    before it starts moving it, and the way to check a widened query before it is
    turned loose on a shared volume.

    Parameters
    ----------
    engine
        The database to plan from

    query
        What to download

    root
        The directory the files would go into

    skip_done
        Whether to leave out the files we already have. `False` plans every file we
        know of, which is how to ask "what exists?" rather than "what is missing?".

    Returns
    -------
    :
        One item per file, in a stable order
    """
    with Session(engine) as session:
        return plan_downloads(session, query, root, skip_done=skip_done)


def run_downloads(
    engine: Engine,
    query: QueryProtocol,
    root: Path,
    *,
    max_workers: int | None = None,
    per_data_node: int | None = DEFAULT_PER_DATA_NODE,
) -> tuple[DownloadResult, ...]:
    """
    Download everything `query` asks for which we have not already got

    A thin wrapper over [esmporium.db.download][], which already does the whole of
    plan, queue, fetch and record. The one thing added is the observer: every attempt
    against every data node is written to the database, so "why did this file not
    arrive" is a question the database can answer later rather than one that needs
    the log from the night it happened.

    Re-running is cheap and is how the automation works. A file is skipped when its
    download row says it is done *and* it is still on disk at the published size, so
    a second run on the same morning fetches nothing, and a file deleted behind our
    back is fetched again rather than being assumed present forever.

    Parameters
    ----------
    engine
        The database to plan from and record into

    query
        What to download

    root
        The directory to download into

    max_workers
        How many files to fetch at once. `None`, the default, fetches them one after
        another.

        A ceiling rather than a promise, and `per_data_node` is why: a requirement
        whose files all live on one node moves `per_data_node` of them at a time
        however high this is set. See
        [esmporium.downloads.download_files][].

    per_data_node
        The most transfers to run at once against any one data node.

        Defaults to esmporium's own
        [DEFAULT_PER_DATA_NODE][esmporium.downloads.DEFAULT_PER_DATA_NODE] rather
        than a number of our own, so that "how hard is it polite to lean on one
        archive" stays one judgement made in one place upstream.

        Worth understanding before raising it: a worker whose file's best node is
        already at its limit **waits for a slot** rather than being sent to a node
        the selector ranked lower. So this paces a run, and never quietly changes
        which node served us. `None` removes the limit, which is a way to get a data
        node to start refusing us.

    Returns
    -------
    :
        One result per file attempted, in the order they were planned

        Files already on disk are not attempted and so are not here.
    """
    return download(
        engine,
        query,
        root,
        observer=record_data_node_calls(engine),
        max_workers=max_workers,
        per_data_node=per_data_node,
    )


@dataclass(frozen=True)
class DownloadRecord:
    """
    One file a run tried, and what it is

    A [DownloadResult][esmporium.downloads.DownloadResult] knows the path it wrote and
    the file id it wrote for, but not which model that file belongs to, so a report
    built from results alone could only show paths. This pairs the two.

    Deliberately a flat record of strings rather than the database rows themselves, so
    that the renderer is a pure function of plain data and its tests need no database
    -- the same division [local.summary_formatting][] has.
    """

    result: DownloadResult
    """What came of trying to fetch it"""

    facets: Mapping[str, str]
    """
    Which dataset it belongs to, keyed by facet name

    The keys are [RECORDED_FACETS][(m).]; which of them a report *prints* is a
    separate choice. A facet the database holds as `NULL`, which `grid_label` is
    allowed to be, reads as `'-'`.
    """

    version: str
    """The dataset version it belongs to, as ESGF reports it"""

    filename: str
    """The file's own name"""

    dataset_version_id: int
    """
    Which dataset version it belongs to

    The same number the search report's `id` column shows, so one dataset can be
    followed from "added overnight" through to "fetched". `-1` for a file we could
    not find a row for, which should not happen.
    """


def describe_results(
    engine: Engine, results: Sequence[DownloadResult]
) -> tuple[DownloadRecord, ...]:
    """
    Say which dataset each result belongs to

    One query for the whole run rather than one per file: a bulk first run is a
    thousand results and a thousand round trips to answer "which model was that"
    would cost more than the download did.

    Parameters
    ----------
    engine
        The database to read the identities from

    results
        What [run_downloads][(m).] returned

    Returns
    -------
    :
        One record per result, in the order the results came in

        A result whose file we cannot find a row for -- which should not happen, since
        the plan came from those rows -- is still returned, with its facets unknown,
        because losing a failure from a report is worse than printing it bare.
    """
    file_ids = [
        result.request.file_id
        for result in results
        if result.request.file_id is not None
    ]
    if not file_ids:
        return tuple(_bare_record(result) for result in results)

    with Session(engine) as session:
        rows = session.exec(
            select(File, DatasetVersion, Dataset)
            .join(
                DatasetVersion, col(File.dataset_version_id) == col(DatasetVersion.id)
            )
            .join(Dataset, col(DatasetVersion.dataset_id) == col(Dataset.id))
            .where(col(File.id).in_(file_ids))
        ).all()

        known = {
            file.id: (
                {facet: getattr(dataset, facet) or "-" for facet in RECORDED_FACETS},
                version.version,
                file.filename,
                version.id if version.id is not None else -1,
            )
            for file, version, dataset in rows
        }

    described = []
    for result in results:
        found = known.get(result.request.file_id)
        if found is None:
            described.append(_bare_record(result))
            continue

        facets, version, filename, dataset_version_id = found
        described.append(
            DownloadRecord(
                result=result,
                facets=facets,
                version=version,
                filename=filename,
                dataset_version_id=dataset_version_id,
            )
        )

    return tuple(described)


def _bare_record(result: DownloadResult) -> DownloadRecord:
    """
    Describe a result we could not find a database row for

    Parameters
    ----------
    result
        The result to describe

    Returns
    -------
    :
        The record, with the filename read off the path it was going to write
    """
    return DownloadRecord(
        result=result,
        facets=UNKNOWN_FACETS,
        version="-",
        filename=result.request.dest.name,
        dataset_version_id=-1,
    )
