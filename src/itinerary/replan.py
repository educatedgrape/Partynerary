"""Re-planning — cheapest-change-first exploration and the repair loop.

Walks the graph and varies exactly ONE dimension at a time. Moving one leg
disturbs the group's agreement less than moving the whole trip, so the
agents are offered the smallest repair before the largest.

Nothing that breaches a ceiling is offered at all. Filter before returning,
not in the UI — a ceiling becomes erodable by majority in a moment of
inconvenience if the group gets to vote on something one of them cannot afford.
"""

from dataclasses import dataclass, field

from src.itinerary.propagate import Change, Impact, propagate
from src.discovery.routes import search_nodes


# Re-plan dimensions, ordered cheapest-change-first
RETURN_SWAP = "return"
OUTBOUND_SWAP = "outbound"
STOPOVER_SWAP = "stopover"   # vary the intermediate city in a 3-leg chain
DESTINATION_SWAP = "destination"

KIND_LABEL = {
    RETURN_SWAP:      "same place, different return",
    OUTBOUND_SWAP:    "same place, different outbound",
    STOPOVER_SWAP:    "same endpoints, different stopover",
    DESTINATION_SWAP: "different destination",
}

# Termination constants
MAX_REPLAN_ROUNDS = 4      # per change
MAX_SESSION_ROUNDS = 12    # across the whole session

# Stop reasons
EXHAUSTED = "exhausted"   # ran out of dimensions to vary
BLOCKED = "blocked"       # one constraint killed every candidate
BUDGET = "budget"         # round cap hit with candidates unexplored


@dataclass
class Alternative:
    """One repair option the graph can adopt."""
    kind: str = ""
    graph: object = None
    delta_vs_broken: float = None   # per-person, against the trip that broke
    note: str = ""
    rejected_for: list = field(default_factory=list)

    def __repr__(self):
        label = KIND_LABEL.get(self.kind, self.kind)
        delta_str = ""
        if self.delta_vs_broken is not None:
            sign = "+" if self.delta_vs_broken >= 0 else ""
            delta_str = " Δ%s%.2f" % (sign, self.delta_vs_broken)
        return "Alternative(%s%s)" % (label, delta_str)


@dataclass
class LoopOutcome:
    """Why the repair loop stopped, and the best thing it found.

    This is a RESULT, not an error. 'I could not solve this, and here is
    exactly what blocked it' is a successful outcome for an unsolvable
    constraint set.
    """
    stopped_because: str = ""       # EXHAUSTED | BLOCKED | BUDGET
    rounds_used: int = 0
    best: object = None             # best surviving ItineraryGraph, or None
    blocking_member: str = None     # set when stopped_because == BLOCKED
    shortfall: float = None         # how far the best option missed, per person
    detail: str = ""

    def narrate(self):
        """One line a human can act on. Always names numbers when it has them."""
        if self.stopped_because == BLOCKED and self.blocking_member:
            if self.shortfall is not None and self.best is not None:
                dest = getattr(self.best, "destination_name", "") or \
                       getattr(self.best, "destination", "unknown")
                return ("After %d round(s), nothing cleared %s's ceiling. "
                        "Closest was %s — short by $%.2f." % (
                            self.rounds_used, self.blocking_member,
                            dest, self.shortfall))
            return ("After %d round(s), %s's ceiling blocked every option."
                    % (self.rounds_used, self.blocking_member))
        if self.stopped_because == EXHAUSTED:
            return ("After %d round(s), no more alternatives exist."
                    % self.rounds_used)
        if self.stopped_because == BUDGET:
            return ("After %d round(s), the repair budget is spent."
                    % self.rounds_used)
        return "Repair loop ended after %d round(s)." % self.rounds_used


def explore(client, broken, ceilings, outbound_date, return_dates,
            party_size, destinations=None, stopover_candidates=None):
    """Walk the graph and vary exactly ONE dimension at a time.

    The ordering is cheapest-change-first:
      1. return_swap     — same place, different return date
      2. outbound_swap   — same place, different outbound routing
      3. stopover_swap   — same endpoints, different intermediate city
      4. destination_swap — different destination entirely

    Nothing that breaches a ceiling is offered at all.
    Every alternative is a complete ItineraryGraph, priced from refs Atlas
    returned this run, and re-checked against every ceiling.

    stopover_candidates: list of dicts with 'cityId' and 'cityName' for
        stopover_swap. Only used when broken graph has 3+ legs (is_chain).
    """
    from src.itinerary.graph import build_chain

    alternatives = []
    origin = broken.origin
    dest = broken.destination

    # Collect ceilings as floats
    ceiling_amounts = []
    if ceilings:
        for c in ceilings:
            amount = getattr(c, "amount", c)
            ceiling_amounts.append(float(amount))

    # 1. Return swaps — vary the return date, keep outbound
    for ret_date in return_dates:
        ret_nodes, ret_err = search_nodes(
            client, "inbound", dest, origin, ret_date, party_size,
            drop_unseatable=True)
        if ret_err or not ret_nodes:
            continue

        for ret_node in ret_nodes:
            try:
                new_graph = build_chain(
                    [broken.outbound, ret_node],
                    party_size=party_size,
                    destination_name=broken.destination_name,
                    vibe_score=broken.vibe_score,
                )
            except ValueError:
                continue

            per_person = new_graph.per_person
            rejected = _check_ceilings(per_person, ceilings)
            if rejected:
                continue

            delta = round(per_person - broken.per_person, 2)
            alternatives.append(Alternative(
                kind=RETURN_SWAP,
                graph=new_graph,
                delta_vs_broken=delta,
                note="return %s" % ret_date,
            ))

    # 2. Outbound swaps — vary the outbound routing, keep return
    out_nodes, out_err = search_nodes(
        client, "outbound", origin, dest, outbound_date, party_size,
        drop_unseatable=True)
    if not out_err and out_nodes:
        for out_node in out_nodes:
            # Skip the same routing
            if out_node.key == broken.outbound.key:
                continue
            try:
                new_graph = build_chain(
                    [out_node, broken.inbound],
                    party_size=party_size,
                    destination_name=broken.destination_name,
                    vibe_score=broken.vibe_score,
                )
            except ValueError:
                continue

            per_person = new_graph.per_person
            rejected = _check_ceilings(per_person, ceilings)
            if rejected:
                continue

            delta = round(per_person - broken.per_person, 2)
            alternatives.append(Alternative(
                kind=OUTBOUND_SWAP,
                graph=new_graph,
                delta_vs_broken=delta,
                note="outbound %s" % "+".join(out_node.flight_numbers),
            ))

    # 3. Stopover swaps — vary the intermediate city in a 3-leg chain
    if broken.is_chain and stopover_candidates:
        from src.discovery.multileg import search_multileg_routes
        multileg_options, ml_errors = search_multileg_routes(
            client, origin, stopover_candidates, dest,
            outbound_date, return_dates, party_size,
            min_stopover_days=2, limit=5)
        for ml_opt in multileg_options:
            graph = ml_opt.graph
            if graph.key == broken.key:
                continue  # Skip same graph
            per_person = graph.per_person
            rejected = _check_ceilings(per_person, ceilings)
            if rejected:
                continue
            delta = round(per_person - broken.per_person, 2)
            alternatives.append(Alternative(
                kind=STOPOVER_SWAP,
                graph=graph,
                delta_vs_broken=delta,
                note="stopover %s" % ml_opt.stopover_city_id,
            ))

    # 4. Destination swaps — vary the destination entirely
    if destinations:
        from src.discovery.sweep import sweep, return_dates_for
        for alt_dest in destinations:
            if alt_dest == dest:
                continue
            alt_ret_dates = return_dates_for(outbound_date)
            alt_trips, alt_errors = sweep(
                client, origin, outbound_date, alt_ret_dates,
                party_size, destinations=[alt_dest])
            for trip in alt_trips:
                per_person = trip.per_person
                rejected = _check_ceilings(per_person, ceilings)
                if rejected:
                    continue

                delta = round(per_person - broken.per_person, 2)
                alternatives.append(Alternative(
                    kind=DESTINATION_SWAP,
                    graph=trip,
                    delta_vs_broken=delta,
                    note="destination %s" % alt_dest,
                ))

    # Sort by delta_vs_broken ascending (cheapest change first)
    alternatives.sort(key=lambda a: a.delta_vs_broken or 0)

    return alternatives


def _check_ceilings(per_person, ceilings):
    """Return list of members who cannot afford this per_person price.

    Returns empty list if everyone can afford it.
    """
    if not ceilings:
        return []
    rejected = []
    for c in ceilings:
        member = getattr(c, "member", "unknown")
        if hasattr(c, "amount"):
            amount = c.amount
        else:
            amount = float(c)
        if per_person > amount:
            rejected.append(member)
    return rejected


def should_stop(round_result, previous, ceilings):
    """Returns a stop reason, or None to continue.

    - Zero new candidates → EXHAUSTED immediately.
    - Every candidate breaches the SAME member → BLOCKED.
    """
    if not round_result:
        return EXHAUSTED

    # Check if every candidate breaches the same member
    all_breach_sets = []
    for alt in round_result:
        if alt.rejected_for:
            all_breach_sets.append(set(alt.rejected_for))

    if all_breach_sets:
        # Intersection of all breach sets
        common = all_breach_sets[0]
        for s in all_breach_sets[1:]:
            common = common & s
        if common and len(all_breach_sets) == len(round_result):
            return BLOCKED

    return None


def repair_loop(client, broken, ceilings, outbound_date, return_dates,
                party_size, destinations=None, session_rounds_used=0,
                stopover_candidates=None):
    """Run the repair loop until a fix is found or termination fires.

    Returns a LoopOutcome — a RESULT, not an error.

    Args:
        client:               AtlasClient
        broken:               the ItineraryGraph that broke
        ceilings:             list of Ceiling objects
        outbound_date:        departure date
        return_dates:         list of return dates
        party_size:           number of travellers
        destinations:         candidate destinations for dest swaps
        session_rounds_used:  rounds already consumed in this session
        stopover_candidates:  list of dicts with 'cityId'/'cityName' for
                              stopover_swap (only used for 3-leg chains)
    """
    best = None
    rounds_used = 0
    previous = []

    for round_no in range(MAX_REPLAN_ROUNDS):
        # Check session budget
        if session_rounds_used + rounds_used >= MAX_SESSION_ROUNDS:
            return LoopOutcome(
                stopped_because=BUDGET,
                rounds_used=rounds_used,
                best=best,
                detail="session round cap (%d) reached" % MAX_SESSION_ROUNDS,
            )

        candidates = explore(
            client, broken, ceilings, outbound_date, return_dates,
            party_size, destinations=destinations,
            stopover_candidates=stopover_candidates)

        rounds_used += 1

        if not candidates:
            return LoopOutcome(
                stopped_because=EXHAUSTED,
                rounds_used=rounds_used,
                best=best,
                detail="no alternatives found in round %d" % round_no,
            )

        # Track best
        if candidates:
            best = candidates[0].graph

        # Check for BLOCKED: every candidate was rejected by same member
        stop = should_stop(candidates, previous, ceilings)
        if stop == BLOCKED:
            # Find the blocking member
            all_rejected = set()
            for c in candidates:
                for r in c.rejected_for:
                    all_rejected.add(r)
            blocking = all_rejected.pop() if all_rejected else None

            # Compute shortfall
            shortfall = None
            if best is not None:
                tightest = min(getattr(c, "amount", float(c)) for c in ceilings)
                shortfall = round(best.per_person - tightest, 2)

            return LoopOutcome(
                stopped_because=BLOCKED,
                rounds_used=rounds_used,
                best=best,
                blocking_member=blocking,
                shortfall=shortfall,
                detail="%s's ceiling blocked every option" % blocking,
            )

        # If we found viable alternatives, return the best one
        if candidates:
            return LoopOutcome(
                stopped_because="",
                rounds_used=rounds_used,
                best=candidates[0].graph,
                detail="repaired in round %d" % round_no,
            )

        previous = candidates

    # Hit MAX_REPLAN_ROUNDS
    return LoopOutcome(
        stopped_because=BUDGET,
        rounds_used=rounds_used,
        best=best,
        detail="per-change budget (%d rounds) exhausted" % MAX_REPLAN_ROUNDS,
    )
