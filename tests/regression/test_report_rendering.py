"""
Regression tests on what the morning email looks like, across all nine endpoints
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest
from esmporium.db import DatasetChangeType

from local.download_formatting import format_download_intent
from local.downloads import DownloadIntent
from local.summary_formatting import PATTERN_EFFECT_FACETS, format_report

ADDED = DatasetChangeType.ADDED
REMOVED = DatasetChangeType.REMOVED
MODIFIED = DatasetChangeType.MODIFIED

NEWEST = "20200724"
SUPERSEDED = "20190914"
"""Two plausible CMIP6 version stamps, one superseding the other"""

CMIP6_COVERAGE = {
    "nsc": (1010, 0),
    "ornl": (901, 2),
    "nci": (800, 0),
    "dkrz": (716, 9),
    "ceda": (723, 1),
    "east": (460, 0),
    "west": (460, 0),
}
"""
How much of CMIP6 each endpoint can serve, as `(at the newest version, superseded)`

Taken from a real run rather than invented, because the shape is the whole point and
it is not the shape you would guess. `nsc` holds every dataset any endpoint knows
about; `dkrz` and `ceda` hold a lot but are quietly behind on a handful; the two STAC
indexes hold under half and are never behind on what they do hold.
"""

CMIP6_MODELS = (
    "ACCESS-CM2",
    "CanESM5",
    "EC-Earth3",
    "GISS-E2-1-G",
    "MIROC6",
    "NorESM2-LM",
    "UKESM1-0-LL",
)
"""
Real CMIP6 model names to spread the fixture's datasets across

Real names rather than `model-1`, for the same reason `REALISTIC_FACETS` uses real
values: a checked-in copy of the morning email full of placeholders is one nobody can
review by eye, which is the only thing it is for.
"""

STAC_ONLY = 20
"""
Datasets only the STAC indexes and `nsc` carry

Here so that `nsc` is not merely the biggest endpoint but the only complete one, and
so that losing it costs more than its own row: without `nsc`, no single endpoint can
serve everything and the report has to say which combination can.
"""


def cmip6_holdings(summary_scenes, tag):
    """
    Build what one CMIP6 endpoint reported, as a run saw it

    Dataset ids are shared across endpoints on purpose: that is what makes the
    comparison exact, and it is true of the real database, where every endpoint's
    search writes into the same `dataset` table.
    """
    current, stale = CMIP6_COVERAGE[tag]
    held = [
        summary_scenes.version(
            CMIP6_MODELS[n % len(CMIP6_MODELS)],
            "historical",
            f"r{n // len(CMIP6_MODELS) + 1}i1p1f1",
            dataset_version_id=1000 + n,
            version_string=NEWEST if n < current else SUPERSEDED,
        )
        for n in range(current + stale)
    ]
    if tag in ("nsc", "east", "west"):
        held += [
            summary_scenes.version(
                "CanESM5-1",
                "historical",
                f"r{n + 1}i1p1f1",
                dataset_version_id=9000 + n,
                version_string=NEWEST,
            )
            for n in range(STAC_ONLY)
        ]

    return tuple(held)


DOWNLOAD_ROOT = PurePosixPath("/data/cmip")
"""
Where the job would be downloading to

A `PurePosixPath` so the checked-in copy renders identically whoever runs the tests.
"""


def report(results, summary_scenes, intents=()):
    """
    Render the search email the way the job does, so the fixture is what gets mailed

    Including the composition: the download plan is appended to the search report by
    the driver, not by the renderer, so a fixture which only called `format_report`
    would be a copy of something nobody receives.
    """
    rendered = format_report(
        summary_scenes.groups(results, detailed=("CMIP7",)),
        max_rows=50,
        facets=PATTERN_EFFECT_FACETS,
    )
    intent_block = format_download_intent(intents, root=DOWNLOAD_ROOT)
    if intent_block:
        rendered = f"{rendered}\n{intent_block}\n"

    return rendered


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
            current=cmip6_holdings(summary_scenes, tag),
        )
        for tag in CMIP6_COVERAGE
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
        ],
        summary_scenes,
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
        ],
        summary_scenes,
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
            # thing about the output. `nsc` is the one that fails, deliberately: it is
            # the only complete source, so losing it is what forces the report to work
            # out which combination of the rest can still serve everything.
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
        ],
        summary_scenes,
    )

    file_regression.check(rendered, extension=".txt")


def test_a_morning_with_something_to_download(
    file_regression, summary_scenes, cmip7_datasets, cmip6_results
):
    """
    The email that goes out *before* the download starts

    A new variant turned up in CMIP7, so there are files to fetch. The plan is
    appended to the search report and emailed immediately, so that a box which dies
    part way through a long download has still told somebody it was busy.

    This is the only report in which the two halves of the job appear together, and
    it is the one almost every working morning produces.
    """
    arrived = summary_scenes.version(
        "CanESM6-0-MR", "historical", "r26i1p1f1", 107, grid_label="g150"
    )
    rendered = report(
        [
            summary_scenes.result(
                "east",
                current=(*cmip7_datasets, arrived),
                changes=(summary_scenes.change(ADDED, of=arrived),),
            ),
            summary_scenes.result("west", current=(*cmip7_datasets, arrived)),
            *cmip6_results,
        ],
        summary_scenes,
        intents=(
            DownloadIntent(
                label="CMIP7",
                n_files=2,
                n_bytes=130691052,
                n_unsized=0,
                n_known=327,
            ),
        ),
    )

    file_regression.check(rendered, extension=".txt")


def test_a_morning_with_nothing_to_download(
    file_regression, summary_scenes, cmip7_datasets, cmip6_results
):
    """
    The same email on a morning when everything is already on disk

    Worth its own copy because it is the common case, and because "nothing new to
    fetch" has to be said rather than left as an absent section -- an email which
    simply stops after the search tables cannot be told from one where the download
    planning fell over.
    """
    rendered = report(
        [
            summary_scenes.result("east", current=cmip7_datasets),
            summary_scenes.result("west", current=cmip7_datasets),
            *cmip6_results,
        ],
        summary_scenes,
        intents=(
            DownloadIntent(
                label="CMIP7", n_files=0, n_bytes=0, n_unsized=0, n_known=325
            ),
        ),
    )

    file_regression.check(rendered, extension=".txt")
