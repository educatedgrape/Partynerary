"""Feed — the CLI entry point for ranking trips with no party and no ceilings.

Usage:
    python -m src.discovery.feed --origin SIN --dates 3

Ranked trips print, each with its comparator line, with no party and no
ceilings. This is the honest behaviour before anyone has said what they can
afford.

DEFAULT_DATES = 3, because each date costs a full sweep.
"""

import argparse
import sys

from src.atlas.client import AtlasClient
from src.discovery.sweep import sweep, return_dates_for, best_per_destination, DEFAULT_DATES
from src.discovery.score import score_sweep, apply_ceilings


def feed(client, origin, dates=DEFAULT_DATES, destinations=None,
         party_size=1, ceilings=None, limit=8):
    """Run a sweep and return ranked trips.

    Same engine with no party: no ceilings, so headroom() returns None and
    ranking falls back to value and fit.

    Args:
        client:       AtlasClient instance
        origin:       departure city code
        dates:        number of departure dates to try (consecutive days)
        destinations: list of destination codes
        party_size:   number of travellers
        ceilings:     optional list of per-member ceilings
        limit:        max results per destination

    Returns:
        (ranked, errors, vetoed)
    """
    # Generate departure dates
    from datetime import datetime, timedelta
    today = datetime.now()
    out_dates = []
    for i in range(dates):
        d = today + timedelta(days=14 + i * 7)  # 2 weeks out, weekly
        out_dates.append(d.strftime("%Y%m%d"))

    if not out_dates:
        return ([], [], [])

    # Use the first departure date for return date derivation
    out_date = out_dates[0]
    ret_dates = return_dates_for(out_date)

    # Sweep
    all_trips, errors = sweep(
        client, origin, out_date, ret_dates, party_size,
        destinations=destinations)

    # Score
    scored = score_sweep(all_trips, ceilings=ceilings)

    # Apply ceilings
    survivors, vetoed = apply_ceilings(scored, ceilings)

    # Best per destination
    ranked = best_per_destination(survivors, limit=limit)

    return (ranked, errors, vetoed)


def main():
    parser = argparse.ArgumentParser(description="Feed — rank trips from Atlas")
    parser.add_argument("--origin", default="SIN", help="Origin city code")
    parser.add_argument("--dates", type=int, default=DEFAULT_DATES,
                        help="Number of departure dates")
    parser.add_argument("--destinations", nargs="*",
                        help="Destination codes (space-separated)")
    parser.add_argument("--party-size", type=int, default=1,
                        help="Number of travellers")
    args = parser.parse_args()

    client = AtlasClient()
    ranked, errors, vetoed = feed(
        client, args.origin, args.dates, args.destinations,
        party_size=args.party_size)

    if errors:
        print("=== ERRORS ===")
        for e in errors:
            print("  %s: %s" % (e.get("destination", "?"), e.get("error", "?")))
        print()

    if vetoed:
        print("=== MATCHED BUT UNAFFORDABLE ===")
        for v in vetoed:
            print("  %s — per_person %.2f, over tightest ceiling of %.2f by %.2f" % (
                v["destination"], v["per_person"],
                v["tightest_ceiling"], v["over_by"]))
        print()

    if ranked:
        print("=== RANKED TRIPS ===")
        for st in ranked:
            t = st.trip
            print("%s → %s (%d nights)" % (
                t.origin, t.destination_name or t.destination,
                _nights(t)))
            print("  per_person: %.2f  group_total: %.2f  seats: %d" % (
                t.per_person, t.group_total, t.min_seats))
            print("  comparators: %s" % st.comparators)
            print("  cost_refs: %s" % t.cost_refs)
            print()
    else:
        print("No trips found.")

    if not ranked and not errors:
        print("Sweep returned no trips and no errors — check fixtures.")


def _nights(graph):
    """Compute nights from outbound/inbound dates."""
    from datetime import datetime
    if len(graph.legs) < 2:
        return 0
    try:
        out_dt = datetime.strptime(graph.outbound.date, "%Y%m%d")
        ret_dt = datetime.strptime(graph.inbound.date, "%Y%m%d")
        return (ret_dt - out_dt).days
    except (ValueError, AttributeError):
        return 0


if __name__ == "__main__":
    main()
