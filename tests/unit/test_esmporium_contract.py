"""
What we rely on esmporium for, written down so that a version bump says so
"""

# A note for developers:
# This repository pins esmporium at a single commit and we expect to move that pin
# often, following whatever is being worked on upstream. The hazard of doing that is
# that esmporium is still young: names move, fields get renamed, things which were
# exported stop being. If the first thing to notice is the cron job at 6am, we find
# out by not getting an email -- the one failure mode that looks exactly like "nothing
# changed today".

# So this file is the tripwire. It names every piece of esmporium's surface this
# repository touches, and it fails loudly, by name, the moment one of them moves.
# Run it immediately after `make pin-esmporium`; that is what the target does.

# Two rules for editing it:

# - it asserts on *names and shapes*, never on behaviour. Behaviour is esmporium's
#   own test suite's business, and duplicating it here would be a second copy to keep
#   in step for no gain.
# - when a name here legitimately changes upstream, fix the renderer and fix this
#   file in the same commit. A tripwire quietly loosened to make a bump pass is worse
#   than no tripwire, because it still looks like one.

from __future__ import annotations

import datetime
import importlib
import inspect

import esmporium.datasets
import esmporium.db
import esmporium.downloads
import pytest
from esmporium.db import DatasetChangeType

MODULE_LEVEL_NAMES: dict[str, tuple[str, ...]] = {
    "esmporium.datasets": (
        "DATASET_FACET_COLUMNS",
        # What a route is made of and how an attempt ended, read by the download
        # report and built by its fixtures
        "AccessService",
        "ContainerFormat",
        "DownloadOutcome",
    ),
    "esmporium.db": (
        # The summary types our renderer is a function of
        "ChangeRecord",
        "DatasetChangeType",
        "RunHealth",
        "SearchRunLeafInfo",
        "SearchSummary",
        "VersionRecord",
        "describe_moved",
        "summarise_search",
        # What the driver script needs to run and record a search
        "configure_sqlite_for_concurrency",
        "search_and_track",
        # What the download job needs to plan, fetch, record and report
        "DownloadPlanItem",
        "DownloadSummary",
        "download",
        "download_summary",
        "plan_downloads",
        "record_data_node_calls",
        # The rows `local.downloads.describe_results` reads an identity back out of
        "Dataset",
        "DatasetVersion",
        "File",
    ),
    "esmporium.db.migrate": ("upgrade_to_head",),
    "esmporium.downloads": (
        # The result types the download report is a pure function of
        "DownloadRequest",
        "DownloadResult",
        "FetchOutcome",
        "Route",
        "RouteFailure",
        "RunTotals",
        "totals",
        # Where a file goes on our disk. Named here because the layout is part of
        # the contract: moving it moves every file we have already downloaded.
        "FileTarget",
        "esmporium_layout",
        "local_path",
        # Raised by a fetch, and built by the fixtures which pretend to be one
        "DataNodeFetchError",
    ),
    "esmporium.query": ("Query", "QueryProtocol"),
    "esmporium.requirements": ("Requirement", "leaf", "requirement"),
    # What `local.endpoints` needs to ask one endpoint and only that endpoint
    "esmporium.search.search_api_facade": (
        "INBUILT_SEARCH_API_FACADE_STORE",
        "build_list_selector",
    ),
}
"""
Every esmporium name this repository imports, by the module we import it from

The module matters as much as the name: importing from `esmporium.db` rather than
from `esmporium.db.summaries` is what keeps us on the public surface, so a name
which is still importable from a submodule but no longer exported should fail here.
"""

ATTRIBUTES_READ: dict[str, tuple[str, ...]] = {
    "VersionRecord": (
        "dataset_version_id",
        "dataset_id",
        "facets",
        "version",
        "retracted",
        "data_nodes",
    ),
    "ChangeRecord": ("change_type", "version", "detail", "added_but_retracted"),
    "SearchSummary": (
        "headline",
        "any_changes",
        "changes",
        "net_unchanged",
        "current",
        "window_runs",
        "baseline_run_id",
        "run",
        # Read by `_change_counts` for the per-endpoint overview table
        "counts",
        # The frames are the contract we agreed to keep upstream: as long as these
        # exist, the layout can be rewritten here without touching esmporium.
        "changes_frame",
        "current_frame",
    ),
    "RunHealth": (
        "started_at",
        "complete",
        "leaves",
        "n_answered",
        "n_leaves",
        "hosts",
        "n_matches",
        "n_documents",
        "failures",
        "truncated",
    ),
    "SearchRunLeafInfo": ("label", "failure", "n_documents", "n_matches"),
    "DownloadSummary": ("n_by_status", "n_total", "n_outstanding"),
    "DownloadPlanItem": ("request",),
    # Read by `local.downloads.describe_results` to say which dataset a downloaded
    # file belongs to. A column renamed upstream would otherwise show up as a report
    # full of `-`, which reads as "ESGF published nothing" rather than as a bug.
    "File": ("id", "filename", "dataset_version_id", "size", "checksum"),
    "DatasetVersion": ("id", "version", "dataset_id"),
    "Dataset": ("id", "model", "variant_label", "grid_label"),
}
"""
Every attribute of an esmporium summary type that our renderer reads

Keyed by the type's name in `esmporium.db`. Read off `local.summary_formatting`
by hand: if you teach the renderer to read a new field, add it here, because a
field we read and do not list is a field that can vanish silently.
"""


DOWNLOAD_ATTRIBUTES_READ: dict[str, tuple[str, ...]] = {
    "DownloadResult": ("request", "fetch", "failures", "skipped", "succeeded"),
    "DownloadRequest": (
        "routes",
        "dest",
        "file_id",
        "expected_size",
        "checksum",
        "checksum_type",
    ),
    "FetchOutcome": (
        "path",
        "bytes_written",
        "size_on_disk",
        "checksum_verified",
        "checksum_type_used",
        "resumed",
        "route",
    ),
    "RouteFailure": ("route", "outcome", "error"),
    "Route": ("url", "data_node", "service", "container_format", "data_access_id"),
    "RunTotals": (
        "n_files",
        "n_fetched",
        "n_skipped",
        "n_failed",
        "n_given_up",
        "bytes_fetched",
    ),
}
"""
Every attribute of an esmporium download type that our renderer or its fixtures read

Keyed by the type's name in `esmporium.downloads`. Separate from
[ATTRIBUTES_READ][] only because these types are exported from a different module,
and the module is half of what is being pinned.
"""


@pytest.mark.parametrize(
    ("module_name", "name"),
    [
        (module_name, name)
        for module_name, names in MODULE_LEVEL_NAMES.items()
        for name in names
    ],
)
def test_esmporium_still_exports_what_we_import(module_name, name):
    module = importlib.import_module(module_name)

    assert hasattr(module, name), (
        f"esmporium no longer exports {name!r} from {module_name}. "
        "Either it was renamed, or it stopped being public. Find what replaced it, "
        "update the renderer and this list together."
    )


@pytest.mark.parametrize(
    ("type_name", "attribute"),
    [
        (type_name, attribute)
        for type_name, attributes in ATTRIBUTES_READ.items()
        for attribute in attributes
    ],
)
def test_the_summary_types_still_carry_what_we_read(type_name, attribute):
    summary_type = getattr(esmporium.db, type_name)

    # Fields of a frozen dataclass live in `__dataclass_fields__`; properties and
    # methods live on the class. Either is fine -- we only care that reading it
    # works -- so both are accepted.
    available = set(getattr(summary_type, "__dataclass_fields__", {})) | set(
        dir(summary_type)
    )

    assert attribute in available, (
        f"esmporium's {type_name} no longer has {attribute!r}, which "
        "local.summary_formatting reads. Update the renderer and this list together."
    )


def test_a_summary_we_build_by_hand_is_still_a_summary(summary_scenes):
    # The scene builders in `conftest.py` call these constructors with keyword
    # arguments. A renamed or newly-required field makes every test in the suite
    # fail at once with a `TypeError`; this one says what actually happened.
    summary = summary_scenes.summary(current=(summary_scenes.version(),))

    assert summary.requirement_name == "pattern-effect"
    assert isinstance(summary.run.started_at, datetime.datetime)
    assert isinstance(summary.headline, str)
    assert len(summary.current) == 1


def test_the_frames_we_asked_esmporium_to_keep_still_work(summary_scenes):
    # `changes_frame` and `current_frame` are the agreed boundary between the two
    # repositories: esmporium computes the fields, we arrange them. If these stop
    # working, the split itself needs renegotiating, not patching.
    summary = summary_scenes.summary(
        changes=(summary_scenes.change(DatasetChangeType.ADDED),),
        current=(summary_scenes.version(),),
    )

    changes = summary.changes_frame()
    current = summary.current_frame()

    assert len(changes) == 1
    assert len(current) == 1
    for frame in (changes, current):
        assert "dataset_version_id" in frame.columns
        assert "model" in frame.columns


def test_the_facade_store_still_pairs_a_host_with_a_project():
    # `local.endpoints.single_facade_selector` asks the store for exactly one facade.
    # Without this method it would have to filter the store's classifications itself,
    # which are not part of esmporium's public surface.
    # Imported here rather than at the top of the file so that a name which has
    # moved upstream fails this one test by name. A top-level import would fail the
    # whole module instead, which is the same news told less usefully.
    from esmporium.search.search_api_facade import (  # noqa: PLC0415
        INBUILT_SEARCH_API_FACADE_STORE,
    )

    facade = INBUILT_SEARCH_API_FACADE_STORE.get_api_facade_for_project_from_host(
        "CMIP7", "search.east.esgf.io"
    )

    assert facade.search_api.host == "search.east.esgf.io"


def test_a_role_may_still_carry_our_endpoint_tag():
    # This is the load-bearing assumption of `local.endpoints`, and nothing else
    # pins it: we put the endpoint in the leaf's role so that the same query asked of
    # two hosts gives two requirements. If esmporium ever constrains `role` to a
    # vocabulary, or starts sending it anywhere, this is where we find out.
    # Local for the same reason as above.
    from esmporium.query import Query  # noqa: PLC0415
    from esmporium.requirements import leaf, requirement  # noqa: PLC0415

    query = Query(project=("CMIP7",), variable=("tas",))
    built = [
        requirement(
            name=f"pattern-effect-{tag}",
            tree=leaf(query, f"tas-{tag}"),
            group_by=("model", "variant_label"),
        )
        for tag in ("east", "west")
    ]

    east, west = built

    # Different questions, as far as the database is concerned...
    assert east.requirement_hash() != west.requirement_hash()
    # ...and the same question, as far as ESGF is concerned. A role is a label, not a
    # facet; if that stops being true, both endpoints start searching for a variable
    # named after a data centre.
    assert east.tree.query == west.tree.query


@pytest.mark.parametrize(
    ("type_name", "attribute"),
    [
        (type_name, attribute)
        for type_name, attributes in DOWNLOAD_ATTRIBUTES_READ.items()
        for attribute in attributes
    ],
)
def test_the_download_types_still_carry_what_we_read(type_name, attribute):
    download_type = getattr(esmporium.downloads, type_name)

    available = set(getattr(download_type, "__dataclass_fields__", {})) | set(
        dir(download_type)
    )

    assert attribute in available, (
        f"esmporium's {type_name} no longer has {attribute!r}, which "
        "local.download_formatting reads. Update the renderer and this list together."
    )


def test_the_download_layout_is_still_the_one_our_files_are_already_in():
    # Not behaviour for its own sake: this layout is where every file we have
    # already downloaded *is*. If esmporium changes it, a download run stops seeing
    # what is on disk and quietly fetches the whole archive again under new paths.
    # So this is pinned as a path, not as a name.
    placed = esmporium.downloads.esmporium_layout(
        esmporium.downloads.FileTarget(
            id_project_specific="MIP-DRS7.CMIP7.CMIP.UKNCSP.UKESM1-3-LL",
            version="20261020",
            filename="tas.nc",
        )
    )

    assert placed.as_posix() == (
        "MIP-DRS7/CMIP7/CMIP/UKNCSP/UKESM1-3-LL/v20261020/tas.nc"
    )


def test_a_download_is_still_planned_from_the_same_query_a_search_takes():
    # The whole reason `local.job_config.QUERIES` can be shared between the two
    # jobs. If `download` stopped accepting what `search_and_track` accepts, the two
    # would need translating and they could drift apart.
    parameters = inspect.signature(esmporium.db.download).parameters

    assert list(parameters)[:3] == ["engine", "query", "root"]
