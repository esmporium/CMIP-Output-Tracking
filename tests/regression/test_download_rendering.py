"""
Regression tests on what the download email looks like

The checked-in files beside this one are copies of the mail itself, so a change to
the layout arrives as a diff you have to read rather than as a surprise in somebody's
inbox. `make accept-layout` rewrites them; read `git diff` before committing.
"""

from __future__ import annotations

import pytest
from esmporium.datasets import DownloadOutcome

from local.download_formatting import download_subject, format_download_report
from local.downloads import IDENTITY_FACETS

CMIP7_MODELS = (
    ("ACCESS-ESM1-6", "g115", "20261008", 18),
    ("CanESM6-0-MR", "g150", "20260629", 3),
    ("UKCM2-0-LL", "g110", "20260909", 2),
    ("UKESM1-3-LL", "g110", "20261020", 2),
)
"""
Real CMIP7 models, grids, version stamps and file counts

Real values rather than `model-1`, for the same reason the search fixtures use them:
a checked-in copy of an email full of placeholders is one nobody can review by eye,
which is the only thing it is for. The file counts are real too, and lopsided --
measured 2026-11-03 against the live archive.
"""

REQUIREMENT = "pattern-effect-tas-mon-historical \u00b7 CMIP7 tas"
"""
What the driver titles the `tas` download block with

The requirement and its label, not just the project: two questions now *do* share
CMIP7, so the project no longer identifies which one asked for the bytes. Spelled
out here rather than defaulted, because a checked-in copy of the mail should carry
the title that really goes out.

The variable appears twice over -- once in the stem, once in the label -- which is
mildly redundant and kept anyway: the label is what the *search* report heads this
question with, so carrying it here is what lets a reader match the two emails up.
"""

SST_REQUIREMENT = "pattern-effect-sst-mon-historical \u00b7 CMIP7 tos"
"""What the driver titles the `tos` download block with"""

SST_MODELS = (
    ("ACCESS-ESM1-6", "r1i1p1f1", "g118", "20261008", 6, "esgf.nci.org.au"),
    ("CanESM6-0-MR", "r1i1p1f1", "g147", "20260629", 3, "crd-esgf-drc.ec.gc.ca"),
    ("UKESM1-3-LL", "r1i1p1f2", "g126", "20261020", 2, "ceda.ac.uk"),
)
"""
Real CMIP7 models, variants, ocean grids, version stamps, file counts and nodes

Ocean grids, so `g118`/`g147`/`g126` where the `tas` fixtures are `g115`/`g150`/`g110`
-- the same models on a different grid, which is what `tos` being an ocean variable
looks like in a report.

The whole row is measured rather than invented, and the node in particular, because
which node serves a dataset follows which institution ran the model: ACCESS is at
NCI, CanESM at Environment Canada, UKESM at CEDA. That is the fact this fixture
exists to show. Every `tos` dataset version has exactly one node, so ranking nodes by
health has nothing to choose between -- and yet the requirement as a whole is spread
over four of them, which is what makes fetching several files at once worth doing.

Measured 2026-11-03 against the live archive, where the full split is CanESM6-0-MR 25
(g147, Canada), ACCESS-ESM1-6 10 (g118, NCI), UKCM2-0-LL 5 and UKESM1-3-LL 2 (both
g126, CEDA), CAS-ESM2-1-CSM 1 (g207, IAP).
"""

TYPICAL_SST_FILE_BYTES = 68100000
"""
How large one monthly `tos` file really is, measured 2026-11-03

Within a per-cent of the `tas` figure, which was the surprise: the expectation going
in was that `tos` would be the heavier of the two. Across the whole requirement it
is in fact the lighter -- 18.4 GB against 22.7 GB -- because there are fewer of them
(270 files to 325), not because each one is smaller.
"""

TYPICAL_FILE_BYTES = 65345526
"""
How large one of these files really is, measured 2026-11-02

Monthly `tas` for a century on a native grid. Worth being a real number: it is what
makes `21.2 GB` in a report read as a plausible total rather than an invented one.
"""


def report(groups, held, *, root, max_rows=50):
    """Render a report the way the job does, so the fixture is what gets mailed."""
    rendered = format_download_report(
        groups,
        held,
        root=root,
        facets=IDENTITY_FACETS,
        max_rows=max_rows,
    )
    # The subject is half of what anybody reads, so a checked-in copy of the mail
    # which leaves it out is a copy of the wrong thing.
    subject = download_subject(groups, stem="pattern-effect")

    return f"Subject: {subject}\n\n{rendered}"


@pytest.fixture
def first_run(download_scenes):
    """
    Get the bulk run: everything the search has found, arriving at once

    The morning this is set up, and the only one on which the totals are large.

    The file counts are the real ones, which are lopsided: CMIP7 publishes
    ACCESS-ESM1-6 as 18 files per variant and everything else as 2 or 3. That is
    exactly why both emails report dataset versions rather than files.
    """
    records = []
    file_id = 1
    dataset_version_id = 40
    for model, grid, version, n_files in CMIP7_MODELS:
        for part in range(n_files):
            records.append(
                download_scenes.fetched(
                    f"tas_tavg-h2m-hxy-u_mon_glb_{grid}_{model}"
                    f"_historical_r1i1p1f1_part{part:02d}.nc",
                    size=TYPICAL_FILE_BYTES // n_files,
                    file_id=file_id,
                    model=model,
                    variant_label="r1i1p1f1",
                    version=version,
                    grid_label=grid,
                    dataset_version_id=dataset_version_id,
                )
            )
            file_id += 1
        dataset_version_id += 1

    return tuple(records)


def test_the_first_bulk_run(file_regression, download_scenes, first_run):
    """
    The morning the download job is switched on

    Everything the search has found arrives at once, and the database holds exactly
    what this run put there, because there was nothing before it.

    In miniature: the real first run is 325 files and 21.2 GB. Four models here
    rather than seven, and one variant each rather than twenty-five, because a
    checked-in copy of the email is for reading and a 325-row table is not.
    """
    rendered = report(
        [download_scenes.group(first_run, label=REQUIREMENT)],
        download_scenes.held(
            done=len(first_run), bytes_done=len(first_run) * TYPICAL_FILE_BYTES
        ),
        root=download_scenes.root,
    )

    file_regression.check(rendered, extension=".txt")


def test_a_quiet_morning(file_regression, download_scenes):
    """
    What almost every morning looks like

    Nothing new has been published, so there is nothing to fetch. The job says so in
    a sentence, because an empty report and a broken job otherwise look identical.

    Note that this report is rendered but, by default, not mailed:
    `EMAIL_WHEN_NOTHING_DOWNLOADED` is off, and the search job's own morning mail is
    what covers the job having run at all.
    """
    rendered = report(
        [download_scenes.group(label=REQUIREMENT)],
        download_scenes.held(done=325, bytes_done=325 * TYPICAL_FILE_BYTES),
        root=download_scenes.root,
    )

    file_regression.check(rendered, extension=".txt")


def test_a_new_variant_appears(file_regression, download_scenes):
    """
    The morning this was all built for

    A model has published another variant since yesterday, so its files arrive on
    their own. This is what the automation is for: nobody asked for these, the search
    found them and the download followed.
    """
    arrived = tuple(
        download_scenes.fetched(
            f"tas_tavg-h2m-hxy-u_mon_glb_g150_CanESM6-0-MR"
            f"_historical_r26i1p1f1_{span}.nc",
            size=TYPICAL_FILE_BYTES,
            file_id=400 + n,
            model="CanESM6-0-MR",
            variant_label="r26i1p1f1",
            version="20260629",
            grid_label="g150",
            dataset_version_id=108,
        )
        for n, span in enumerate(("185001-194912", "195001-202112"))
    )
    rendered = report(
        [download_scenes.group(arrived, label=REQUIREMENT)],
        download_scenes.held(done=327, bytes_done=327 * TYPICAL_FILE_BYTES),
        root=download_scenes.root,
    )

    file_regression.check(rendered, extension=".txt")


def test_a_morning_with_trouble(file_regression, download_scenes):
    """
    A mirror was down, a node wants credentials, and a file has gone

    Three failures which read very differently and must not be flattened into one
    "failed" count: one arrived from a later mirror and is fine, one needs somebody
    to find a certificate, and one is simply not there any more. The second and third
    are `given up` -- waiting will not fix either.
    """
    records = (
        download_scenes.fetched(
            "tas_tavg-h2m-hxy-u_mon_glb_g110_UKESM1-3-LL"
            "_historical_r1i1p1f2_185001-194912.nc",
            size=TYPICAL_FILE_BYTES,
            after_failures=("esgf.nci.org.au",),
        ),
        # Same dataset as the failure below, so it appears in both tables: three
        # files landed and one did not. A partial dataset has to stay visible.
        download_scenes.fetched(
            "tas_tavg-h2m-hxy-u_mon_glb_g137_UKCM2a-0-HH"
            "_historical_r1i1p1f1_185001-194912.nc",
            size=TYPICAL_FILE_BYTES // 3,
            file_id=50,
            model="UKCM2a-0-HH",
            variant_label="r1i1p1f1",
            version="20260826",
            grid_label="g137",
            dataset_version_id=51,
            served_by="esgf.nci.org.au",
        ),
        download_scenes.failed(
            "tas_tavg-h2m-hxy-u_mon_glb_g137_UKCM2a-0-HH"
            "_historical_r1i1p1f1_185001-194912.nc",
            file_id=51,
            model="UKCM2a-0-HH",
            version="20260826",
            grid_label="g137",
            outcome=DownloadOutcome.NEEDS_CREDENTIALS,
            tried=("esgf.ceda.ac.uk",),
            dataset_version_id=51,
        ),
        download_scenes.failed(
            "tas_tavg-h2m-hxy-u_mon_glb_g134_CAS-ESM2-1-CSM"
            "_historical_r1i1p1f1_185001-194912.nc",
            file_id=52,
            model="CAS-ESM2-1-CSM",
            version="20260927",
            grid_label="g134",
            outcome=DownloadOutcome.MISSING,
            tried=("esgf.ceda.ac.uk", "esgf.nci.org.au"),
            dataset_version_id=52,
        ),
        download_scenes.skipped(),
    )
    rendered = report(
        [download_scenes.group(records, label=REQUIREMENT, n_files=5, n_given_up=2)],
        download_scenes.held(done=323, given_up=2, bytes_done=323 * TYPICAL_FILE_BYTES),
        root=download_scenes.root,
    )

    file_regression.check(rendered, extension=".txt")


@pytest.fixture
def sst_first_run(download_scenes):
    """
    Get the `tos` bulk run: the morning the second question is switched on

    Deliberately alongside a quiet `tas` run in the test below, because that is the
    realistic shape of the morning it happens: `tas` has been running for weeks and
    has nothing to do, and the new question arrives with its whole archive at once.
    """
    records = []
    file_id = 900
    dataset_version_id = 200
    for model, variant, grid, version, n_files, node in SST_MODELS:
        for part in range(n_files):
            records.append(
                download_scenes.fetched(
                    f"tos_tavg-u-hxy-sea_mon_glb_{grid}_{model}"
                    f"_historical_{variant}_part{part:02d}.nc",
                    size=TYPICAL_SST_FILE_BYTES // n_files,
                    file_id=file_id,
                    model=model,
                    variant_label=variant,
                    version=version,
                    grid_label=grid,
                    dataset_version_id=dataset_version_id,
                    served_by=node,
                )
            )
            file_id += 1
        dataset_version_id += 1

    return tuple(records)


def test_two_requirements_in_one_morning(
    file_regression, download_scenes, sst_first_run
):
    """
    The morning `tos` was added to a job which was already fetching `tas`

    The layout question this answers: one email now carries two download blocks, and
    a reader has to be able to tell at a glance which bytes belong to which question
    and that the quiet one really did run rather than being left out.

    `tas` is quiet and `tos` arrives in bulk, which is the asymmetry that makes the
    titles load-bearing. The `In the database` figures at the end are shared -- they
    are the whole database, not one requirement's share of it -- which is worth
    seeing stated once rather than per block.
    """
    rendered = report(
        [
            download_scenes.group(label=REQUIREMENT),
            download_scenes.group(sst_first_run, label=SST_REQUIREMENT),
        ],
        download_scenes.held(
            done=325 + len(sst_first_run),
            bytes_done=325 * TYPICAL_FILE_BYTES
            + len(sst_first_run) * TYPICAL_SST_FILE_BYTES,
        ),
        root=download_scenes.root,
    )

    file_regression.check(rendered, extension=".txt")


def test_more_rows_than_anyone_can_read(file_regression, download_scenes, first_run):
    """
    A bulk run under a small cap

    The rest has to be folded away rather than mailed in full, and the count of what
    was folded away has to survive -- otherwise a capped table reads as the whole of
    what happened.
    """
    rendered = report(
        [download_scenes.group(first_run, label=REQUIREMENT)],
        download_scenes.held(done=len(first_run)),
        root=download_scenes.root,
        max_rows=3,
    )

    file_regression.check(rendered, extension=".txt")
