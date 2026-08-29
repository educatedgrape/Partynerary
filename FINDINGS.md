# Probe Findings

Recorded results from Atlas sandbox probe runs. Every coverage figure cited
in the project scope must trace to a line in this file.

---

## Smoke test

**Command:** `LIVE=1 python probe/probe.py smoke`
**Date:** _pending live run_
**Status:** _not yet run_

- Route: SIN → DPS
- Auth: header AK/SK
- Encoding: gzip
- Contract fields: routings[], segments[], adultPrice, adultTax, transactionFee

---

## Roundtrip test

**Command:** `LIVE=1 python probe/probe.py roundtrip`
**Date:** _pending live run_
**Status:** _not yet run_

- Outbound: SIN → DPS @ 20260918
- Returns: DPS → SIN @ 20260920, 20260922, 20260925
- Each return date is its own search.do call (independent)
- City ≠ airport observations: _to be recorded_

---

## Breadth sweep

**Command:** `LIVE=1 python probe/breadth.py --origin SIN --date 20260918`
**Date:** _pending live run_
**Status:** _not yet run_

### Results

| Category | Count | Details |
|---|---|---|
| REACHABLE | _pending_ | _to be recorded from live run_ |
| EMPTY | _pending_ | _to be recorded from live run_ |
| ERROR | _pending_ | _to be recorded from live run_ |

### Reachable destinations

_To be populated from live breadth sweep output._

### Empty destinations (no routings returned)

_To be populated — recorded explicitly so nobody re-adds them hopefully._

### Error destinations

_To be populated — an error is a finding, not noise._

### City ≠ Airport observations

_To be populated — pairs where a city-code search returns flights into a
different airport in that city._

---

## Hub feasibility

**Command:** `LIVE=1 python probe/hubs.py`
**Date:** _pending live run_
**Status:** _not yet run_

Origin: SIN · Destination: NRT · Leg 1: 20260920 · Leg 2: 20260922

| Hub | Leg 1 (SIN→hub) | Routings | Cheapest | Leg 2 (hub→NRT) | Routings | Cheapest | Viable |
|---|---|---|---|---|---|---|---|
| BKK | _pending_ | - | - | _pending_ | - | - | - |
| KUL | _pending_ | - | - | _pending_ | - | - | - |
| HKG | _pending_ | - | - | _pending_ | - | - | - |
| TPE | _pending_ | - | - | _pending_ | - | - | - |
| MNL | _pending_ | - | - | _pending_ | - | - | - |

Viable hubs: _pending_ of 5
Legs with data: _pending_ of 10

---

## Notes

- Coverage is measured, not assumed.
- No destination enters the catalogue without a probe result.
- EMPTY codes are recorded explicitly so nobody re-adds them.
- A city-code search may return flights into a different airport — recorded above.
- Re-query must re-run the query that produced the offer, never a new query
  built from the flown airport.
