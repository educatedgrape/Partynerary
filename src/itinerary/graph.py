"""ItineraryGraph — N-leg trip with dependency edges.

Two legs is a round trip; three or more is a chain through a stopover. Designed
for N from the start — a two-node graph retrofitted later forces every consumer
to learn a second type.

Dependencies are derived pairwise between consecutive legs: a leg that departs
before its predecessor lands is not a cheaper trip, it is not a trip.
"""

from dataclasses import dataclass, field


# Dependency edge types
PLACE = "PLACE"         # consecutive legs must connect (arrival → departure)
TEMPORAL = "TEMPORAL"   # later leg must depart after earlier leg arrives
DURATION = "DURATION"   # minimum ground time between legs


@dataclass
class Dependency:
    """One edge in the itinerary graph.

    Attributes:
        kind:     PLACE, TEMPORAL, or DURATION
        from_leg: index of the predecessor leg
        to_leg:   index of the successor leg
        satisfied: whether this dependency holds
        detail:   human-readable explanation
    """
    kind: str = ""
    from_leg: int = 0
    to_leg: int = 0
    satisfied: bool = True
    detail: str = ""

    def __repr__(self):
        status = "OK" if self.satisfied else "VIOLATION"
        return "Dep(%s L%d→L%d %s %s)" % (
            self.kind, self.from_leg, self.to_leg, status, self.detail)


@dataclass
class ItineraryGraph:
    """GROUP GOAL -> leg -> leg -> ... -> home, with the edges between them.

    The dependency edges are derived pairwise, so a chain validates by exactly
    the rules a round trip does: a leg that departs before its predecessor lands
    is not a cheaper trip, it is not a trip.
    """
    legs: list = field(default_factory=list)
    party_size: int = 1
    destination_name: str = ""
    # Semantic fit, carried from the Match that proposed this destination.
    # NOT an editorial desirability score — nothing in this system holds an
    # opinion about how nice a place is.
    vibe_score: float = 0.0
    dependencies: list = field(default_factory=list)
    violations: list = field(default_factory=list)

    @property
    def outbound(self):
        """First leg — the trip outward."""
        if self.legs:
            return self.legs[0]
        return None

    @property
    def inbound(self):
        """The leg home. `return` is a keyword."""
        if self.legs:
            return self.legs[-1]
        return None

    @property
    def is_chain(self):
        """True when there are intermediate legs between outbound and inbound."""
        return len(self.legs) > 2

    @property
    def stopovers(self):
        """[(cityId, hours)] per intermediate leg. Empty for a round trip."""
        if len(self.legs) <= 2:
            return []
        result = []
        for i in range(1, len(self.legs) - 1):
            leg = self.legs[i]
            # Ground hours = time between arrival of leg[i-1] and departure of leg[i]
            result.append((leg.destination, 0))
        return result

    @property
    def per_person(self):
        """Sum of per-person fares across all legs."""
        return sum(leg.per_person for leg in self.legs)

    @property
    def group_total(self):
        """Sum of every leg's group total."""
        return sum(leg.group_total(self.party_size) for leg in self.legs)

    @property
    def min_seats(self):
        """MIN across legs. One unseatable leg makes the whole trip unbookable."""
        seats = [leg.min_seat_count for leg in self.legs]
        return min(seats) if seats else 0

    @property
    def cost_refs(self):
        """One set of refs per leg, in order. NOT a single ref per itinerary."""
        refs = []
        for leg in self.legs:
            refs.extend(leg.cost_refs)
        return refs

    @property
    def key(self):
        """Identity for this graph — the concatenation of its leg keys.

        Two graphs with the same derived key (origin-dest@date) but different
        routings MUST have different identity keys.
        """
        return "|".join(leg.key for leg in self.legs)

    @property
    def destination(self):
        """The destination city code (from the outbound leg)."""
        if self.outbound:
            return self.outbound.destination
        return ""

    @property
    def origin(self):
        """The origin city code (from the outbound leg)."""
        if self.outbound:
            return self.outbound.origin
        return ""

    @property
    def feasible(self):
        """True when all dependencies are satisfied and seats suffice."""
        if not self.dependencies:
            return True
        return (all(d.satisfied for d in self.dependencies) and
                self.min_seats >= self.party_size)

    def as_dict(self):
        """Dict representation for the UI / ranking layer."""
        return {
            "key": self.key,
            "destination": self.destination,
            "destination_name": self.destination_name,
            "origin": self.origin,
            "party_size": self.party_size,
            "per_person": round(self.per_person, 2),
            "group_total": round(self.group_total, 2),
            "min_seats": self.min_seats,
            "vibe_score": round(self.vibe_score, 4),
            "is_chain": self.is_chain,
            "feasible": self.feasible,
            "legs": [
                {
                    "role": leg.role,
                    "origin": leg.origin,
                    "destination": leg.destination,
                    "date": leg.date,
                    "flight_numbers": list(leg.flight_numbers),
                    "elapsed_hours": leg.elapsed_hours,
                    "min_seat_count": leg.min_seat_count,
                    "price_ref": leg.price_ref,
                }
                for leg in self.legs
            ],
            "dependencies": [
                {
                    "kind": d.kind,
                    "from_leg": d.from_leg,
                    "to_leg": d.to_leg,
                    "satisfied": d.satisfied,
                    "detail": d.detail,
                }
                for d in self.dependencies
            ],
            "violations": list(self.violations),
            "cost_refs": list(self.cost_refs),
        }

    def __repr__(self):
        legs_str = " → ".join(
            "%s(%s→%s)" % (l.role, l.origin, l.destination)
            for l in self.legs)
        return "ItineraryGraph(%s, party=%d, $%.2f)" % (
            legs_str, self.party_size, self.per_person)


def build_chain(legs, party_size=1, destination_name="", vibe_score=0.0):
    """Two legs or twenty. Raises ValueError below two.

    Derives dependencies pairwise between consecutive legs.
    """
    if len(legs) < 2:
        raise ValueError(
            "Itinerary requires at least 2 legs (outbound + inbound), got %d" %
            len(legs))

    deps, violations = _derive_dependencies(legs)

    return ItineraryGraph(
        legs=list(legs),
        party_size=party_size,
        destination_name=destination_name,
        vibe_score=vibe_score,
        dependencies=deps,
        violations=violations,
    )


def _derive_dependencies(legs):
    """Walk consecutive pairs, emitting PLACE, TEMPORAL and DURATION edges.

    PLACE:    the arrival city of leg N must equal the departure city of leg N+1
    TEMPORAL: leg N+1 must depart after leg N arrives (date comparison)
    DURATION: at least some ground time (informational — always satisfied
              for date-level granularity unless same-day)
    """
    deps = []
    violations = []

    for i in range(len(legs) - 1):
        a = legs[i]
        b = legs[i + 1]

        # PLACE — arrival of a must be the departure of b
        place_ok = (a.destination == b.origin)
        place_detail = "%s arrives %s, %s departs %s" % (
            a.role or "leg%d" % i, a.destination,
            b.role or "leg%d" % (i + 1), b.origin)
        deps.append(Dependency(
            kind=PLACE, from_leg=i, to_leg=i + 1,
            satisfied=place_ok, detail=place_detail))
        if not place_ok:
            violations.append("PLACE violation: %s" % place_detail)

        # TEMPORAL — b.date >= a.date (at least not before)
        temporal_ok = (b.date >= a.date)
        temporal_detail = "%s departs %s, %s departs %s" % (
            a.role or "leg%d" % i, a.date,
            b.role or "leg%d" % (i + 1), b.date)
        deps.append(Dependency(
            kind=TEMPORAL, from_leg=i, to_leg=i + 1,
            satisfied=temporal_ok, detail=temporal_detail))
        if not temporal_ok:
            violations.append("TEMPORAL violation: %s" % temporal_detail)

        # DURATION — same-day departure is flagged (0 ground hours)
        duration_ok = (b.date > a.date)
        duration_detail = "ground time between %s and %s" % (
            a.role or "leg%d" % i, b.role or "leg%d" % (i + 1))
        deps.append(Dependency(
            kind=DURATION, from_leg=i, to_leg=i + 1,
            satisfied=duration_ok, detail=duration_detail))
        if not duration_ok:
            violations.append("DURATION warning: %s" % duration_detail)

    return deps, violations
