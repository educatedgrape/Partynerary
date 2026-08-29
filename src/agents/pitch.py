"""Pitch — build booking proposals for the orchestrator.

The orchestrator uses this module to create ActionProposals from trips.
The proposal carries one ref per leg and the payload carries adults + legs.
"""

from src.agent.proposal import ActionProposal


def pitch_booking(trip, party_size=None):
    """Build an ActionProposal for booking a trip.

    The proposal carries one cost_ref per leg. payload carries adults and legs.
    The executor dereferences cost_refs to compute the amount.

    Args:
        trip:       ItineraryGraph
        party_size: number of travellers (defaults to trip.party_size)

    Returns:
        (proposal, payload) tuple
    """
    if party_size is None:
        party_size = getattr(trip, "party_size", 1)

    nodes = trip.legs
    cost_refs = tuple(leg.price_ref for leg in nodes)

    nights = _compute_nights(trip)
    node_labels = []
    for leg in nodes:
        label = "%s→%s" % (leg.origin, leg.destination)
        node_labels.append(label)

    reason = "booking %s for %d — %d night(s), %s" % (
        trip.destination_name or trip.destination,
        party_size,
        nights,
        " + ".join(node_labels))

    proposal = ActionProposal(
        action="book_group",
        target=trip.key,
        reason=reason,
        cost_refs=cost_refs,
    )

    payload = {
        "adults": party_size,
        "legs": len(nodes),
    }

    return (proposal, payload)


def pitch_payment(trip, party_size=None):
    """Build an ActionProposal for paying a booked trip.

    Same refs, different action. The executor settles authority instead of
    reserving it.
    """
    if party_size is None:
        party_size = getattr(trip, "party_size", 1)

    nodes = trip.legs
    cost_refs = tuple(leg.price_ref for leg in nodes)

    proposal = ActionProposal(
        action="pay_group",
        target=trip.key,
        reason="paying for %s" % (trip.destination_name or trip.destination),
        cost_refs=cost_refs,
    )

    payload = {
        "adults": party_size,
        "legs": len(nodes),
    }

    return (proposal, payload)


def _compute_nights(trip):
    """Compute nights from outbound and inbound dates."""
    if len(trip.legs) < 2:
        return 0
    try:
        from datetime import datetime
        out_dt = datetime.strptime(trip.outbound.date, "%Y%m%d")
        ret_dt = datetime.strptime(trip.inbound.date, "%Y%m%d")
        return max(0, (ret_dt - out_dt).days)
    except (ValueError, AttributeError):
        return 0
