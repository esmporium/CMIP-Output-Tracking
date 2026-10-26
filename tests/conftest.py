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

import pytest
from esmporium.db import (
    ChangeRecord,
    RunHealth,
    SearchRunLeafInfo,
    SearchSummary,
    VersionRecord,
)

from local.endpoints import Endpoint, EndpointResult

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

    return SummaryScenes(
        version=version,
        change=change,
        leaf=leaf,
        run=run,
        summary=summary,
        endpoint=endpoint,
        result=result,
    )
