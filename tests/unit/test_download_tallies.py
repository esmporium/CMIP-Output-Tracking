"""
Turning files into dataset versions

A file is an arbitrary slice of a dataset version -- CMIP7 publishes ACCESS-ESM1-6
as eighteen files per variant and CanESM6-0-MR as three -- so both emails report in
dataset versions. This is where that collapsing happens, and it is a pure function
of what the database handed back, so none of it needs a database.
"""

from __future__ import annotations

from local.downloads import tally_records


def test_a_dataset_several_files_wide_becomes_one_row(download_scenes):
    records = [
        download_scenes.fetched(f"tas_part{n}.nc", size=1000, file_id=n)
        for n in range(18)
    ]

    tallies = tally_records(records)

    assert len(tallies) == 1
    assert tallies[0].n_files == 18
    assert tallies[0].n_bytes == 18 * 1000


def test_bytes_counted_are_what_landed_not_what_was_promised(download_scenes):
    # `size` is what the API published; the tally reads `size_on_disk` off the
    # fetch. They agree here, and the point is which one is asked.
    tallies = tally_records([download_scenes.fetched(size=65345526)])

    assert tallies[0].n_bytes == 65345526


def test_a_skipped_file_is_not_counted_as_moved(download_scenes):
    # It was already on disk. Counting it would overstate what the run did, and the
    # headline already reports it separately.
    assert tally_records([download_scenes.skipped()]) == ()


def test_a_failed_file_is_not_counted_as_moved(download_scenes):
    assert tally_records([download_scenes.failed()]) == ()


def test_a_dataset_half_fetched_tallies_only_what_arrived(download_scenes):
    # The case worth getting right: three files of a dataset landed and one did
    # not, so the dataset appears in the fetched tally *and* in the failed table.
    # Averaging that into one verdict would hide a partial dataset.
    records = [
        *[
            download_scenes.fetched(f"got{n}.nc", size=1000, file_id=n)
            for n in range(3)
        ],
        download_scenes.failed("missed.nc", file_id=9, dataset_version_id=101),
    ]

    tallies = tally_records(records)

    assert len(tallies) == 1
    assert tallies[0].n_files == 3
    assert tallies[0].dataset_version_id == 101


def test_two_datasets_stay_apart(download_scenes):
    records = [
        download_scenes.fetched("a.nc", file_id=1, dataset_version_id=101),
        download_scenes.fetched(
            "b.nc", file_id=2, dataset_version_id=102, model="CanESM6-0-MR"
        ),
    ]

    tallies = tally_records(records)

    assert {tally.dataset_version_id for tally in tallies} == {101, 102}


def test_tallies_come_back_in_reading_order(download_scenes):
    # Model then variant, case-insensitively -- the same ordering `local.models`
    # uses, so `CanESM6-0-MR` sits beside `CanESM6-0-LR` instead of being thrown
    # elsewhere by where ASCII puts a lower-case letter.
    records = [
        download_scenes.fetched(
            "a.nc", file_id=1, dataset_version_id=1, model="UKESM1-3-LL"
        ),
        download_scenes.fetched(
            "b.nc", file_id=2, dataset_version_id=2, model="ACCESS-ESM1-6"
        ),
        download_scenes.fetched(
            "c.nc", file_id=3, dataset_version_id=3, model="CanESM6-0-MR"
        ),
    ]

    models = [tally.facets["model"] for tally in tally_records(records)]

    assert models == ["ACCESS-ESM1-6", "CanESM6-0-MR", "UKESM1-3-LL"]


def test_every_node_which_served_a_dataset_is_kept(download_scenes):
    # So that the report can say `mixed (2 nodes)` rather than picking a winner.
    records = [
        download_scenes.fetched("a.nc", file_id=1, served_by="esgf.ceda.ac.uk"),
        download_scenes.fetched("b.nc", file_id=2, served_by="esgf.nci.org.au"),
    ]

    assert set(tally_records(records)[0].nodes) == {
        "esgf.ceda.ac.uk",
        "esgf.nci.org.au",
    }
