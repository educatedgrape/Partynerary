"""The sweep — scan and filter, never construct.

Takes the retrieval shortlist (destinations), searches Atlas for every
(destination × direction × date) combination, and assembles every structurally
valid outbound × return into an ItineraryGraph.

EVERY structurally valid combination comes back. Returning only the cheapest
would be locally optimal — no single-node swap could improve on it — and the
re-planner would have nothing to find.
"""

from datetime import datetime, timedelta

from src.atlas.models import format_date
from src.discovery.routes import search_nodes
from src.itinerary.graph import build_chain


# Default trip duration offsets in nights
DEFAULT_OFFSETS = (2, 3, 4)
DEFAULT_DATES = 3


def return_dates_for(date, offsets=None):
    """Derive the return dates a departure implies.

    DERIVED, never fixed — a group cannot agree a date and then be shown
    returns that predate it.

    Args:
        date:    departure date (YYYYMMDD)
        offsets: tuple of night counts; defaults to (2, 3, 4)

    Returns:
        list of YYYYMMDD strings
    """
    if offsets is None:
        offsets = DEFAULT_OFFSETS

    date_str = format_date(date)
    # Parse YYYYMMDD
    dt = datetime.strptime(date_str, "%Y%m%d")

    results = []
    for nights in offsets:
        ret = dt + timedelta(days=nights)
        results.append(ret.strftime("%Y%m%d"))
    return results


def sweep(client, origin, out_date, return_dates, party_size,
          destinations=None, members=None):
    """Returns (all_valid_trips, errors).

    EVERY structurally valid outbound × return combination comes back.
    destinations is a list of city codes from the retrieval shortlist.

    Args:
        client:       AtlasClient instance
        origin:       departure city code
        out_date:     departure date (YYYYMMDD)
        return_dates: list of return dates (YYYYMMDD strings)
        party_size:   number of travellers
        destinations: list of destination city codes; if None, returns empty
        members:      optional list of Match objects for vibe_score carry
    """
    if destinations is None:
        return ([], [])

    out_date = format_date(out_date)
    all_trips = []
    errors = []

    # Build a vibe_score lookup from members (Match objects)
    vibe_lookup = {}
    if members:
        for m in members:
            if hasattr(m, "city_id") and hasattr(m, "vibe_score"):
                vibe_lookup[m.city_id] = m.vibe_score

    for dest in destinations:
        # Search outbound
        out_nodes, out_err = search_nodes(
            client, "outbound", origin, dest, out_date, party_size,
            drop_unseatable=True)

        if out_err:
            errors.append({"destination": dest, "direction": "outbound",
                           "error": out_err})

        if not out_nodes:
            if not out_err:
                errors.append({
                    "destination": dest,
                    "direction": "outbound",
                    "error": "No routings returned for %s→%s @%s" % (
                        origin, dest, out_date),
                })
            continue

        # Search returns for each return date
        for ret_date in return_dates:
            ret_nodes, ret_err = search_nodes(
                client, "inbound", dest, origin, ret_date, party_size,
                drop_unseatable=True)

            if ret_err:
                errors.append({
                    "destination": dest,
                    "direction": "inbound",
                    "date": ret_date,
                    "error": ret_err,
                })

            if not ret_nodes:
                if not ret_err:
                    errors.append({
                        "destination": dest,
                        "direction": "inbound",
                        "date": ret_date,
                        "error": "No routings for %s→%s @%s" % (
                            dest, origin, ret_date),
                    })
                continue

            # Cross-product: every outbound × every return
            for out_node in out_nodes:
                for ret_node in ret_nodes:
                    vibe = vibe_lookup.get(dest, 0.0)
                    try:
                        graph = build_chain(
                            legs=[out_node, ret_node],
                            party_size=party_size,
                            destination_name=dest,
                            vibe_score=vibe,
                        )
                        all_trips.append(graph)
                    except ValueError as exc:
                        errors.append({
                            "destination": dest,
                            "error": str(exc),
                        })

    return (all_trips, errors)


def best_per_destination(ranked, limit=8):
    """Top trip per CITY SEARCHED. Use for a board of places to go.

    `ranked` is a list of ItineraryGraph objects, already sorted by the
    scoring layer. Returns at most `limit` trips, one per destination.
    """
    seen = set()
    result = []
    for trip in ranked:
        dest = trip.destination
        if dest not in seen:
            seen.add(dest)
            result.append(trip)
            if len(result) >= limit:
                break
    return result


def best_per_shape(ranked, limit=8):
    """Top per (destination, nights). Use where trip LENGTH is the comparison.

    Returns at most `limit` trips, one per (destination, nights) pair.
    """
    seen = set()
    result = []
    for trip in ranked:
        if len(trip.legs) >= 2:
            nights = _nights(trip)
        else:
            nights = 0
        shape = (trip.destination, nights)
        if shape not in seen:
            seen.add(shape)
            result.append(trip)
            if len(result) >= limit:
                break
    return result


def _nights(graph):
    """Compute number of nights from outbound and inbound dates."""
    if len(graph.legs) < 2:
        return 0
    try:
        out_dt = datetime.strptime(graph.outbound.date, "%Y%m%d")
        ret_dt = datetime.strptime(graph.inbound.date, "%Y%m%d")
        return (ret_dt - out_dt).days
    except (ValueError, AttributeError):
        return 0
