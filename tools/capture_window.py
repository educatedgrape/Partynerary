#!/usr/bin/env python3
"""Capture search.do responses for offline analysis.

The breadth probe captures one date. This script captures EVERY leg the
demo queries: outbound SIN->D on the agreed date, inbound D->SIN on each
derived return date.

Every successful live response is saved to fixtures/capture/ for offline
inspection. Missing responses fail loudly later, so capturing less than
the window is visible, not silent.

Usage:
    LIVE=1 python tools/capture_window.py --probe probe/results_SIN_20260918.json \
        --out-date 20260918
"""

import argparse
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.atlas.client import AtlasClient, AtlasHTTPError
from src.atlas.models import fixture_key, parse_routings
from src.discovery.sweep import return_dates_for
from probe.breadth import _payload


# Default capture directory — not fixtures/live/ (removed), not fixtures/test/.
CAPTURE_DIR = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "capture"


def capture(client, origin, dest, date, capture_dir, retries=2, delay=1.0):
    """One search.do call, retried on 429. Returns 'ok', 'empty', or error."""
    key = fixture_key(origin, dest, date)
    for attempt in range(retries + 1):
        try:
            result = client.post("search.do", _payload(origin, dest, date),
                                 fixture_key=key)
            if result is None:
                return "error: no response"
            routings = parse_routings(result, cache_key=key)
            # Save to capture directory for offline inspection
            if routings:
                safe_key = key.replace(":", "/")
                out_path = capture_dir / (safe_key + ".json")
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with open(out_path, "w") as f:
                    json.dump(result, f, indent=2)
            return "ok" if routings else "empty"
        except AtlasHTTPError as exc:
            if exc.status == 429 and attempt < retries:
                time.sleep(5.0)
                continue
            return "error: HTTP %d" % exc.status
        except Exception as exc:
            return "error: %s" % str(exc)[:60]
    return "error: retries exhausted"


def main():
    parser = argparse.ArgumentParser(
        description="Capture search.do responses for offline analysis")
    parser.add_argument("--probe", required=True,
                        help="Probe results JSON (from breadth probe)")
    parser.add_argument("--origin", default="SIN")
    parser.add_argument("--out-date", default="20260918")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Seconds between calls (rate-limit guard)")
    args = parser.parse_args()

    if os.environ.get("LIVE", "0") != "1":
        sys.exit("Set LIVE=1 — this script only captures live responses.")

    data = json.loads(pathlib.Path(args.probe).read_text(encoding="utf-8"))
    reachable = [r["dest"] for r in data["reachable"]]
    # Destinations captured individually after rate-limit errors
    for extra in ("KUL", "PEN"):
        if extra not in reachable:
            reachable.append(extra)

    ret_dates = return_dates_for(args.out_date)

    # Inbound legs: D->SIN on every derived return date. Hub->dest stopover
    # legs are captured targeted — they depend on which hubs reconcile ranks,
    # and 404s name exactly which keys are missing.
    plan = []
    for dest in reachable:
        for rd in ret_dates:
            plan.append((dest, args.origin, rd))

    client = AtlasClient()

    print("Window capture: %d legs" % len(plan))
    stats = {"ok": 0, "empty": 0, "error": 0}
    for i, (frm, to, date) in enumerate(plan):
        status = capture(client, frm, to, date, CAPTURE_DIR)
        bucket = status.split(":")[0]
        stats[bucket] = stats.get(bucket, 0) + 1
        print("[%3d/%d] %s->%s @%s  %s" % (
            i + 1, len(plan), frm, to, date, status))
        time.sleep(args.delay)

    print("\nDone. ok=%d empty=%d error=%d" % (
        stats["ok"], stats["empty"], stats["error"]))
    if stats["error"]:
        sys.exit("Errors above must be re-captured.")


if __name__ == "__main__":
    main()
