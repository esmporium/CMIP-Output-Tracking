"""
Which endpoint can actually give you the data, and at which version

Everything here is computed at version level, against the newest version any
endpoint reported, and three outcomes are kept apart because they fail differently:

- `current`  -- this endpoint has the newest usable version
- `stale`    -- it has the dataset, at an older version. You get old data and you do
                not notice
- `absent`   -- it has no usable version. You get nothing and you do notice

Retracted versions are never the target and are not usable.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from esmporium.db import VersionRecord

    from local.endpoints import EndpointResult

# Future TODO: may need to figure out how to handle
# non-datetime formats of version, such as CMIP5.
_DATED_VERSION = re.compile(r"\d{8}")
"""
What a version we can order looks like

CMIP versions are `YYYYMMDD`, which sorts chronologically as a string. The DRS does
not guarantee it though, so a dataset with a version of any other shape is reported as
unordered rather than sorted wrongly: guessing which of `v1` and `20200724` is newer
would be inventing an answer.
"""

_EXACT_COVER_LIMIT = 12
"""
How many endpoints we will search exhaustively for a smallest covering set
"""


@dataclass(frozen=True)
class EndpointCoverage:
    """How much of a project one endpoint can actually serve"""

    tag: str
    """The endpoint's short name"""

    host: str
    """The endpoint's host"""

    current: int
    """How many datasets it holds at the newest usable version"""

    stale: int
    """How many it holds at an older version than some other endpoint has"""

    absent: int
    """
    How many it has no usable version of

    Both "never reported it" and "reported only retracted versions of it", because
    the question this answers is whether you can get usable data here, and the answer
    in both cases is no.
    """

    ahead_on: int
    """
    How many contested datasets it holds the newest version of

    Contested meaning the endpoints disagreed about the version. This is what makes
    "`ornl` is ahead of everyone on 50 datasets" sayable, which is the actionable
    half of a disagreement: pull those from anywhere else and you get stale data.
    """

    @property
    def total(self) -> int:
        """How many datasets were counted, which is the same for every endpoint"""
        return self.current + self.stale + self.absent


@dataclass(frozen=True)
class Coverage:
    """What every endpoint of one project can serve, and what it takes to get it all"""

    endpoints: tuple[EndpointCoverage, ...]
    """One entry per endpoint which answered, in the order they were given"""

    n_datasets: int
    """How many datasets were counted"""

    n_answered: int
    """How many endpoints answered"""

    n_endpoints: int
    """How many endpoints were asked"""

    contested: int
    """How many datasets the endpoints reported different versions of"""

    unordered: int
    """
    How many datasets had a version we could not order, and so could not judge

    Counted and excluded rather than guessed at. See [_DATED_VERSION][(m).].
    """

    unusable: int
    """
    How many datasets no endpoint had a usable version of

    Every version of them, everywhere, was retracted. Excluded from `n_datasets`,
    because "nobody has a usable copy" is not a fact about any one endpoint.
    """

    smallest_cover: tuple[str, ...]
    """
    The fewest endpoints which together hold the newest version of everything

    Empty when nothing was counted. One endpoint when a single one is a complete and
    current source, which is the case worth knowing.
    """

    cover_is_exact: bool
    """
    Whether `smallest_cover` is provably the smallest, or merely a good one

    `False` once there are more endpoints than [_EXACT_COVER_LIMIT][(m).].
    """


def newest_usable_records(result: EndpointResult) -> dict[int, VersionRecord]:
    """
    Get the newest usable version of each dataset one endpoint reported

    Newest *usable*: a retracted version is not something you can use, so an endpoint
    which reports only retracted versions of a dataset is not holding it for our
    purposes. An endpoint can report several versions of one dataset, and the one
    that matters is the best it can serve.

    Parameters
    ----------
    result
        The endpoint's result. One which did not answer holds nothing.

    Returns
    -------
    :
        The newest usable version of each dataset, keyed by dataset id
    """
    if result.summary is None:
        return {}

    best: dict[int, VersionRecord] = {}
    for version in result.summary.current:
        if version.retracted:
            continue

        known = best.get(version.dataset_id)
        if known is None or version.version > known.version:
            best[version.dataset_id] = version

    return best


def _held(result: EndpointResult) -> dict[int, str]:
    """Get just the newest usable version string of each dataset."""
    return {
        dataset: record.version
        for dataset, record in newest_usable_records(result).items()
    }


def _smallest_cover(
    current_by_tag: Mapping[str, Collection[int]], target: Collection[int]
) -> tuple[tuple[str, ...], bool]:
    """
    Find the fewest endpoints which together cover `target`

    Exhaustive while that is cheap, because "you need three endpoints, not one" is
    worth being right about rather than approximately right about. Greedy past
    [_EXACT_COVER_LIMIT][(m).], and the caller is told which it got.
    """
    wanted = set(target)
    if not wanted:
        return (), True

    tags = list(current_by_tag)
    if len(tags) <= _EXACT_COVER_LIMIT:
        for size in range(1, len(tags) + 1):
            for combination in itertools.combinations(tags, size):
                covered: set[int] = set()
                for tag in combination:
                    covered |= set(current_by_tag[tag])
                if wanted <= covered:
                    return combination, True

        return tuple(tags), True

    chosen: list[str] = []
    remaining = set(wanted)
    while remaining:
        best = max(tags, key=lambda tag: len(remaining & set(current_by_tag[tag])))
        gained = remaining & set(current_by_tag[best])
        if not gained:
            break

        chosen.append(best)
        remaining -= gained

    return tuple(chosen), False


def coverage_of(results: Sequence[EndpointResult]) -> Coverage:
    """
    Work out what each endpoint of one project can serve

    Parameters
    ----------
    results
        Every endpoint asked about one project. Endpoints which did not answer are
        counted in `n_endpoints` and otherwise left out: an endpoint we could not ask
        has not been shown to be missing anything, and folding its silence in as
        `absent` would read as a fact about ESGF.

        Pass one project's endpoints at a time. Tags only have to be unique within a
        project, and `east` means two different searches across two projects.

    Returns
    -------
    :
        The coverage, holding only counts and tags, so it renders anywhere
    """
    answered = [result for result in results if result.summary is not None]
    held = {result.endpoint.tag: _held(result) for result in answered}

    every_dataset = {dataset for holdings in held.values() for dataset in holdings}

    # A dataset nobody has a usable version of, and one whose versions we cannot
    # order, are both set aside rather than guessed at. Each is counted so that a
    # reader can see the totals do not add up to the whole archive, and why.
    unusable = len(
        {
            version.dataset_id
            for result in answered
            if result.summary is not None
            for version in result.summary.current
            if version.dataset_id not in every_dataset
        }
    )

    target: dict[int, str] = {}
    unordered: set[int] = set()
    for dataset in every_dataset:
        versions = [
            holdings[dataset] for holdings in held.values() if dataset in holdings
        ]
        if any(not _DATED_VERSION.fullmatch(version) for version in versions):
            unordered.add(dataset)
            continue

        target[dataset] = max(versions)

    contested = sum(
        1
        for dataset, newest in target.items()
        if any(
            holdings[dataset] != newest
            for holdings in held.values()
            if dataset in holdings
        )
    )

    current_by_tag = {
        tag: {
            dataset
            for dataset, newest in target.items()
            if holdings.get(dataset) == newest
        }
        for tag, holdings in held.items()
    }

    endpoints = []
    for result in answered:
        tag = result.endpoint.tag
        holdings = held[tag]
        current = current_by_tag[tag]
        stale = {
            dataset
            for dataset in target
            if dataset in holdings and dataset not in current
        }
        endpoints.append(
            EndpointCoverage(
                tag=tag,
                host=result.endpoint.host,
                current=len(current),
                stale=len(stale),
                absent=len(target) - len(current) - len(stale),
                ahead_on=sum(
                    1
                    for dataset in current
                    if any(
                        other[dataset] != target[dataset]
                        for other in held.values()
                        if dataset in other
                    )
                ),
            )
        )

    cover, exact = _smallest_cover(current_by_tag, target)

    return Coverage(
        endpoints=tuple(endpoints),
        n_datasets=len(target),
        n_answered=len(answered),
        n_endpoints=len(results),
        contested=contested,
        unordered=len(unordered),
        unusable=unusable,
        smallest_cover=cover,
        cover_is_exact=exact,
    )
