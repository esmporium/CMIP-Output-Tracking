"""
Walking back up the parent chain, for the questions which ask about one

A requirement can say "this run *and* the control it branched from" -- that is
[TrackedRequirement.lineage][local.endpoints.TrackedRequirement.lineage] -- but a
search does not follow parent links, deliberately and upstream. Searching records what
a query matches; climbing from a dataset to its parent is a separate call, and this
module is where we make it.

The division of labour is [local.endpoints][]'s, one step along: esmporium knows how to
read a parent fact, match it to a dataset, pick a release and write the link
([esmporium.db.find_parents][]), and this module knows the two things it cannot know for
us -- which datasets we want walked, and that a chain which cannot be walked is a thing
to report rather than a reason to abandon the morning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from esmporium.db import find_parents
from esmporium.requirements import set_facets
from esmporium.search import to_search_plan
from sqlmodel import Session

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

    from esmporium.db import ParentWalkResult
    from esmporium.requirements import Requirement
    from esmporium.search.search_api_facade import SearchAPIFacadeSelector
    from sqlalchemy.engine import Engine


class UnwalkableUntilError(ValueError):
    """
    Raised when a lineage says to stop somewhere a parent walk cannot be told about
    """

    def __init__(self, facets: Sequence[str]) -> None:
        """
        Initialise the error

        Parameters
        ----------
        facets
            The facets the lineage's `until` sets which the walk cannot honour
        """
        self.facets = tuple(facets)
        """The facets which cannot be honoured"""

        super().__init__(
            f"A lineage's `until` sets {list(self.facets)}, but a parent walk is "
            "told where to stop by experiment alone "
            "(`esmporium.db.find_parents(until_experiment=...)`). Those facets "
            "would still be honoured when the requirement is solved, so the walk "
            "and the requirement would disagree about where the chain ends -- "
            "which is worse than refusing. Name the experiment and nothing else."
        )


def until_experiments(requirement_to_walk: Requirement) -> tuple[str, ...]:
    """
    Read where this requirement's lineages stop, in the form a walk is told it

    Read off the requirement rather than passed in beside it, which is the point:
    `until` is declared once, on the relation, and is part of the stored fingerprint.
    A walk told something else would resolve links the requirement does not ask for,
    and nothing would say so.

    Parameters
    ----------
    requirement_to_walk
        The requirement whose lineages to read. One with no lineage gives `()`.

    Returns
    -------
    :
        The experiments to walk back to, deduplicated, in tree order

    Raises
    ------
    UnwalkableUntilError
        An `until` sets a facet other than `experiment` or `project`
    """
    plan = to_search_plan(requirement_to_walk)

    extra = sorted(
        {
            facet
            for until in plan.ancestry_until
            for facet in set_facets(until)
            # `project` is safe to ignore: a walk refuses to climb across projects
            # anyway (`esmporium.db.CrossProjectParentError`), so naming one adds
            # nothing a walk could fail to honour.
            if facet not in ("experiment", "project")
        }
    )
    if extra:
        raise UnwalkableUntilError(extra)

    # `dict.fromkeys` rather than a set, so the order is the tree's and two runs of
    # the same configuration tell the walk the same thing.
    return tuple(
        dict.fromkeys(
            experiment
            for until in plan.ancestry_until
            for experiment in until.experiment
        )
    )


@dataclass(frozen=True)
class ParentWalk:
    """
    What came of walking one question's ancestry, whether or not the walk finished

    The parent twin of [local.endpoints.EndpointResult][], and carries the label for
    the same reason: a report shows several of these and a walk result on its own does
    not say which question asked for it. One of `result` and `failure` is always set
    and never both.
    """

    label: str
    """Which tracked requirement this is the walk for"""

    n_started_from: int
    """
    How many dataset versions the walk was asked to start from

    Needed to read an empty result: no links written because there was nothing to
    walk is a different morning from no links written because every chain was already
    resolved, and both have an empty `linked`.
    """

    result: ParentWalkResult | None
    """What the walk managed, or `None` if it raised"""

    failure: str | None
    """Why it raised, or `None` if it finished"""

    @property
    def walked(self) -> bool:
        """Whether there is a result to read"""
        return self.result is not None

    @property
    def n_linked(self) -> int:
        """
        How many parent links were written

        `0` for a walk which raised -- which does **not** mean it wrote none. See
        [walk_parents][(m).].
        """
        if self.result is None:
            return 0

        return len(self.result.linked)


def walk_parents(  # noqa: PLR0913 - one argument per thing a walk has to be told
    engine: Engine,
    label: str,
    dataset_version_ids: Collection[int],
    *,
    until_experiment: Sequence[str],
    selector: SearchAPIFacadeSelector,
    max_hops: int = 16,
    find: Callable[..., ParentWalkResult] = find_parents,
) -> ParentWalk:
    """
    Walk back from these dataset versions, and contain whatever that costs

    Catches whatever the walk raised and returns it as a [ParentWalk][(m).] rather than
    propagating it, for the reason [local.endpoints.search_endpoint][] does the same:
    this runs between the morning's search and the morning's download, and a chain
    nobody can climb is a thing to report, not a reason to fetch no data.

    Two things worth knowing about a walk which raised. It raises **once, at the end**,
    with every failed chain on it ([esmporium.db.ParentResolutionError][]), so one bad
    chain does not stop the others being resolved. And it **commits once per hop**, so
    the links it managed before it raised are already in the database and the next
    run picks up where it left off -- the `0` that [ParentWalk.n_linked][(m).] reports
    for a failed walk is what we were told, not what was written.

    Parameters
    ----------
    engine
        The database to read the parent facts from and write the links into

    label
        Which tracked requirement this is the walk for

    dataset_version_ids
        The versions to walk back from.

        **Always passed, and never empty for a question with a lineage.** esmporium
        reads `None` as "every unresolved assertion in the database", and since
        pointing at this commit every CMIP7 search records parent facts on every
        ingest -- so `None` here would quietly start climbing the ancestry of the
        pattern-effect questions as well, searching for and ingesting controls nobody
        asked about.

    until_experiment
        The experiments to walk back to, from [until_experiments][(m).]

    selector
        Which endpoints to search for a missing ancestor, best first. See
        [local.endpoints.facade_selector][].

    max_hops
        How many times to climb before giving up. esmporium's own default, restated
        here only so that it is visible; it is a guard against a chain which never
        ends, not the stopping rule.

    find
        How to do the walk. Injected so that this function's own behaviour -- that a
        failed walk is contained and reported -- can be tested without a network or a
        database.

    Returns
    -------
    :
        What the walk managed, or the reason it has none
    """
    ids = tuple(dataset_version_ids)

    try:
        with Session(engine) as session:
            result = find(
                session,
                ids,
                until_experiment=tuple(until_experiment),
                selector=selector,
                max_hops=max_hops,
            )
    # Deliberately broad, for the reason in the docstring: between the search and the
    # download is the worst place in this job to let an exception out.
    except Exception as exc:
        return ParentWalk(
            label=label, n_started_from=len(ids), result=None, failure=f"{exc}"
        )

    return ParentWalk(label=label, n_started_from=len(ids), result=result, failure=None)
