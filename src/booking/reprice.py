"""Re-price — gate file. Check every leg after the click, before the order.

A price check before confirmation tells you what the fare was when you asked.
The window that matters is between the human saying yes and the order existing,
and low-cost fares move in exactly that window.

Do not invent a verify endpoint. Re-run search.do and match the candidate on
flightNumber across segments. Never parse routingIdentifier, which is
documented as opaque.
"""

from src.agent.cost_ref import resolve, sibling, CostRefError, _resolve_path
from src.atlas import cache as response_cache
from src.atlas.models import fixture_key as _fixture_key
from src.atlas.models import raw_segments


# Severity ordering — data, not a chain of comparisons.
GONE = "GONE"
DEARER = "DEARER"
UNCHANGED = "UNCHANGED"
CHEAPER = "CHEAPER"

SEVERITY = {
    GONE: 3,
    DEARER: 2,
    UNCHANGED: 1,
    CHEAPER: 0,
}


class RepriceResult:
    """One leg's re-price outcome."""

    def __init__(self, verdict, old_price, new_price, delta, ref, detail=""):
        self.verdict = verdict
        self.old_price = round(old_price, 2)
        self.new_price = round(new_price, 2) if new_price is not None else None
        self.delta = round(delta, 2) if delta is not None else None
        self.ref = ref
        self.detail = detail

    def __repr__(self):
        if self.verdict == GONE:
            return "RepriceResult(%s ref=%s)" % (self.verdict, self.ref)
        return "RepriceResult(%s $%.2f→$%.2f Δ%+.2f)" % (
            self.verdict, self.old_price, self.new_price or 0, self.delta or 0)


def check(client, card, confirmation, date, party_size,
          origin, destination, price_ref, force_key=None):
    """The single-leg decision. UNCHANGED | CHEAPER | DEARER | GONE.

    Re-runs search.do, then finds the CONFIRMED flight again by matching the
    full ordered sequence of segment flight numbers. Atlas reorders results
    when fares move — that reordering is the normal case in exactly the window
    this gate polices — so list position is meaningless. No match is GONE:
    never fall back to an index, to a nearest price, or to the cheapest
    remaining routing.

    Args:
        client:      AtlasClient instance
        card:        TestCard (unused in reprice but present for consistency)
        confirmation: Confirmation object (carries price_shown)
        date:        departure date (YYYYMMDD)
        party_size:  number of travellers
        origin:      departure city code
        destination: arrival city code
        price_ref:   cost_ref pointing at the original adultPrice
        force_key:   override the cache key (for testing)
    """
    cache_key = force_key or _fixture_key(origin, destination, date)

    try:
        old_price = resolve(price_ref)
    except CostRefError:
        return RepriceResult(
            GONE, 0.0, None, None, price_ref,
            detail="original cost_ref could not be resolved")

    old_tax = 0.0
    old_fee = 0.0
    try:
        old_tax = resolve(sibling(price_ref, "adultTax"))
        old_fee = resolve(sibling(price_ref, "transactionFee"))
    except CostRefError:
        pass

    old_total = round((old_price + old_tax) * party_size + old_fee, 2)

    # The confirmed flight's identity: its segment flight numbers, in order,
    # read from the original response the cost_ref points at.
    try:
        original_numbers = _flight_numbers(price_ref)
    except CostRefError:
        return RepriceResult(
            GONE, old_price, None, None, price_ref,
            detail="could not read the confirmed flight's segments")

    # Re-run search.do
    payload = {
        "originAirportCode": origin,
        "destinationAirportCode": destination,
        "departureDate": str(date),
    }
    try:
        response = client.post(
            "search.do", payload, fixture_key=cache_key, allow_error=True)
    except Exception:
        return RepriceResult(
            GONE, old_price, None, None, price_ref,
            detail="search.do failed on re-price")

    if response is None:
        return RepriceResult(
            GONE, old_price, None, None, price_ref,
            detail="no response from search.do on re-price")

    new_routings = response.get("routings", [])
    if not new_routings:
        return RepriceResult(
            GONE, old_price, None, None, price_ref,
            detail="no routings in re-price response")

    # Match on the full ordered flight-number sequence — one flight number
    # can appear in several routings under different fare families, so a
    # single-segment prefix is not an identity.
    new_routing = None
    for routing in new_routings:
        numbers = tuple(
            s.get("flightNumber") for s in raw_segments(routing))
        if numbers == original_numbers:
            new_routing = routing
            break

    if new_routing is None:
        return RepriceResult(
            GONE, old_price, None, None, price_ref,
            detail=("confirmed flights %s not present in fresh response"
                    % "/".join(original_numbers)))

    new_price = float(new_routing.get("adultPrice", 0))
    new_tax = float(new_routing.get("adultTax", 0))
    new_fee = float(new_routing.get("transactionFee", 0))
    new_total = round((new_price + new_tax) * party_size + new_fee, 2)

    delta = round(new_total - old_total, 2)

    if delta == 0:
        verdict = UNCHANGED
    elif delta < 0:
        verdict = CHEAPER
    else:
        verdict = DEARER

    return RepriceResult(
        verdict, old_total, new_total, delta, price_ref,
        detail="re-priced leg %s→%s" % (origin, destination))


def check_all(client, legs, confirmation, party_size, force_keys=None):
    """Re-price EVERY leg. Returns (worst_result, per_leg_results).

    The verdict is the worst across legs: any leg DEARER or GONE voids the
    whole confirmation. A trip is one purchase decision and cannot be partially
    re-confirmed — re-pricing only the outbound leaves the return fare
    unguarded, which is a silent hole in the one gate this product is built
    around.

    check_all compares the SUMMED per-person across legs against price_shown;
    individual legs report their own deltas for the UI but are not each
    measured against the trip total.

    Args:
        client:      AtlasClient instance
        legs:        list of dicts with keys: origin, destination, date, price_ref
        confirmation: Confirmation object
        party_size:  number of travellers
        force_keys:  dict of {leg_index: cache_key} overrides
    """
    force_keys = force_keys or {}
    results = []

    for i, leg in enumerate(legs):
        fk = force_keys.get(i)
        result = check(
            client=client,
            card=None,
            confirmation=confirmation,
            date=leg["date"],
            party_size=party_size,
            origin=leg["origin"],
            destination=leg["destination"],
            price_ref=leg["price_ref"],
            force_key=fk,
        )
        results.append(result)

    # The worst verdict across all legs
    if not results:
        return (None, [])

    worst = results[0]
    for r in results[1:]:
        if SEVERITY.get(r.verdict, 0) > SEVERITY.get(worst.verdict, 0):
            worst = r

    # If the worst is DEARER, check the summed per-person against price_shown
    if worst.verdict == DEARER and confirmation is not None:
        total_new = sum(
            r.new_price for r in results if r.new_price is not None)
        per_person = round(total_new / party_size, 2) if party_size else total_new
        if not confirmation.still_valid_for(per_person):
            # Upgrade to a stale confirmation — the trip as a whole
            # is now more expensive than what was confirmed
            worst = RepriceResult(
                DEARER, worst.old_price, worst.new_price, worst.delta,
                worst.ref,
                detail=("summed per-person $%.2f exceeds confirmed $%.2f"
                        % (per_person, confirmation.price_shown)))

    return (worst, results)


def _flight_numbers(price_ref):
    """The confirmed routing's segment flight numbers, in order.

    Resolves the routing the cost_ref points at in the CACHED original
    response — the list index is meaningful there, because that is the
    response the index was minted against. Raises CostRefError if the
    original response or its segments are unreachable.
    """
    if "#" not in price_ref:
        raise CostRefError("Malformed cost_ref (no '#'): %r" % price_ref)
    cache_key, fragment = price_ref.split("#", 1)
    data = response_cache.get(cache_key)
    if data is None:
        raise CostRefError(
            "Original response %r not in cache — cannot identify the "
            "confirmed flight" % cache_key)

    # Strip the leaf field: routings[2].adultPrice -> routings[2]
    parts = fragment.rsplit(".", 1)
    if len(parts) < 2:
        raise CostRefError(
            "Cannot locate the routing in %r — no dotted path" % price_ref)
    routing = _resolve_path(data, parts[0])
    if not isinstance(routing, dict):
        raise CostRefError(
            "cost_ref %r does not point inside a routing" % price_ref)

    segments = raw_segments(routing)
    if not segments:
        raise CostRefError(
            "Confirmed routing has no segments — cannot match on flight "
            "numbers")
    return tuple(s.get("flightNumber") for s in segments)
