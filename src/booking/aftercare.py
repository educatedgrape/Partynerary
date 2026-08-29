"""Aftercare — post-booking repair loop entry point.

Not a second engine. §8.3's repair loop entered from a post-order trigger.
Re-shops booked legs with search.do (the only characterised endpoint), diffs
against what was paid, and returns Changes the propagation machinery handles.

Authority is per action, not per group:
  - reshop_order: autonomous (read-only, safe on a timer)
  - refund_order: autonomous (credit recorded, never spent)
  - change_order: confirmation required (moves money)
  - cancel_order: confirmation required (moves money)

All three order-mutating endpoints are UNCHARACTERISED — stubs that record
executed_stub. The re-shop, the propagation, the ceiling arithmetic and the
receipt are all real.
"""

from dataclasses import dataclass, field

from src.itinerary.propagate import Change, PRICE, SCHEDULE, GONE
from src.discovery.routes import search_nodes


POLL_INTERVAL_SECONDS = 900  # re-shop booked legs; read-only, safe on a timer

# Authority is per action, not per group.
AUTONOMOUS_ACTIONS = {"reshop_order", "refund_order"}


@dataclass
class BookedLeg:
    """One booked leg for reshop comparison.

    Identifies the routing by cache_key and routing_index — never by
    routingIdentifier. The per_person is what was paid, for the price diff.
    """
    role: str
    origin: str
    destination: str
    date: str
    cache_key: str
    routing_index: int
    per_person: float
    flight_numbers: tuple = ()


@dataclass
class Order:
    """A booked order — legs, party_size, and identity."""
    legs: list = field(default_factory=list)
    party_size: int = 1
    booking_reference: str = ""


@dataclass
class RepairOutcome:
    """The result of settle_difference — credit or veto."""
    accepted: bool = True
    difference: float = 0.0
    credit: float = 0.0
    vetoed_by: str = ""
    shortfall: float = 0.0
    detail: str = ""


def reshop(client, order, party_size, current_routings=None):
    """Re-run search.do for the booked legs; diff against what was paid.

    Uses search.do — the only characterised endpoint. queryOrderDetails, void,
    refund and balance are undocumented and never invented.

    Returns a list of propagate.Change — GONE, SCHEDULE or PRICE — so
    everything downstream is the machinery that already exists.

    Args:
        client:           AtlasClient instance
        order:            Order with BookedLeg entries
        party_size:       number of travellers
        current_routings: optional dict {cache_key: [routings]} to override
                          fresh Atlas calls (for testing price drops, etc.)
    """
    changes = []

    for leg in order.legs:
        if current_routings and leg.cache_key in current_routings:
            current = current_routings[leg.cache_key]
        else:
            nodes, error = search_nodes(
                client, leg.role, leg.origin, leg.destination,
                leg.date, party_size, drop_unseatable=False)
            if error or not nodes:
                changes.append(Change(
                    node_key="%s#%d" % (leg.cache_key, leg.routing_index),
                    kind=GONE,
                    detail="search error or no routings for %s" % leg.cache_key))
                continue
            current = nodes

        # Check if the booked routing index still exists
        same_index = [n for n in current if n.routing_index == leg.routing_index]
        if not same_index:
            changes.append(Change(
                node_key="%s#%d" % (leg.cache_key, leg.routing_index),
                kind=GONE,
                detail="routing index %d no longer available" % leg.routing_index))
            continue

        match = same_index[0]

        # Check for price difference
        new_pp = match.per_person
        old_pp = leg.per_person
        if abs(new_pp - old_pp) > 0.001:
            changes.append(Change(
                node_key="%s#%d" % (leg.cache_key, leg.routing_index),
                kind=PRICE,
                was=old_pp, now=new_pp,
                cost_ref=match.price_ref,
                replacement=match,
                detail="fare $%.2f → $%.2f" % (old_pp, new_pp)))

        # Check for schedule change (different flight numbers)
        if match.flight_numbers != leg.flight_numbers:
            changes.append(Change(
                node_key="%s#%d" % (leg.cache_key, leg.routing_index),
                kind=SCHEDULE,
                detail="flight numbers changed",
                replacement=match))

    return changes


def watch(client, order, party_size, on_change=None):
    """Poll re-shop. The only trigger that works without provider cooperation.

    Calls reshop and invokes on_change for each detected change.
    In production this would loop on POLL_INTERVAL_SECONDS; here it runs once.
    """
    changes = reshop(client, order, party_size)
    if on_change:
        for change in changes:
            on_change(change)
    return changes


def inject(event):
    """Operator-supplied event, for rehearsal only.

    The payload shape is OURS, not Atlas's. Every Change it produces carries
    source="injector", and the UI renders that label. An injected event that
    looks identical to a real one is the same misrepresentation as a stubbed
    call rendering as a success.

    Event shape:
        {"kind": "GONE"|"PRICE"|"SCHEDULE", "node_key": str, ...}
    """
    kind = event.get("kind", "")
    node_key = event.get("node_key", "")
    was = event.get("was")
    now = event.get("now")
    detail = event.get("detail", "injected event")

    return [Change(
        node_key=node_key,
        kind=kind,
        was=was,
        now=now,
        detail="[injector] %s" % detail)]


def settle_difference(executor, booked_per_person, new_per_person,
                      party_size, ceilings=None):
    """A dearer repair must fit REMAINING authority; a cheaper one is a credit.

    A member who granted 210 does not owe a change fee they never authorised,
    so the ceiling vetoes a repair exactly as it vetoes a trip. The executor
    owns settlement accounting — the mandate never grows a credit method;
    a refund is RECORDED as owed, never spent.

    A repair is never applied because it is available — only because it
    survives every ceiling.
    """
    diff = round((new_per_person - booked_per_person) * party_size, 2)

    if diff < 0:
        # Cheaper — a refund recorded as owed, never spent
        credit_amount = abs(diff)
        executor.record_credit(credit_amount)
        return RepairOutcome(
            accepted=True, difference=diff, credit=credit_amount,
            detail="credit of $%.2f recorded" % credit_amount)

    if diff > 0:
        # Dearer — check remaining authority
        if diff > executor.remaining + 0.001:
            return RepairOutcome(
                accepted=False, difference=diff,
                detail="repair costs $%.2f more than remaining authority $%.2f"
                       % (diff, executor.remaining))

        # Check ceilings
        if ceilings:
            for c in ceilings:
                amount = c.amount if hasattr(c, "amount") else float(c)
                member = getattr(c, "member", "unknown")
                if new_per_person > amount:
                    shortfall = round(new_per_person - amount, 2)
                    return RepairOutcome(
                        accepted=False, difference=diff,
                        vetoed_by=member, shortfall=shortfall,
                        detail="repair breaches %s's ceiling by $%.2f"
                               % (member, shortfall))

        return RepairOutcome(
            accepted=True, difference=diff,
            detail="repair fits within remaining authority")

    # diff == 0 — no change
    return RepairOutcome(
        accepted=True, difference=0.0,
        detail="no price difference")
