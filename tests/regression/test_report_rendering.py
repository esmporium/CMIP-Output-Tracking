"""
Regression tests on what the morning email looks like, across all nine endpoints
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import PurePosixPath

import pytest
from esmporium.db import DatasetChangeType

from local.download_formatting import format_download_intent
from local.downloads import IDENTITY_FACETS, DatasetTally, DownloadIntent
from local.summary_formatting import (
    PATTERN_EFFECT_FACETS,
    ReportGroup,
    format_report,
)

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


TAS_LABELS = {"CMIP7": "CMIP7 tas", "CMIP6": "CMIP6 tas"}
"""
What the driver calls the two `tas` groups

Not just `"CMIP7"` and `"CMIP6"` any more: CMIP7 is asked two questions now, so the
project alone no longer says which one a group is. A checked-in copy of the email
should carry the headings that really go out.
"""

SST_LABEL = "CMIP7 tos"
"""What the driver calls the `tos` group"""


def render(groups):
    """Render pre-built groups the way the job does."""
    return format_report(groups, max_rows=50, facets=PATTERN_EFFECT_FACETS)


def with_plans(groups, intents):
    """
    Hang each group's download plan inside its own section

    The same matching `drive-daily.with_download_blocks` does, and by label for the
    same reason -- which is also why the driver's labels have to be distinct.
    """
    by_label = {intent.label: intent for intent in intents}

    return tuple(
        replace(
            group,
            appendix=format_download_intent(
                [by_label[group.label]],
                root=DOWNLOAD_ROOT,
                facets=IDENTITY_FACETS,
                max_rows=50,
            ),
        )
        if group.label in by_label
        else group
        for group in groups
    )


def report(results, summary_scenes, intents=()):
    """
    Render the search email the way the job does, so the fixture is what gets mailed

    Including the placement: each requirement's download plan rides inside its own
    section, before its model-support table, as the driver puts it there. A fixture
    which pasted the block at the end would be a copy of something nobody receives.
    """
    groups = summary_scenes.groups(
        results, detailed=(TAS_LABELS["CMIP7"],), labels=TAS_LABELS
    )
    by_label = {intent.label: intent for intent in intents}
    groups = tuple(
        replace(
            group,
            appendix=format_download_intent(
                [by_label[group.label]],
                root=DOWNLOAD_ROOT,
                facets=IDENTITY_FACETS,
                max_rows=50,
            ),
        )
        if group.label in by_label
        else group
        for group in groups
    )

    return format_report(groups, max_rows=50, facets=PATTERN_EFFECT_FACETS)


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
                label=TAS_LABELS["CMIP7"],
                tallies=(
                    DatasetTally(
                        dataset_version_id=107,
                        facets={
                            "model": "CanESM6-0-MR",
                            "variant_label": "r26i1p1f1",
                            "grid_label": "g150",
                        },
                        version="20261018",
                        n_files=2,
                        n_bytes=130691052,
                    ),
                ),
                n_known=327,
            ),
        ),
    )

    file_regression.check(rendered, extension=".txt")


@pytest.fixture
def cmip7_sst_datasets(summary_scenes):
    """
    Get the CMIP7 `tos` datasets, as a run saw them

    The same models as `cmip7_datasets`, on their ocean grids rather than their
    atmosphere ones -- `g118`/`g147`/`g126` against `g115`/`g150`/`g110`. That is what
    the second question looks like in a table, and it is the reason both questions can
    sit in one email without a reader having to be told which is which.

    One data node each, which is real and is the point: every CMIP7 `tos` dataset
    version is published to exactly one node, so ranking nodes by health has nothing
    to choose between for this question. Which node follows which institution ran the
    model, hence NCI, Environment Canada and CEDA. Measured 2026-11-03.
    """
    return (
        summary_scenes.version(
            "ACCESS-ESM1-6",
            "historical",
            "r1i1p1f1",
            201,
            grid_label="g118",
            variable="tos",
            data_nodes=("esgf.nci.org.au",),
        ),
        summary_scenes.version(
            "CanESM6-0-MR",
            "historical",
            "r1i1p1f1",
            202,
            grid_label="g147",
            variable="tos",
            data_nodes=("crd-esgf-drc.ec.gc.ca",),
        ),
        summary_scenes.version(
            "UKESM1-3-LL",
            "historical",
            "r1i1p1f2",
            203,
            grid_label="g126",
            variable="tos",
            data_nodes=("ceda.ac.uk",),
        ),
    )


def test_both_cmip7_questions(
    file_regression, summary_scenes, cmip7_datasets, cmip7_sst_datasets, cmip6_results
):
    """
    The morning `tos` was added: three groups in one email

    The layout question this is here to answer. Two of the three groups are CMIP7,
    so the headings are the only thing telling a reader which bytes belong to which
    question -- and each group gets its own coverage, its own model table and its own
    download plan, because a tag like `east` identifies an endpoint only within one
    question.

    `tas` is quiet and `tos` arrives whole, which is the real shape of the morning:
    one question has been running for weeks and the other has just been asked for
    the first time. Note `tos` reporting a first run rather than 43 additions -- a
    new question has no baseline to have changed against.

    Built by hand rather than through `summary_scenes.groups`, which groups by
    project and would therefore fold the two CMIP7 questions into one. That folding
    is exactly what `ReportGroup` exists to prevent, so a fixture which went through
    it would be a copy of the bug rather than of the email.
    """
    groups = (
        ReportGroup(
            label=TAS_LABELS["CMIP7"],
            detailed=True,
            results=(
                summary_scenes.result("east", current=cmip7_datasets),
                summary_scenes.result("west", current=cmip7_datasets),
            ),
        ),
        *summary_scenes.groups(cmip6_results, labels=TAS_LABELS),
        ReportGroup(
            label=SST_LABEL,
            detailed=True,
            results=tuple(
                summary_scenes.result(
                    tag,
                    requirement_name=f"pattern-effect-sst-cmip7-{tag}",
                    current=cmip7_sst_datasets,
                    changes=tuple(
                        summary_scenes.change(ADDED, of=each)
                        for each in cmip7_sst_datasets
                    ),
                    baseline_run_id=None,
                    window_runs=(7,),
                )
                for tag in ("east", "west")
            ),
        ),
    )
    rendered = render(
        with_plans(
            groups,
            (
                DownloadIntent(label=TAS_LABELS["CMIP7"], tallies=(), n_known=325),
                DownloadIntent(
                    label=SST_LABEL,
                    tallies=tuple(
                        DatasetTally(
                            dataset_version_id=each.dataset_version_id,
                            facets={
                                "model": each.facets["model"],
                                "variant_label": each.facets["variant_label"],
                                "grid_label": each.facets["grid_label"],
                            },
                            version=each.version,
                            n_files=n_files,
                            n_bytes=n_files * 68100000,
                        )
                        for each, n_files in zip(cmip7_sst_datasets, (6, 3, 2))
                    ),
                    # Nothing fetched yet, so everything known is outstanding.
                    n_known=11,
                ),
            ),
        )
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
        intents=(DownloadIntent(label=TAS_LABELS["CMIP7"], tallies=(), n_known=325),),
    )

    file_regression.check(rendered, extension=".txt")
