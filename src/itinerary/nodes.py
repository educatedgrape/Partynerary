"""FlightNode — one priced routing for one direction on one date.

A node is a leaf in the itinerary graph. It wraps a Routing from the Atlas
response and tags it with its role (outbound/inbound/stopover), search key,
and the destination city it serves.

The node holds cost_ref pointers, never dereferenced prices. The figure on
screen is resolved at render time by cost_ref.resolve().
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FlightNode:
    """One priced routing from an Atlas response, annotated for the graph.

    Attributes:
        role:         'outbound', 'inbound', or 'stopover'
        origin:       departure city code (e.g. 'SIN')
        destination:  arrival city code (e.g. 'DPS')
        date:         departure date (YYYYMMDD)
        cache_key:    the RESPONSE_CACHE key for this search
        routing_index: index into the routings[] array
        flight_numbers: tuple of flight numbers across all segments
        carriers:     tuple of carrier codes
        elapsed_hours: total trip duration in hours
        adult_price:  raw price from Atlas (for display only — use refs)
        adult_tax:    raw tax from Atlas
        transaction_fee: raw fee from Atlas
        min_seat_count: minimum seatCount across all segments
        price_ref:    cost_ref pointing at adultPrice
        tax_ref:      cost_ref pointing at adultTax
        fee_ref:      cost_ref pointing at transactionFee
        segments:     tuple of dicts with per-segment detail
                      (flight_number, depAirport, arrAirport, depTime, arrTime)
    """
    role: str = ""
    origin: str = ""
    destination: str = ""
    date: str = ""
    cache_key: str = ""
    routing_index: int = 0
    flight_numbers: tuple = ()
    carriers: tuple = ()
    elapsed_hours: float = 0.0
    adult_price: float = 0.0
    adult_tax: float = 0.0
    transaction_fee: float = 0.0
    min_seat_count: int = 0
    price_ref: str = ""
    tax_ref: str = ""
    fee_ref: str = ""
    segments: tuple = ()

    @property
    def per_person(self):
        """(adultPrice + adultTax) — the per-person fare for this node."""
        return self.adult_price + self.adult_tax

    def group_total(self, party_size=1):
        """(adultPrice + adultTax) * party_size + transactionFee."""
        return (self.adult_price + self.adult_tax) * party_size + self.transaction_fee

    @property
    def cost_refs(self):
        """All three refs for this node, in canonical order."""
        return (self.price_ref, self.tax_ref, self.fee_ref)

    @property
    def key(self):
        """Unique identity for this node — NOT a derived key.

        Uses the cache_key and routing_index so two routings with the same
        origin/destination/date but different prices remain distinct objects.
        """
        return "%s#%d" % (self.cache_key, self.routing_index)

    def __repr__(self):
        flights = "+".join(self.flight_numbers) if self.flight_numbers else "?"
        return "FlightNode(%s %s→%s @%s %s $%.2f)" % (
            self.role, self.origin, self.destination, self.date,
            flights, self.adult_price)
