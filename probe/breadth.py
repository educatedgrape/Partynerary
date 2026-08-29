#!/usr/bin/env python3
"""Atlas breadth probe — sweep candidate destinations from one origin.

Sweeps a candidate list on one date and records three buckets:
  REACHABLE  — returned priced routings
  EMPTY      — answered, with nothing
  ERROR      — did not answer

Run with LIVE=1:
    LIVE=1 python probe/breadth.py --origin SIN --date 20260918

The candidate list here is INPUT to a measurement tool, not a curated
catalogue. It answers 'what does Atlas reach?' — the output populates
the catalogue, never the other way around.

No module may contain a hand-written list of destinations to capture.
"""

import argparse
import json
import sys
import time

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.atlas.client import AtlasClient, AtlasHTTPError, LiveCallBlocked
from src.atlas.models import fixture_key, parse_routings


# Candidate destinations — input to the breadth probe, not a catalogue.
# These are cities that *might* be reachable from SIN; the probe decides.
CANDIDATES = [
    # Southeast Asia
    "DPS", "CGK", "SUB", "MDC", "LOP", "PKY", "PNK",
    "BKK", "DMK", "CNX", "HKT", "CEI", "USM",
    "KUL", "PEN", "LGK", "BKI", "KCH",
    "MNL", "CEB", "CRK", "DVO",
    "SGN", "HAN", "DAD", "CXR", "HKG",
    "PNH", "REP",
    # East Asia
    "ICN", "PUS", "NRT", "KIX", "FUK", "CTS", "NGO",
    "TPE", "KHH",
    "SHA", "PEK", "CAN", "SZX", "CKG",
    # South Asia
    "BOM", "DEL", "MAA", "BLR", "HYD", "COK",
    "CMB",
    # Oceania
    "SYD", "MEL", "BNE", "PER",
    # Middle East
    "DXB", "DOH",
]


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


def breadth(client, origin, date):
    """Sweep candidates and record reachable/empty/error."""
    print("=== BREADTH PROBE: %s @ %s ===" % (origin, date))
    print("Candidates: %d" % len(CANDIDATES))
    print()

    reachable = []
    empty = []
    errors = []
    city_airport_notes = []

    for i, dest in enumerate(CANDIDATES):
        key = fixture_key(origin, dest, date)
        label = "[%2d/%2d] %s -> %s" % (i + 1, len(CANDIDATES), origin, dest)

        try:
            t0 = time.time()
            result = client.post(
                "search.do", _payload(origin, dest, date),
                fixture_key=key)
            elapsed = time.time() - t0

            if result is None:
                errors.append((dest, "no response"))
                print("%s  ERROR (no response)" % label)
                continue

            routings = parse_routings(result, cache_key=key)

            if not routings:
                empty.append(dest)
                print("%s  EMPTY  (%.1fs)" % (label, elapsed))
                continue

            # Record cheapest price and seat count
            cheapest = min(r.adult_price for r in routings)
            max_seats = max(r.min_seat_count for r in routings)
            reachable.append({
                "dest": dest,
                "routings": len(routings),
                "cheapest": cheapest,
                "max_seats": max_seats,
            })
            print("%s  REACHABLE  %d routings, cheapest=%.2f, seats=%d  (%.1fs)" % (
                label, len(routings), cheapest, max_seats, elapsed))

            # Check city != airport
            r0 = routings[0]
            for seg in r0.segments:
                if seg.arrival_airport != dest:
                    note = "%s: city=%s but airport=%s" % (
                        dest, dest, seg.arrival_airport)
                    city_airport_notes.append(note)

        except AtlasHTTPError as exc:
            errors.append((dest, "HTTP %d: %s" % (exc.status, exc.body[:100])))
            print("%s  ERROR (HTTP %d)" % (label, exc.status))

        except LiveCallBlocked:
            print("BLOCKED: set LIVE=1 to allow live calls.")
            return

        except Exception as exc:
            errors.append((dest, str(exc)[:100]))
            print("%s  ERROR (%s)" % (label, str(exc)[:80]))

    # Summary
    print("\n=== SUMMARY ===")
    print("REACHABLE: %d" % len(reachable))
    print("EMPTY:     %d  %s" % (len(empty), empty))
    print("ERROR:     %d  %s" % (len(errors),
          [(d, e[:40]) for d, e in errors]))

    if city_airport_notes:
        print("\n=== CITY != AIRPORT ===")
        for note in city_airport_notes:
            print("  %s" % note)

    # Print results as JSON for piping to FINDINGS
    print("\n=== RESULTS (JSON) ===")
    print(json.dumps({
        "origin": origin,
        "date": date,
        "reachable": reachable,
        "empty": empty,
        "errors": [{"dest": d, "error": e} for d, e in errors],
        "city_airport_notes": city_airport_notes,
    }, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="Atlas breadth probe — sweep candidate destinations")
    parser.add_argument("--origin", default="SIN",
                        help="Origin city code (default: SIN)")
    parser.add_argument("--date", default="20260918",
                        help="Departure date YYYYMMDD")
    args = parser.parse_args()

    client = AtlasClient()  # live by default; LIVE=1 required
    breadth(client, args.origin, args.date)


if __name__ == "__main__":
    main()
