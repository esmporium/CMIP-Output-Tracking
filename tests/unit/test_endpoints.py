"""
Tests of turning one question into one tracked search per endpoint
"""

from __future__ import annotations

import dataclasses
import re

import pytest
from esmporium.query import Query

from local.endpoints import (
    Endpoint,
    EndpointResult,
    endpoint_requirement,
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
