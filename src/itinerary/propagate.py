"""Propagation — change impact analysis and graph repair.

A Change is something Atlas now says that it did not say before. The Impact
traces the consequences through the dependency graph. apply_change returns a
COPY of the graph — the original is never modified.

Edges are one-way by construction: downstream_of(node_key) follows the
dependency direction, so a change to the return can never strand the outbound.
"""

import copy
from dataclasses import dataclass, field

from src.itinerary.graph import ItineraryGraph, build_chain, Dependency


# Change kinds
PRICE = "PRICE"
SCHEDULE = "SCHEDULE"
GONE = "GONE"
CEILING = "CEILING"


@dataclass
class Change:
    """Something Atlas now says that it did not say before."""
    node_key: str = ""           # the FlightNode key (cache_key#routing_index)
    kind: str = ""               # PRICE | SCHEDULE | GONE | CEILING
    was: float = None            # the fare, for PRICE; the ceiling, for CEILING
    now: float = None
    member: str = None           # who moved their ceiling, for CEILING
    cost_ref: str = None
    replacement: object = None   # the FlightNode Atlas returned THIS TIME
    detail: str = ""

    def __repr__(self):
        if self.kind == PRICE:
            return "Change(%s %.2f→%.2f)" % (self.kind, self.was or 0, self.now or 0)
        if self.kind == GONE:
            return "Change(GONE %s)" % self.node_key
        return "Change(%s %s)" % (self.kind, self.node_key)


@dataclass
class Impact:
    """The consequences of a change, traced through the dependency graph."""
    change: Change = None
    total_before: float = None
    total_after: float = None
    downstream: list = field(default_factory=list)
    breached: list = field(default_factory=list)
    structural: list = field(default_factory=list)
    replanable: list = field(default_factory=list)
    consensus_invalidated: bool = False
    still_feasible: bool = True

    def narrate(self):
        """The chain, in order, for the UI to render one line at a time.

        Produces consequences, not a delta.
        """
        lines = []
        if self.change and self.change.kind == PRICE:
            if self.total_after is not None and self.total_before is not None:
                delta = round(self.total_after - self.total_before, 2)
                sign = "+" if delta >= 0 else ""
                lines.append("fare $%.2f → $%.2f (%s%.2f)" % (
                    self.total_before, self.total_after, sign, delta))
        elif self.change and self.change.kind == GONE:
            lines.append("routing no longer offered")
        elif self.change and self.change.kind == CEILING:
            if self.change.member:
                lines.append("%s re-granted a ceiling of $%.2f" % (
                    self.change.member, self.change.now or 0))

        for item in self.downstream:
            lines.append("downstream: %s" % item)

        for item in self.breached:
            lines.append("%s exceeds the ceiling they granted" % item)

        for item in self.structural:
            lines.append("structural: %s" % item)

        if self.consensus_invalidated:
            lines.append("group consensus invalidated")

        if not self.still_feasible:
            lines.append("re-planning the dependent itinerary")

        if self.still_feasible and not self.breached and not self.structural:
            lines.append("the trip still holds")

        return lines


def _find_leg_index(graph, node_key):
    """Find the index of the leg matching the given node key."""
    for i, leg in enumerate(graph.legs):
        if leg.key == node_key:
            return i
    return -1


def downstream_of(graph, node_key):
    """Leg keys downstream of the given node, following dependency direction.

    Edges are one-way by construction: from_leg → to_leg. A change to the
    return can never strand the outbound.
    """
    idx = _find_leg_index(graph, node_key)
    if idx < 0:
        return []

    result = []
    for dep in graph.dependencies:
        if dep.from_leg == idx:
            to_idx = dep.to_leg
            if 0 <= to_idx < len(graph.legs):
                result.append(graph.legs[to_idx].key)
    return result


def apply_change(graph, change):
    """A COPY of the graph with the change applied. The original is untouched.

    The change is applied by SWAPPING IN the node Atlas actually returned,
    never by editing a price in place. Editing would leave the graph reporting
    one number while its cost_ref resolved to another.

    A change carrying no replacement leaves the graph alone; the caller
    learns that from still_feasible.
    """
    if change is None or change.replacement is None:
        return graph  # unchanged — no replacement to swap

    idx = _find_leg_index(graph, change.node_key)
    if idx < 0:
        return graph  # node not found — leave alone

    # Build a copy with the replacement leg swapped in
    new_legs = list(graph.legs)
    new_legs[idx] = change.replacement

    try:
        new_graph = build_chain(
            new_legs,
            party_size=graph.party_size,
            destination_name=graph.destination_name,
            vibe_score=graph.vibe_score,
        )
    except ValueError:
        return graph  # structural failure — leave unchanged

    return new_graph


def propagate(graph, change, ceilings=None):
    """Graph + change → (Impact, repaired_graph).

    Traces the impact through dependencies, checks ceilings, and returns
    the repaired graph (which may be identical if no replacement was given).
    """
    impact = Impact(change=change)
    impact.total_before = round(graph.group_total, 2)

    # Find the affected leg
    idx = _find_leg_index(graph, change.node_key)

    if idx >= 0:
        # The affected leg is replanable (includes the node that moved)
        impact.replanable.append(change.node_key)

    # Downstream legs
    if idx >= 0:
        downstream_keys = downstream_of(graph, change.node_key)
        impact.downstream.extend(downstream_keys)
        # Downstream legs are also replanable
        impact.replanable.extend(downstream_keys)

    # Apply the change
    repaired = apply_change(graph, change)
    impact.total_after = round(repaired.group_total, 2)

    # Check structural viability
    if not repaired.feasible:
        impact.still_feasible = False
        for v in repaired.violations:
            impact.structural.append(v)

    # Check ceilings
    if ceilings:
        per_person = repaired.per_person
        for ceiling in ceilings:
            member_name = getattr(ceiling, "member", str(ceiling))
            amount = getattr(ceiling, "amount", ceiling)
            if per_person > amount:
                impact.breached.append(member_name)
                impact.consensus_invalidated = True

    # GONE change always invalidates
    if change.kind == GONE:
        impact.still_feasible = False
        impact.consensus_invalidated = True

    # CEILING change invalidates consensus if it breaches
    if change.kind == CEILING:
        if change.now is not None and repaired.per_person > change.now:
            impact.consensus_invalidated = True
            if change.member:
                impact.breached.append(change.member)

    return (impact, repaired)
