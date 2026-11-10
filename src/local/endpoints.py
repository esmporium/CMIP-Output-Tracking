"""
Asking one question of several search endpoints, one endpoint at a time
"""

# Potential future TODO:
# A tracked search in esmporium is a question, and the endpoint it was asked of is not
# part of that question: `Requirement.requirement_hash` covers everything except the
# requirement's `name`, so "tas from east" and "tas from west" fingerprint identically
# and cannot both be stored. `SearchRunVersion` carries no host either, so one search
# spread across several endpoints cannot be pulled apart again afterwards.

# We want them apart. Whether east and west agree, and whether five Solr hosts answer
# the same question with the same answer, is the thing we are trying to find out, so
# each endpoint needs its own history, its own baseline and its own notion of a
# complete run.

# So this module makes the endpoint part of the question, by putting its tag in the
# leaf's role. The role is the only field we can vary which the search machinery never
# sends anywhere: `Leaf.role` is a label a later check refers to, it is not a facet, so
# `"tas-east"` and `"tas-west"` go out on the wire as byte-identical queries while
# hashing differently. Everything else -- one facade per selector, one host per run --
# falls out of that.

# It is a bend in esmporium's data model and it is meant to be visible as one. The
# upstream fix is a host on `SearchRunVersion`, or a leaf-to-version link table; with
# either, every endpoint below collapses back into a single tracked search with
# `stop_at_first_result=False`, and this module goes away.

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
    from esmporium.requirements import Relation, Requirement
    from esmporium.search.search_api_facade import (
        SearchAPIFacade,
        SearchAPIFacadeSelector,
    )
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
class TrackedRequirement:
    """
    One question, the endpoints we ask it of, and what to do with the answer

    The unit this repository is organised around. Adding a second question is adding
    one of these to a list, rather than finding every dict keyed by project and
    teaching it about a new key.

    A question is per *project*, not per phase of CMIP: "the pattern effect in CMIP7"
    and "the pattern effect in CMIP6" are two of these, because their queries differ
    (`processing_id` is `tavg-h2m-hxy-u` for one and `Amon` for the other) and because
    we want one of them downloaded and the other only watched.
    """

    label: str
    """
    What to call this in a report, e.g. `'CMIP7 pattern effect'`

    Report-only, and therefore **free to change**. Nothing is stored under it.
    """

    stem: str
    """
    What this search is called in the database, before the project and endpoint

    One stored requirement per endpoint is filed as `<stem>-<project>-<tag>`, so this
    is the key an endpoint's whole history hangs off. **Changing it starts that
    history again from scratch**, and esmporium refuses to let a search change
    meaning without changing name, so editing [query][(c).query] without changing
    this (or bumping the database version) is refused rather than silently comparing
    apples with oranges.

    Two requirements may share a stem -- ours do -- because the project is appended.
    That is what keeps "the same question, asked of two projects" readable in the
    database.
    """

    role_stem: str
    """
    What the leaf's role is called, before the endpoint tag is appended

    Part of the stored requirement's fingerprint, so it is as load-bearing as
    [stem][(c).stem] and must not be changed casually. See this module's docstring
    for why the endpoint tag rides in the role at all.
    """

    query: QueryProtocol
    """The question itself. Must name the same project its endpoints do."""

    endpoints: tuple[Endpoint, ...]
    """Every endpoint to ask it of, in the order the report should show them"""

    download: bool = False
    """
    Whether to fetch the bytes for what this finds

    Searching and downloading are not the same appetite: we watch CMIP6 across seven
    endpoints to see what exists, and have no intention of pulling all of it onto
    this disk.
    """

    detailed: bool = False
    """
    Whether this gets a full changes table, one block per endpoint

    `False` gets a single row per endpoint instead: how much it returned, and whether
    that differs from its own previous run.
    """

    lineage: Relation | None = None
    """
    The related datasets this question also needs, or `None` for a question which
    stands alone

    Today the only relation esmporium offers is
    [Ancestors][esmporium.requirements.Ancestors], i.e. "and the control this run
    branched from". It is **part of the stored fingerprint**, as load-bearing as
    [stem][(c).stem]: adding one to a question which did not have one starts that
    question's history from scratch.

    Setting this does not by itself fetch anything, or even search for anything. A
    search still records only what its own query matches; following the parent links
    is the separate, explicit call [local.parents.walk_parents][] makes. See
    [download_query][(c).download_query] for the other half anyone adding a lineage
    has to think about.
    """

    download_query: QueryProtocol | None = None
    """
    What the download plans from, or `None` to use [query][(c).query]

    Needed because a download is planned from a *query* rather than from a solved
    requirement (esmporium's `plan_downloads`), and the two are not the same question
    the moment a lineage is involved: a query naming `abrupt-4xCO2` cannot select the
    `piControl` datasets a parent walk just went and found, however many links were
    written. So a question which wants its ancestors' bytes has to name their
    experiment here as well.

    Report-only in the sense that matters: nothing is stored under it, so unlike
    [stem][(c).stem] and [lineage][(c).lineage] it is **free to change**. Widening it
    costs disk, not history.
    """

    report_facets: tuple[str, ...] = ()
    """
    Which facets this question's tables name a row by, or `()` for the caller's default

    Both reports, deliberately one knob rather than two. The defaults
    ([local.downloads.IDENTITY_FACETS][] and
    [local.summary_formatting.DEFAULT_FACETS][]) are enough for a question which asks
    about one variable in one experiment, and that is what `()` gets you. A question
    spanning several of either has to say so, or both of its tables print rows that
    cannot be told apart: the Gregory question covers four variables, so its `ADDED`
    table without `variable` is four identical lines per model, and its download table
    without `experiment` cannot show which rows are the controls.

    One knob and not two because the overlap is almost total, and the cost of sharing
    is small and in the right direction: the search report gains an `experiment`
    column whose value is the same in every row, which is redundant rather than
    misleading. Two knobs would be one more thing to get wrong for one saved column.

    Report-only, and therefore free to change.
    """

    def __post_init__(self) -> None:
        """
        Check this question is internally consistent before anything uses it

        Raises
        ------
        ValueError
            An endpoint names a project one of the queries does not ask about

            Cheap to check and miserable to debug: the search would be built from
            this query, sent to a facade chosen for that project, and the mismatch
            would surface as an endpoint which answers nothing for no stated reason.

            Or: this question declares a lineage and wants the bytes, but says
            nothing about where its ancestors live. See
            [download_query][(c).download_query].
        """
        self._check_projects_agree(self.query, "asks about")
        if self.download_query is not None:
            self._check_projects_agree(self.download_query, "downloads")

        if self.lineage is not None and self.download and self.download_query is None:
            msg = (
                f"{self.label!r} has a lineage and downloads, but no "
                "`download_query`. A download is planned from a query, and this "
                f"one names {list(self.query.experiment)}, so the ancestors the "
                "walk goes and finds would be linked, reported and never fetched "
                "-- with nothing raised and nothing logged. Give it a "
                "`download_query` which names their experiment too, e.g. "
                'experiment=("abrupt-4xCO2", "piControl").'
            )
            raise ValueError(msg)

    def _check_projects_agree(self, query: QueryProtocol, doing: str) -> None:
        """
        Check one of this question's queries agrees with its endpoints about the project

        Parameters
        ----------
        query
            The query to check

        doing
            What this query is for, for the message, e.g. `'asks about'`

        Raises
        ------
        ValueError
            An endpoint names a project `query` does not ask about
        """
        asked = tuple(query.project)
        wrong = sorted(
            {
                endpoint.project
                for endpoint in self.endpoints
                if endpoint.project not in asked
            }
        )
        if wrong:
            msg = (
                f"{self.label!r} {doing} {list(asked)} but has endpoints for "
                f"{wrong}. An endpoint is paired with a project in esmporium's "
                "facade store, so these have to agree."
            )
            raise ValueError(msg)

    @property
    def download_target(self) -> QueryProtocol:
        """
        The query this question's download plans from

        [download_query][(c).download_query] when it has one, and [query][(c).query]
        otherwise, so a caller never has to remember which of the two to read.
        """
        return self.query if self.download_query is None else self.download_query

    @property
    def facets(self) -> tuple[str, ...] | None:
        """
        Which facets this question's download tables name a row by

        `None` rather than `()` for "use the caller's default", because that is what
        the renderers take. See [report_facets][(c).report_facets].
        """
        return self.report_facets or None


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


def endpoint_requirement(  # noqa: PLR0913 - one argument per thing a question has
    endpoint: Endpoint,
    query: QueryProtocol,
    *,
    name_stem: str,
    role_stem: str,
    lineage: Relation | None = None,
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

    lineage
        The relation to hang off the leaf, or `None` for a question which stands
        alone. See [TrackedRequirement.lineage][(m).TrackedRequirement.lineage].

    group_by
        Passed to esmporium's `requirement`. It has no default upstream, deliberately,
        and `('model', 'variant_label')` is what our searches have wanted so far.

    Returns
    -------
    :
        The requirement, named and roled so that one endpoint's history is its own
    """
    # The endpoint tag rides in the role. This is not a typo and it is not cosmetic:
    # it is what makes this requirement hash differently from the same query asked of
    # another host. See this module's docstring. Changing it back to a bare role
    # silently merges every endpoint's history into one.
    tree = leaf(query, f"{role_stem}-{endpoint.tag}")
    if lineage is not None:
        # The lineage's own role is left as it was given, *without* the tag. It does
        # not need one: `requirement_hash` covers the whole tree, the leaf's role
        # inside it already carries the tag, and so east and west still fingerprint
        # apart. Roles only have to be unique within one requirement, and each of
        # ours has exactly one leaf.
        tree = tree.with_lineage(lineage)

    return requirement(
        name=f"{name_stem}-{endpoint.project.lower()}-{endpoint.tag}",
        tree=tree,
        group_by=tuple(group_by),
    )


def requirement_for(tracked: TrackedRequirement, endpoint: Endpoint) -> Requirement:
    """
    Build the requirement which asks `endpoint` about `tracked`

    The pairing [endpoint_requirement][(m).] does, with the stems read off the
    tracked requirement instead of being passed by hand -- so a caller cannot ask
    one endpoint under one stem and its neighbour under another, which would split
    one search's history in two without saying so.

    Parameters
    ----------
    tracked
        The question, and the names its history is stored under

    endpoint
        The endpoint to ask. Must be one of `tracked.endpoints`; this is not checked,
        because asking a requirement about an endpoint it does not list is a
        reasonable thing to do by hand when debugging.

    Returns
    -------
    :
        The requirement, named and roled so that one endpoint's history is its own
    """
    return endpoint_requirement(
        endpoint,
        tracked.query,
        name_stem=tracked.stem,
        role_stem=tracked.role_stem,
        lineage=tracked.lineage,
    )


def facade_for(endpoint: Endpoint) -> SearchAPIFacade:
    """
    Look up the facade which asks `endpoint` about its project

    Parameters
    ----------
    endpoint
        The endpoint to look up

    Returns
    -------
    :
        Its facade

    Raises
    ------
    ValueError
        esmporium has no facade pairing this host with this project. The message lists
        every host it does have, and which projects each supports.
    """
    return INBUILT_SEARCH_API_FACADE_STORE.get_api_facade_for_project_from_host(
        endpoint.project, endpoint.host
    )


def single_facade_selector(endpoint: Endpoint) -> SearchAPIFacadeSelector:
    """
    Build a selector which offers `endpoint` and nothing else

    What a *tracked search* uses, and the reason is this module's whole premise: one
    endpoint, one history, so one search has to be asked of exactly one host.

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
    return build_list_selector([facade_for(endpoint)])


def facade_selector(endpoints: Sequence[Endpoint]) -> SearchAPIFacadeSelector:
    """
    Build a selector which offers every one of `endpoints`, in order

    The counterpart to [single_facade_selector][(m).] for the searches which are *not*
    tracked per endpoint -- today, the parent walk. A walk writes links between
    datasets rather than a history under a requirement's name, so there is nothing to
    keep apart and every reason to take the failover: an ancestor is a dataset we have
    never seen, and it can be published on a host which never answered the search that
    found its child.

    Parameters
    ----------
    endpoints
        The endpoints to offer, best first. May name more than one project.

    Returns
    -------
    :
        A selector over their facades

    Raises
    ------
    ValueError
        esmporium has no facade pairing one of these hosts with its project
    """
    return build_list_selector([facade_for(endpoint) for endpoint in endpoints])


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
