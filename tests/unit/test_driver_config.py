"""
What the driver script is actually configured to do

The other tests check that the machinery works. These check that the one instance of
it we really run is pointed where we think it is -- which is a different question,
and the one whose answer costs disk space and bandwidth when it is wrong.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from local.downloads import RECORDED_FACETS
from local.endpoints import requirement_for

DRIVER = Path(__file__).resolve().parents[2] / "scripts" / "drive-daily.py"


@pytest.fixture(scope="module")
def driver():
    """
    Load the driver script as a module

    Loaded by path because its filename has a hyphen in it, which is deliberate --
    it is a script to be run, not a module to be imported, and the hyphen says so.
    Importing it here is the exception, and it is safe because everything at module
    level is configuration: nothing runs until `main` is called.
    """
    spec = importlib.util.spec_from_file_location("drive_daily", DRIVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def test_only_cmip7_is_downloaded(driver):
    # The whole point of this file. CMIP6 is watched across seven endpoints so we
    # know what exists; we do not want ~680 of its dataset versions on this disk,
    # and we could not get them all anyway -- five of those endpoints are Solr and
    # publish no per-file URLs, so turning this on would fetch the ~460 that `east`
    # and `west` know about and say nothing at all about the rest.
    assert [each.label for each in driver.downloading()] == [
        "CMIP7 tas",
        "CMIP7 tos",
        "CMIP7 gregory",
    ]


def test_every_requirement_is_searched_whether_or_not_it_is_fetched(driver):
    # Downloading less than we search for is the intended shape, not an oversight:
    # the report is about what exists, the download is about what we want locally.
    assert len(driver.REQUIREMENTS) > len(driver.downloading())
    assert all(each.endpoints for each in driver.REQUIREMENTS)


def test_the_stored_requirement_names_are_the_ones_already_in_the_database(driver):
    # These strings are the keys every endpoint's search history hangs off. If one
    # changes, that endpoint starts again from scratch and the next email reports
    # the entire archive as new. They are not free to edit.
    names = sorted(
        requirement_for(tracked, endpoint).name
        for tracked in driver.REQUIREMENTS
        for endpoint in tracked.endpoints
    )

    assert names == sorted(
        [
            # The `-g2` on these eleven is the deliberate break of 2026-11-10, and the
            # one thing in this file that really did start again from scratch. Pointing
            # at an esmporium whose `Leaf` carries a `lineage` changed every stored
            # requirement's hash, and a search arriving under a held name against a
            # different hash is refused outright -- so these had to be renamed or the
            # job would have stopped working. See the note above `TAS_STEM`.
            #
            # They are the only generation suffix here, and `DB_VERSION_ID` is still
            # `002`, which together is the whole point: search history reset, download
            # bookkeeping kept.
            "pattern-effect-tas-mon-historical-g2-cmip7-east",
            "pattern-effect-tas-mon-historical-g2-cmip7-west",
            "pattern-effect-tas-mon-historical-g2-cmip6-nsc",
            "pattern-effect-tas-mon-historical-g2-cmip6-ornl",
            "pattern-effect-tas-mon-historical-g2-cmip6-nci",
            "pattern-effect-tas-mon-historical-g2-cmip6-dkrz",
            "pattern-effect-tas-mon-historical-g2-cmip6-ceda",
            "pattern-effect-tas-mon-historical-g2-cmip6-east",
            "pattern-effect-tas-mon-historical-g2-cmip6-west",
            "pattern-effect-sst-mon-historical-g2-cmip7-east",
            "pattern-effect-sst-mon-historical-g2-cmip7-west",
            # Added 2026-11-10, and genuinely new rather than renamed: a name nobody
            # has stored cannot clash, so this question needed no generation suffix.
            "gregory-toa-mon-abrupt4x-cmip7-east",
            "gregory-toa-mon-abrupt4x-cmip7-west",
        ]
    )


def test_the_pattern_effect_questions_are_what_we_think_they_are(driver):
    # Monthly and historical throughout; the variable and the branding suffix are
    # what differ. Keyed by label rather than by project, because CMIP7 is now asked
    # two different questions and the project alone no longer says which.
    #
    # None of these `processing_id` values are interchangeable with each other.
    # `tavg-u-hxy-sea` in particular has a near neighbour, `tavg-u-hm-sea`, which is
    # the zonal mean rather than the gridded field -- a different quantity under the
    # same variable name. See the comment on the requirement itself.
    wanted = {
        "CMIP7 tas": ("tas", ("tavg-h2m-hxy-u",)),
        "CMIP6 tas": ("tas", ("Amon",)),
        "CMIP7 tos": ("tos", ("tavg-u-hxy-sea",)),
    }

    # Scoped to the pattern-effect three on purpose. The Gregory question is not one
    # of them -- it is a different experiment, four variables and a lineage -- and
    # folding it in here would mean loosening every assertion below until none of
    # them said anything. It has its own tests.
    pattern_effect = [each for each in driver.REQUIREMENTS if each.label in wanted]
    assert {each.label for each in pattern_effect} == set(wanted)

    for tracked in pattern_effect:
        query = tracked.query
        variable, processing_id = wanted[tracked.label]

        assert tuple(query.variable) == (variable,)
        assert tuple(query.experiment) == ("historical",)
        assert tuple(query.reporting_interval) == ("mon",)
        assert tuple(query.processing_id) == processing_id
        # None of the three asks about anything but itself, so none of them needs a
        # separate download query. If one grows a lineage, this is the assertion that
        # should make somebody think about `download_query` before the bytes go
        # missing.
        assert tracked.lineage is None
        assert tracked.download_query is None


def test_every_requirement_has_its_own_label(driver):
    # Not a readability check. `with_appendices` matches each download plan back
    # to its search results through a dict keyed by label, so two requirements
    # sharing one would overwrite each other there and a download block would go
    # missing from the morning email -- with nothing raised and nothing logged.
    #
    # That is a live hazard rather than a theoretical one: both CMIP7 questions would
    # naturally have been labelled "CMIP7".
    labels = [each.label for each in driver.REQUIREMENTS]

    assert len(set(labels)) == len(labels), (
        f"two requirements share a label: {sorted(labels)}. "
        "The download plan is matched to its search results by label, so a "
        "duplicate silently drops a download block out of the email."
    )


def test_the_subject_stem_does_not_name_just_one_of_the_questions(driver):
    # The subject line names the job, and the job is now three questions in two
    # different scientific registers. A subject built from one of the stems would read
    # as a mail about the pattern effect on a morning when only the Gregory search
    # moved.
    #
    # This used to assert that every stem *started with* the subject stem, which was
    # true while every question was a pattern-effect question and the subject stem was
    # `pattern-effect`. It is the wrong shape now, and inverted deliberately: the
    # subject names the job and the stems name the questions, so it should be a prefix
    # of none of them.
    stems = {each.stem for each in driver.REQUIREMENTS}

    assert driver.SUBJECT_STEM not in stems
    assert not any(stem.startswith(driver.SUBJECT_STEM) for stem in stems)


def test_the_downloads_are_paced_per_data_node(driver):
    # These are other people's servers. `PER_DATA_NODE` is what stops a parallel run
    # turning into a pile of simultaneous requests against one host, so a `None` here
    # -- which esmporium accepts, and reads as "no limit" -- would be the one value
    # worth failing the suite over.
    assert driver.PER_DATA_NODE is not None
    assert driver.PER_DATA_NODE >= 1

    # And that parallelism is actually turned on. `test_download_pacing.py` proves the
    # plumbing forwards a worker count; this is what proves we ask for one. A
    # `DOWNLOAD_WORKERS` quietly left at 1 would pass every test in that file.
    assert driver.DOWNLOAD_WORKERS > 1


def test_the_database_both_halves_use_is_the_one_on_the_server(driver):
    # Both halves share it by necessity: the download plans from rows the search
    # wrote. Bumping the version starts a fresh database, which would also forget
    # which files are already on disk.
    assert driver.DB_PATH.name == "002_db.sqlite"
    assert driver.DB_PATH.parent.name == "dbs"


def test_the_gregory_question_is_what_we_think_it_is(driver):
    # Four variables, one experiment, monthly, and both branding suffixes. Measured
    # against the live archive on 2026-11-10: 24 dataset versions across 6 models.
    #
    # The suffixes are the load-bearing part, and for the same reason `tavg-u-hxy-sea`
    # is on the `tos` question. Monthly `tas` is *also* published as
    # `tminavg-h2m-hxy-u` and `tmaxavg-h2m-hxy-u` -- the monthly means of the daily
    # minimum and maximum, different quantities under the same variable name. A query
    # naming no suffix returns 10 `tas` versions where we want 6, and reports all ten
    # as one requirement.
    (gregory,) = [each for each in driver.REQUIREMENTS if each.label == "CMIP7 gregory"]

    assert tuple(gregory.query.project) == ("CMIP7",)
    assert tuple(gregory.query.variable) == ("tas", "rsdt", "rsut", "rlut")
    assert tuple(gregory.query.experiment) == ("abrupt-4xCO2",)
    assert tuple(gregory.query.reporting_interval) == ("mon",)
    assert tuple(gregory.query.processing_id) == (
        "tavg-h2m-hxy-u",
        "tavg-u-hxy-u",
    )


def test_the_gregory_question_asks_for_the_control_it_branched_from(driver):
    # A Gregory regression is of the anomaly against the control, so the control is
    # half the measurement rather than a nice extra. Both spellings of it, because an
    # Earth-system model's control is `esm-piControl`.
    (gregory,) = [each for each in driver.REQUIREMENTS if each.label == "CMIP7 gregory"]

    assert gregory.lineage is not None
    assert tuple(gregory.lineage.until.experiment) == ("piControl", "esm-piControl")

    # No `project` on the `until`. A walk refuses to climb across projects anyway, so
    # naming one would only add a facet that has to match -- and the `Ancestors`
    # docstring warns that a project-specific query style sets it for you.
    assert not tuple(gregory.lineage.until.project)


def test_a_question_which_walks_back_also_downloads_what_it_walked_back_to(driver):
    # The trap this is here for. A download is planned from a *query*, and the Gregory
    # search's query names `abrupt-4xCO2` only -- so with no `download_query` the
    # controls would be searched for, linked, reported in the morning email, and never
    # fetched. Nothing would raise and nothing would be logged; you would simply not
    # have the data.
    #
    # `TrackedRequirement.__post_init__` refuses that combination outright, so this is
    # belt and braces -- but it is also where the *values* are checked, which the
    # constructor cannot do.
    for tracked in driver.REQUIREMENTS:
        if tracked.lineage is None or not tracked.download:
            continue

        assert tracked.download_query is not None
        downloaded = set(tracked.download_query.experiment)

        # Everything the search asks about.
        assert set(tracked.query.experiment) <= downloaded
        # And everywhere the lineage stops. Written as an intersection rather than a
        # subset because `until` names both spellings of the control and only the ones
        # that actually exist for these models have to be downloadable.
        assert downloaded & set(tracked.lineage.until.experiment)


def test_a_question_spanning_several_variables_names_its_own_report_columns(driver):
    # `IDENTITY_FACETS` is model, variant and grid, which is enough only while a
    # question asks about one variable in one experiment. The Gregory question covers
    # four and two, so those three columns would describe eight of its rows
    # identically -- a download table where every line looks the same.
    for tracked in driver.REQUIREMENTS:
        if not tracked.download:
            continue

        spans = len(tuple(tracked.download_target.variable)) > 1 or (
            len(tuple(tracked.download_target.experiment)) > 1
        )
        if not spans:
            continue

        assert "variable" in tracked.report_facets
        assert "experiment" in tracked.report_facets


def test_the_facets_a_report_asks_for_are_ones_a_plan_actually_carries(driver):
    # `report_facets` is read off the `Dataset` row by `local.downloads._identities`,
    # which looks up `RECORDED_FACETS` and nothing else. A facet named here but not
    # recorded there does not raise: it renders as `-` in every row, which reads as a
    # database full of nulls rather than as a typo in this file.
    for tracked in driver.REQUIREMENTS:
        assert set(tracked.report_facets) <= set(RECORDED_FACETS), (
            f"{tracked.label!r} asks its download report for "
            f"{sorted(set(tracked.report_facets) - set(RECORDED_FACETS))}, which "
            "`local.downloads._identities` does not look up. Add it to "
            "`RECORDED_FACETS` or the column renders as '-' for every row."
        )
