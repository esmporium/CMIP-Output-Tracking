"""
Reading a data node's record the way a person would

Every test here builds a `DataNodeHealth` by hand, which it can because that is a
plain frozen dataclass -- so none of this needs a database, a network or a download,
the same as the rest of this suite.
"""

from __future__ import annotations

import datetime

import pytest
from esmporium.datasets import DownloadOutcome
from esmporium.db import DataNodeHealth

from local.node_health import (
    FetchTime,
    attributable_failures,
    describe_failures,
    format_node_health,
    ok_rate,
    readable_duration,
)

UTC = datetime.timezone.utc


def health(data_node: str = "esgf.nci.org.au", **overrides: object) -> DataNodeHealth:
    """
    Build one node's health, naming only the fields a test is about

    `DataNodeHealth` requires every field, which would put thirteen arguments in front
    of every assertion and bury the one that matters.
    """
    fields: dict[str, object] = {
        "data_node": data_node,
        "n_calls": 0,
        "n_success": 0,
        "n_judged": 0,
        # Passed through so a test can check we are *not* reading it. See
        # `test_we_do_not_quote_esmporiums_own_failure_rate`.
        "failure_rate": 0.0,
        "median_throughput_mb_per_s": None,
        "total_bytes": 0,
        "n_blocked": 0,
        "n_timeout": 0,
        "n_host_fault": 0,
        "n_checksum_failed": 0,
        "n_missing": 0,
        "n_needs_credentials": 0,
    }
    fields.update(overrides)

    return DataNodeHealth(**fields)  # type: ignore[arg-type]


def test_a_success_still_does_not_count_towards_the_judged_total():
    # The load-bearing assumption behind `local.node_health.ok_rate`, pinned here
    # because nothing else pins it and the consequence of it changing is silent.
    #
    # esmporium's `_summarise` builds `judged` from the attempts for which
    # `counts_against_data_node` is true and then divides by it, so while SUCCESS is
    # excluded, `n_judged` holds only the node's failures and `failure_rate` can come
    # out as nothing but 0.0 or 1.0. We therefore read `n_success` and `n_judged`,
    # which are each right, and do the division ourselves.
    #
    # If this fails, esmporium has been fixed: `ok_rate` and `attributable_failures`
    # should then go back to reading `failure_rate` directly, and this test should go.
    assert not DownloadOutcome.SUCCESS.counts_against_data_node


def test_we_do_not_quote_esmporiums_own_failure_rate():
    # Nineteen successes and one timeout. esmporium reports `failure_rate` as 1.0
    # here; a report repeating that would be telling a colleague a working node is
    # dead.
    node = health(n_calls=20, n_success=19, n_judged=1, n_timeout=1, failure_rate=1.0)

    assert ok_rate(node) == 0.95
    assert "95%" in format_node_health({node.data_node: node}, window="all history")


def test_a_node_which_never_failed_reads_as_fine():
    node = health(n_calls=325, n_success=325, total_bytes=21_200_000_000)

    assert ok_rate(node) == 1.0
    assert attributable_failures(node) == 0
    assert describe_failures(node) == []


def test_a_node_we_only_ever_asked_for_things_that_had_gone_has_no_verdict():
    # Every attempt a 404, which says the routes we recorded are stale and says
    # nothing whatsoever about the node. Reporting that as 0% would retire a node for
    # answering us correctly; reporting it as 100% would claim a measurement we have
    # not got.
    node = health(n_calls=12, n_success=0, n_judged=0, n_missing=12)

    assert ok_rate(node) is None
    assert "-" in format_node_health({node.data_node: node}, window="all history")


def test_the_failures_which_are_not_the_nodes_fault_say_so():
    node = health(
        n_calls=10,
        n_success=7,
        n_judged=1,
        n_timeout=1,
        n_missing=1,
        n_needs_credentials=1,
    )

    assert attributable_failures(node) == 1
    # 7 of the 8 attempts that are about the node at all.
    assert ok_rate(node) == 0.875

    phrases = describe_failures(node)
    assert "1 timed out" in phrases
    assert any(
        "404" in phrase and "not the node's fault" in phrase for phrase in phrases
    )
    assert any(
        "401" in phrase and "not the node's fault" in phrase for phrase in phrases
    )


def test_an_outcome_esmporium_does_not_count_separately_is_still_explained():
    # `SHORT_READ`, `SIZE_MISMATCH` and `TRANSPORT_ERROR` have no field of their own
    # on `DataNodeHealth`. Without the remainder, this node would show a dismal `ok%`
    # beside a blank explanation, which reads as a bug in the report.
    node = health(n_calls=10, n_success=4, n_judged=6)

    assert describe_failures(node) == ["6 other (a short read, a bad size, or a reset)"]


def test_the_worst_node_is_at_the_top():
    good = health("esgf.nci.org.au", n_calls=325, n_success=325)
    bad = health("esgf.ceda.ac.uk", n_calls=8, n_success=2, n_judged=6, n_timeout=6)

    rendered = format_node_health(
        {good.data_node: good, bad.data_node: bad}, window="all history"
    )

    assert rendered.index("esgf.ceda.ac.uk") < rendered.index("esgf.nci.org.au")


def test_ties_break_on_how_much_we_asked_of_the_node():
    # On a clean run every row ties at 100%, so the node the download actually leaned
    # on should be the one at the top rather than whichever sorts first.
    busy = health("esgf.nci.org.au", n_calls=325, n_success=325)
    barely = health("aims3.llnl.gov", n_calls=2, n_success=2)

    rendered = format_node_health(
        {busy.data_node: busy, barely.data_node: barely}, window="all history"
    )

    assert rendered.index("esgf.nci.org.au") < rendered.index("aims3.llnl.gov")


def test_an_empty_window_says_which_kind_of_nothing_it_is():
    # An empty table and a database nobody has downloaded against look identical, and
    # on this repository the downloads happen on another machine entirely -- so the
    # likeliest reading of no rows is "wrong database", and it is worth saying.
    rendered = format_node_health({}, window="all history")

    assert "No fetch attempts recorded" in rendered
    assert "not the database" in rendered


def test_the_window_it_was_asked_for_is_in_the_heading():
    # A report gets forwarded. A table of a week's rows under a heading saying
    # `all history` is worse than one with no heading at all.
    node = health(n_calls=1, n_success=1)

    rendered = format_node_health({node.data_node: node}, window="the last 7 days")

    assert "DATA NODE HEALTH (the last 7 days)" in rendered


def test_it_ends_in_one_newline_and_has_no_trailing_whitespace():
    # The same contract `format_download_report` keeps, so this can be dropped into
    # either email later without being reformatted.
    node = health(n_calls=20, n_success=19, n_judged=1, n_timeout=1)

    rendered = format_node_health({node.data_node: node}, window="all history")

    assert rendered.endswith("\n")
    assert not rendered.endswith("\n\n")
    assert all(line == line.rstrip() for line in rendered.splitlines())


def took(
    seconds: float, *, first: datetime.datetime | None = None, days: float = 0.0
) -> FetchTime:
    """
    Build a run's timing

    `days` spreads the first and last attempt apart, which is how a window covering
    several nightly runs differs from one covering a single bulk run.
    """
    start = first if first is not None else datetime.datetime(2026, 10, 30, 21, 0)

    return FetchTime(
        seconds=seconds,
        first=start,
        last=start + datetime.timedelta(days=days, seconds=seconds),
    )


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, "0.0s"),
        (0.42, "0.4s"),
        # Below ten seconds keeps a decimal: a single attempt against a nearby node
        # is over in a fraction of a second, and `0s` reads as "no time at all".
        (9.9, "9.9s"),
        (18, "18s"),
        (60, "1m"),
        (252, "4m 12s"),
        # Never a unit that is zero, and never three of them.
        (10800, "3h"),
        (11520, "3h 12m"),
        (183600, "2d 3h"),
        (172800, "2d"),
    ],
)
def test_durations_are_rendered_the_way_a_person_would_say_them(seconds, expected):
    assert readable_duration(seconds) == expected


def test_the_total_time_is_reported_once_at_the_foot():
    node = health(n_calls=300, n_success=300, total_bytes=19_500_000_000)

    rendered = format_node_health(
        {node.data_node: node}, took(13_800), window="all history"
    )

    assert "3h 50m spent fetching, summed over every attempt" in rendered


def test_there_is_no_per_node_time_column():
    # How long one node took is only interesting divided by what it served, which is
    # what `MB/s` already is. A second time column would be the same fact twice.
    node = health(n_calls=300, n_success=300, total_bytes=19_500_000_000)

    rendered = format_node_health(
        {node.data_node: node}, took(13_800), window="all history"
    )

    header = rendered.splitlines()[1]
    assert "MB/s" in header
    assert "time" not in header


def test_without_a_timing_nothing_is_said_about_time():
    # A caller holding only a health mapping should get no timing line, rather than a
    # figure it never measured.
    node = health(n_calls=1, n_success=1)

    rendered = format_node_health({node.data_node: node}, window="all history")

    assert "spent fetching" not in rendered
    assert "first attempt" not in rendered


def test_a_single_run_does_not_bother_reporting_its_span():
    # Over one run the span and the summed time are within a few per cent of each
    # other, so saying both would be saying it twice.
    node = health(n_calls=300, n_success=300)

    rendered = format_node_health(
        {node.data_node: node}, took(13_800), window="all history"
    )

    assert "spent fetching" in rendered
    assert "first attempt" not in rendered


def test_a_window_of_several_nights_says_the_span_is_mostly_idle():
    # The number that would otherwise get quoted as "the download took three days".
    # Four hours of fetching spread over three nights: both figures are true and only
    # one of them is the download time.
    node = health(n_calls=309, n_success=309)

    rendered = format_node_health(
        {node.data_node: node}, took(13_980, days=3), window="all history"
    )

    assert "3h 53m spent fetching" in rendered
    assert "first attempt 2026-10-30 21:00 UTC" in rendered
    assert "includes any time between runs" in rendered


def test_the_elapsed_span_is_not_the_time_spent_fetching():
    # The distinction the report is built around, pinned on the dataclass itself.
    spread = took(13_980, days=3)

    assert spread.seconds == 13_980
    assert spread.elapsed > datetime.timedelta(days=3)
