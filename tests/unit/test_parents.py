"""
That a parent walk is contained, and told the right thing

The walk itself is esmporium's and is tested there. These are about the two things
this repository adds: that a chain nobody can climb does not cost us the morning's
download, and that the walk is told where to stop by reading the requirement rather
than by being handed a second copy of the answer.
"""

from __future__ import annotations

import pytest
from esmporium.db import ParentWalkResult
from esmporium.query import Query
from esmporium.requirements import Ancestors, leaf, requirement

from local.parents import (
    UnwalkableUntilError,
    until_experiments,
    walk_parents,
)

EAST_SAID_NO = "east said no"
"""What a pretend walk fails with, named so `raise` carries no message of its own"""

ABRUPT = Query(
    project=("CMIP7",),
    experiment=("abrupt-4xCO2",),
    variable=("tas", "rsdt"),
    reporting_interval=("mon",),
)
"""A query shaped like the Gregory question's, trimmed to what these tests read"""


def gregory(until: Query | None = None, *, role: str = "control"):
    """
    Build a requirement shaped like the Gregory question

    Parameters
    ----------
    until
        Where the lineage stops. `None` gives both spellings of the control, and
        omitting the lineage entirely is `with_no_lineage`.

    role
        What the lineage files its last member under

    Returns
    -------
    :
        The requirement
    """
    stop = Query(experiment=("piControl", "esm-piControl")) if until is None else until

    return requirement(
        name="gregory-cmip7-east",
        tree=leaf(ABRUPT, "gregory-east").with_lineage(
            Ancestors(until=stop, role=role)
        ),
        group_by=("model", "variant_label"),
    )


def with_no_lineage():
    """
    Build a requirement which asks about nothing but itself

    Returns
    -------
    :
        The requirement
    """
    return requirement(
        name="pattern-effect-cmip7-east",
        tree=leaf(ABRUPT, "tas-east"),
        group_by=("model", "variant_label"),
    )


def test_where_to_stop_is_read_off_the_requirement():
    # The point of reading it rather than passing it: `until` is declared once, on the
    # relation, and is part of the stored fingerprint. A walk told something else
    # would resolve links the requirement does not ask for and nothing would say so.
    assert until_experiments(gregory()) == ("piControl", "esm-piControl")


def test_a_question_which_asks_about_nothing_but_itself_has_nowhere_to_stop():
    # `()` rather than a raise, which is what lets the driver put every question
    # through the same code path and simply not walk the ones with no lineage.
    assert until_experiments(with_no_lineage()) == ()


def test_the_order_is_the_tree_s_rather_than_a_set_s():
    # Two runs of the same configuration have to tell the walk the same thing, or an
    # `until` reordered by set iteration would make two identical configurations look
    # like two different ones in a log.
    reversed_control = Query(experiment=("esm-piControl", "piControl"))

    assert until_experiments(gregory(reversed_control)) == (
        "esm-piControl",
        "piControl",
    )


def test_an_until_a_walk_cannot_be_told_about_is_refused():
    # `find_parents` is told where to stop by experiment alone. An `until` which also
    # pinned the variant would still be honoured when the requirement was *solved*,
    # so the walk and the requirement would disagree about where the chain ends --
    # and the walk would write links to a control the requirement does not accept.
    with pytest.raises(UnwalkableUntilError) as caught:
        until_experiments(
            gregory(Query(experiment=("piControl",), variant_label=("r1i1p1f1",)))
        )

    assert caught.value.facets == ("variant_label",)
    assert "experiment alone" in str(caught.value)


def test_naming_the_project_to_stop_at_is_not_refused():
    # A walk refuses to climb across projects anyway, so `project` is the one extra
    # facet that adds nothing a walk could fail to honour. Worth its own test because
    # a query written in a project's own parameter names sets it for you, which would
    # otherwise make the check above fire on a perfectly ordinary `until`.
    assert until_experiments(
        gregory(Query(project=("CMIP7",), experiment=("piControl",)))
    ) == ("piControl",)


def test_a_walk_which_raises_is_reported_rather_than_propagated():
    # The containment this module exists for. This runs between the morning's search
    # and the morning's download: a chain nobody can climb is a thing to report, not
    # a reason to fetch no data.
    def explode(session, ids, **kwargs):
        raise RuntimeError(EAST_SAID_NO)

    walk = walk_parents(
        engine=None,
        label="CMIP7 gregory",
        dataset_version_ids=(1, 2, 3),
        until_experiment=("piControl",),
        selector=None,
        find=explode,
    )

    assert walk.failure == EAST_SAID_NO
    assert walk.result is None
    assert not walk.walked
    assert walk.n_started_from == 3
    # Zero because that is what we were *told*, not because nothing was written: the
    # walk commits once per hop, so the links it managed are already in the database.
    assert walk.n_linked == 0


def test_a_walk_which_finishes_carries_what_it_managed():
    captured = {}

    def succeed(session, ids, **kwargs):
        captured["ids"] = ids
        captured.update(kwargs)

        return ParentWalkResult(linked=["one", "two"], hops=1)

    walk = walk_parents(
        engine=None,
        label="CMIP7 gregory",
        dataset_version_ids=[7, 9],
        until_experiment=("piControl", "esm-piControl"),
        selector="the-selector",
        find=succeed,
    )

    assert walk.failure is None
    assert walk.walked
    assert walk.n_linked == 2
    assert walk.n_started_from == 2


def test_the_versions_to_walk_from_are_always_passed():
    # esmporium reads an omitted collection as "every unresolved assertion in the
    # database", and since this esmporium every CMIP7 search records parent facts on
    # ingest. So a `None` here would quietly start climbing the pattern-effect
    # questions' ancestry too, searching for and ingesting controls nobody asked
    # about -- which is a bandwidth bill, not an error.
    captured = {}

    def capture(session, ids, **kwargs):
        captured["ids"] = ids

        return ParentWalkResult()

    walk_parents(
        engine=None,
        label="CMIP7 gregory",
        dataset_version_ids=(4, 5),
        until_experiment=("piControl",),
        selector=None,
        find=capture,
    )

    assert captured["ids"] is not None
    assert tuple(captured["ids"]) == (4, 5)


def test_a_search_which_found_nothing_walks_nothing_rather_than_everything():
    # The empty case of the above, and the dangerous one: an endpoint which answered
    # with no datasets gives an empty set of ids, and an empty collection must stay
    # an empty collection rather than being turned into "no filter".
    captured = {}

    def capture(session, ids, **kwargs):
        captured["ids"] = ids

        return ParentWalkResult()

    walk = walk_parents(
        engine=None,
        label="CMIP7 gregory",
        dataset_version_ids=(),
        until_experiment=("piControl",),
        selector=None,
        find=capture,
    )

    assert captured["ids"] == ()
    assert captured["ids"] is not None
    assert walk.n_started_from == 0
