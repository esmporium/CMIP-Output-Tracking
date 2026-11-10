"""
Tests of turning one question into one tracked search per endpoint
"""

from __future__ import annotations

import dataclasses
import re

import pytest
from esmporium.query import Query
from esmporium.requirements import Ancestors, role_paths

from local.endpoints import (
    Endpoint,
    EndpointResult,
    TrackedRequirement,
    endpoint_requirement,
    facade_selector,
    requirement_for,
    search_endpoint,
    single_facade_selector,
)

QUERY = Query(
    project=("CMIP7",),
    variable=("tas",),
    experiment=("historical",),
    reporting_interval=("mon",),
    processing_id=("tavg-h2m-hxy-u",),
)

EAST = Endpoint("CMIP7", "search.east.esgf.io", "east")
WEST = Endpoint("CMIP7", "search.west.esgf.io", "west")


def build(endpoint):
    return endpoint_requirement(
        endpoint, QUERY, name_stem="pattern-effect", role_stem="tas"
    )


def test_the_name_carries_the_project_and_the_endpoint():
    assert build(EAST).name == "pattern-effect-cmip7-east"


def test_two_endpoints_are_two_requirements():
    assert build(EAST).requirement_hash() != build(WEST).requirement_hash()


def test_two_endpoints_send_the_same_query():
    # The whole comparison rests on this. If the role leaked into the query, east and
    # west would be answering different questions and their difference would mean
    # nothing.
    assert build(EAST).tree.query == build(WEST).tree.query


def test_the_host_alone_does_not_make_two_requirements():
    # The reason the tag has to exist at all. These two differ only in host, which is
    # nowhere in the question esmporium fingerprints, so they are the *same*
    # requirement and could not both be stored. Anyone who removes the tag from the
    # role gets this behaviour back for all nine endpoints.
    shared_tag = [
        endpoint_requirement(
            Endpoint("CMIP7", host, "same"),
            QUERY,
            name_stem="pattern-effect",
            role_stem="tas",
        )
        for host in ("search.east.esgf.io", "search.west.esgf.io")
    ]

    assert shared_tag[0].requirement_hash() == shared_tag[1].requirement_hash()


def test_a_selector_offers_exactly_one_facade():
    selector = single_facade_selector(EAST)

    first = selector(None, 0)

    assert first is not None
    assert first.search_api.host == "search.east.esgf.io"
    # Nothing on the second attempt: one host answered or nobody did, so a run's
    # host and its completeness both mean what they say.
    assert selector(None, 1) is None


def test_an_endpoint_esmporium_cannot_serve_is_an_error():
    # CMIP5 is deliberately not served over STAC upstream, so this pairing has no
    # facade. Better to hear about it by name than to search the wrong thing.
    with pytest.raises(ValueError, match=re.escape("search.east.esgf.io")):
        single_facade_selector(Endpoint("CMIP5", "search.east.esgf.io", "east"))


def test_one_endpoint_failing_is_a_result_not_an_exception():
    # Nine endpoints, and one being down is a thing to report rather than a reason to
    # abandon the other eight.
    def explode(*args, **kwargs):
        msg = "read timeout"
        raise TimeoutError(msg)

    result = search_endpoint(None, EAST, build(EAST), search=explode)

    assert isinstance(result, EndpointResult)
    assert result.summary is None
    assert result.failure == "read timeout"
    assert not result.answered
    # A failed endpoint has no datasets, which a report must not read as "it returned
    # nothing": that is what `failure` is for.
    assert result.n_datasets == 0


def test_an_endpoint_which_answers_carries_its_summary(summary_scenes):
    built = summary_scenes.summary(current=(summary_scenes.version(),))

    result = search_endpoint(
        None,
        EAST,
        build(EAST),
        search=lambda *args, **kwargs: None,
        summarise=lambda *args, **kwargs: built,
    )

    assert result.answered
    assert result.failure is None
    assert result.n_datasets == 1


def test_n_datasets_counts_datasets_not_versions(summary_scenes):
    # Two versions of one dataset is one dataset. The raw document counts the hosts
    # report differ by a factor of four through replicas alone, so the report counts
    # identities and this is what pins that.
    one = summary_scenes.version(dataset_version_id=101)
    another = summary_scenes.version(dataset_version_id=102)
    # A second version of the dataset `one` is a version of.
    sharing = dataclasses.replace(
        summary_scenes.version(dataset_version_id=103),
        dataset_id=one.dataset_id,
        version="20261101",
    )

    result = EndpointResult(
        endpoint=EAST,
        summary=summary_scenes.summary(current=(one, another, sharing)),
        failure=None,
    )

    assert len(result.summary.current) == 3
    assert result.n_datasets == 2


def test_a_tracked_requirement_stores_under_the_name_it_always_did():
    # The load-bearing test in this file. The stored requirement name is the key an
    # endpoint's whole search history hangs off, so if `requirement_for` ever
    # produced a different string, every endpoint would silently start again from
    # scratch and the first morning's email would report the entire archive as new.
    # These names are what is in the database today; they are not free to change.
    tracked = TrackedRequirement(
        label="CMIP7",
        stem="pattern-effect-tas-mon-historical",
        role_stem="tas",
        query=Query(
            project=("CMIP7",),
            variable=("tas",),
            experiment=("historical",),
            reporting_interval=("mon",),
            processing_id=("tavg-h2m-hxy-u",),
        ),
        endpoints=(
            Endpoint("CMIP7", "search.east.esgf.io", "east"),
            Endpoint("CMIP7", "search.west.esgf.io", "west"),
        ),
    )

    names = [requirement_for(tracked, endpoint).name for endpoint in tracked.endpoints]

    assert names == [
        "pattern-effect-tas-mon-historical-cmip7-east",
        "pattern-effect-tas-mon-historical-cmip7-west",
    ]


def test_two_requirements_sharing_a_stem_still_store_apart():
    # Ours do share one: "the pattern effect" asked of CMIP7 and of CMIP6 is one
    # question, and the project is what the database files them apart by. If it did
    # not, the two would collide on `requirement_hash` and esmporium would refuse
    # the second one.
    shared = dict(stem="pattern-effect", role_stem="tas")
    cmip7 = TrackedRequirement(
        label="CMIP7",
        query=Query(project=("CMIP7",), variable=("tas",)),
        endpoints=(Endpoint("CMIP7", "search.east.esgf.io", "east"),),
        **shared,
    )
    cmip6 = TrackedRequirement(
        label="CMIP6",
        query=Query(project=("CMIP6",), variable=("tas",)),
        endpoints=(Endpoint("CMIP6", "esgf.ceda.ac.uk", "ceda"),),
        **shared,
    )

    first = requirement_for(cmip7, cmip7.endpoints[0])
    second = requirement_for(cmip6, cmip6.endpoints[0])

    assert first.name != second.name
    assert first.requirement_hash() != second.requirement_hash()


def test_an_endpoint_for_the_wrong_project_is_refused():
    # Cheap to check and miserable to debug: the facade store pairs a host with a
    # project, so a CMIP6 endpoint on a CMIP7 question would surface as an endpoint
    # that answers nothing, for no stated reason.
    with pytest.raises(ValueError, match="CMIP6"):
        TrackedRequirement(
            label="muddled",
            stem="pattern-effect",
            role_stem="tas",
            query=Query(project=("CMIP7",), variable=("tas",)),
            endpoints=(
                Endpoint("CMIP7", "search.east.esgf.io", "east"),
                Endpoint("CMIP6", "esgf.ceda.ac.uk", "ceda"),
            ),
        )


def test_a_requirement_spanning_two_projects_is_allowed():
    # Not something we do, but the check is about agreement, not about there being
    # exactly one project: a query naming both is entitled to endpoints for both.
    spanning = TrackedRequirement(
        label="both",
        stem="pattern-effect",
        role_stem="tas",
        query=Query(project=("CMIP6", "CMIP7"), variable=("tas",)),
        endpoints=(
            Endpoint("CMIP7", "search.east.esgf.io", "east"),
            Endpoint("CMIP6", "esgf.ceda.ac.uk", "ceda"),
        ),
    )

    assert len(spanning.endpoints) == 2


def test_only_requirements_asking_to_be_downloaded_say_so():
    watched = TrackedRequirement(
        label="CMIP6",
        stem="pattern-effect",
        role_stem="tas",
        query=Query(project=("CMIP6",), variable=("tas",)),
        endpoints=(Endpoint("CMIP6", "esgf.ceda.ac.uk", "ceda"),),
    )

    # Defaults matter here: a new requirement must not start downloading by
    # accident, and must not take a block of the report it did not ask for.
    assert not watched.download
    assert not watched.detailed


TO_CONTROL = Ancestors(
    until=Query(experiment=("piControl", "esm-piControl")), role="control"
)
"""A lineage shaped like the Gregory question's"""

ABRUPT = Query(
    project=("CMIP7",),
    variable=("tas", "rsdt"),
    experiment=("abrupt-4xCO2",),
    reporting_interval=("mon",),
)
"""A query a lineage can legally hang off: it selects no `piControl` of its own"""


def with_control(endpoint):
    """
    Build the requirement one endpoint asks about the Gregory question

    Parameters
    ----------
    endpoint
        The endpoint to build it for

    Returns
    -------
    :
        The requirement, with a lineage on its leaf
    """
    return endpoint_requirement(
        endpoint,
        ABRUPT,
        name_stem="gregory-toa-mon-abrupt4x",
        role_stem="gregory",
        lineage=TO_CONTROL,
    )


def test_a_lineage_reaches_the_leaf_of_the_built_requirement():
    built = with_control(EAST)

    assert built.tree.lineage == TO_CONTROL
    # The role path the lineage adds, which is how a later check refers to the
    # control it found.
    assert sorted(role_paths(built.tree)) == ["control.gregory-east", "gregory-east"]


def test_the_lineage_role_is_left_alone_and_the_leaf_role_carries_the_tag():
    # Deliberate, and the one place this repository departs from "the tag rides in
    # the role". It does not need to ride in *both*: `requirement_hash` covers the
    # whole tree, the leaf's role inside it already carries the tag, so east and west
    # fingerprint apart regardless -- and roles only have to be unique within one
    # requirement, which has one leaf.
    east = with_control(EAST)

    assert east.tree.lineage.role == "control"
    assert east.tree.role == "gregory-east"


def test_two_endpoints_are_still_two_requirements_with_a_lineage_on():
    # The assertion that lets the simpler lineage role above stand. If this failed,
    # east and west would collide on their fingerprint and esmporium would refuse the
    # second of them -- which `search_endpoint` would report as that endpoint being
    # down, for a reason that has nothing to do with the endpoint.
    assert (
        with_control(EAST).requirement_hash() != with_control(WEST).requirement_hash()
    )


def test_a_lineage_does_not_change_what_goes_out_on_the_wire():
    # A search still searches for `abrupt-4xCO2` only. Climbing to the control is the
    # separate, explicit call `local.parents` makes, and if a lineage ever started
    # widening the query this is where it would show.
    plain = endpoint_requirement(
        EAST, ABRUPT, name_stem="gregory-toa-mon-abrupt4x", role_stem="gregory"
    )

    assert with_control(EAST).tree.query == plain.tree.query


def test_a_tracked_question_hands_its_lineage_to_every_endpoint():
    tracked = TrackedRequirement(
        label="CMIP7 gregory",
        stem="gregory-toa-mon-abrupt4x",
        role_stem="gregory",
        query=ABRUPT,
        download_query=Query(
            project=("CMIP7",),
            variable=("tas", "rsdt"),
            experiment=("abrupt-4xCO2", "piControl"),
            reporting_interval=("mon",),
        ),
        endpoints=(EAST, WEST),
        lineage=TO_CONTROL,
        download=True,
    )

    assert all(
        requirement_for(tracked, endpoint).tree.lineage == TO_CONTROL
        for endpoint in tracked.endpoints
    )


def test_a_question_which_walks_back_but_cannot_download_what_it_finds_is_refused():
    # The trap. A download is planned from a query, so without a `download_query` the
    # controls would be searched for, linked, reported -- and never fetched, with
    # nothing raised and nothing logged. Refused at construction instead.
    with pytest.raises(ValueError, match="no `download_query`"):
        TrackedRequirement(
            label="CMIP7 gregory",
            stem="gregory-toa-mon-abrupt4x",
            role_stem="gregory",
            query=ABRUPT,
            endpoints=(EAST,),
            lineage=TO_CONTROL,
            download=True,
        )


def test_a_question_which_only_watches_its_ancestry_needs_no_download_query():
    # The same question with `download=False` is fine: nothing is being promised, so
    # nothing can go missing.
    tracked = TrackedRequirement(
        label="CMIP7 gregory",
        stem="gregory-toa-mon-abrupt4x",
        role_stem="gregory",
        query=ABRUPT,
        endpoints=(EAST,),
        lineage=TO_CONTROL,
        download=False,
    )

    assert tracked.download_query is None
    assert tracked.download_target is tracked.query


def test_a_download_query_naming_another_project_than_its_endpoints_is_refused():
    # The same check the search query has always had, applied to the second query. A
    # download query for CMIP6 under CMIP7 endpoints would plan from rows those
    # endpoints never wrote and find nothing, for no stated reason.
    with pytest.raises(ValueError, match="downloads"):
        TrackedRequirement(
            label="CMIP7 gregory",
            stem="gregory-toa-mon-abrupt4x",
            role_stem="gregory",
            query=ABRUPT,
            download_query=Query(project=("CMIP6",), variable=("tas",)),
            endpoints=(EAST,),
            lineage=TO_CONTROL,
            download=True,
        )


def test_the_download_target_is_the_download_query_when_there_is_one():
    tracked = TrackedRequirement(
        label="CMIP7 gregory",
        stem="gregory-toa-mon-abrupt4x",
        role_stem="gregory",
        query=ABRUPT,
        download_query=Query(
            project=("CMIP7",),
            variable=("tas", "rsdt"),
            experiment=("abrupt-4xCO2", "piControl"),
        ),
        endpoints=(EAST,),
        lineage=TO_CONTROL,
        download=True,
    )

    assert tuple(tracked.download_target.experiment) == (
        "abrupt-4xCO2",
        "piControl",
    )


def test_report_facets_are_none_rather_than_empty_when_unset():
    # `None` is what the renderers read as "use your default"; `()` would be read as
    # "show no columns at all", which renders a table of bare ids.
    tracked = TrackedRequirement(
        label="CMIP7 tas",
        stem="pattern-effect",
        role_stem="tas",
        query=QUERY,
        endpoints=(EAST,),
    )

    assert tracked.facets is None


def test_a_walk_s_selector_offers_every_endpoint_in_order():
    # Unlike a tracked search, which must be asked of exactly one host, a parent walk
    # takes the failover: an ancestor is a dataset we have never seen and can be
    # published on a host which never answered the search that found its child.
    selector = facade_selector((EAST, WEST))

    assert selector(None, 0).search_api.host == "search.east.esgf.io"
    assert selector(None, 1).search_api.host == "search.west.esgf.io"
    assert selector(None, 2) is None


def test_a_walk_s_selector_refuses_a_host_esmporium_cannot_serve():
    with pytest.raises(ValueError, match=re.escape("search.east.esgf.io")):
        facade_selector((EAST, Endpoint("CMIP5", "search.east.esgf.io", "east")))
