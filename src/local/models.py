"""
Which models each endpoint can actually give you, and how much of each

The coverage table counts datasets, which answers "how much of this search can I get
here" but not "which models". Those come apart more than you would expect:
`ACCESS-CM2` is served by five of our CMIP6 endpoints, but `nsc`, `ornl` and `nci`
offer ten variants of it while `dkrz` offers three and `ceda` two. A tick in a column
would call all five "has it".

So a cell here is the number of that model's variants the endpoint can serve, not a
tick. One row per model, one column per endpoint, which makes a model's row readable
across and an endpoint's column readable down.

A variant *satisfies* when the endpoint returned at least one non-retracted dataset
for it, which is the same "usable" rule [local.coverage][] applies: a retracted
dataset is not data you can work with. A model satisfies when any of its variants do.

**A limitation to know before a requirement grows a second leaf.** Our requirement
has one leaf, so every dataset an endpoint returned matches the query by
construction, and "this variant satisfies the requirement" and "this variant appeared
in the results" are the same statement. They stop being the same as soon as a
requirement asks for two things at once -- `historical` *and* `piControl` for one
variant, say -- because then a variant can appear in the results while satisfying only
half of what was asked. This module would overstate in that case. Doing it properly
needs to know which leaf each returned dataset answered, and `SearchRunVersion`
records no link to the leaf which found it: the same gap in esmporium's schema that
made [local.endpoints][] carry the endpoint in a leaf's role.

esmporium does have a solver which answers satisfaction properly
([esmporium.requirements.solve][]), and it is not usable here: it reads through a
`DatabaseCatalogue`, which is built from the shared `Dataset` rows and has no notion
of which run or which endpoint found them. It would answer "does this model satisfy
the requirement given everything every endpoint has ever told us", which is a
different question from the one this table asks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from local.endpoints import EndpointResult


@dataclass(frozen=True)
class ModelSupport:
    """Which models each endpoint of one project can serve, and how many variants"""

    endpoints: tuple[str, ...]
    """The tags of the endpoints which answered, in the order they were given"""

    silent: tuple[str, ...]
    """
    The tags of the endpoints which did not answer

    Named rather than given an empty column. A column of dashes would read as "this
    endpoint has none of these models", and what happened is that we could not ask it.
    """

    models: tuple[str, ...]
    """
    Every model at least one answering endpoint can serve, sorted for reading

    Sorted case-insensitively, so that `CanESM5` sits beside `CanESM5-1` instead of
    being thrown after `CESM2` by where ASCII puts a lower-case letter.
    """

    variants: Mapping[str, Mapping[str, int]]
    """How many variants of each model each endpoint can serve, keyed model then tag"""

    @property
    def n_models(self) -> int:
        """How many models satisfy at one endpoint or more"""
        return len(self.models)

    def satisfied_at(self, tag: str) -> int:
        """
        How many models this one endpoint can serve at least one variant of

        Parameters
        ----------
        tag
            The endpoint's tag

        Returns
        -------
        :
            The count, which is that endpoint's column total
        """
        return sum(1 for model in self.models if tag in self.variants[model])


def model_support(results: Sequence[EndpointResult]) -> ModelSupport:
    """
    Work out which models each endpoint of one project can serve

    Parameters
    ----------
    results
        Every endpoint asked about one project. Endpoints which did not answer are
        named in `silent` and given no column.

        Pass one project's endpoints at a time: a tag only identifies an endpoint
        within a project.

    Returns
    -------
    :
        The support, holding only counts and names, so it renders anywhere
    """
    seen: dict[str, dict[str, set[str]]] = {}
    for result in results:
        if result.summary is None:
            continue

        for version in result.summary.current:
            if version.retracted:
                continue

            # A facet esmporium did not record reads as `-` rather than crashing the
            # morning report. `model` is not nullable on `Dataset`, so this is
            # defensive rather than expected.
            model = version.facets.get("model") or "-"
            variant = version.facets.get("variant_label") or "-"
            # A set, because one variant can arrive several times over: at more than
            # one version, and at more than one grid. It is one variant each time.
            seen.setdefault(model, {}).setdefault(result.endpoint.tag, set()).add(
                variant
            )

    return ModelSupport(
        endpoints=tuple(
            result.endpoint.tag for result in results if result.summary is not None
        ),
        silent=tuple(
            result.endpoint.tag for result in results if result.summary is None
        ),
        models=tuple(sorted(seen, key=str.lower)),
        variants={
            model: {tag: len(variants) for tag, variants in by_tag.items()}
            for model, by_tag in seen.items()
        },
    )
