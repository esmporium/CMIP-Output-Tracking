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
    assert [each.label for each in driver.downloading()] == ["CMIP7"]


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
        ]
    )


def test_the_pattern_effect_question_is_what_we_think_it_is(driver):
    # tas, monthly, historical -- per project, with the branding suffix that pins
    # which output stream. The two `processing_id` values are not interchangeable.
    wanted = {
        "CMIP7": ("tavg-h2m-hxy-u",),
        "CMIP6": ("Amon",),
    }
    for tracked in driver.REQUIREMENTS:
        query = tracked.query

        assert tuple(query.variable) == ("tas",)
        assert tuple(query.experiment) == ("historical",)
        assert tuple(query.reporting_interval) == ("mon",)
        assert tuple(query.processing_id) == wanted[tracked.label]


def test_the_database_both_halves_use_is_the_one_on_the_server(driver):
    # Both halves share it by necessity: the download plans from rows the search
    # wrote. Bumping the version starts a fresh database, which would also forget
    # which files are already on disk.
    assert driver.DB_PATH.name == "002_db.sqlite"
    assert driver.DB_PATH.parent.name == "dbs"
