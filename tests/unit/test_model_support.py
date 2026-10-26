"""
Tests of which models each endpoint can serve

The point of counting variants rather than ticking a box is that endpoints which both
"have" a model disagree about how much of it they have, so most of these are about a
cell being a count and that count being right. The rest are about the two ways a
model can fail to be there -- nobody published it here, or everything published here
is retracted -- not being confused with each other.
"""

from __future__ import annotations

from local.models import model_support
from local.summary_formatting import format_model_support


def support(results):
    return format_model_support(model_support(results), title="CMIP6")


def header_row(rendered):
    """
    Find the table's header row

    By its first cell, not by looking for "model" anywhere: the block's heading says
    "models with at least one satisfying variant" and matches a naive search.
    """
    return next(line for line in rendered.splitlines() if line.split()[:1] == ["model"])


def test_a_cell_counts_variants_not_datasets(summary_scenes):
    # One variant published at two versions is one variant. Counting datasets here
    # would make a model look better served than it is.
    rendered = support(
        [
            summary_scenes.result(
                "nsc",
                project="CMIP6",
                current=(
                    summary_scenes.version(
                        "CanESM5",
                        "historical",
                        "r1i1p1f1",
                        101,
                        version_string="20190914",
                    ),
                    summary_scenes.version(
                        "CanESM5",
                        "historical",
                        "r1i1p1f1",
                        102,
                        version_string="20200724",
                    ),
                    summary_scenes.version("CanESM5", "historical", "r2i1p1f1", 103),
                ),
            )
        ]
    )

    assert (
        model_support(
            [
                summary_scenes.result(
                    "nsc",
                    project="CMIP6",
                    current=(
                        summary_scenes.version(
                            "CanESM5", "historical", "r1i1p1f1", 101
                        ),
                        summary_scenes.version(
                            "CanESM5", "historical", "r1i1p1f1", 102
                        ),
                        summary_scenes.version(
                            "CanESM5", "historical", "r2i1p1f1", 103
                        ),
                    ),
                )
            ]
        ).variants["CanESM5"]["nsc"]
        == 2
    )
    assert "CanESM5" in rendered


def test_endpoints_can_disagree_about_how_much_of_a_model_they_have(summary_scenes):
    # The finding this table exists for: both endpoints "have" ACCESS-CM2, and one
    # serves three times as much of it.
    built = model_support(
        [
            summary_scenes.result(
                "nsc",
                project="CMIP6",
                current=tuple(
                    summary_scenes.version(
                        "ACCESS-CM2", "historical", f"r{n}i1p1f1", 100 + n
                    )
                    for n in range(1, 4)
                ),
            ),
            summary_scenes.result(
                "ceda",
                project="CMIP6",
                current=(
                    summary_scenes.version("ACCESS-CM2", "historical", "r1i1p1f1", 101),
                ),
            ),
        ]
    )

    assert built.variants["ACCESS-CM2"] == {"nsc": 3, "ceda": 1}
    assert built.satisfied_at("nsc") == 1
    assert built.satisfied_at("ceda") == 1


def test_a_retracted_variant_does_not_satisfy(summary_scenes):
    built = model_support(
        [
            summary_scenes.result(
                "nsc",
                project="CMIP6",
                current=(
                    summary_scenes.version("CanESM5", "historical", "r1i1p1f1", 101),
                    summary_scenes.version(
                        "CanESM5", "historical", "r2i1p1f1", 102, retracted=True
                    ),
                ),
            )
        ]
    )

    assert built.variants["CanESM5"]["nsc"] == 1


def test_a_model_retracted_everywhere_is_not_listed(summary_scenes):
    rendered = support(
        [
            summary_scenes.result(
                "nsc",
                project="CMIP6",
                current=(
                    summary_scenes.version("CanESM5", "historical", "r1i1p1f1", 101),
                    summary_scenes.version(
                        "MIROC6", "historical", "r1i1p1f1", 102, retracted=True
                    ),
                ),
            )
        ]
    )

    assert "CanESM5" in rendered
    assert "MIROC6" not in rendered
    assert "1 models have at least one variant" in rendered


def test_a_silent_endpoint_gets_no_column(summary_scenes):
    # A column of dashes would read as "ceda has none of these models", and what
    # happened is that we could not ask it.
    rendered = support(
        [
            summary_scenes.result(
                "nsc",
                project="CMIP6",
                current=(
                    summary_scenes.version("CanESM5", "historical", "r1i1p1f1", 101),
                ),
            ),
            summary_scenes.result("ceda", project="CMIP6", failure="read timeout"),
        ]
    )
    header = header_row(rendered)

    assert header.split() == ["model", "nsc"]
    assert "ceda is not shown: it did not answer" in rendered


def test_the_totals_row_counts_models_per_endpoint(summary_scenes):
    rendered = support(
        [
            summary_scenes.result(
                "nsc",
                project="CMIP6",
                current=(
                    summary_scenes.version("CanESM5", "historical", "r1i1p1f1", 101),
                    summary_scenes.version("MIROC6", "historical", "r1i1p1f1", 102),
                ),
            ),
            summary_scenes.result(
                "ceda",
                project="CMIP6",
                current=(
                    summary_scenes.version("CanESM5", "historical", "r1i1p1f1", 101),
                ),
            ),
        ]
    )
    totals = next(line for line in rendered.splitlines() if "models satisfied" in line)

    assert totals.split()[2:] == ["2", "1"]
    # The sentence counts the table's rows, which is models satisfied *somewhere*.
    assert "2 models have at least one variant which satisfies the requirement" in (
        rendered
    )


def test_models_are_sorted_so_a_family_stays_together(summary_scenes):
    # Plain ASCII ordering puts CESM2 before CanESM5, because upper case sorts first.
    built = model_support(
        [
            summary_scenes.result(
                "nsc",
                project="CMIP6",
                current=(
                    summary_scenes.version("CESM2", "historical", "r1i1p1f1", 101),
                    summary_scenes.version("CanESM5-1", "historical", "r1i1p1f1", 102),
                    summary_scenes.version("CanESM5", "historical", "r1i1p1f1", 103),
                ),
            )
        ]
    )

    assert built.models == ("CanESM5", "CanESM5-1", "CESM2")


def test_every_model_is_listed(summary_scenes):
    # Never folded behind a row cap: the list is the point of the table.
    many = tuple(
        summary_scenes.version("MIROC6", "historical", "r1i1p1f1", 100 + n)
        for n in range(1)
    ) + tuple(
        summary_scenes.version(f"MODEL-{n:02d}", "historical", "r1i1p1f1", 200 + n)
        for n in range(60)
    )
    rendered = support([summary_scenes.result("nsc", project="CMIP6", current=many)])

    assert "more)" not in rendered
    assert "MODEL-59" in rendered
    assert "61 models have at least one variant" in rendered


def test_nothing_to_list_is_said_rather_than_drawn(summary_scenes):
    silent = support([summary_scenes.result("nsc", project="CMIP6", failure="down")])
    empty = support([summary_scenes.result("nsc", project="CMIP6", current=())])

    assert "no endpoint answered" in silent
    assert "no endpoint returned a usable dataset" in empty
