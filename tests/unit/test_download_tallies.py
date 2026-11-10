"""
Turning files into dataset versions

A file is an arbitrary slice of a dataset version -- CMIP7 publishes ACCESS-ESM1-6
as eighteen files per variant and CanESM6-0-MR as three -- so both emails report in
dataset versions. This is where that collapsing happens, and it is a pure function
of what the database handed back, so none of it needs a database.
"""

from __future__ import annotations

from local.downloads import IDENTITY_FACETS, describe_plan, tally_records


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


GREGORY_COLUMNS = ("model", "variant_label", "experiment", "variable")
"""The columns a question spanning several experiments and variables asks for"""


def plan_item(dataset_version_id, file_id, size=1000):
    """
    Build the one thing `describe_plan` reads off a planned file

    A stand-in rather than the real `DownloadPlanItem`, because `describe_plan` reads
    exactly three things off one -- which dataset version it belongs to, and the
    expected size on its request -- and building a real one would mean building a
    real route to nowhere.

    Parameters
    ----------
    dataset_version_id
        Which dataset version the file belongs to

    file_id
        The file's id

    size
        What the API said it weighs

    Returns
    -------
    :
        Something shaped like a plan item
    """

    class Request:
        expected_size = size

    class Item:
        request = Request()

    Item.dataset_version_id = dataset_version_id
    Item.file_id = file_id

    return Item()


def identities_for(facets):
    """
    Build an identity lookup which answers with `facets` for every version

    Parameters
    ----------
    facets
        The facet mapping to answer with

    Returns
    -------
    :
        A lookup `describe_plan` can be given instead of the database one
    """

    def lookup(session, dataset_version_ids):
        return {each: (facets, "20260101") for each in dataset_version_ids}

    return lookup


IDENTITY = {
    "model": "UKCM2a-0-HH",
    "variant_label": "r1i1p1f1",
    "grid_label": "g137",
    "experiment": "piControl",
    "variable": "rsut",
}
"""One dataset's worth of facets, with every one `RECORDED_FACETS` names"""


def test_a_requirements_choice_of_columns_survives_the_trip_to_the_renderer():
    # The regression this exists for, and it was a real one. `describe_plan` took the
    # column names as `facets` and then reused that name for the loop variable
    # holding each dataset's facet *mapping* -- so every intent came back carrying
    # the last dataset's facets where its column choice should have been, and every
    # table in both emails rendered the same five columns.
    #
    # Nothing raised. The only symptom was a `grid` column on a question that never
    # asked for one, and `experiment` and `variable` columns on two questions where
    # they are the same value in every row.
    intent = describe_plan(
        engine=None,
        label="CMIP7 gregory",
        plan=[plan_item(1, 1)],
        n_known=1,
        facets=GREGORY_COLUMNS,
        identities=identities_for(IDENTITY),
    )

    assert intent.facets == GREGORY_COLUMNS


def test_a_question_which_asks_for_no_columns_gets_the_renderers_default():
    # `None` rather than `()`, because the renderers read `None` as "use your default"
    # and `()` as "show no columns at all", which renders a table of bare ids.
    intent = describe_plan(
        engine=None,
        label="CMIP7 tas",
        plan=[plan_item(1, 1)],
        n_known=1,
        identities=identities_for(IDENTITY),
    )

    assert intent.facets is None


def test_a_tally_carries_every_recorded_facet_whatever_is_printed():
    # The division the column choice depends on: the lookup happens once and carries
    # everything, and choosing what to show is the renderer's job. A tally which
    # carried only the printed columns would make `report_facets` unchangeable
    # without re-planning.
    intent = describe_plan(
        engine=None,
        label="CMIP7 tas",
        plan=[plan_item(1, 1)],
        n_known=1,
        facets=IDENTITY_FACETS,
        identities=identities_for(IDENTITY),
    )

    assert intent.tallies[0].facets == IDENTITY
    assert set(IDENTITY_FACETS) <= set(intent.tallies[0].facets)


def test_files_of_one_dataset_version_become_one_tally():
    intent = describe_plan(
        engine=None,
        label="CMIP7 gregory",
        plan=[plan_item(7, file_id=each, size=100) for each in range(25)],
        n_known=25,
        facets=GREGORY_COLUMNS,
        identities=identities_for(IDENTITY),
    )

    assert intent.n_datasets == 1
    assert intent.n_files == 25
    assert intent.n_bytes == 2500
