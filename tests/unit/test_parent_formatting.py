"""
How a parent walk reads

The numbers are the easy part. What these tests are really about is the three things
esmporium does *not* call a failure but which still mean an ancestor we will not have,
or one we chose rather than read -- a release picked by a rule of ours, a control which
does not publish one of the four variables, a parent the matcher could not tell from
its neighbour. A walk succeeds with any of them set, so if the report stops mentioning
them nothing else will.
"""

from __future__ import annotations

from esmporium.db import (
    AmbiguousParent,
    AmbiguousParentVersion,
    ParentSimulation,
    ParentVersionChoice,
    ParentVersionDecision,
    ParentWalkResult,
    VariableGap,
)

from local.parent_formatting import format_parent_walks
from local.parents import ParentWalk


def simulation(model="UKESM1-3-LL", experiment="piControl", variant="r1i1p1f1"):
    """
    Build one parent simulation

    Parameters
    ----------
    model
        The model that ran it

    experiment
        The experiment

    variant
        The variant label

    Returns
    -------
    :
        The simulation
    """
    return ParentSimulation(
        project="CMIP7", model=model, experiment=experiment, variant_label=variant
    )


def walk(result=None, *, failure=None, n=24, label="CMIP7 gregory"):
    """
    Build one walk's worth of result

    Parameters
    ----------
    result
        What the walk managed, or `None` for one which raised

    failure
        Why it raised

    n
        How many dataset versions it started from

    label
        Which question it is the walk for

    Returns
    -------
    :
        The walk
    """
    return ParentWalk(label=label, n_started_from=n, result=result, failure=failure)


def test_a_question_with_no_lineage_grows_no_heading_about_ancestry():
    # Empty rather than a sentence. Three of the four questions this job tracks stand
    # alone, and a report for them should not carry a section explaining that they
    # have no ancestors.
    assert format_parent_walks([]) == ""


def test_a_finished_walk_says_what_it_managed():
    rendered = format_parent_walks(
        [
            walk(
                ParentWalkResult(
                    linked=["a"] * 24,
                    terminals=[simulation(), simulation("CanESM6-0-MR")],
                    searched=[simulation()] * 6,
                    hops=1,
                )
            )
        ]
    )

    assert "CMIP7 gregory -- ancestry" in rendered
    assert "Walked back from 24 dataset version(s)" in rendered
    assert "24 link(s) written" in rendered
    assert "1 hop(s)" in rendered
    assert "6 search(es) fired" in rendered
    assert "Chains stopped at (2)" in rendered
    assert "UKESM1-3-LL" in rendered
    assert "CanESM6-0-MR" in rendered


def test_a_release_we_picked_ourselves_is_shown_rather_than_counted():
    # The one place in this report where the answer rests on a rule of ours instead of
    # on something the archive published, so it gets rows rather than a number.
    rendered = format_parent_walks(
        [
            walk(
                ParentWalkResult(
                    linked=["a"],
                    version_decisions=[
                        ParentVersionDecision(
                            child_dataset_version_id=101,
                            parent=simulation(),
                            parent_dataset_id=7,
                            chosen_dataset_version_id=55,
                            choice=ParentVersionChoice.DEFAULTED_TO_NEWEST,
                            passed_over_dataset_version_ids=(53, 54),
                        )
                    ],
                )
            )
        ]
    )

    assert "Parent release decided by us (1)" in rendered
    assert "101" in rendered
    assert "55" in rendered
    assert "no release shared the child's, so the newest was taken" in rendered
    # How many releases were passed over to get there, because "the newest of two" and
    # "the newest of nine" are not equally comfortable.
    assert "passed over" in rendered
    assert "publication date, not a run date" in rendered


def test_a_choice_esmporium_adds_later_is_shown_as_itself_rather_than_crashing():
    # The notes are keyed by the enum's value, so a new member upstream prints its
    # bare value. A `KeyError` here would mean no email at all, which is a much worse
    # way to find out that esmporium grew a fourth way of choosing a release.
    class Invented:
        value = "invented_by_upstream"

    rendered = format_parent_walks(
        [
            walk(
                ParentWalkResult(
                    linked=["a"],
                    version_decisions=[
                        ParentVersionDecision(
                            child_dataset_version_id=1,
                            parent=simulation(),
                            parent_dataset_id=2,
                            chosen_dataset_version_id=3,
                            choice=Invented(),
                            passed_over_dataset_version_ids=(),
                        )
                    ],
                )
            )
        ]
    )

    assert "invented_by_upstream" in rendered


def test_a_control_which_does_not_publish_one_of_the_variables_is_named():
    # Not a failure, and that is the problem: the walk succeeds, the link is written,
    # and the Gregory regression for that model is short of one of its four fluxes.
    rendered = format_parent_walks(
        [
            walk(
                ParentWalkResult(
                    linked=["a"],
                    variable_gaps=[
                        VariableGap(
                            child_dataset_version_id=12,
                            parent=simulation(),
                            variable="rsdt",
                        )
                    ],
                )
            )
        ]
    )

    assert "Ancestor does not publish the variable (1)" in rendered
    assert "rsdt" in rendered


def test_both_kinds_of_ambiguity_are_reported_together():
    # An ambiguous dataset and an ambiguous release of a chosen dataset are different
    # upstream, and the same for us: the walk did not climb through the link, so that
    # ancestor is missing.
    rendered = format_parent_walks(
        [
            walk(
                ParentWalkResult(
                    linked=["a"],
                    ambiguous=[
                        AmbiguousParent(
                            child_dataset_version_id=3,
                            parent=simulation(),
                            candidate_dataset_ids=(8, 9),
                            reason="two datasets matched",
                        )
                    ],
                    ambiguous_versions=[
                        AmbiguousParentVersion(
                            child_dataset_version_id=4,
                            parent=simulation("CanESM6-0-MR"),
                            parent_dataset_id=11,
                            candidate_dataset_version_ids=(21, 22),
                            reason="chooser declined",
                        )
                    ],
                )
            )
        ]
    )

    assert "Could not be chosen, so not climbed through (2)" in rendered
    assert "two datasets matched" in rendered
    assert "chooser declined" in rendered
    assert "dataset" in rendered
    assert "release" in rendered


def test_a_failed_walk_says_so_and_says_that_it_kept_what_it_learned():
    # The second half matters as much as the first. A reader who sees "FAILED" and
    # assumes nothing was written would re-run from scratch; the walk commits once per
    # hop, so the next run carries on from where it stopped.
    rendered = format_parent_walks([walk(failure="east said no")])

    assert "FAILED after starting from 24 dataset version(s): east said no" in rendered
    assert "commits once per hop" in rendered


def test_a_walk_with_nothing_to_walk_says_which_kind_of_nothing_it_was():
    # No links written because the search returned nothing is a different morning from
    # no links written because every chain was already resolved, and both have an
    # empty `linked`.
    rendered = format_parent_walks([walk(ParentWalkResult(), n=0)])

    assert "Nothing to walk" in rendered
    assert "no dataset" in rendered


def test_long_tables_are_folded_away_rather_than_mailed_in_full():
    rendered = format_parent_walks(
        [
            walk(
                ParentWalkResult(
                    linked=["a"],
                    terminals=[simulation(f"model-{each}") for each in range(10)],
                )
            )
        ],
        max_rows=3,
    )

    assert "Chains stopped at (10)" in rendered
    assert "... and 7 more" in rendered


def test_the_block_has_no_trailing_whitespace_and_ends_in_one_newline():
    # The same constraint `format_report` is under, and for the same duller reason:
    # `end-of-file-fixer` adds the final newline to any checked-in copy, so a
    # renderer which omits it makes every regression file fail once it is committed.
    rendered = format_parent_walks(
        [walk(ParentWalkResult(linked=["a"], terminals=[simulation()]))]
    )

    assert rendered.endswith("\n")
    assert not rendered.endswith("\n\n")
    assert all(line == line.rstrip() for line in rendered.splitlines())
