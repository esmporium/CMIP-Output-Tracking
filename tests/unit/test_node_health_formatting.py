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
    Transfer,
    attributable_failures,
    describe_failures,
    fetch_time,
    format_node_health,
    measure_parallelism,
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


# --- how much was in flight at once -------------------------------------------------

RUN_START = datetime.datetime(2026, 11, 3, 21, 0)
"""When the fixtures below start their first transfer. Naive UTC, as the rows are."""


def transfer(
    at: float,
    seconds: float,
    *,
    node: str = "esgf.nci.org.au",
    n_bytes: int = 8_000_000,
) -> Transfer:
    """
    Build one transfer, placed by when it *started*

    `at` is seconds after `RUN_START`, which is how a test describes an overlap. The
    dataclass itself is keyed on when a transfer finished, because that is the one
    timestamp esmporium records, so this does the subtraction a test should not have
    to think about.
    """
    return Transfer(
        data_node=node,
        finished=RUN_START + datetime.timedelta(seconds=at + seconds),
        seconds=seconds,
        n_bytes=n_bytes,
    )


def test_transfers_back_out_when_they_started_from_when_they_finished():
    # esmporium records one timestamp and one duration, so the interval's near end is
    # always derived. Everything else here is a sweep over those intervals.
    one = transfer(10, 5)

    assert one.started == RUN_START + datetime.timedelta(seconds=10)
    assert one.finished == RUN_START + datetime.timedelta(seconds=15)


def test_transfers_that_never_overlap_report_one_at_a_time():
    # The sequential case, which is what every run before 2026-11-03 looked like.
    parallelism = measure_parallelism(
        [transfer(0, 10), transfer(10, 10), transfer(20, 10)]
    )

    assert parallelism.overall.peak == 1
    assert parallelism.overall.mean == pytest.approx(1.0)
    # And no speedup to claim, which is the honest reading of a serial run.
    assert parallelism.overall.active_seconds == pytest.approx(30)
    assert parallelism.overall.worker_seconds == pytest.approx(30)


def test_a_handoff_is_not_an_overlap():
    # The bug this test exists for. A worker pool hands its slot straight on, so with
    # files of equal size the ties are *exact*: one transfer ends at the instant the
    # next begins. Counting that as two in flight reported a peak of 11 against a
    # pool of 8 -- a number the run could not physically have produced.
    parallelism = measure_parallelism([transfer(0, 10), transfer(10, 10)])

    assert parallelism.overall.peak == 1


def test_fully_overlapping_transfers_report_all_of_them():
    parallelism = measure_parallelism([transfer(0, 10) for _ in range(8)])

    assert parallelism.overall.peak == 8
    assert parallelism.overall.mean == pytest.approx(8.0)


def test_the_average_is_the_speedup_over_doing_them_one_at_a_time():
    # Four transfers of ten seconds each, two at a time: twenty seconds of wall clock
    # for forty seconds of transfer, so twice as fast as a serial run would have been.
    parallelism = measure_parallelism(
        [transfer(0, 10), transfer(0, 10), transfer(10, 10), transfer(10, 10)]
    )

    assert parallelism.overall.active_seconds == pytest.approx(20)
    assert parallelism.overall.worker_seconds == pytest.approx(40)
    assert parallelism.overall.mean == pytest.approx(2.0)


def test_idle_time_is_left_out_of_the_average():
    # The whole reason the union is swept rather than the first-to-last span taken.
    # Two transfers a week apart are not "0.00003 transfers in flight on average";
    # they are one at a time, twice, with a week of nothing in between.
    a_week = 7 * 24 * 60 * 60
    parallelism = measure_parallelism([transfer(0, 10), transfer(a_week, 10)])

    assert parallelism.overall.mean == pytest.approx(1.0)
    assert parallelism.overall.active_seconds == pytest.approx(20)
    # The span is still available, and still enormous, for the line that reports it.
    assert parallelism.overall.span_seconds == pytest.approx(a_week + 10)


def test_each_node_is_measured_on_its_own():
    # What says whether one archive was leant on harder than we meant. Eight at once
    # overall, four against each of two nodes, is a run behaving exactly as configured.
    parallelism = measure_parallelism(
        [transfer(0, 10, node="a") for _ in range(4)]
        + [transfer(0, 10, node="b") for _ in range(4)]
    )

    assert parallelism.overall.peak == 8
    assert parallelism.by_node["a"].peak == 4
    assert parallelism.by_node["b"].peak == 4


def test_the_aggregate_rate_is_in_the_same_megabytes_as_the_column_beside_it():
    # esmporium divides by 1_000_000 for `median_throughput_mb_per_s`, so this has to
    # as well: the two numbers are printed to be compared, and `readable_bytes` in
    # the same report steps by 1024.
    parallelism = measure_parallelism([transfer(0, 10, n_bytes=50_000_000)])

    assert parallelism.overall.aggregate_mb_per_s == pytest.approx(5.0)


def test_nothing_fetched_measures_nothing():
    # `None` rather than a zeroed record, for the same reason `fetch_time` does it:
    # "nothing was downloaded" must not print as "it took no time and ran perfectly".
    assert measure_parallelism([]) is None
    assert fetch_time([]) is None


def test_instantaneous_attempts_do_not_divide_by_zero():
    # Reachable with a cached or refused attempt recorded as taking no time at all.
    # There is then no interval to overlap, so there is nothing to report rather than
    # an infinite rate.
    parallelism = measure_parallelism([transfer(0, 0.0), transfer(0, 0.0)])

    assert parallelism.overall.mean is None
    assert parallelism.overall.aggregate_mb_per_s is None


def test_the_timing_total_agrees_with_the_transfers_it_came_from():
    transfers = [transfer(0, 10), transfer(5, 20)]

    took = fetch_time(transfers)

    assert took.seconds == pytest.approx(30)
    assert took.first == RUN_START
    assert took.last == RUN_START + datetime.timedelta(seconds=25)


# --- and how it reads ---------------------------------------------------------------


def test_the_parallel_block_says_what_the_mb_per_s_column_cannot():
    # The point of the whole block. Per-transfer throughput does not improve with
    # parallelism -- contention makes it slightly worse -- so without this a run that
    # went four times faster reads as one that went marginally slower.
    node = health(n_calls=8, n_success=8, median_throughput_mb_per_s=8.0)
    transfers = [transfer(0, 10) for _ in range(4)] + [
        transfer(10, 10) for _ in range(4)
    ]

    rendered = format_node_health(
        {node.data_node: node},
        fetch_time(transfers),
        window="all history",
        parallelism=measure_parallelism(transfers),
    )

    assert "IN PARALLEL" in rendered
    assert "4 transfers in flight at peak" in rendered
    assert "4.0x longer" in rendered
    # Stated as a ceiling, not as a measurement, because a lone transfer would have
    # had the uplink to itself.
    assert "upper bound" in rendered


def test_a_parallel_run_reports_its_wall_clock_and_not_only_the_sum():
    # The regression this guards. The span line used to be printed only when the span
    # was *longer* than the summed time, which is the multi-night case. A parallel run
    # is the opposite -- the sum is several times the wall clock -- so the one figure
    # anybody actually waited through could never be printed.
    node = health(n_calls=60, n_success=60)
    transfers = [
        transfer(offset, 120) for offset in range(0, 600, 120) for _ in range(8)
    ]

    rendered = format_node_health(
        {node.data_node: node},
        fetch_time(transfers),
        window="all history",
        parallelism=measure_parallelism(transfers),
    )

    assert "spent fetching" in rendered
    assert "first attempt" in rendered
    assert "because transfers overlapped" in rendered
    assert "includes any time between runs" not in rendered


def test_the_per_node_column_only_appears_once_it_has_been_measured():
    # A column of dashes would read as "we never asked this node for more than one
    # thing at once", which is a claim rather than an absence.
    node = health(n_calls=1, n_success=1)

    without = format_node_health({node.data_node: node}, window="all history")
    transfers = [transfer(0, 10)]
    with_it = format_node_health(
        {node.data_node: node},
        fetch_time(transfers),
        window="all history",
        parallelism=measure_parallelism(transfers),
    )

    assert "at once" not in without.splitlines()[1]
    assert "at once" in with_it.splitlines()[1]
    assert "IN PARALLEL" not in without


def test_the_busiest_node_is_named_rather_than_counted():
    # "4 at once against a single node" is only actionable with the host attached,
    # because the question it answers is which archive to ease off.
    node = health("crd-esgf-drc.ec.gc.ca", n_calls=4, n_success=4)
    transfers = [transfer(0, 10, node="crd-esgf-drc.ec.gc.ca") for _ in range(4)]

    rendered = format_node_health(
        {node.data_node: node},
        fetch_time(transfers),
        window="all history",
        parallelism=measure_parallelism(transfers),
    )

    assert "against a single node (crd-esgf-drc.ec.gc.ca)" in rendered


def test_a_node_we_have_health_for_but_no_transfers_reads_as_none_at_once():
    # Cannot happen -- both come from the same rows over the same window -- but a
    # read-only report should print zero rather than raise.
    seen = health("a", n_calls=1, n_success=1)
    unseen = health("b", n_calls=1, n_success=1)
    transfers = [transfer(0, 10, node="a")]

    rendered = format_node_health(
        {seen.data_node: seen, unseen.data_node: unseen},
        fetch_time(transfers),
        window="all history",
        parallelism=measure_parallelism(transfers),
    )

    # Both nodes still get a row, and the one we have no intervals for reads as zero
    # in flight rather than taking the report down with it.
    rows = {
        line.split()[0]: line.split()
        for line in rendered.splitlines()
        if line.startswith("  a ") or line.startswith("  b ")
    }
    assert rows["a"][-1] == "1"
    assert rows["b"][-1] == "0"


def test_the_parallel_report_still_ends_cleanly():
    node = health(n_calls=1, n_success=1)
    transfers = [transfer(0, 10)]

    rendered = format_node_health(
        {node.data_node: node},
        fetch_time(transfers),
        window="all history",
        parallelism=measure_parallelism(transfers),
    )

    assert rendered.endswith("\n")
    assert not rendered.endswith("\n\n")
    assert all(line == line.rstrip() for line in rendered.splitlines())
