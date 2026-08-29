"""Receipt UI — render the booking receipt after the order.

Shows what was booked, at what price, with which cost_refs. The receipt
is the audit trail the human sees; the DecisionLog is the one the system
keeps.
"""

from src.atlas.payment import redact


def render_receipt(execution_result, trip=None, ceilings=None):
    """Render a receipt for a completed booking.

    Args:
        execution_result: ExecutionResult from the executor
        trip:             ItineraryGraph or dict with trip data
        ceilings:         dict of {member_name: ceiling_amount}

    Returns a dict suitable for display.
    """
    data = {}
    if trip is not None:
        if hasattr(trip, "as_dict"):
            data = trip.as_dict()
        elif isinstance(trip, dict):
            data = trip

    receipt = {
        "status": "SUCCESS" if execution_result.accepted else "FAILED",
        "stubbed": execution_result.stage == "stubbed",
        "amount": execution_result.amount,
        "remaining": execution_result.remaining,
        "legs": data.get("legs", []),
        "cost_refs": data.get("cost_refs", []),
    }

    if ceilings:
        receipt["perMemberCeilings"] = ceilings

    if execution_result.atlas_response:
        safe_response = redact(execution_result.atlas_response)
        receipt["bookingReference"] = safe_response.get(
            "bookingReference", "N/A")
    else:
        receipt["bookingReference"] = "N/A"

    return receipt


def format_receipt_text(receipt_data):
    """Format a receipt as a human-readable string."""
    lines = [
        "=== BOOKING RECEIPT ===",
        "Status: %s%s" % (
            receipt_data.get("status", "?"),
            " (stubbed)" if receipt_data.get("stubbed") else ""),
        "Booking ref: %s" % receipt_data.get("bookingReference", "N/A"),
        "Group total: $%.2f" % receipt_data.get("amount", 0),
    ]
    if receipt_data.get("remaining") is not None:
        lines.append("Mandate remaining: $%.2f" % receipt_data["remaining"])
    ceilings = receipt_data.get("perMemberCeilings", {})
    if ceilings:
        lines.append("Per-member ceilings:")
        for name, amount in ceilings.items():
            lines.append("  %s: $%.2f" % (name, amount))
    lines.append("=======================")
    return "\n".join(lines)
