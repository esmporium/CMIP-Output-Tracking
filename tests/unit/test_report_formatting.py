"""
Invariants of the multi-endpoint report

The regression files beside these are what we read to decide whether the report is
any good. These are for the things a regression diff cannot show: that a failed
endpoint does not read as an empty one, that "they agree" is only ever said when it
is true, and that dropping the dataset listing keeps its count.
"""

from __future__ import annotations

from esmporium.db import DatasetChangeType

from local.summary_formatting import (
    compare_endpoint_summaries,
    format_endpoint_overview,
    format_report,
    format_summary,
    report_subject,
)

ADDED = DatasetChangeType.ADDED
REMOVED = DatasetChangeType.REMOVED


def test_a_failed_endpoint_does_not_read_as_an_empty_one(summary_scenes):
    # The failure mode this exists to stop: `ceda  0  no changes` is a sentence about
    # ESGF, and what happened was a timeout. A reader must not be able to confuse
    # "this endpoint holds nothing" with "we could not ask it".
    rendered = format_endpoint_overview(
        [
            summary_scenes.result("nci", project="CMIP6"),
            summary_scenes.result("ceda", project="CMIP6", failure="read timeout"),
        ],
        title="CMIP6",
    )

    assert "0" not in rendered.split("ceda")[1].split("\n")[0]
    assert "FAILED" in rendered
    assert "read timeout" in rendered
    assert "1 failed" in rendered


def test_an_incomplete_run_is_called_out(summary_scenes):
    # A short count from a truncated run looks exactly like datasets going away.
    rendered = format_endpoint_overview(
        [
            summary_scenes.result(
                "nsc",
                project="CMIP6",
                health=summary_scenes.run(complete=False),
            )
        ],
        title="CMIP6",
    )

    assert "INCOMPLETE" in rendered


def test_a_first_run_is_not_reported_as_a_change(summary_scenes):
    # Everything is an addition on a first run, which is not news about ESGF.
    rendered = format_endpoint_overview(
        [
            summary_scenes.result(
                "east",
                baseline_run_id=None,
                changes=(summary_scenes.change(ADDED),),
            )
        ],
        title="CMIP7",
    )

    assert "first run (1)" in rendered


def test_a_quiet_run_is_not_mistaken_for_a_first_one(summary_scenes):
    # The trap: esmporium reads `baseline_run_id` off the recorded changes, so a run
    # which changed nothing recorded no baseline and reports `None` -- exactly like a
    # first run. Every CMIP6 endpoint read "first run" on its second run until this
    # was pinned.
    quiet = summary_scenes.result("nci", project="CMIP6", baseline_run_id=None)

    rendered = format_endpoint_overview([quiet], title="CMIP6")
    detailed = format_summary(quiet.summary)

    assert "no changes" in rendered
    assert "first run" not in rendered
    assert "this is the first run" not in detailed
    assert "no baseline was recorded" in detailed


def test_a_first_run_which_found_nothing_claims_neither(summary_scenes):
    # The one case `_is_first_run` cannot call: a first run which found nothing has
    # no changes, exactly like a later quiet run. So the report says what it knows --
    # that no baseline was recorded -- rather than inventing a previous run or
    # claiming to be the first.
    detailed = format_summary(
        summary_scenes.summary(baseline_run_id=None, current=()),
    )

    assert "no baseline was recorded" in detailed
    assert "this is the first run" not in detailed
    assert "previous complete run" not in detailed


def test_change_counts_appear_per_endpoint(summary_scenes):
    rendered = format_endpoint_overview(
        [
            summary_scenes.result(
                "nci",
                project="CMIP6",
                changes=(
                    summary_scenes.change(ADDED),
                    summary_scenes.change(REMOVED),
                ),
            )
        ],
        title="CMIP6",
    )

    assert "+1 -1 ~0" in rendered


def test_agreement_is_only_claimed_when_they_agree(summary_scenes):
    shared = (
        summary_scenes.version("ACCESS-ESM1-6", "historical", "r1i1p1f1", 101),
        summary_scenes.version("CESM3", "historical", "r1i1p1f1", 102),
    )

    lines = compare_endpoint_summaries(
        summary_scenes.result("east", current=shared),
        summary_scenes.result("west", current=shared),
    )

    assert any("agree" in line for line in lines)
    assert not any("DIVERGE" in line for line in lines)


def test_divergence_names_which_side_is_missing_what(summary_scenes):
    only_east = summary_scenes.version("UKESM1-3-LL", "historical", "r3i1p1f1", 103)
    shared = summary_scenes.version("CESM3", "historical", "r1i1p1f1", 102)

    lines = compare_endpoint_summaries(
        summary_scenes.result("east", current=(shared, only_east)),
        summary_scenes.result("west", current=(shared,)),
    )
    rendered = "\n".join(lines)

    assert "DIVERGE" in rendered
    assert "only in east (1)" in rendered
    assert "UKESM1-3-LL" in rendered
    # Nothing is only in west, so west gets no empty table of its own.
    assert "only in west" not in rendered


def test_a_silent_endpoint_cannot_be_compared(summary_scenes):
    lines = compare_endpoint_summaries(
        summary_scenes.result("east", current=(summary_scenes.version(),)),
        summary_scenes.result("west", failure="read timeout"),
    )
    rendered = "\n".join(lines)

    assert "cannot compare" in rendered
    assert "'west'" in rendered
    # Saying nothing about agreement is the point: one side is unknown, so the two
    # are neither in sync nor out of it.
    assert "agree" not in rendered
    assert "DIVERGE" not in rendered


def test_dropping_the_dataset_listing_keeps_its_count(summary_scenes):
    everything = tuple(
        summary_scenes.version("CESM3", "historical", f"r{n}i1p1f1", 100 + n)
        for n in range(1, 6)
    )
    summary = summary_scenes.summary(current=everything)

    counted = format_summary(summary, show_current_table=False)
    listed = format_summary(summary, show_current_table=True)

    assert "CURRENT STATE (5 datasets)" in counted
    assert "CURRENT STATE (5 datasets)" in listed
    assert "CESM3" in listed
    assert "CESM3" not in counted
    # The bug this replaced: `max_rows=0` leaves a bare heading row and a
    # `... (+5 more)` behind, which reads like something went wrong.
    assert "more)" not in counted


def test_a_project_not_asked_for_detail_gets_one_row_each(summary_scenes):
    rendered = format_report(
        [
            summary_scenes.result("east", project="CMIP7"),
            summary_scenes.result("west", project="CMIP7"),
            summary_scenes.result("nci", project="CMIP6"),
        ],
        detailed=("CMIP7",),
    )

    # CMIP7 got blocks, so its run health is in there; CMIP6 got a row.
    assert "RUN HEALTH" in rendered
    assert rendered.count("RUN HEALTH") == 2
    assert "CMIP6 (1 endpoints)" in rendered


def test_the_report_ends_in_exactly_one_newline(summary_scenes):
    rendered = format_report(
        [summary_scenes.result("east"), summary_scenes.result("west")],
        detailed=("CMIP7",),
    )

    assert rendered.endswith("\n")
    assert not rendered.endswith("\n\n")


def test_no_line_of_the_report_has_trailing_whitespace(summary_scenes):
    # Our own pre-commit would strip it from a checked-in regression file, and the
    # test comparing the two would then fail for no reason.
    rendered = format_report(
        [
            summary_scenes.result("east", changes=(summary_scenes.change(ADDED),)),
            summary_scenes.result("west", failure="read timeout"),
            summary_scenes.result("nci", project="CMIP6"),
        ],
        detailed=("CMIP7",),
    )

    assert all(line == line.rstrip() for line in rendered.splitlines())


def test_the_subject_says_how_many_endpoints_moved(summary_scenes):
    subject = report_subject(
        [
            summary_scenes.result("east", changes=(summary_scenes.change(ADDED),)),
            summary_scenes.result("west"),
            summary_scenes.result("nci", project="CMIP6"),
        ],
        stem="pattern-effect",
    )

    assert subject == "pattern-effect: CMIP7 1/2 changed, CMIP6 0/1 changed"


def test_the_subject_leads_with_a_failure(summary_scenes):
    # A subject line's job is deciding whether to open the mail.
    subject = report_subject(
        [
            summary_scenes.result("east"),
            summary_scenes.result("west", failure="read timeout"),
        ],
        stem="pattern-effect",
    )

    assert subject.startswith("[1 FAILED]")
