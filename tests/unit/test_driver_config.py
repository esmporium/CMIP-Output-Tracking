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
    assert [each.label for each in driver.downloading()] == ["CMIP7 tas", "CMIP7 tos"]


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
            "pattern-effect-tas-mon-historical-cmip7-east",
            "pattern-effect-tas-mon-historical-cmip7-west",
            "pattern-effect-tas-mon-historical-cmip6-nsc",
            "pattern-effect-tas-mon-historical-cmip6-ornl",
            "pattern-effect-tas-mon-historical-cmip6-nci",
            "pattern-effect-tas-mon-historical-cmip6-dkrz",
            "pattern-effect-tas-mon-historical-cmip6-ceda",
            "pattern-effect-tas-mon-historical-cmip6-east",
            "pattern-effect-tas-mon-historical-cmip6-west",
            # Added 2026-11-03. New names, not changed ones: the nine above are
            # byte-identical to what the server's database already has, which is why
            # adding this question needed no `DB_VERSION_ID` bump and cost no history.
            "pattern-effect-sst-mon-historical-cmip7-east",
            "pattern-effect-sst-mon-historical-cmip7-west",
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

    assert {each.label for each in driver.REQUIREMENTS} == set(wanted)

    for tracked in driver.REQUIREMENTS:
        query = tracked.query
        variable, processing_id = wanted[tracked.label]

        assert tuple(query.variable) == (variable,)
        assert tuple(query.experiment) == ("historical",)
        assert tuple(query.reporting_interval) == ("mon",)
        assert tuple(query.processing_id) == processing_id


def test_every_requirement_has_its_own_label(driver):
    # Not a readability check. `with_download_blocks` matches each download plan back
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
    # The subject line names the job, and the job is more than one question now. A
    # subject built from either stem would read as a mail about `tas` on a morning
    # when only `tos` moved.
    stems = {each.stem for each in driver.REQUIREMENTS}

    assert driver.SUBJECT_STEM not in stems
    assert all(stem.startswith(driver.SUBJECT_STEM) for stem in stems)


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
