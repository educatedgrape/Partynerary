#!/usr/bin/env python3
"""Atlas hub feasibility probe.

Checks whether multi-leg chaining works at all by probing a handful of
origin -> C / C -> destination pairs. This is a FEASIBILITY CHECK, not a
hub list. There is no hub table and no city carries a hub flag.

Each candidate stopover is searched on BOTH legs. A hub that answers in
only one direction cannot carry a chain.

Run with LIVE=1:
    LIVE=1 python probe/hubs.py

Records results as a table for FINDINGS.md.
"""

import argparse
import json
import sys
import time

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.atlas.client import AtlasClient, AtlasHTTPError, LiveCallBlocked
from src.atlas.models import fixture_key, parse_routings


ORIGIN = "SIN"

# A handful of candidates to test multi-leg feasibility.
# These are checked on BOTH legs: origin->hub and hub->destination.
HUB_CANDIDATES = [
    "BKK", "KUL", "HKG", "TPE", "MNL",
]

# Destination to test the second leg against.
HUB_DESTINATION = "NRT"

# Dates — outbound leg and connecting leg.
DATE_LEG1 = "20260920"
DATE_LEG2 = "20260922"


def _payload(origin, destination, date):
    return {
        "tripType": "1",
        "adultNum": 1,
        "childNum": 0,
        "infantNum": 0,
        "fromCity": origin,
        "fromAirport": "",
        "toCity": destination,
        "toAirport": "",
        "fromDate": date,
        "retDate": "",
        "airlines": [],
        "fromFlightNumbers": [],
        "retFlightNumbers": [],
        "includeMultipleFareFamily": False,
        "currency": None,
        "displayCurrency": "",
        "requestSource": None,
    }


def probe_leg(client, origin, destination, date, label):
    """Probe one leg. Returns (routings_count, cheapest, error)."""
    key = fixture_key(origin, destination, date)
    try:
        t0 = time.time()
        result = client.post(
            "search.do", _payload(origin, destination, date),
            fixture_key=key)
        elapsed = time.time() - t0

        if result is None:
            return 0, None, "no response"

        routings = parse_routings(result, cache_key=key)
        if not routings:
            return 0, None, None  # empty, not an error

        cheapest = min(r.adult_price for r in routings)
        return len(routings), cheapest, None

    except AtlasHTTPError as exc:
        return 0, None, "HTTP %d" % exc.status
    except LiveCallBlocked:
        raise
    except Exception as exc:
        return 0, None, str(exc)[:80]


def hubs(client, origin, destination, date_leg1, date_leg2):
    """Probe hub candidates on both legs."""
    print("=== HUB FEASIBILITY PROBE ===")
    print("Origin:      %s" % origin)
    print("Destination: %s" % destination)
    print("Leg 1 date:  %s" % date_leg1)
    print("Leg 2 date:  %s" % date_leg2)
    print("Candidates:  %s" % ", ".join(HUB_CANDIDATES))
    print()

    results = []
    header = "%-5s | %-18s | %-6s | %-10s | %-18s | %-6s | %-10s | %s" % (
        "Hub", "Leg 1", "Rout.", "Cheapest", "Leg 2", "Rout.", "Cheapest", "Viable")
    print(header)
    print("-" * len(header))

    for hub in HUB_CANDIDATES:
        leg1_key = "%s->%s" % (origin, hub)
        leg2_key = "%s->%s" % (hub, destination)

        leg1_count, leg1_cheapest, leg1_err = probe_leg(
            client, origin, hub, date_leg1, leg1_key)
        leg2_count, leg2_cheapest, leg2_err = probe_leg(
            client, hub, destination, date_leg2, leg2_key)

        # Determine viability
        if leg1_err or leg2_err:
            viable = "ERROR"
            detail = leg1_err or leg2_err or ""
        elif leg1_count == 0 and leg2_count == 0:
            viable = "BOTH EMPTY"
            detail = ""
        elif leg1_count == 0:
            viable = "LEG1 EMPTY"
            detail = ""
        elif leg2_count == 0:
            viable = "LEG2 EMPTY"
            detail = ""
        else:
            viable = "YES"
            detail = ""

        leg1_price = "%.2f" % leg1_cheapest if leg1_cheapest else "-"
        leg2_price = "%.2f" % leg2_cheapest if leg2_cheapest else "-"

        row = "%-5s | %-18s | %-6d | %-10s | %-18s | %-6d | %-10s | %s %s" % (
            hub, leg1_key, leg1_count, leg1_price,
            leg2_key, leg2_count, leg2_price,
            viable, detail)
        print(row)

        results.append({
            "hub": hub,
            "leg1": {
                "pair": leg1_key,
                "routings": leg1_count,
                "cheapest": leg1_cheapest,
                "error": leg1_err,
            },
            "leg2": {
                "pair": leg2_key,
                "routings": leg2_count,
                "cheapest": leg2_cheapest,
                "error": leg2_err,
            },
            "viable": viable == "YES",
        })

    # Summary
    viable_count = sum(1 for r in results if r["viable"])
    total_legs = len(HUB_CANDIDATES) * 2
    answered_legs = sum(
        1 for r in results
        if (r["leg1"]["routings"] > 0 or r["leg1"]["error"] is None)
        and (r["leg2"]["routings"] > 0 or r["leg2"]["error"] is None)
    )
    print("\nViable hubs: %d of %d" % (viable_count, len(HUB_CANDIDATES)))
    print("Legs with data: %d of %d" % (
        sum(1 for r in results
            for leg in [r["leg1"], r["leg2"]]
            if leg["routings"] > 0),
        total_legs))

    print("\n=== RESULTS (JSON) ===")
    print(json.dumps({
        "origin": origin,
        "destination": destination,
        "date_leg1": date_leg1,
        "date_leg2": date_leg2,
        "results": results,
    }, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="Atlas hub feasibility probe")
    parser.add_argument("--origin", default=ORIGIN,
                        help="Origin city code (default: SIN)")
    parser.add_argument("--destination", default=HUB_DESTINATION,
                        help="Destination for second leg (default: NRT)")
    parser.add_argument("--date1", default=DATE_LEG1,
                        help="First leg date YYYYMMDD")
    parser.add_argument("--date2", default=DATE_LEG2,
                        help="Second leg date YYYYMMDD")
    args = parser.parse_args()

    client = AtlasClient()
    try:
        hubs(client, args.origin, args.destination, args.date1, args.date2)
    except LiveCallBlocked:
        print("BLOCKED: set LIVE=1 to allow live calls.")


if __name__ == "__main__":
    main()
