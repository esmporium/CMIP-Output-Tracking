"""
Tests of the invariants the rendered summary has to keep

Deliberately few. The regression files beside these cover what the output *says*,
with more context than an assertion about a substring ever could, and duplicating
them here would only mean editing two files every time the layout changes -- a tax
on exactly the churn this repository exists to make cheap.

What is left is the handful of properties a regression diff cannot tell you about.
A regression file says the text changed; it does not say whether the change was
wrong. These say that.
"""

from __future__ import annotations

import pytest
from esmporium.datasets import DATASET_FACET_COLUMNS
from esmporium.db import DatasetChangeType

from local.summary_formatting import (
    DEFAULT_FACETS,
    MisalignedRowError,
    UnknownFacetError,
    check_facets,
    format_summary,
    render_table,
)

ADDED = DatasetChangeType.ADDED
MODIFIED = DatasetChangeType.MODIFIED

RETRACTED_MOVED = {"retracted": {"old": False, "new": True}}


def test_render_leaves_no_trailing_whitespace(summary_scenes):
    # Our pre-commit strips trailing whitespace, so a renderer which emitted it
    # would make every checked-in regression file fail the moment it was committed.
    # It also keeps the email from arriving with ragged lines.
    rendered = format_summary(
        summary_scenes.summary(
            changes=(summary_scenes.change(ADDED),),
            current=(summary_scenes.version(),),
        )
    )

    assert rendered.endswith("\n")
    assert not rendered.endswith("\n\n")
    assert [line for line in rendered.splitlines() if line != line.rstrip()] == []


def test_the_id_column_tells_rows_apart_whatever_facets_are_shown(summary_scenes):
    # Two datasets can agree on every facet we model and still be different rows, so
    # narrowing the facets must never make them indistinguishable. This is the
    # guarantee which makes the `facets` argument safe to play with.
    same_facets = (
        summary_scenes.version(dataset_version_id=101),
        summary_scenes.version(dataset_version_id=102),
    )

    rendered = format_summary(
        summary_scenes.summary(current=same_facets), facets=("model",)
    )

    body = [line for line in rendered.splitlines() if "ACCESS-ESM1-6" in line]
    assert len(body) == 2
    assert body[0] != body[1]
    assert [line.split()[0] for line in body] == ["101", "102"]


def test_what_moved_cannot_widen_the_table(summary_scenes):
    # `what moved` is the one free-text field here, and as a column of its own it set
    # the whole table's width. Under its row instead, however wordy it is, it cannot.
    rendered = format_summary(
        summary_scenes.summary(
            changes=(summary_scenes.change(MODIFIED, detail=RETRACTED_MOVED),)
        )
    )
    lines = rendered.splitlines()
    header = next(line for line in lines if line.strip().startswith("id"))
    assert "what moved" not in header

    # On its own line, not trailing the row it belongs to
    moved = next(line for line in lines if "retracted: no -> yes" in line)
    assert moved.strip() == "retracted: no -> yes"

    wordy = format_summary(
        summary_scenes.summary(
            changes=(
                summary_scenes.change(
                    MODIFIED,
                    detail={
                        "data_nodes": {
                            "old": ["a.very.long.data.node.example.org"],
                            "new": ["a.second.very.long.data.node.example.org"],
                        }
                    },
                ),
            )
        )
    )
    wordy_header = next(
        line for line in wordy.splitlines() if line.strip().startswith("id")
    )
    assert len(wordy_header) == len(header)


def test_render_table_refuses_a_row_which_does_not_match_its_headings():
    # The failure this guards against is silent: headings and cells built from two
    # separate lists can be ordered differently, putting a value under the wrong name.
    with pytest.raises(MisalignedRowError) as excinfo:
        render_table(["model", "version"], [["CESM3"], ["CESM3", "20261018", "extra"]])

    assert excinfo.value.n_headers == 2
    assert excinfo.value.row_lengths == (1, 3)
    assert "A table with 2 headings was given 2 rows which do not match it" in str(
        excinfo.value
    )


def test_render_table_takes_a_table_which_does_match():
    assert render_table(["a"], [["1"]]) == "  a\n  1"


def test_default_facets_are_dataset_facets():
    # `DEFAULT_FACETS` is a list we expect to edit while working out what the email
    # should show, and a typo in it would name something unrenderable.
    assert set(DEFAULT_FACETS) <= set(DATASET_FACET_COLUMNS)


def test_check_facets_refuses_a_facet_no_dataset_records(summary_scenes):
    with pytest.raises(UnknownFacetError) as excinfo:
        # A real facet, but a document-only one: CORDEX's driving model is not a
        # Dataset column, so a summary cannot show it.
        check_facets(("model", "cordex-cmip6:driving_source_id"))

    assert excinfo.value.unknown == ("cordex-cmip6:driving_source_id",)
    assert "Cannot show 'cordex-cmip6:driving_source_id'" in str(excinfo.value)


def test_the_scenes_fill_in_every_facet_column(summary_scenes):
    # The cross-repo tripwire. When esmporium adds a facet column and our scene
    # builder is not updated with it, the tables render it as a gap and the
    # regression files quietly lose a column. This repository is now the only one
    # which would notice, so this is the test which tells us to update
    # `REALISTIC_FACETS` after pointing at a new esmporium commit.
    assert set(summary_scenes.version().facets) == set(DATASET_FACET_COLUMNS)
    assert "" not in summary_scenes.version().facets.values()
