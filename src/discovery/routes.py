"""Turn search responses into FlightNodes.

search_nodes() takes a client, role, origin, destination, date, and party_size,
calls Atlas, and returns every priced routing as a FlightNode. A pair that
errors or returns nothing is a data gap surfaced in the UI — never a crash,
never silently dropped.
"""

from src.atlas.models import fixture_key, parse_routings
from src.itinerary.nodes import FlightNode


def search_nodes(client, role, origin, destination, date, party_size,
                 drop_unseatable=True, currency=None, airlines=None,
                 return_date=None):
    """Every priced routing for one direction on one date, as FlightNodes.

    Returns (nodes, error). A pair that errors or returns nothing is a data gap
    surfaced in the UI — never a crash, never silently dropped.

    Args:
        client:          AtlasClient instance
        role:            'outbound', 'inbound', or 'stopover'
        origin:          departure city code
        destination:     arrival city code
        date:            departure date (YYYYMMDD or YYYY-MM-DD)
        party_size:      number of travellers (for seat filtering)
        drop_unseatable: if True, drop nodes where min_seat_count < party_size
        currency:        optional ISO currency code (e.g. 'USD', 'SGD')
        airlines:        optional list of airline codes to filter
        return_date:     optional return date for round-trip search
    """
    cache_key = fixture_key(origin, destination, date)

    payload = {
        "tripType": "2" if return_date else "1",
        "adultNum": max(1, int(party_size)),
        "childNum": 0,
        "infantNum": 0,
        "fromCity": origin,
        "fromAirport": "",
        "toCity": destination,
        "toAirport": "",
        "fromDate": str(date).replace("-", ""),
        "retDate": str(return_date).replace("-", "") if return_date else "",
        "airlines": list(airlines) if airlines else [],
        "fromFlightNumbers": [],
        "retFlightNumbers": [],
        "includeMultipleFareFamily": False,
        "currency": currency,
        "displayCurrency": currency or "",
        "requestSource": None,
    }

    error = None
    try:
        response = client.post(
            "search.do", payload,
            fixture_key=cache_key, allow_error=True)
    except Exception as exc:
        error = str(exc)
        return ([], error)

    if response is None:
        le = getattr(client, "last_error", None)
        if le:
            error = "Atlas %s for %s: %s" % (le["code"], cache_key, le["body"][:120])
        else:
            error = "No response for %s" % cache_key
        return ([], error)

    routings = parse_routings(response, cache_key=cache_key)

    nodes = []
    for r in routings:
        node = _routing_to_node(r, role, origin, destination, date)
        if drop_unseatable and node.min_seat_count < party_size:
            continue
        nodes.append(node)

    return (nodes, error)


def _routing_to_node(routing, role, origin, destination, date):
    """Convert a Routing into a FlightNode."""
    segments = routing.segments
    flight_numbers = tuple(s.flight_number for s in segments)
    carriers = tuple(routing.carriers)

    seg_dicts = tuple(
        {
            "flight_number": s.flight_number,
            "dep_airport": s.departure_airport,
            "arr_airport": s.arrival_airport,
            "dep_time": s.departure_datetime,
            "arr_time": s.arrival_datetime,
            "carrier": s.carrier,
        }
        for s in segments
    )

    return FlightNode(
        role=role,
        origin=origin,
        destination=destination,
        date=str(date).replace("-", ""),
        cache_key=routing.cache_key,
        routing_index=routing.index,
        flight_numbers=flight_numbers,
        carriers=carriers,
        elapsed_hours=routing.elapsed_hours,
        adult_price=routing.adult_price,
        adult_tax=routing.adult_tax,
        transaction_fee=routing.transaction_fee,
        min_seat_count=routing.min_seat_count,
        price_ref=routing.price_ref(),
        tax_ref=routing.tax_ref(),
        fee_ref=routing.fee_ref(),
        segments=seg_dicts,
    )
