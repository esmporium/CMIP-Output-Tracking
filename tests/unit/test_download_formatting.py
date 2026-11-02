"""
The invariants of the download report which a regression diff cannot tell you about

Deliberately few, the same way `test_summary_formatting.py` is: what the report
*says* is covered by the checked-in copies under
`tests/regression/test_download_rendering/`, and duplicating that here would be a
second copy of the layout to keep in step. These are the things a diff would show
you without telling you they were wrong.
"""

import pytest
from esmporium.datasets import DownloadOutcome

from local.download_formatting import (
    download_subject,
    format_download_report,
    readable_bytes,
)
from local.downloads import IDENTITY_FACETS


def _report(scenes, records, **totals):
    """
    Render a report of `records` as one group

    One group, because what these tests are about is the tables inside a group. That
    there is a block per tracked requirement is covered by the checked-in copies.
    """
    return format_download_report(
        [scenes.group(records, **totals)],
        scenes.held(done=len(records)),
        root=scenes.root,
        facets=IDENTITY_FACETS,
    )


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1.0 KB"),
        (52593, "51.4 KB"),
        (65345526, "62.3 MB"),
        (22716867033, "21.2 GB"),
        (5 * 1024**4, "5.0 TB"),
        # Bigger than the ladder goes: stays in terabytes rather than falling off
        # the end, because a wrong unit is worse than a large number.
        (5000 * 1024**4, "5000.0 TB"),
    ],
)
def test_bytes_are_rendered_the_way_a_person_would_say_them(n, expected):
    assert readable_bytes(n) == expected


def test_every_line_has_no_trailing_whitespace(download_scenes):
    # The report is sent as an email, and a trailing space on a table row is
    # invisible in review and visible in a diff forever.
    rendered = _report(
        download_scenes,
        [download_scenes.fetched(), download_scenes.failed()],
        n_files=2,
        n_fetched=1,
        n_failed=1,
    )

    assert all(line == line.rstrip() for line in rendered.splitlines())


def test_nothing_to_do_says_so_out_loud(download_scenes):
    # An empty report and a broken job otherwise look identical, which is the whole
    # reason this sentence exists.
    rendered = _report(download_scenes, [])

    assert "Nothing to do" in rendered


def test_a_failure_is_never_rendered_as_a_success(download_scenes):
    rendered = _report(
        download_scenes, [download_scenes.failed()], n_files=1, n_failed=1
    )

    assert "Failed (1)" in rendered
    assert "Fetched" not in rendered


def test_a_skipped_file_is_in_neither_table(download_scenes):
    # A file already on disk was not fetched and did not fail. Counting it as either
    # would overstate what the run did, and the headline already carries it.
    rendered = _report(
        download_scenes, [download_scenes.skipped()], n_files=1, n_skipped=1
    )

    assert "Fetched" not in rendered
    assert "Failed" not in rendered
    assert "1 file(s): 0 fetched, 1 already here, 0 failed" in rendered


def test_a_file_which_took_a_later_mirror_says_how_many_it_took(download_scenes):
    # "we got it, but only from the fourth mirror" is the thing worth noticing
    # before every mirror is down.
    rendered = _report(
        download_scenes,
        [download_scenes.fetched(after_failures=("esgf.nci.org.au", "aims3.llnl.gov"))],
        n_files=1,
        n_fetched=1,
    )

    row = next(line for line in rendered.splitlines() if "UKESM1-3-LL" in line)

    assert row.split()[-1] == "2"


def test_a_file_with_no_route_offered_reads_as_that(download_scenes):
    # Distinct from every route having been tried and failed: nothing was tried, so
    # a count of attempts would be a misleading zero on its own.
    rendered = _report(
        download_scenes,
        [download_scenes.failed(tried=())],
        n_files=1,
        n_failed=1,
    )

    assert "no route offered" in rendered


def test_the_root_is_named_so_a_reader_knows_which_disk(download_scenes):
    rendered = _report(download_scenes, [download_scenes.fetched()], n_files=1)

    assert str(download_scenes.root) in rendered


def test_given_up_is_called_out_separately(download_scenes):
    # A subset of the failures, and the one that will not fix itself by waiting.
    rendered = _report(
        download_scenes,
        [download_scenes.failed(outcome=DownloadOutcome.NEEDS_CREDENTIALS)],
        n_files=1,
        n_failed=1,
        n_given_up=1,
    )

    assert "1 for good" in rendered


def test_a_long_error_cannot_widen_the_table(download_scenes):
    # The outcome is printed, never the node's own message, which is as long as
    # whatever the node felt like saying.
    rendered = _report(
        download_scenes,
        [download_scenes.failed(outcome=DownloadOutcome.HOST_FAULT)],
        n_files=1,
        n_failed=1,
    )

    assert max(len(line) for line in rendered.splitlines()) < 200


def test_the_subject_says_nothing_new_when_there_was_nothing_to_do(download_scenes):
    subject = download_subject([download_scenes.group()], stem="pattern-effect")

    assert subject == "pattern-effect downloads: nothing new"


def test_the_subject_leads_with_a_failure(download_scenes):
    # In front, because it is the half of the subject line worth reacting to, and a
    # subject line is often all that gets read.
    subject = download_subject(
        [download_scenes.group(n_files=3, n_fetched=2, n_failed=1, bytes_fetched=2048)],
        stem="pattern-effect",
    )

    assert subject.startswith("[1 FAILED]")


def test_the_subject_carries_how_much_arrived(download_scenes):
    subject = download_subject(
        [download_scenes.group(n_files=2, n_fetched=2, bytes_fetched=65345526)],
        stem="pattern-effect",
    )

    assert "2 fetched" in subject
    assert "62.3 MB" in subject


def test_the_subject_sums_across_requirements(download_scenes):
    # One email however many requirements downloaded, so the subject has to add up
    # rather than report whichever group happened to be last.
    subject = download_subject(
        [
            download_scenes.group(
                label="CMIP7", n_files=2, n_fetched=2, bytes_fetched=1024
            ),
            download_scenes.group(
                label="CMIP8", n_files=3, n_fetched=1, n_failed=2, bytes_fetched=1024
            ),
        ],
        stem="pattern-effect",
    )

    assert subject.startswith("[2 FAILED]")
    assert "3 fetched" in subject
    assert "2.0 KB" in subject


def test_each_requirement_gets_its_own_block(download_scenes):
    # The failure this prevents: two requirements' files in one undifferentiated
    # table, which cannot say which question asked for what.
    rendered = format_download_report(
        [
            download_scenes.group([download_scenes.fetched()], label="CMIP7"),
            download_scenes.group([download_scenes.failed()], label="CMIP8"),
        ],
        download_scenes.held(done=1, failed=1),
        root=download_scenes.root,
        facets=IDENTITY_FACETS,
    )

    assert "CMIP7 this run" in rendered
    assert "CMIP8 this run" in rendered
