"""
How a download run is paced, and that the pacing really reaches esmporium

`local.downloads.run_downloads` is a wrapper, so the only thing about it worth
testing is what it passes on. That sounds like testing the obvious until you notice
the failure mode: a keyword that silently stops being forwarded leaves a run
fetching one file at a time, which is not an error, is not logged, and shows up only
as a download that takes eight times as long as the last one did.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from esmporium.downloads import DEFAULT_PER_DATA_NODE
from esmporium.query import Query

from local import downloads as downloads_module
from local.downloads import run_downloads

ENGINE = object()
"""
Something to stand in for a database engine

`run_downloads` only ever hands this to `download` and to
`record_data_node_calls`, and neither touches it while being built -- the observer
closes over the engine and opens its session per call. So a bare sentinel is enough,
and a test which needed a real engine would be testing esmporium rather than us.
"""

QUERY = Query(
    project=("CMIP7",),
    variable=("tos",),
    experiment=("historical",),
    reporting_interval=("mon",),
    processing_id=("tavg-u-hxy-sea",),
)
"""The `tos` question, as the driver really asks it"""


@pytest.fixture
def captured(monkeypatch):
    """
    Capture what `run_downloads` passes to esmporium, and fetch nothing

    Returns
    -------
    :
        A dict which the call's keyword arguments land in
    """
    seen: dict[str, object] = {}

    def fake_download(engine, query, root, **kwargs):
        seen["engine"] = engine
        seen["query"] = query
        seen["root"] = root
        seen.update(kwargs)

        return ()

    monkeypatch.setattr(downloads_module, "download", fake_download)

    return seen


def test_the_worker_count_reaches_esmporium(captured):
    run_downloads(ENGINE, QUERY, Path("/data/cmip"), max_workers=8)

    assert captured["max_workers"] == 8


def test_the_per_node_limit_reaches_esmporium(captured):
    run_downloads(ENGINE, QUERY, Path("/data/cmip"), per_data_node=2)

    assert captured["per_data_node"] == 2


def test_a_run_is_paced_per_data_node_unless_told_otherwise(captured):
    # The default matters more than most defaults here, because the thing it protects
    # is somebody else's server. A caller who says nothing should still be polite.
    run_downloads(ENGINE, QUERY, Path("/data/cmip"))

    assert captured["per_data_node"] == DEFAULT_PER_DATA_NODE


def test_one_file_at_a_time_is_still_the_default(captured):
    # Parallelism is opt-in, so that `--download-only` run by hand while debugging
    # behaves the way the logs read: one file, then the next.
    run_downloads(ENGINE, QUERY, Path("/data/cmip"))

    assert captured["max_workers"] is None


def test_the_observer_is_still_wired_up(captured):
    # The whole reason this wrapper exists. Without it nothing records which data node
    # was asked for what, and `make data-node-health` has nothing to report -- which
    # is also how we would find out whether eight workers is too many.
    run_downloads(ENGINE, QUERY, Path("/data/cmip"), max_workers=8)

    assert callable(captured["observer"])


def test_the_query_and_root_are_passed_through_untouched(captured):
    root = Path("/data/cmip")

    run_downloads(ENGINE, QUERY, root, max_workers=8)

    assert captured["query"] is QUERY
    assert captured["root"] is root
    assert captured["engine"] is ENGINE
