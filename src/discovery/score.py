"""Fare evaluation — comparator with a visible denominator.

There is no price history, so the system cannot say "30% below normal". What
one sweep honestly supports is a comparator with a visible denominator:

    vibe_score:  semantic fit from retrieval
    value:       the fare, normalised across today's sweep
    headroom:    slack against the TIGHTEST ceiling in the group

Fit leads, price orders. A ceiling is a hard filter applied BEFORE any of this,
and nothing in ranking can resurrect a trip somebody cannot afford.

seatCount is displayed but never scored. Letting scarcity raise a score means
a dearer trip outranks an identical cheaper one because it is running out.
That is pressure-selling, not ranking.

Index scored trips by identity, not by a derived key. A key like
"<origin>-<dest>@<return-date>" is shared by every outbound × return
combination on that date — trips are objects; index them as objects (id(g)).
"""

import statistics


WEIGHTS = {
    "vibeScore": 0.55,   # semantic fit from retrieval
    "value":     0.25,   # the fare, normalised across today's sweep
    "headroom":  0.20,   # slack against the TIGHTEST ceiling in the group
}


def score_trip(trip, median_fare, tightest_ceiling=None):
    """Score one ItineraryGraph against the sweep's comparators.

    Args:
        trip:              ItineraryGraph
        median_fare:       median per_person across the entire sweep
        tightest_ceiling:  the lowest ceiling in the group (or None if no party)

    Returns:
        ScoredTrip with rank, comparators, and the original trip.
    """
    fare = trip.per_person

    # Value: inverse normalised — cheaper is better
    if median_fare > 0:
        vs_median = median_fare - fare
        value = max(0.0, min(1.0, (vs_median + median_fare) / (2 * median_fare)))
    else:
        vs_median = 0.0
        value = 0.5

    # Headroom: slack against tightest ceiling
    headroom = None
    headroom_score = 0.5  # neutral when no ceiling
    if tightest_ceiling is not None:
        headroom = tightest_ceiling - fare
        if tightest_ceiling > 0:
            headroom_score = max(0.0, min(1.0, headroom / tightest_ceiling))
        else:
            headroom_score = 0.0

    # Vibe score carried from retrieval
    vibe = trip.vibe_score

    # Composite rank score
    rank_score = (
        WEIGHTS["vibeScore"] * vibe +
        WEIGHTS["value"] * value +
        WEIGHTS["headroom"] * headroom_score
    )

    comparators = {
        "median_fare_today": round(median_fare, 2),
        "vs_median": round(vs_median, 2),
        "seats_left": trip.min_seats,
        "vibeScore": round(vibe, 4),
    }
    if headroom is not None:
        comparators["headroom_vs_tightest_ceiling"] = round(headroom, 2)

    return ScoredTrip(
        trip=trip,
        rank_score=round(rank_score, 4),
        comparators=comparators,
    )


def score_sweep(trips, ceilings=None):
    """Score every trip in the sweep and return them ranked.

    Args:
        trips:    list of ItineraryGraph objects
        ceilings: list of per-member ceilings (floats); the tightest is used

    Returns:
        list of ScoredTrip, ranked descending.
    """
    if not trips:
        return []

    # Compute median fare across the sweep
    fares = [t.per_person for t in trips]
    median_fare = statistics.median(fares) if fares else 0.0

    # Tightest ceiling
    tightest = None
    if ceilings:
        tightest = min(ceilings)

    scored = [score_trip(t, median_fare, tightest) for t in trips]

    # Sort by rank_score descending; break ties by per_person ascending
    scored.sort(key=lambda s: (-s.rank_score, s.trip.per_person))

    return scored


class ScoredTrip:
    """A trip with its ranking score and comparators."""

    def __init__(self, trip, rank_score, comparators):
        self.trip = trip
        self.rank_score = rank_score
        self.comparators = comparators

    @property
    def key(self):
        """Identity key — NOT a derived key. Uses id() of the graph."""
        return self.trip.key

    def as_dict(self):
        d = self.trip.as_dict()
        d["rank_score"] = self.rank_score
        d["comparators"] = self.comparators
        return d

    def __repr__(self):
        return "ScoredTrip(%s $%.2f rank=%.4f)" % (
            self.trip.destination_name or self.trip.destination,
            self.trip.per_person, self.rank_score)


def apply_ceilings(scored_trips, ceilings):
    """Hard filter — remove trips above the tightest ceiling.

    A ceiling is absolute. Nothing in ranking can resurrect a trip somebody
    cannot afford. Returns (survivors, vetoed) where vetoed carries the
    matched-but-unaffordable list for the gap display.
    """
    if not ceilings:
        return (scored_trips, [])

    tightest = min(ceilings)
    survivors = []
    vetoed = []

    for st in scored_trips:
        fare = st.trip.per_person
        if fare <= tightest:
            survivors.append(st)
        else:
            vetoed.append({
                "destination": st.trip.destination_name or st.trip.destination,
                "destination_id": st.trip.destination,
                "per_person": round(fare, 2),
                "tightest_ceiling": round(tightest, 2),
                "over_by": round(fare - tightest, 2),
                "vibeScore": round(st.trip.vibe_score, 4),
                "cost_ref": (
                    st.trip.legs[0].price_ref if st.trip.legs else ""),
            })

    return (survivors, vetoed)
