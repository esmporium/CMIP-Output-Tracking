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

Spelled out rather than derived from the column names, so that a rendered table
reads like a real one: a checked-in regression file full of
`reporting_interval-value` is one nobody can review by eye, which is the whole
point of having it.

Every key of esmporium's `DATASET_FACET_COLUMNS` must be present, which
`test_the_scenes_fill_in_every_facet_column` pins. When esmporium adds a facet,
that test is what tells us to add it here rather than letting the tables render a
silent blank column.
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

    def leaf(
        role="historical.tas",
        *,
        answered=True,
        n_matches=209,
        n_documents=209,
        failure=None,
    ):
        """Build one sub-search's outcome, as a run's health carries it."""
        return SearchRunLeafInfo(
            roles=(role,),
            project="CMIP7",
            answered=answered,
            host="search.east.esgf.io" if answered else None,
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
    ):
        """Build a whole summary, with the boring parts filled in."""
        return SearchSummary(
            requirement_name="pattern-effect",
            stored_requirement_id=1,
            run=health if health is not None else run(),
            baseline_run_id=baseline_run_id,
            window_runs=window_runs,
            changes=changes,
            net_unchanged=net_unchanged,
            current=current,
        )

    return SummaryScenes(
        version=version,
        change=change,
        leaf=leaf,
        run=run,
        summary=summary,
    )
