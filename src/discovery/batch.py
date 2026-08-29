"""Batch search — pre-sales route fare lookup and price comparison.

Accepts multiple (origin, destination, date, passengers, currency, airlines)
combinations. Returns normalised FareRecords and a comparison report.

This module is the ONLY batch entry point. It delegates to search_nodes()
for the Atlas call and normalises every response into a flat record that
can be rendered as a table or JSON report.

Usage:
    queries = [
        SearchQuery("SIN", "BKK", "20261010", adults=2),
        SearchQuery("SIN", "HKT", "20261010", adults=2, currency="SGD"),
        SearchQuery("SIN", "BKK", "20261010", adults=2, airlines=["TR"]),
    ]
    report = batch_search(client, queries)
    report.print_table()
    report.to_json()  # for API response
"""

from dataclasses import dataclass, field
from src.discovery.routes import search_nodes
from src.atlas.models import parse_routings, fixture_key, format_date


# ---------------------------------------------------------------------------
# Query — one search request
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SearchQuery:
    """One origin→destination search request.

    All fields are required except return_date, currency, and airlines.
    Dates are YYYYMMDD (no separators). Hyphens are stripped automatically.
    """
    origin: str
    destination: str
    departure_date: str
    return_date: str = ""
    adults: int = 1
    currency: str = ""
    airlines: tuple = ()

    def __post_init__(self):
        # Normalise dates — strip hyphens for consistent cache keys
        object.__setattr__(self, "departure_date",
                           format_date(self.departure_date))
        if self.return_date:
            object.__setattr__(self, "return_date",
                               format_date(self.return_date))


# ---------------------------------------------------------------------------
# FareRecord — one normalised row in the comparison table
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FareRecord:
    """One row in the comparison table.

    Normalised from a Routing so that every field is a plain value —
    no cost_ref pointers, no raw dicts, no Atlas-specific types.
    """
    # Query identity
    query_index: int = 0
    origin: str = ""
    destination: str = ""
    departure_date: str = ""
    return_date: str = ""
    adults: int = 1

    # Flight identity
    airline: str = ""
    flight_numbers: str = ""      # comma-separated, e.g. "TR624,TR625"
    carrier_count: int = 1        # 1 = single carrier, >1 = multi-carrier

    # Pricing
    fare: float = 0.0             # adultPrice (per person)
    taxes: float = 0.0            # adultTax (per person)
    total_per_person: float = 0.0 # fare + taxes
    total_group: float = 0.0      # (fare + taxes) * adults + fee
    transaction_fee: float = 0.0
    currency: str = "USD"

    # Baggage
    baggage_kg: int = 0           # cheapest purchasable baggage weight
    baggage_price: float = 0.0    # cheapest purchasable baggage price

    # Status
    sellable: bool = True         # riskSellout=False and not expired
    seats_available: int = 0      # min_seat_count across segments
    fare_family: str = ""

    # Metadata
    elapsed_hours: float = 0.0
    cache_key: str = ""           # for cost_ref traceability


# ---------------------------------------------------------------------------
# BatchReport — results + errors + summary statistics
# ---------------------------------------------------------------------------

@dataclass
class BatchReport:
    """Aggregated results from a batch search.

    records: list of FareRecord (one per priced routing per query)
    errors:  list of {query_index, origin, destination, date, error}
    queries: list of SearchQuery (echoed for reference)
    """
    records: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    queries: list = field(default_factory=list)

    # -- accessors ----------------------------------------------------------

    @property
    def total_queries(self):
        return len(self.queries)

    @property
    def successful_queries(self):
        """Number of queries that returned at least one record."""
        seen = set()
        for r in self.records:
            seen.add(r.query_index)
        return len(seen)

    @property
    def total_records(self):
        return len(self.records)

    @property
    def total_errors(self):
        return len(self.errors)

    def records_for(self, query_index):
        """All FareRecords for one query."""
        return [r for r in self.records if r.query_index == query_index]

    def cheapest_per_route(self):
        """Cheapest FareRecord per (origin, destination, departure_date).

        Returns dict: (origin, destination, date) -> FareRecord
        """
        best = {}
        for r in self.records:
            if not r.sellable:
                continue
            key = (r.origin, r.destination, r.departure_date)
            if key not in best or r.total_per_person < best[key].total_per_person:
                best[key] = r
        return best

    def cheapest_per_airline(self):
        """Cheapest FareRecord per (route, airline).

        Returns dict: (origin, destination, date, airline) -> FareRecord
        """
        best = {}
        for r in self.records:
            if not r.sellable:
                continue
            key = (r.origin, r.destination, r.departure_date, r.airline)
            if key not in best or r.total_per_person < best[key].total_per_person:
                best[key] = r
        return best

    # -- output: table ------------------------------------------------------

    def print_table(self):
        """Print a formatted comparison table to stdout.

        Columns: # | Route | Date | Airline | Flights | Fare | Tax | Total |
                 Currency | Baggage | Sellable
        """
        # Header
        hdr = (
            f"{'#':>3} | {'Route':<12} | {'Date':<10} | {'Airline':<8} | "
            f"{'Flights':<16} | {'Fare':>8} | {'Tax':>7} | {'Total':>8} | "
            f"{'Curr':<4} | {'Bag(kg)':>7} | {'Sell':>4}"
        )
        print(hdr)
        print("-" * len(hdr))

        for i, r in enumerate(self.records):
            print(
                f"{i+1:>3} | "
                f"{r.origin}-{r.destination:<9} | "
                f"{r.departure_date:<10} | "
                f"{r.airline:<8} | "
                f"{r.flight_numbers:<16} | "
                f"{r.fare:>8.2f} | "
                f"{r.taxes:>7.2f} | "
                f"{r.total_per_person:>8.2f} | "
                f"{r.currency:<4} | "
                f"{r.baggage_kg:>7} | "
                f"{'Y' if r.sellable else 'N':>4}"
            )

        # Summary
        print()
        print(f"Queries: {self.total_queries} | "
              f"Successful: {self.successful_queries} | "
              f"Records: {self.total_records} | "
              f"Errors: {self.total_errors}")

        if self.errors:
            print("\nErrors:")
            for e in self.errors:
                print(f"  [{e.get('query_index', '?')}] "
                      f"{e.get('origin', '?')}-{e.get('destination', '?')} "
                      f"@{e.get('date', '?')}: {e.get('error', '?')[:80]}")

    # -- output: JSON -------------------------------------------------------

    def to_dict(self):
        """Serialise the report to a plain dict (JSON-safe)."""
        return {
            "queries": [
                {
                    "origin": q.origin,
                    "destination": q.destination,
                    "departure_date": q.departure_date,
                    "return_date": q.return_date,
                    "adults": q.adults,
                    "currency": q.currency,
                    "airlines": list(q.airlines),
                }
                for q in self.queries
            ],
            "records": [
                {
                    "query_index": r.query_index,
                    "route": f"{r.origin}-{r.destination}",
                    "origin": r.origin,
                    "destination": r.destination,
                    "departure_date": r.departure_date,
                    "return_date": r.return_date,
                    "airline": r.airline,
                    "flight_numbers": r.flight_numbers,
                    "fare": r.fare,
                    "taxes": r.taxes,
                    "total_per_person": r.total_per_person,
                    "total_group": r.total_group,
                    "currency": r.currency,
                    "baggage_kg": r.baggage_kg,
                    "baggage_price": r.baggage_price,
                    "sellable": r.sellable,
                    "seats_available": r.seats_available,
                    "fare_family": r.fare_family,
                    "elapsed_hours": r.elapsed_hours,
                }
                for r in self.records
            ],
            "errors": self.errors,
            "summary": {
                "total_queries": self.total_queries,
                "successful_queries": self.successful_queries,
                "total_records": self.total_records,
                "total_errors": self.total_errors,
                "coverage_by_route": self._coverage_summary(),
            },
        }

    def _coverage_summary(self):
        """Price coverage: how many records per route."""
        counts = {}
        for r in self.records:
            key = f"{r.origin}-{r.destination}"
            counts[key] = counts.get(key, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Normaliser — Routing → FareRecord
# ---------------------------------------------------------------------------

def _routing_to_record(routing, query, query_index):
    """Convert a Routing into a FareRecord."""
    carriers = routing.carriers
    airline = carriers[0] if carriers else ""
    flight_nums = ",".join(s.flight_number for s in routing.segments)

    fare = routing.adult_price
    taxes = routing.adult_tax
    fee = routing.transaction_fee

    return FareRecord(
        query_index=query_index,
        origin=query.origin,
        destination=query.destination,
        departure_date=query.departure_date,
        return_date=query.return_date,
        adults=query.adults,
        airline=airline,
        flight_numbers=flight_nums,
        carrier_count=len(carriers) if carriers else 1,
        fare=fare,
        taxes=taxes,
        total_per_person=fare + taxes,
        total_group=(fare + taxes) * query.adults + fee,
        transaction_fee=fee,
        currency=routing.currency,
        baggage_kg=routing.default_baggage_kg,
        baggage_price=routing.default_baggage_price,
        sellable=routing.sellable,
        seats_available=routing.min_seat_count,
        fare_family=routing.fare_family,
        elapsed_hours=routing.elapsed_hours,
        cache_key=routing.cache_key,
    )


# ---------------------------------------------------------------------------
# Batch search entry point
# ---------------------------------------------------------------------------

def batch_search(client, queries, drop_unseatable=True):
    """Execute a batch of search queries and return a BatchReport.

    Each query produces multiple FareRecords (one per priced routing).
    Errors are collected, never raised — a failed query becomes an entry
    in report.errors, not an exception.

    Args:
        client:          AtlasClient instance
        queries:         list of SearchQuery objects
        drop_unseatable: if True, drop routings where seats < adults

    Returns:
        BatchReport with records, errors, and summary.
    """
    report = BatchReport(queries=list(queries))

    for qi, query in enumerate(queries):
        nodes, error = search_nodes(
            client,
            role="outbound",
            origin=query.origin,
            destination=query.destination,
            date=query.departure_date,
            party_size=query.adults,
            drop_unseatable=drop_unseatable,
            currency=query.currency or None,
            airlines=list(query.airlines) if query.airlines else None,
            return_date=query.return_date or None,
        )

        if error:
            report.errors.append({
                "query_index": qi,
                "origin": query.origin,
                "destination": query.destination,
                "date": query.departure_date,
                "error": error,
            })

        if not nodes:
            if not error:
                report.errors.append({
                    "query_index": qi,
                    "origin": query.origin,
                    "destination": query.destination,
                    "date": query.departure_date,
                    "error": "No routings returned",
                })
            continue

        # To convert nodes back to fare records we need the raw routings.
        # search_nodes returns FlightNode objects; we reconstruct from
        # the cache_key and re-parse.
        cache_key = fixture_key(query.origin, query.destination,
                                query.departure_date)
        from src.atlas import cache as response_cache
        cached = response_cache.get(cache_key)
        if cached:
            routings = parse_routings(cached, cache_key=cache_key)
            for routing in routings:
                if drop_unseatable and routing.min_seat_count < query.adults:
                    continue
                record = _routing_to_record(routing, query, qi)
                report.records.append(record)
        else:
            # Fallback: build records from FlightNode fields
            for node in nodes:
                record = FareRecord(
                    query_index=qi,
                    origin=query.origin,
                    destination=query.destination,
                    departure_date=query.departure_date,
                    return_date=query.return_date,
                    adults=query.adults,
                    airline=node.carriers[0] if node.carriers else "",
                    flight_numbers=",".join(node.flight_numbers),
                    carrier_count=len(node.carriers) if node.carriers else 1,
                    fare=node.adult_price,
                    taxes=node.adult_tax,
                    total_per_person=node.adult_price + node.adult_tax,
                    total_group=(
                        (node.adult_price + node.adult_tax) * query.adults
                        + node.transaction_fee
                    ),
                    transaction_fee=node.transaction_fee,
                    currency="USD",
                    baggage_kg=0,
                    baggage_price=0.0,
                    sellable=True,
                    seats_available=node.min_seat_count,
                    fare_family="",
                    elapsed_hours=node.elapsed_hours,
                    cache_key=node.cache_key,
                )
                report.records.append(record)

    return report
