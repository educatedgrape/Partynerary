"""Confirmation UI — render the confirmation dialog before the order.

Displays the whole-trip per-person price, the group total, and the per-member
ceilings. The human sees exactly what they are approving.
"""


def render_confirmation(trip, party_size, ceilings=None):
    """Render a confirmation dialog for a trip.

    Args:
        trip:      ItineraryGraph or dict with trip data
        party_size: number of travellers
        ceilings:  dict of {member_name: ceiling_amount}

    Returns a dict suitable for display.
    """
    if hasattr(trip, "as_dict"):
        data = trip.as_dict()
    elif isinstance(trip, dict):
        data = trip
    else:
        data = {}

    per_person = data.get("per_person", 0.0)
    group_total = data.get("group_total", 0.0)

    result = {
        "title": "Confirm booking",
        "destination": data.get("destination_name", data.get("destination", "")),
        "per_person": round(per_person, 2),
        "group_total": round(group_total, 2),
        "party_size": party_size,
        "legs": data.get("legs", []),
        "cost_refs": data.get("cost_refs", []),
    }

    if ceilings:
        result["ceilings"] = {
            name: {"amount": amount, "permits": amount >= per_person}
            for name, amount in ceilings.items()
        }

    return result


def format_confirmation_text(confirmation_data):
    """Format a confirmation as a human-readable string."""
    lines = [
        "Confirm booking: %s" % confirmation_data.get("destination", "?"),
        "  Per person: $%.2f" % confirmation_data.get("per_person", 0),
        "  Group total: $%.2f (%d travellers)" % (
            confirmation_data.get("group_total", 0),
            confirmation_data.get("party_size", 1)),
    ]
    ceilings = confirmation_data.get("ceilings", {})
    if ceilings:
        lines.append("  Ceilings:")
        for name, info in ceilings.items():
            status = "OK" if info["permits"] else "EXCEEDS"
            lines.append("    %s: $%.2f [%s]" % (name, info["amount"], status))
    return "\n".join(lines)
