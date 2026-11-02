"""
Re-useable fixtures for the tests

Everything here builds a `SearchSummary` by hand, so no test in this repository
needs a database, a network or a live ESGF. That is the whole reason the renderer
is testable at all: it is a pure function of a summary, and a summary is a plain
frozen dataclass we can fill in ourselves.

Ported from esmporium's own `tests/conftest.py`, trimmed to just the summary
scene builders. If you find yourself needing one of the fixtures left behind
there (`get_dataset_kwargs`, the opt-in network markers), that is a sign the test
you are writing belongs upstream rather than here.
"""

import datetime
import typing
from dataclasses import dataclass
from pathlib import PurePosixPath

import pytest
from esmporium.datasets import AccessService, ContainerFormat, DownloadOutcome
from esmporium.db import (
    ChangeRecord,
    DownloadSummary,
    RunHealth,
    SearchRunLeafInfo,
    SearchSummary,
    VersionRecord,
)
from esmporium.downloads import (
    DataNodeFetchError,
    DownloadRequest,
    DownloadResult,
    FetchOutcome,
    FileTarget,
    Route,
    RouteFailure,
    RunTotals,
    esmporium_layout,
)

from local.download_formatting import DownloadGroup
from local.downloads import DatasetTally, DownloadIntent, DownloadRecord
from local.endpoints import Endpoint, EndpointResult
from local.summary_formatting import ReportGroup

REALISTIC_FACETS: dict[str, str] = {
    "project": "CMIP7",
    "institution": "CSIRO",
    "model": "ACCESS-ESM1-6",
    "experiment": "historical",
    "variant_label": "r1i1p1f1",
    "variable": "tas",
    "reporting_interval": "mon",
    "grid_label": "gn",
    "processing_id": "Amon",
}
"""
A plausible set of dataset facets, for the scenes whose facets get rendered
"""


@dataclass(frozen=True)
class SummaryScenes:
    """The vocabulary the `summary_scenes` fixture hands a test"""

    version: typing.Callable[..., VersionRecord]
    """Build one dataset version, as a summary carries it"""

    change: typing.Callable[..., ChangeRecord]
    """Build one change, as a summary carries it"""

    leaf: typing.Callable[..., SearchRunLeafInfo]
    """Build one sub-search's outcome, as a run's health carries it"""

    run: typing.Callable[..., RunHealth]
    """Build one run's health"""

    summary: typing.Callable[..., SearchSummary]
    """Build a whole summary, with the parts a test doesn't care about filled in"""

    endpoint: typing.Callable[..., Endpoint]
    """Build one endpoint, as the report identifies it"""

    result: typing.Callable[..., EndpointResult]
    """Build one endpoint's result, answered or failed"""

    groups: typing.Callable[..., tuple[ReportGroup, ...]]
    """Group results the way the driver does, so a test can pass a flat list"""


@pytest.fixture
def summary_scenes():
    """
    Get builders for the pieces of a search summary

    Every builder fills in the parts a test is not making a point about, so that a
    test reads as the one thing it is checking.
    """

    def version(  # noqa: PLR0913 - one argument per thing a scene varies
        model="ACCESS-ESM1-6",
        experiment="historical",
        variant_label="r1i1p1f1",
        dataset_version_id=101,
        *,
        version_string="20261018",
        retracted=False,
        is_latest=True,
        data_nodes=("esgf.nci.org.au",),
        seen_in_run_id=7,
        **facets,
    ):
        """
        Build one dataset version, as a summary carries it

        Real CMIP values rather than placeholders, because these facets are
        rendered. Pass any facet by name to vary it.
        """
        return VersionRecord(
            dataset_version_id=dataset_version_id,
            dataset_id=dataset_version_id * 10,
            id_project_specific=f"CMIP7.{model}.{experiment}.{variant_label}",
            facets={
                **REALISTIC_FACETS,
                "model": model,
                "experiment": experiment,
                "variant_label": variant_label,
                **facets,
            },
            version=version_string,
            is_latest=is_latest,
            retracted=retracted,
            data_nodes=data_nodes,
            seen_in_run_id=seen_in_run_id,
        )

    def change(change_type, of=None, *, detail=None, first_run_id=7, last_run_id=7):
        """Build one change, as a summary carries it."""
        return ChangeRecord(
            change_type=change_type,
            version=of if of is not None else version(),
            detail=detail,
            first_run_id=first_run_id,
            last_run_id=last_run_id,
        )

    def leaf(  # noqa: PLR0913 - one argument per thing a scene varies
        role="historical.tas",
        *,
        answered=True,
        n_matches=209,
        n_documents=209,
        failure=None,
        host="search.east.esgf.io",
        project="CMIP7",
    ):
        """Build one sub-search's outcome, as a run's health carries it."""
        return SearchRunLeafInfo(
            roles=(role,),
            project=project,
            answered=answered,
            host=host if answered else None,
            n_matches=n_matches,
            n_documents=n_documents,
            failure=failure,
        )

    def run(run_id=7, *, complete=True, leaves=None):
        """Build one run's health."""
        return RunHealth(
            run_id=run_id,
            started_at=datetime.datetime(
                2026, 10, 20, 6, 2, tzinfo=datetime.timezone.utc
            ),
            finished_at=datetime.datetime(
                2026, 10, 20, 6, 4, tzinfo=datetime.timezone.utc
            ),
            complete=complete,
            leaves=(leaf(),) if leaves is None else leaves,
        )

    def summary(  # noqa: PLR0913 - one argument per field a scene fills in
        *,
        health=None,
        changes=(),
        net_unchanged=0,
        current=(),
        window_runs=(7,),
        baseline_run_id=6,
        requirement_name="pattern-effect",
    ):
        """Build a whole summary, with the boring parts filled in."""
        return SearchSummary(
            requirement_name=requirement_name,
            stored_requirement_id=1,
            run=health if health is not None else run(),
            baseline_run_id=baseline_run_id,
            window_runs=window_runs,
            changes=changes,
            net_unchanged=net_unchanged,
            current=current,
        )

    def endpoint(tag="east", *, project="CMIP7", host=None):
        """
        Build one endpoint

        The host defaults to the real one for `tag` where we have it, so that a
        rendered table reads like a real one rather than carrying `host-east`.
        """
        known = {
            "east": "search.east.esgf.io",
            "west": "search.west.esgf.io",
            "nsc": "esg-dn1.nsc.liu.se",
            "ornl": "esgf-node.ornl.gov",
            "nci": "esgf.nci.org.au",
            "dkrz": "esgf-data.dkrz.de",
            "ceda": "esgf.ceda.ac.uk",
        }

        return Endpoint(
            project=project,
            host=host if host is not None else known.get(tag, f"{tag}.example.org"),
            tag=tag,
        )

    def result(
        tag="east",
        *,
        project="CMIP7",
        failure=None,
        complete=True,
        **summary_kwargs,
    ):
        """
        Build one endpoint's result

        Pass `failure` for an endpoint which did not answer, `complete=False` for one
        whose run came back short, or any `summary` argument to shape the one which
        answered in full. `failure` and the rest are mutually exclusive in the type,
        so passing it ignores them.
        """
        where = endpoint(tag, project=project)
        if failure is not None:
            return EndpointResult(endpoint=where, summary=None, failure=failure)

        # A result's run health has to report this endpoint's own host, or a fixture
        # reads as every endpoint having been answered by east -- which is the exact
        # bug the whole per-endpoint report exists to make visible.
        summary_kwargs.setdefault(
            "health",
            run(
                complete=complete,
                leaves=(leaf(host=where.host, project=project),),
            ),
        )

        return EndpointResult(
            endpoint=where,
            summary=summary(
                requirement_name=f"pattern-effect-{project.lower()}-{tag}",
                **summary_kwargs,
            ),
            failure=None,
        )

    def groups(results, *, detailed=()):
        """
        Group results for the renderer, keyed by project

        The driver groups by tracked requirement, and its two requirements happen to
        be labelled by project, so grouping on `endpoint.project` here produces
        exactly what it produces. This exists so that a test can go on reading as a
        flat list of endpoints rather than as a list of groups of endpoints.
        """
        by_label: dict[str, list[EndpointResult]] = {}
        for result in results:
            by_label.setdefault(result.endpoint.project, []).append(result)

        return tuple(
            ReportGroup(
                label=label, detailed=label in detailed, results=tuple(of_label)
            )
            for label, of_label in by_label.items()
        )

    return SummaryScenes(
        version=version,
        change=change,
        leaf=leaf,
        run=run,
        summary=summary,
        endpoint=endpoint,
        result=result,
        groups=groups,
    )


DOWNLOAD_ROOT = PurePosixPath("/data/cmip")
"""
Where the download scenes pretend to write

An absolute path, because a report prints it and a reader is meant to see which disk
is being filled. Nothing is ever written there: every download scene is built by
hand, the same way the summary scenes are.

A `PurePosixPath` rather than a `Path`, so that the checked-in copies of the email
render identically whoever runs the tests. A `Path` would come out with backslashes
on Windows and the regression files would fail for the platform rather than for the
layout.
"""


@dataclass(frozen=True)
class DownloadScenes:
    """The vocabulary the `download_scenes` fixture hands a test"""

    route: typing.Callable[..., Route]
    """Build one route to one file's bytes"""

    fetched: typing.Callable[..., DownloadRecord]
    """Build a record of a file which arrived"""

    failed: typing.Callable[..., DownloadRecord]
    """Build a record of a file which did not"""

    skipped: typing.Callable[..., DownloadRecord]
    """Build a record of a file which was already on disk"""

    totals: typing.Callable[..., RunTotals]
    """Build what a run added up to"""

    held: typing.Callable[..., DownloadSummary]
    """Build what the database holds overall"""

    tally: typing.Callable[..., DatasetTally]
    """Build one dataset version's worth of files, counted"""

    intent: typing.Callable[..., DownloadIntent]
    """Build what one requirement is about to fetch"""

    group: typing.Callable[..., DownloadGroup]
    """Build one tracked requirement's download run"""

    root: PurePosixPath
    """Where the scenes pretend to write"""


@pytest.fixture
def download_scenes():
    """
    Get builders for the pieces of a download run

    The download twin of `summary_scenes`, and for the same reason: a report is a
    pure function of these objects, so a test can build the morning it wants to see
    without a network, a disk or a database.
    """

    def route(data_node="esgf.ceda.ac.uk", *, url=None, service=AccessService.HTTPS):
        """Build one route, with a plausible URL for `data_node`."""
        return Route(
            url=url if url is not None else f"https://{data_node}/thredds/tas.nc",
            data_node=data_node,
            service=service,
            container_format=ContainerFormat.NETCDF,
            data_access_id=1,
        )

    def _request(  # noqa: PLR0913 - one argument per thing a scene varies
        filename, *, size, file_id, model, variant_label, version, grid_label
    ):
        """Build the request half of a result, including where it would land."""
        relative = esmporium_layout(
            FileTarget(
                id_project_specific=(
                    f"MIP-DRS7.CMIP7.CMIP.UKNCSP.{model}.historical."
                    f"{variant_label}.glb.mon.tas.tavg-h2m-hxy-u.{grid_label}"
                ),
                version=version,
                filename=filename,
            )
        )

        return DownloadRequest(
            routes=(route(),),
            dest=DOWNLOAD_ROOT / relative,
            file_id=file_id,
            expected_size=size,
            checksum="e311b6e532776cdb",
            checksum_type="SHA256",
        )

    def _record(  # noqa: PLR0913 - one argument per thing a scene varies
        result,
        *,
        model,
        variant_label,
        version,
        filename,
        grid_label,
        dataset_version_id,
    ):
        """Pair a result with the identity a report shows it under."""
        return DownloadRecord(
            result=result,
            facets={
                "model": model,
                "variant_label": variant_label,
                "grid_label": grid_label,
            },
            version=version,
            filename=filename,
            dataset_version_id=dataset_version_id,
        )

    def fetched(  # noqa: PLR0913 - one argument per thing a scene varies
        filename="tas_mon_UKESM1-3-LL_historical_r1i1p1f2_185001-194912.nc",
        *,
        size=65345526,
        file_id=1,
        model="UKESM1-3-LL",
        variant_label="r1i1p1f2",
        version="20261020",
        grid_label="g110",
        dataset_version_id=101,
        served_by="esgf.ceda.ac.uk",
        after_failures=(),
    ):
        """
        Build a record of a file which arrived

        Pass `after_failures` to say it only arrived from a later mirror, which is
        the thing the report's `after` column exists to show.
        """
        request = _request(
            filename,
            size=size,
            file_id=file_id,
            model=model,
            variant_label=variant_label,
            version=version,
            grid_label=grid_label,
        )
        result = DownloadResult(
            request=request,
            fetch=FetchOutcome(
                path=request.dest,
                bytes_written=size,
                size_on_disk=size,
                checksum_verified=True,
                checksum_type_used="SHA256",
                resumed=False,
                route=route(served_by),
            ),
            failures=tuple(
                RouteFailure(
                    route=route(node),
                    outcome=DownloadOutcome.HOST_FAULT,
                    error=DataNodeFetchError(
                        f"https://{node}/thredds/tas.nc", "500 from the node"
                    ),
                )
                for node in after_failures
            ),
        )

        return _record(
            result,
            model=model,
            variant_label=variant_label,
            version=version,
            filename=filename,
            grid_label=grid_label,
            dataset_version_id=dataset_version_id,
        )

    def failed(  # noqa: PLR0913 - one argument per thing a scene varies
        filename="tas_mon_UKCM2-0-LL_historical_r1i1p1f1_185001-194912.nc",
        *,
        size=48000000,
        file_id=9,
        model="UKCM2-0-LL",
        variant_label="r1i1p1f1",
        version="20260909",
        grid_label="g110",
        dataset_version_id=109,
        tried=("esgf.ceda.ac.uk", "esgf.nci.org.au"),
        outcome=DownloadOutcome.HOST_FAULT,
    ):
        """
        Build a record of a file which did not arrive

        `tried` empty is the case where the selector offered no route at all, which
        reads differently from every route having been tried and failed.
        """
        result = DownloadResult(
            request=_request(
                filename,
                size=size,
                file_id=file_id,
                model=model,
                variant_label=variant_label,
                version=version,
                grid_label=grid_label,
            ),
            fetch=None,
            failures=tuple(
                RouteFailure(
                    route=route(node),
                    outcome=outcome,
                    error=DataNodeFetchError(
                        f"https://{node}/thredds/tas.nc", str(outcome)
                    ),
                )
                for node in tried
            ),
        )

        return _record(
            result,
            model=model,
            variant_label=variant_label,
            version=version,
            filename=filename,
            grid_label=grid_label,
            dataset_version_id=dataset_version_id,
        )

    def skipped(  # noqa: PLR0913 - one argument per thing a scene varies
        filename="tas_mon_CanESM6-0-MR_historical_r1i1p1f1_185001-202112.nc",
        *,
        size=51000000,
        file_id=20,
        model="CanESM6-0-MR",
        variant_label="r1i1p1f1",
        version="20260629",
        grid_label="g150",
        dataset_version_id=120,
    ):
        """Build a record of a file which was already on disk."""
        result = DownloadResult(
            request=_request(
                filename,
                size=size,
                file_id=file_id,
                model=model,
                variant_label=variant_label,
                version=version,
                grid_label=grid_label,
            ),
            fetch=None,
            skipped=True,
        )

        return _record(
            result,
            model=model,
            variant_label=variant_label,
            version=version,
            filename=filename,
            grid_label=grid_label,
            dataset_version_id=dataset_version_id,
        )

    def totals(  # noqa: PLR0913 - one argument per field a scene fills in
        *,
        n_files=0,
        n_fetched=0,
        n_skipped=0,
        n_failed=0,
        n_given_up=0,
        bytes_fetched=0,
    ):
        """Build what a run added up to."""
        return RunTotals(
            n_files=n_files,
            n_fetched=n_fetched,
            n_skipped=n_skipped,
            n_failed=n_failed,
            n_given_up=n_given_up,
            bytes_fetched=bytes_fetched,
        )

    def held(*, done=0, queued=0, failed=0, given_up=0, bytes_done=0):
        """Build what the database holds overall, keyed as the statuses are stored."""
        counts = {
            "done": done,
            "queued": queued,
            "failed": failed,
            "given_up": given_up,
        }

        return DownloadSummary(
            n_by_status={status: count for status, count in counts.items() if count},
            bytes_by_status={"done": bytes_done} if bytes_done else {},
        )

    def tally(  # noqa: PLR0913 - one argument per thing a scene varies
        dataset_version_id=101,
        *,
        model="UKESM1-3-LL",
        variant_label="r1i1p1f2",
        grid_label="g110",
        version="20261020",
        n_files=3,
        n_bytes=3 * 65345526,
        n_unsized=0,
        nodes=(),
    ):
        """Build one dataset version's worth of files, counted."""
        return DatasetTally(
            dataset_version_id=dataset_version_id,
            facets={
                "model": model,
                "variant_label": variant_label,
                "grid_label": grid_label,
            },
            version=version,
            n_files=n_files,
            n_bytes=n_bytes,
            n_unsized=n_unsized,
            nodes=nodes,
        )

    def intent(tallies=None, *, label="CMIP7", n_known=325):
        """
        Build what one requirement is about to fetch

        `tallies=None` means one plausible dataset; `()` means nothing to fetch,
        which `n_known` then distinguishes from nothing being known at all.
        """
        return DownloadIntent(
            label=label,
            tallies=(tally(),) if tallies is None else tuple(tallies),
            n_known=n_known,
        )

    def group(records=(), *, label="CMIP7", **total_kwargs):
        """
        Build one requirement's download run

        The totals default to being worked out from `records`, so a test which only
        cares about the tables does not have to keep a tally in step by hand. Pass
        any total by name to say something the records do not, e.g. `n_given_up`.
        """
        fetched = [
            record
            for record in records
            if record.result.fetch is not None and not record.result.skipped
        ]
        counted = {
            "n_files": len(records),
            "n_fetched": len(fetched),
            "n_skipped": sum(1 for record in records if record.result.skipped),
            "n_failed": sum(1 for record in records if not record.result.succeeded),
            "bytes_fetched": sum(
                record.result.fetch.size_on_disk
                for record in fetched
                if record.result.fetch is not None
            ),
            **total_kwargs,
        }

        return DownloadGroup(
            label=label, records=tuple(records), totals=totals(**counted)
        )

    return DownloadScenes(
        route=route,
        fetched=fetched,
        failed=failed,
        skipped=skipped,
        totals=totals,
        held=held,
        tally=tally,
        intent=intent,
        group=group,
        root=DOWNLOAD_ROOT,
    )
