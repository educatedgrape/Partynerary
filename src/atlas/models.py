"""Atlas response contract — routings, segments, and ref builders.

Atlas returns routings[], NOT offers[]. A priced itinerary is a routing.
Dates are YYYYMMDD; datetimes YYYYMMDDHHmm. No separators, ever.

There is no total price field. Compute it:
    total = (adultPrice + adultTax) * adults + transactionFee

There is no fareFamilies[] array and no priceDelta. fareFamily is a string
per segment, so 'changeable' is an inference and must never be stated as fact.

Segment legs live in a container on each routing. Live Atlas puts outbound
legs under 'fromSegments'; a bare 'segments' key only ever appears in older
hand-written fixtures. Read via raw_segments() so neither shape parses to a
silent empty list.
"""

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Date helpers — YYYYMMDD everywhere, no separators
# ---------------------------------------------------------------------------

def format_date(date_str):
    """Normalise a date string to YYYYMMDD (no separators).

    Accepts YYYYMMDD (passthrough) or YYYY-MM-DD (strips hyphens).
    """
    if isinstance(date_str, str):
        return date_str.replace("-", "")
    return str(date_str)


# ---------------------------------------------------------------------------
# Fixture key — one definition, one place
# ---------------------------------------------------------------------------

def fixture_key(origin, destination, date):
    """search.do:SIN-DPS@20260918 — the ONE key format.

    Every module that searches builds its key here. A module that invents
    its own variant will look for a cache slot that cannot exist, and the
    failure surfaces as an empty result rather than an error.
    """
    return "search.do:%s-%s@%s" % (origin, destination, format_date(date))


# ---------------------------------------------------------------------------
# Routing segment
# ---------------------------------------------------------------------------

def _fmt_atlas_time(raw):
    """Atlas depTime/arrTime: '202609250820' -> '2026-09-25 08:20'.

    If the format doesn't match, return the raw string unchanged.
    """
    s = str(raw)
    if len(s) >= 12 and s[:12].isdigit():
        return "%s-%s-%s %s:%s" % (s[:4], s[4:6], s[6:8], s[8:10], s[10:12])
    return s


@dataclass(frozen=True)
class BaggageOption:
    """One purchasable baggage allowance from ancillaryProductElements.

    Atlas productType=1 means StandardCheckInBaggage. Only those are
    parsed here — seat selections, meals, etc. are out of scope.
    """
    code: str = ""
    name: str = ""
    weight_kg: int = 0
    piece: int = 0
    price: float = 0.0
    currency: str = "USD"

    @classmethod
    def from_dict(cls, d):
        aux = d.get("auxBaggageElement") or {}
        return cls(
            code=str(d.get("productCode", "")),
            name=str(d.get("productName", "")),
            weight_kg=int(aux.get("weight", 0)),
            piece=int(aux.get("piece", 0)),
            price=float(d.get("price", 0)),
            currency=str(d.get("currency", "USD")),
        )


@dataclass(frozen=True)
class RoutingSegment:
    """One flight leg within a routing."""
    flight_number: str = ""
    seat_count: int = 0
    fare_family: str = ""
    departure_airport: str = ""
    arrival_airport: str = ""
    departure_datetime: str = ""
    arrival_datetime: str = ""
    carrier: str = ""

    @classmethod
    def from_dict(cls, d):
        return cls(
            flight_number=str(d.get("flightNumber", "")),
            seat_count=int(d.get("seatCount", 0)),
            fare_family=str(d.get("fareFamily", "")),
            departure_airport=str(d.get("depAirport", "")),
            arrival_airport=str(d.get("arrAirport", "")),
            departure_datetime=_fmt_atlas_time(d.get("depTime", "")),
            arrival_datetime=_fmt_atlas_time(d.get("arrTime", "")),
            carrier=str(d.get("carrier", "")),
        )


# ---------------------------------------------------------------------------
# Segment container — one place names the key, loudly
# ---------------------------------------------------------------------------

# Live Atlas stores outbound legs under 'fromSegments'. Older hand-written
# fixtures used a bare 'segments' key. Both are read here so that a routing
# carrying legs under either container never parses to a silent empty list.
_SEGMENT_CONTAINERS = ("fromSegments", "segments")


def raw_segments(routing_data):
    """The raw segment list for one routing dict.

    Reads the first recognised container that actually carries legs. Returns
    [] only when no container has legs — callers that need an identity
    (reprice) treat that as an error, never a silent empty match.
    """
    for key in _SEGMENT_CONTAINERS:
        legs = routing_data.get(key)
        if legs:
            return legs
    return []


# ---------------------------------------------------------------------------
# Routing — one priced itinerary in the response
# ---------------------------------------------------------------------------

@dataclass
class Routing:
    """One priced itinerary from an Atlas response.

    Wraps a single entry from the routings[] array. Exposes parsed segments,
    pricing fields, and ref builders for cost_ref pointers.

    Attributes mirror the Atlas response shape. The cache_key and index are
    set by the parser so that ref builders can produce valid cost_ref strings.
    """
    _data: dict = field(repr=False)
    index: int = 0
    cache_key: str = ""

    # -- segments -----------------------------------------------------------

    @property
    def segments(self):
        """Parsed RoutingSegment objects for each leg."""
        return [RoutingSegment.from_dict(s) for s in raw_segments(self._data)]

    @property
    def min_seat_count(self):
        """Minimum seatCount across all segments. Zero if no segments."""
        seats = [s.seat_count for s in self.segments]
        return min(seats) if seats else 0

    @property
    def elapsed_hours(self):
        """Total trip duration in hours.

        Atlas may return totalTripDuration directly, or not at all. Falls
        back to summing segment duration (minutes) when the routing-level
        field is absent or zero.
        """
        raw = self._data.get("totalTripDuration")
        if raw:
            return float(raw)
        # Fallback: sum segment durations (Atlas stores them in minutes)
        total_minutes = 0
        for key in _SEGMENT_CONTAINERS:
            legs = self._data.get(key)
            if legs:
                for leg in legs:
                    total_minutes += int(leg.get("duration", 0))
                break
        return round(total_minutes / 60.0, 1) if total_minutes else 0.0

    @property
    def transit_hours(self):
        return float(self._data.get("totalTransitDuration", 0))

    @property
    def carriers(self):
        """Carrier codes for this routing.

        Atlas sometimes returns a top-level carriers[]; when it is empty
        or absent, extract from individual segment carrier fields.
        """
        top_level = self._data.get("carriers", [])
        if top_level:
            return list(top_level)
        # Fallback: collect unique carriers from segments
        seen = []
        for key in _SEGMENT_CONTAINERS:
            legs = self._data.get(key)
            if legs:
                for leg in legs:
                    c = leg.get("carrier", "")
                    if c and c not in seen:
                        seen.append(c)
                break
        return seen

    @property
    def is_multi_carrier(self):
        return bool(self._data.get("isMultiCarrier", False))

    @property
    def through_checked_baggage(self):
        return bool(self._data.get("throughCheckedBaggage", False))

    # -- pricing (raw — dereference via cost_ref, not direct access) --------

    @property
    def adult_price(self):
        return float(self._data.get("adultPrice", 0))

    @property
    def adult_tax(self):
        return float(self._data.get("adultTax", 0))

    @property
    def transaction_fee(self):
        return float(self._data.get("transactionFee", 0))

    # -- computed total (documented formula, for display only) ---------------

    def total_price(self, adults=1):
        """(adultPrice + adultTax) * adults + transactionFee.

        This is for display convenience. The authoritative total goes through
        cost_ref.resolve_group_total() which dereferences each component.
        """
        return (self.adult_price + self.adult_tax) * adults + self.transaction_fee

    # -- ref builders — the only sanctioned way to name a figure ------------

    def price_ref(self):
        """cost_ref pointing at adultPrice for this routing."""
        return "%s#routings[%d].adultPrice" % (self.cache_key, self.index)

    def tax_ref(self):
        """cost_ref pointing at adultTax for this routing."""
        return "%s#routings[%d].adultTax" % (self.cache_key, self.index)

    def fee_ref(self):
        """cost_ref pointing at transactionFee for this routing."""
        return "%s#routings[%d].transactionFee" % (self.cache_key, self.index)

    # -- normalization: currency, baggage, sellable --------------------------

    @property
    def currency(self):
        """Pricing currency from the routing. Atlas always returns ISO code."""
        return str(self._data.get("currency", "USD"))

    @property
    def baggage_options(self):
        """Purchasable baggage from ancillaryProductElements (type=1 only).

        Returns list of BaggageOption, sorted by weight ascending.
        Empty list when no baggage ancillary is offered.
        """
        elements = self._data.get("ancillaryProductElements", [])
        options = []
        for el in elements:
            if el.get("productType") == 1:  # StandardCheckInBaggage
                options.append(BaggageOption.from_dict(el))
        options.sort(key=lambda b: b.weight_kg)
        return options

    @property
    def default_baggage_kg(self):
        """Weight (kg) of the cheapest purchasable baggage option.

        Zero if no baggage ancillary is offered — caller must check
        baggage_options for details.
        """
        opts = self.baggage_options
        return opts[0].weight_kg if opts else 0

    @property
    def default_baggage_price(self):
        """Price of the cheapest purchasable baggage option."""
        opts = self.baggage_options
        return opts[0].price if opts else 0.0

    @property
    def sellable(self):
        """Whether the fare is still sellable.

        Atlas returns riskSellout (bool). True means seats ARE available.
        False means sold out. Also checks expireTime — expired fares are
        not sellable even if riskSellout is True.
        """
        if self._data.get("riskSellout", False):
            return False
        # Check expiry
        expire = self._data.get("expireTime")
        if expire:
            from datetime import datetime
            try:
                exp_dt = datetime.fromisoformat(
                    expire.replace("Z", "+00:00"))
                now = datetime.now(exp_dt.tzinfo)
                if now > exp_dt:
                    return False
            except (ValueError, AttributeError):
                pass
        return True

    @property
    def fare_family(self):
        """Primary fare family from the first segment."""
        segs = raw_segments(self._data)
        if segs:
            return str(segs[0].get("fareFamily", ""))
        return ""

    # -- passthrough --------------------------------------------------------

    def get(self, key, default=None):
        """Access raw data fields not covered by properties."""
        return self._data.get(key, default)

    def __repr__(self):
        segs = ", ".join(s.flight_number for s in self.segments)
        return "Routing(index=%d, segments=[%s], price=%.2f)" % (
            self.index, segs, self.adult_price)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_routings(response_data, cache_key=""):
    """Parse routings[] from an Atlas response into Routing objects.

    Returns an empty list if no routings key is present or if routings is
    not a list (malformed response guard). Each Routing is tagged with its
    cache_key and array index so ref builders work.
    """
    routings = response_data.get("routings", [])
    if not isinstance(routings, list):
        return []
    result = []
    for i, r in enumerate(routings):
        if not isinstance(r, dict):
            continue  # Skip non-dict entries in malformed data
        result.append(Routing(_data=r, index=i, cache_key=cache_key))
    return result
