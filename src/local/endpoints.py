"""
Asking one question of several search endpoints, one endpoint at a time

A tracked search in esmporium is a question, and the endpoint it was asked of is not
part of that question: `Requirement.requirement_hash` covers everything except the
requirement's `name`, so "tas from east" and "tas from west" fingerprint identically
and cannot both be stored. `SearchRunVersion` carries no host either, so one search
spread across several endpoints cannot be pulled apart again afterwards.

We want them apart. Whether east and west agree, and whether five Solr hosts answer
the same question with the same answer, is the thing we are trying to find out, so
each endpoint needs its own history, its own baseline and its own notion of a
complete run.

So this module makes the endpoint part of the question, by putting its tag in the
leaf's role. The role is the only field we can vary which the search machinery never
sends anywhere: `Leaf.role` is a label a later check refers to, it is not a facet, so
`"tas-east"` and `"tas-west"` go out on the wire as byte-identical queries while
hashing differently. Everything else -- one facade per selector, one host per run --
falls out of that.

It is a bend in esmporium's data model and it is meant to be visible as one. The
upstream fix is a host on `SearchRunVersion`, or a leaf-to-version link table; with
either, every endpoint below collapses back into a single tracked search with
`stop_at_first_result=False`, and this module goes away.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from esmporium.db import search_and_track, summarise_search
from esmporium.requirements import leaf, requirement
from esmporium.search.search_api_facade import (
    INBUILT_SEARCH_API_FACADE_STORE,
    build_list_selector,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from esmporium.db import SearchSummary
    from esmporium.query import QueryProtocol
    from esmporium.requirements import Requirement
    from esmporium.search.search_api_facade import SearchAPIFacadeSelector
    from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class Endpoint:
    """One search endpoint, for one project"""

    project: str
    """The project to ask it about, e.g. `'CMIP7'`"""

    host: str
    """The host to ask, e.g. `'search.east.esgf.io'`"""

    tag: str
    """
    A short name for this endpoint, e.g. `'east'`

    This ends up in a requirement's name and in its leaf's role, which is to say it
    ends up in the database as the thing one endpoint's history is keyed by. Keep it
    short, stable and free of `.`, which esmporium reserves as its role separator.
    """

    @property
    def label(self) -> str:
        """A name for this endpoint to put in front of a person"""
        return f"{self.tag} ({self.host})"


@dataclass(frozen=True)
class EndpointResult:
    """
    What came of asking one endpoint, whether or not it answered

    Carries the endpoint alongside the answer because a report shows several of these
    together and a summary on its own does not say which endpoint produced it. One of
    `summary` and `failure` is always set and never both.
    """

    endpoint: Endpoint
    """Which endpoint this is the result of"""

    summary: SearchSummary | None
    """What it said, or `None` if asking it failed outright"""

    failure: str | None
    """Why asking it failed, or `None` if it answered"""

    @property
    def answered(self) -> bool:
        """Whether there is a summary to read"""
        return self.summary is not None

    @property
    def n_datasets(self) -> int:
        """
        How many distinct datasets this endpoint returned

        Datasets rather than documents, and rather than versions: a replica of a
        dataset is the same dataset, and the raw document counts the hosts report
        differ by a factor of four for that reason alone. `0` for an endpoint which
        did not answer, which is why a report must read `failure` as well as this.
        """
        if self.summary is None:
            return 0

        return len({version.dataset_id for version in self.summary.current})


def endpoint_requirement(
    endpoint: Endpoint,
    query: QueryProtocol,
    *,
    name_stem: str,
    role_stem: str,
    group_by: Sequence[str] = ("model", "variant_label"),
) -> Requirement:
    """
    Build the requirement which asks `endpoint` about `query`

    Parameters
    ----------
    endpoint
        The endpoint to ask. Only its `project` and `tag` are used here; the `host` is
        what [single_facade_selector][(m).] needs.

    query
        The question, which must already name `endpoint.project`

    name_stem
        What to call this search, before the project and endpoint are appended, e.g.
        `'pattern-effect-tas-mon-historical'`

    role_stem
        What the leaf's role is called, before the endpoint tag is appended

    group_by
        Passed to esmporium's `requirement`. It has no default upstream, deliberately,
        and `('model', 'variant_label')` is what our searches have wanted so far.

    Returns
    -------
    :
        The requirement, named and roled so that one endpoint's history is its own
    """
    return requirement(
        name=f"{name_stem}-{endpoint.project.lower()}-{endpoint.tag}",
        # The endpoint tag rides in the role. This is not a typo and it is not
        # cosmetic: it is what makes this requirement hash differently from the same
        # query asked of another host. See this module's docstring. Changing it back
        # to a bare role silently merges every endpoint's history into one.
        tree=leaf(query, f"{role_stem}-{endpoint.tag}"),
        group_by=tuple(group_by),
    )


def single_facade_selector(endpoint: Endpoint) -> SearchAPIFacadeSelector:
    """
    Build a selector which offers `endpoint` and nothing else

    The default selector works through a project's hosts in order and, with
    `stop_at_first_result`, stops at the first which answers -- which is how every run
    so far went to `search.east.esgf.io` and nowhere else. A one-facade selector is
    what makes a run's answer attributable: exactly one host can have produced it, so
    `SearchRunLeaf.host` and `run_is_complete` both mean what they say.

    Parameters
    ----------
    endpoint
        The endpoint to offer

    Returns
    -------
    :
        A selector over just that endpoint's facade

    Raises
    ------
    ValueError
        esmporium has no facade pairing this host with this project. The message lists
        every host it does have, and which projects each supports.
    """
    facade = INBUILT_SEARCH_API_FACADE_STORE.get_api_facade_for_project_from_host(
        endpoint.project, endpoint.host
    )

    return build_list_selector([facade])


def search_endpoint(
    engine: Engine,
    endpoint: Endpoint,
    requirement_to_run: Requirement,
    *,
    search: Callable[..., object] = search_and_track,
    summarise: Callable[..., SearchSummary] = summarise_search,
) -> EndpointResult:
    """
    Search one endpoint, record it, and read the summary back

    Catches whatever asking this endpoint raised and returns it as a
    [EndpointResult][(m).] rather than propagating it, because we are asking nine
    endpoints and one being down is a thing to report, not a reason to abandon the
    other eight. A failure which is nobody's fault but ours -- a selector with no
    facade, say -- lands here too, and reads as that endpoint failing; the overview
    table shows the message, so it does not go unnoticed.

    Parameters
    ----------
    engine
        The database to record the run in and read the summary from

    endpoint
        The endpoint being asked

    requirement_to_run
        The requirement to run, from [endpoint_requirement][(m).]

    search
        How to run and record the search. Injected so this function's own behaviour --
        that one endpoint's failure is contained -- can be tested without a network or
        a database.

    summarise
        How to read the summary back. Injected for the same reason.

    Returns
    -------
    :
        The endpoint's summary, or the reason it has none
    """
    try:
        search(engine, requirement_to_run, single_facade_selector(endpoint))
        # Read the summary back out of the database rather than off what the search
        # returned, so that what gets reported is what was actually recorded.
        summary = summarise(engine, requirement_to_run.name)
    # Deliberately broad: one endpoint's failure must not spread to the other eight.
    except Exception as exc:
        return EndpointResult(endpoint=endpoint, summary=None, failure=f"{exc}")

    return EndpointResult(endpoint=endpoint, summary=summary, failure=None)
