"""
Reading a data node's record the way a person would

Every test here builds a `DataNodeHealth` by hand, which it can because that is a
plain frozen dataclass -- so none of this needs a database, a network or a download,
the same as the rest of this suite.
"""

from __future__ import annotations

from esmporium.datasets import DownloadOutcome
from esmporium.db import DataNodeHealth

from local.node_health import (
    attributable_failures,
    describe_failures,
    format_node_health,
    ok_rate,
)


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
