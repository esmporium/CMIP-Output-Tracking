"""
How a parent walk is laid out for reading

The same division of labour [local.download_formatting][] has: esmporium walks the
chain and writes the links, we arrange what it reports. Everything here is a pure
function of the [local.parents.ParentWalk][]s handed back, so these tests need no
database and no network.

What the layout is trying to say is narrower than it looks. A walk's own numbers --
links written, hops climbed -- are the easy part. The part worth the space is
everything esmporium does **not** treat as a failure but which still means an ancestor
we will not have, or one we chose rather than read: a release picked by a rule of
ours, a control which does not publish one of the four variables, a parent the matcher
could not tell from its neighbour. None of those stop a walk, so none of them would
otherwise be seen.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from local.summary_formatting import _capped, render_table

if TYPE_CHECKING:
    from collections.abc import Sequence

    from esmporium.db import (
        AmbiguousParent,
        AmbiguousParentVersion,
        ParentSimulation,
        ParentVersionDecision,
        VariableGap,
    )

    from local.parents import ParentWalk

_CHOICE_NOTES: dict[str, str] = {
    "only_version": "we hold one release, so nothing was chosen",
    "child_version": "the release sharing the child's version string",
    "defaulted_to_newest": "no release shared the child's, so the newest was taken",
}
"""
What each [esmporium.db.ParentVersionChoice][] means, in a sentence

Keyed by the enum's *value* rather than by the member, so a new choice upstream shows
up as its bare value rather than as a `KeyError` in the 6am email. `only_version` is
listed even though it is not a decision, because a table of decisions which silently
omitted some of its rows would be the worse surprise.
"""


def _simulation(parent: ParentSimulation) -> str:
    """
    Name one simulation the way a person refers to it

    Parameters
    ----------
    parent
        The simulation to name

    Returns
    -------
    :
        Model, experiment and variant, e.g. `'UKESM1-3-LL piControl r1i1p1f1'`

    Examples
    --------
    >>> from esmporium.db import ParentSimulation
    >>> print(
    ...     _simulation(
    ...         ParentSimulation(
    ...             project="CMIP7",
    ...             model="UKESM1-3-LL",
    ...             experiment="piControl",
    ...             variant_label="r1i1p1f1",
    ...         )
    ...     )
    ... )
    UKESM1-3-LL piControl r1i1p1f1
    """
    return f"{parent.model} {parent.experiment} {parent.variant_label}"


def _terminal_lines(
    terminals: Sequence[ParentSimulation], *, max_rows: int
) -> list[str]:
    """
    Render where the chains stopped

    Parameters
    ----------
    terminals
        The simulations the walk ended at, as reported

    max_rows
        How many to show before folding the rest away

    Returns
    -------
    :
        The lines, or `[]` when no chain reached an end
    """
    if not terminals:
        return []

    rows = sorted(
        {(each.model, each.experiment, each.variant_label) for each in terminals}
    )
    kept, hidden = _capped(rows, max_rows)
    lines = [
        f"  Chains stopped at ({len(rows)}):",
        render_table(["model", "experiment", "variant"], kept, indent="    "),
    ]
    if hidden:
        lines.append(f"    ... and {hidden} more")

    return lines


def _decision_lines(
    decisions: Sequence[ParentVersionDecision], *, max_rows: int
) -> list[str]:
    """
    Render the parent releases a rule of ours picked

    Given its own section rather than folded into a count, because it is the one place
    in this report where the answer rests on a judgement we made instead of on
    something the archive published. See [esmporium.db.ParentVersionChoice][].

    Parameters
    ----------
    decisions
        The decisions the walk reported

    max_rows
        How many to show before folding the rest away

    Returns
    -------
    :
        The lines, or `[]` when nothing had to be decided
    """
    if not decisions:
        return []

    rows = [
        [
            str(each.child_dataset_version_id),
            _simulation(each.parent),
            str(each.chosen_dataset_version_id),
            str(len(each.passed_over_dataset_version_ids)),
            _CHOICE_NOTES.get(str(each.choice.value), str(each.choice.value)),
        ]
        for each in decisions
    ]
    kept, hidden = _capped(rows, max_rows)
    lines = [
        f"  Parent release decided by us ({len(rows)}):",
        render_table(
            ["child id", "parent", "used id", "passed over", "why"],
            kept,
            indent="    ",
            right=("passed over",),
        ),
        # Said out loud because the dates read backwards. The releases passed over
        # are often all *newer* than the child, which looks as though a run branched
        # from data that did not exist yet. A version string is a publication date,
        # not a run date: the branch happened on the centre's own disks before either
        # end of it was published.
        "    A version string is a publication date, not a run date, so a release "
        "newer than the child is normal.",
    ]
    if hidden:
        lines.append(f"    ... and {hidden} more")

    return lines


def _gap_lines(gaps: Sequence[VariableGap], *, max_rows: int) -> list[str]:
    """
    Render the variables an ancestor we found does not publish

    Parameters
    ----------
    gaps
        The gaps the walk reported

    max_rows
        How many to show before folding the rest away

    Returns
    -------
    :
        The lines, or `[]` when every ancestor published everything asked of it
    """
    if not gaps:
        return []

    rows = [
        [str(each.child_dataset_version_id), _simulation(each.parent), each.variable]
        for each in gaps
    ]
    kept, hidden = _capped(rows, max_rows)
    lines = [
        f"  Ancestor does not publish the variable ({len(rows)}):",
        render_table(["child id", "parent", "variable"], kept, indent="    "),
    ]
    if hidden:
        lines.append(f"    ... and {hidden} more")

    return lines


def _ambiguous_lines(
    ambiguous: Sequence[AmbiguousParent],
    ambiguous_versions: Sequence[AmbiguousParentVersion],
    *,
    max_rows: int,
) -> list[str]:
    """
    Render the parents nothing could choose between

    Both kinds in one section, because the difference between them -- an ambiguous
    dataset and an ambiguous release of a chosen dataset -- does not change what it
    means for us: the walk did not climb through the link, so that ancestor is
    missing.

    Parameters
    ----------
    ambiguous
        Parent datasets the matcher could not choose

    ambiguous_versions
        Parent releases a chooser declined to pick

    max_rows
        How many to show before folding the rest away

    Returns
    -------
    :
        The lines, or `[]` when nothing was ambiguous
    """
    rows = [
        [
            str(each.child_dataset_version_id),
            _simulation(each.parent),
            "dataset",
            each.reason,
        ]
        for each in ambiguous
    ] + [
        [
            str(each.child_dataset_version_id),
            _simulation(each.parent),
            "release",
            each.reason,
        ]
        for each in ambiguous_versions
    ]
    if not rows:
        return []

    kept, hidden = _capped(rows, max_rows)
    lines = [
        f"  Could not be chosen, so not climbed through ({len(rows)}):",
        render_table(["child id", "parent", "what", "why"], kept, indent="    "),
    ]
    if hidden:
        lines.append(f"    ... and {hidden} more")

    return lines


def _walk_block(walk: ParentWalk, *, max_rows: int) -> list[str]:
    """
    Render one question's walk

    Parameters
    ----------
    walk
        The walk to render

    max_rows
        How many rows of each table to show before folding the rest away

    Returns
    -------
    :
        The lines of the block
    """
    lines = [f"{walk.label} -- ancestry"]

    if walk.result is None:
        lines.extend(
            [
                f"  FAILED after starting from {walk.n_started_from} dataset "
                "version(s): " + (walk.failure or "no reason given"),
                "  Whatever the walk managed before it gave up is already recorded "
                "-- it commits once per hop -- so the next run carries on from "
                "there rather than starting again.",
            ]
        )

        return lines

    result = walk.result
    lines.append(
        f"  Walked back from {walk.n_started_from} dataset version(s): "
        f"{len(result.linked)} link(s) written, {result.hops} hop(s), "
        f"{len(result.searched)} search(es) fired"
    )
    if not walk.n_started_from:
        lines.append(
            "  Nothing to walk: this question's search returned no dataset "
            "versions, so there was no ancestry to follow."
        )

    lines.extend(_terminal_lines(result.terminals, max_rows=max_rows))
    lines.extend(_decision_lines(result.version_decisions, max_rows=max_rows))
    lines.extend(_gap_lines(result.variable_gaps, max_rows=max_rows))
    lines.extend(
        _ambiguous_lines(result.ambiguous, result.ambiguous_versions, max_rows=max_rows)
    )

    return lines


def format_parent_walks(walks: Sequence[ParentWalk], *, max_rows: int = 50) -> str:
    """
    Render every parent walk this morning did

    Parameters
    ----------
    walks
        One per question which has a lineage, in the order to show them

    max_rows
        How many rows of each table to show before folding the rest away

    Returns
    -------
    :
        The block, or `''` when no question has a lineage

        Empty rather than a sentence, so a report covering only questions which
        stand alone does not grow a heading about ancestry. Ends in exactly one
        newline when it is not empty, matching
        [local.summary_formatting.format_report][].
    """
    if not walks:
        return ""

    blocks = ["\n".join(_walk_block(walk, max_rows=max_rows)) for walk in walks]

    return "\n\n".join(blocks) + "\n"
