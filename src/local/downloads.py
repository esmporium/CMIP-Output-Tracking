"""
Fetching the bytes for what a tracked search found

The division of labour mirrors [local.endpoints][]: esmporium knows how to plan, fetch
and record a download, and this module knows the two things it cannot know for us --
where on *this* machine the data goes, and that we want every attempt written down.
"""

from __future__ import annotations

import os
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
from sqlmodel import Session, col, select

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from esmporium.db import DownloadPlanItem
    from esmporium.downloads import DownloadResult
    from esmporium.query import QueryProtocol
    from sqlalchemy.engine import Engine

IDENTITY_FACETS = ("model", "variant_label", "grid_label")
"""
Which facets of a dataset a download is reported under

Enough to tell one CMIP7 pattern-effect download from another, which is what the
report is for: the project, experiment, variable and frequency are the same for every
row by construction, so printing them would be nine identical columns.
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

    n_files: int
    """How many files are to be fetched"""

    n_bytes: int
    """
    How many bytes those files were published as

    An estimate, and the API's number rather than ours: it is what ESGF said, not
    what is on disk. Excludes any file published with no size at all.
    """

    n_unsized: int
    """
    How many of the files were published with no size

    Named rather than folded into zero, so that `0 B across 0 of them` cannot read
    as "nothing to fetch" when there is plenty.
    """

    @property
    def nothing_to_do(self) -> bool:
        """Whether there is nothing to fetch for this requirement"""
        return not self.n_files


def intend(label: str, plan: Sequence[DownloadPlanItem]) -> DownloadIntent:
    """
    Add a plan up into what it is about to do

    Parameters
    ----------
    label
        Which tracked requirement this is the plan for

    plan
        What [plan_only][(m).] returned

    Returns
    -------
    :
        The intent
    """
    sized = [
        item.request.expected_size
        for item in plan
        if item.request.expected_size is not None
    ]

    return DownloadIntent(
        label=label,
        n_files=len(plan),
        n_bytes=sum(sized),
        n_unsized=len(plan) - len(sized),
    )


def plan_only(
    engine: Engine, query: QueryProtocol, root: Path
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

    Returns
    -------
    :
        One item per file which is not already here
    """
    with Session(engine) as session:
        return plan_downloads(session, query, root)


def run_downloads(
    engine: Engine, query: QueryProtocol, root: Path
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

    Returns
    -------
    :
        One result per file attempted, in the order they were attempted

        Files already on disk are not attempted and so are not here.
    """
    return download(
        engine,
        query,
        root,
        observer=record_data_node_calls(engine),
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

    The keys are [IDENTITY_FACETS][(m).]. A facet the database holds as `NULL`, which
    `grid_label` is allowed to be, reads as `'-'`.
    """

    version: str
    """The dataset version it belongs to, as ESGF reports it"""

    filename: str
    """The file's own name"""


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
                {facet: getattr(dataset, facet) or "-" for facet in IDENTITY_FACETS},
                version.version,
                file.filename,
            )
            for file, version, dataset in rows
        }

    described = []
    for result in results:
        found = known.get(result.request.file_id)
        if found is None:
            described.append(_bare_record(result))
            continue

        facets, version, filename = found
        described.append(
            DownloadRecord(
                result=result, facets=facets, version=version, filename=filename
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
        facets=dict.fromkeys(IDENTITY_FACETS, "-"),
        version="-",
        filename=result.request.dest.name,
    )
