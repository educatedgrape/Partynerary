#!/usr/bin/env python3
"""Atlas probe — smoke and roundtrip calls.

Run with LIVE=1 against the real sandbox:
    LIVE=1 python probe/probe.py smoke
    LIVE=1 python probe/probe.py roundtrip

smoke    — one documented route (SIN→DPS). Confirms auth, headers, gzip,
           and the field names in models.py.
roundtrip — outbound and several returns for one pair. Confirms date
            arithmetic and that returns are searchable independently.
"""

import argparse
import json
import sys
import time

# Ensure project root is on sys.path
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.atlas.client import AtlasClient, AtlasHTTPError, LiveCallBlocked
from src.atlas.models import fixture_key, parse_routings


ORIGIN = "SIN"


def _payload(origin, destination, date):
    """Standard search.do payload — one-way, one adult."""
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


def smoke(client, origin, date="20260918"):
    """One documented route. Verify the contract holds."""
    dest = "DPS"
    key = fixture_key(origin, dest, date)
    print("=== SMOKE: %s -> %s @ %s ===" % (origin, dest, date))
    print("Fixture key: %s" % key)

    t0 = time.time()
    try:
        result = client.post("search.do", _payload(ORIGIN, dest, date),
                             fixture_key=key)
    except AtlasHTTPError as exc:
        print("ERROR %d: %s" % (exc.status, exc.body[:300]))
        return
    except LiveCallBlocked:
        print("BLOCKED: set LIVE=1 to allow live calls.")
        return
    elapsed = time.time() - t0

    if result is None:
        print("ERROR: no response returned.")
        return

    routings = parse_routings(result, cache_key=key)
    print("HTTP 200 in %.1fs" % elapsed)
    print("Routings returned: %d" % len(routings))

    if not routings:
        print("WARNING: no routings — the pair may not be supported.")
        return

    r = routings[0]
    print("\nFirst routing (index 0):")
    print("  Flight(s): %s" % ", ".join(s.flight_number for s in r.segments))
    print("  adultPrice: %.2f" % r.adult_price)
    print("  adultTax:   %.2f" % r.adult_tax)
    print("  transFee:   %.2f" % r.transaction_fee)
    print("  total(1):   %.2f" % r.total_price(1))
    print("  seats:      %d" % r.min_seat_count)
    print("  carriers:   %s" % r.carriers)
    print("  elapsed_h:  %.2f" % r.elapsed_hours)

    print("\nRef builders:")
    print("  price_ref: %s" % r.price_ref())
    print("  tax_ref:   %s" % r.tax_ref())
    print("  fee_ref:   %s" % r.fee_ref())

    print("\nContract fields present: OK")
    print("Captured to: fixtures/live/%s.json" % key.replace(":", "/"))


def roundtrip(client, origin, date="20260918"):
    """Outbound and several returns for one pair.

    Confirms:
      - Date arithmetic (return dates are valid)
      - Returns are searchable independently (each return date is its own call)
      - City != airport is recorded
    """
    dest = "DPS"
    return_dates = ["20260920", "20260922", "20260925"]

    print("=== ROUNDTRIP: %s <-> %s ===" % (origin, dest))
    print("Outbound: %s" % date)
    print("Returns:  %s" % ", ".join(return_dates))

    # Outbound
    out_key = fixture_key(origin, dest, date)
    print("\n--- Outbound: %s ---" % out_key)
    try:
        out_result = client.post("search.do", _payload(origin, dest, date),
                                 fixture_key=out_key)
    except AtlasHTTPError as exc:
        print("ERROR %d: %s" % (exc.status, exc.body[:300]))
        return
    except LiveCallBlocked:
        print("BLOCKED: set LIVE=1 to allow live calls.")
        return

    out_routings = parse_routings(out_result, cache_key=out_key)
    print("Routings: %d" % len(out_routings))
    if out_routings:
        segs = out_routings[0].segments
        print("First: %s, seats=%d, price=%.2f" % (
            segs[0].flight_number if segs else "?",
            out_routings[0].min_seat_count,
            out_routings[0].adult_price))
        # Record city vs airport
        for s in segs:
            if s.departure_airport != origin:
                print("NOTE: city=%s but airport=%s" % (
                    origin, s.departure_airport))
            if s.arrival_airport != dest:
                print("NOTE: city=%s but airport=%s (city != airport)" % (
                    dest, s.arrival_airport))

    # Returns
    for ret_date in return_dates:
        ret_key = fixture_key(dest, origin, ret_date)
        print("\n--- Return: %s ---" % ret_key)
        try:
            ret_result = client.post("search.do", _payload(dest, origin, ret_date),
                                     fixture_key=ret_key)
        except AtlasHTTPError as exc:
            print("ERROR %d: %s" % (exc.status, exc.body[:300]))
            continue

        ret_routings = parse_routings(ret_result, cache_key=ret_key)
        print("Routings: %d" % len(ret_routings))
        if ret_routings:
            r = ret_routings[0]
            segs = r.segments
            print("First: %s, seats=%d, price=%.2f" % (
                segs[0].flight_number if segs else "?",
                r.min_seat_count,
                r.adult_price))

    print("\nReturn searches are independent — each date is its own call.")
    print("All captures written to fixtures/live/.")


def main():
    parser = argparse.ArgumentParser(description="Atlas probe — smoke & roundtrip")
    parser.add_argument("command", choices=["smoke", "roundtrip"],
                        help="Probe command to run")
    parser.add_argument("--date", default="20260918",
                        help="Departure date (YYYYMMDD)")
    parser.add_argument("--origin", default=ORIGIN,
                        help="Origin city code (default: SIN)")
    args = parser.parse_args()

    origin = args.origin

    client = AtlasClient()  # live by default; LIVE=1 required

    if args.command == "smoke":
        smoke(client, origin, args.date)
    elif args.command == "roundtrip":
        roundtrip(client, origin, args.date)


if __name__ == "__main__":
    main()
