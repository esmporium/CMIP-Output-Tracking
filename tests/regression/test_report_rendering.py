"""
Regression tests on what the morning email looks like, across all nine endpoints

The same bargain as `test_summary_rendering.py`, one level up: the value is not that
the text is right -- we do not know yet what right looks like -- but that it is
*visible*. A change to a heading, a column or the order of blocks is invisible in a
diff of the renderer and obvious in a diff of its output, and this output is the
thing we read over breakfast.

So this is the file to work against while reshaping the report. Change
`local.summary_formatting`, run the tests, and read the diff: it is the email, before
and after. When the new version is the one you want, take it with

    make accept-layout

and commit the `.txt` files along with the change. Committing a regenerated fixture
without reading the diff defeats the only purpose it has.

The dataset counts are the real ones, measured against ESGF while this was written,
because a fixture reading `nsc 3` teaches nobody what the report is for. The *rows*
are few, because a fixture nobody can read by eye is not a review.
"""

from __future__ import annotations

import pytest
from esmporium.db import DatasetChangeType

from local.summary_formatting import PATTERN_EFFECT_FACETS, format_report

ADDED = DatasetChangeType.ADDED
REMOVED = DatasetChangeType.REMOVED
MODIFIED = DatasetChangeType.MODIFIED

CMIP6_DATASETS = {
    "nsc": 1184,
    "ornl": 1180,
    "nci": 1184,
    "dkrz": 1150,
    "ceda": 991,
    "east": 461,
    "west": 461,
}
"""
How many datasets each CMIP6 endpoint returns, roughly as measured

The spread is the point. These hosts are answering one question and the answers are
not the same, which is most of why the CMIP6 block exists at all.
"""


def report(results):
    """Render a report the way the job does, so the fixture is what gets mailed."""
    return format_report(
        results,
        detailed=("CMIP7",),
        max_rows=50,
        facets=PATTERN_EFFECT_FACETS,
    )


@pytest.fixture
def cmip7_datasets(summary_scenes):
    """Get the CMIP7 datasets a pattern-effect search returns, as a run saw them."""
    return (
        summary_scenes.version(
            "ACCESS-ESM1-6",
            "historical",
            "r1i1p1f1",
            101,
            grid_label="g115",
            data_nodes=("esgf.nci.org.au", "esgf-data.dkrz.de"),
        ),
        summary_scenes.version(
            "CanESM6-0-MR", "historical", "r1i1p1f1", 102, grid_label="g150"
        ),
        summary_scenes.version(
            "CanESM6-0-MR", "historical", "r2i1p1f1", 103, grid_label="g150"
        ),
        summary_scenes.version(
            "UKESM1-3-LL", "historical", "r1i1p1f2", 104, grid_label="g110"
        ),
    )


@pytest.fixture
def cmip6_results(summary_scenes):
    """Get a quiet CMIP6 block: every endpoint answered, nothing moved."""
    return [
        summary_scenes.result(
            tag,
            project="CMIP6",
            current=tuple(
                summary_scenes.version(dataset_version_id=1000 + n)
                for n in range(count)
            ),
        )
        for tag, count in CMIP6_DATASETS.items()
    ]


def test_a_quiet_morning(
    file_regression, summary_scenes, cmip7_datasets, cmip6_results
):
    """
    What most mornings look like

    The most common report by far, so it is worth it being short and worth it being
    obvious at a glance that there is nothing to do.
    """
    rendered = report(
        [
            summary_scenes.result("east", current=cmip7_datasets),
            summary_scenes.result("west", current=cmip7_datasets),
            *cmip6_results,
        ]
    )

    file_regression.check(rendered, extension=".txt")


def test_east_and_west_disagree(
    file_regression, summary_scenes, cmip7_datasets, cmip6_results
):
    """
    The morning we built this to catch

    East has a dataset west has not picked up yet, and east saw real changes. The
    whole reason the two endpoints are tracked separately rather than being assumed
    to mirror each other.
    """
    only_east = summary_scenes.version(
        "CNRM-ESM2-1e", "historical", "r1i1p1f2", 105, grid_label="g105"
    )
    rendered = report(
        [
            summary_scenes.result(
                "east",
                current=(*cmip7_datasets, only_east),
                changes=(
                    summary_scenes.change(ADDED, of=only_east),
                    summary_scenes.change(
                        MODIFIED,
                        of=cmip7_datasets[0],
                        detail={"retracted": {"old": False, "new": True}},
                    ),
                ),
            ),
            summary_scenes.result("west", current=cmip7_datasets),
            *cmip6_results,
        ]
    )

    file_regression.check(rendered, extension=".txt")


def test_an_endpoint_is_down(
    file_regression, summary_scenes, cmip7_datasets, cmip6_results
):
    """
    What a flaky morning looks like

    One CMIP6 endpoint timed out and another was truncated, and west saw a removal.
    A removal on a morning like this is exactly the thing not to act on without
    reading further, which is why the incomplete runs are named out loud.
    """
    gone = summary_scenes.version(
        "UKCM2-0-LL", "historical", "r3i1p1f1", 106, grid_label="g110"
    )
    rendered = report(
        [
            summary_scenes.result("east", current=cmip7_datasets),
            summary_scenes.result(
                "west",
                current=cmip7_datasets,
                changes=(summary_scenes.change(REMOVED, of=gone),),
                complete=False,
            ),
            # Rebuilt in the order the job asks them in, because the report's order is
            # the caller's order and a fixture which scrambles it teaches the wrong
            # thing about the output.
            *[
                summary_scenes.result("nsc", project="CMIP6", failure="read timeout")
                if result.endpoint.tag == "nsc"
                else summary_scenes.result(
                    result.endpoint.tag,
                    project="CMIP6",
                    current=result.summary.current,
                    complete=result.endpoint.tag != "ceda",
                )
                for result in cmip6_results
            ],
        ]
    )

    file_regression.check(rendered, extension=".txt")
