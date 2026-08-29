# Partynerary — Project Scope

**Autonomous group-travel flight agent. Finalized architectural specification.**

Origin `SIN` · Python 3.14 + Next.js (static export) · Atlas sandbox `https://sandbox.atriptech.com`

---

# Executive Summary & Core Premise

Partynerary turns *"where should the four of us go?"* into a priced,
seat-checked, budget-filtered itinerary — using a real flight search API as the
only source of truth about the world.

The premise is a constraint on what an agent is allowed to say. Group travel
planning fails on two things: agreeing a date, and agreeing a price. A language
model can be made to sound authoritative about both, which is precisely the
failure mode. So the system is built the other way round:

**Every number rendered anywhere in the product is a pointer into an Atlas
response.** A figure is stored as a `cost_ref` —
`search.do:SIN-ICN@20260920#routings[5].adultPrice` — and dereferenced at render
time out of the response cache. There is no `amount` field in the agent message
schema; a model that tries to author a price is rejected by the schema itself,
not by a filter. The semantic layer that reads free text runs *before the first
API call* and therefore structurally cannot see a price to invent one.

Four engines sit behind that rule:

| Engine | Answers | Authority |
|---|---|---|
| **Vibe Matcher** | *Which places match what they described?* | Dense vector retrieval. Proposes **queries**, never answers. Cannot see a fare. |
| **Date Scheduler** | *Which departure date can everybody live with?* | Monotonic concession over private rankings. |
| **Atlas Flight Search** | *What exists, what it costs, how many seats are left* | **The only authority on inventory and price.** |
| **Reconciliation Engine** | *Who did the winning trip leave out, and can a hub fix it?* | Constructs the multi-city option from unsatisfied preferences. |
| **Repair Loop** | *Something changed — what breaks, for whom, and what is the smallest fix?* | Walks the dependency graph, re-plans cheapest-change-first, re-negotiates. Ceilings stay absolute. |

## What this is not

The output looks like an itinerary, which invites the wrong reading. A generator
answers a question once and hands back a document. This system holds a live
commitment and repairs it.

| A trip generator | This |
|---|---|
| Answers once, then the answer is stale | Holds a commitment and keeps checking it against Atlas |
| Reports a change: *"the outbound is $42 dearer"* | Reasons about consequences: *fare 200 → 242 → Marcus exceeds the ceiling he granted → consensus invalidated* |
| Returns a list and lets you re-run the search | Repairs cheapest-change-first — swap the return before the outbound, the outbound before the destination |
| Treats a budget as a filter on a query | Treats a ceiling as **standing authority** that can be withdrawn: re-grant a lower one and a trip the group already accepted is vetoed |
| Stops when it has answered | Loops back to the decision node with fresh, ceiling-checked options |
| Prices are text | Every figure is a pointer into a response Atlas returned this run |

The last three rows are the ones a search product cannot reach without becoming
this. A generator has no commitment to invalidate, no delegated authority to
withdraw, and nothing to repair.

And one rule that overrides all four: a member grants a **ceiling**, never a
wallet. A ceiling is the only thing in the system that can delete a trip. It is
never out-voted, never rounded, and no split, transfer, or inter-member ledger
exists anywhere in the system.

**Coverage is measured, not assumed.** The destination catalogue is populated
from a live probe sweep before any of it is built against — the published
documentation names only a handful of supported routes and warns that arbitrary
routes may return empty results, so a catalogue assembled from intuition
produces a board full of gaps. Hub viability is probed separately, on both sides
of each candidate stopover: a hub that answers in only one direction cannot
carry a chain. Every coverage figure this document cites traces to a recorded
probe run, and no destination enters the catalogue without one.

## The single interaction point

The product asks the user for exactly one decision. Everything before it is
agent work; everything after it is autonomous execution.

```
spawn agents → vector vibe match → date consensus → Atlas sweep
    → live ranked board (real-time API coverage, on screen)
    → synthesis: reconcile unsatisfied preferences, query hubs
    → ONE CHOICE:  Option 1 (Cheapest / Primary Fit)
                   Option 2 (Multi-City / Group-Optimized)
    → autonomous: re-price → book → pay → receipt
```

The choice **is** the confirmation. It is bound to an action, a target, and the
price displayed at the moment it was made, and it is voided automatically if the
fare rises before the order exists.

---

# Flight Search API Integration & Engine Architecture

This is the flagship component. Everything else exists to shape a good query or
to reason honestly about the answer.

## 1. Transport layer — `src/atlas/client.py`

One class, `AtlasClient`, is the only thing in the system that opens a socket to
Atlas. Enforced by test.

| Concern | Implementation |
|---|---|
| Auth | Header AK/SK — `x-atlas-client-id` / `x-atlas-client-secret`, from `.env`, never a call site |
| Live gate | No live call fires unless `LIVE=1`; otherwise `LiveCallBlocked` |
| Encoding | Responses are gzip; `urllib` does not auto-decompress, so `_decode()` does |
| Timeout | 12s — a sweep is a dozen sequential calls, and a 30s timeout stalls the UI for half a minute |
| Errors | `AtlasHTTPError` carries status + body. **An error body is never cached and never returned as data** — otherwise a 401 becomes "Bali has no flights" |
| Capture | Every live response is frozen to `fixtures/live/` under its own key, so any live run is replayable exactly as it happened |
| Cache | Every successful response lands in `RESPONSE_CACHE` under its fixture key — the substrate every `cost_ref` dereferences against |

**Fixture key format, one definition** (`routes.fixture_key`):

```
search.do:SIN-DPS@20260918
```

Left of the `#` in every `cost_ref` in the system.

## 2. Request contract — `search_payload`

```python
{
  "tripType": "1",
  "adultNum": adults, "childNum": 0, "infantNum": 0,
  "fromCity": origin,      "fromAirport": "",
  "toCity":   destination, "toAirport":   "",
  "fromDate": "YYYYMMDD",  "retDate": "",
  "airlines": [], "fromFlightNumbers": [], "retFlightNumbers": [],
  "includeMultipleFareFamily": false,
  "currency": null, "displayCurrency": "", "requestSource": null
}
```

Dates are `YYYYMMDD`; datetimes are `YYYYMMDDHHmm`. No separators, ever.

**City ≠ airport.** A `BKK` city search returns flights into `DMK`. Any
re-query — a re-price, a leg swap, a hub chain — must re-run *the query that
produced the offer*, never a new query built from the flown airport.

## 3. Response contract — `src/atlas/models.py`

Atlas returns `routings[]`, **not** `offers[]`. A priced itinerary is a *routing*.

**There is no total price field.** It is computed, and the formula matters:

```python
total = (adultPrice + adultTax) * adultNum + transactionFee
```

A group booking must reserve the **order** total, and it undercounts along two
independent axes if either is missed:

| Axis | Failure | Guard |
|---|---|---|
| **Passengers** | Pointing a mandate at bare `adultPrice` undercounts a party of four by 4× | `resolve_group_total()` applies the documented formula |
| **Legs** | Pointing it at one routing reserves one flight of a return trip | The proposal carries **one ref per leg**; the total is the sum across all of them |

`cost_ref.resolve_group_total()` derives each leg's total from that ref's
siblings, so every input to the arithmetic is still dereferenced — the agent
never holds a number it cannot trace.

**A leg's siblings do not reach another leg.** `sibling()` walks within a
routing — `adultPrice` → `adultTax` — but the return leg lives under a different
cache key (`search.do:PEN-SIN@20260923`), structurally unreachable from the
outbound ref. There is no derivation that recovers it. The refs must be carried,
which is why `ActionProposal.cost_refs` is a list and not a string.

Parsed per routing: `segments[]` (each with `flightNumber`, `seatCount`,
`fareFamily`), `min_seat_count`, `elapsed_hours`, `transit_hours`, `carriers`,
`is_multi_carrier`, `through_checked_baggage`. Each routing exposes
`price_ref()`, `tax_ref()`, `fee_ref()`.

`transactionFee` is frequently `0.00` in sandbox responses. That is an
observation about the data, not a licence to drop the term from the formula.

## 4. Routing evaluation — the sweep

`src/discovery/sweep.py` is the search execution engine. Its governing rule is
**scan and filter, never construct.**

```
sweep(client, origin, out_date, return_dates, party_size, destinations)
  └─ per destination: trips_for(...)
       ├─ search_nodes(OUTBOUND, origin → dest, out_date)
       ├─ search_nodes(RETURN,   dest → origin, each return_date)
       └─ every (outbound × return) combination → ItineraryGraph
```

Each candidate is a **whole itinerary graph** — outbound node, return node, and
the dependency edges between them — not a one-way fare. That is what lets the
system reason about a trip continuously instead of pricing a purchase, and it is
what the multi-leg engine extends rather than replaces.

Filters applied inside the sweep:

- **Seat count is a hard filter, not a warning.** `seatCount >= party_size` or
  the routing is dropped. If *every* routing fails it, the destination reports
  `"N routing(s) returned but none seat a party of M"` — a data gap surfaced in
  the UI, never a crash and never a silent drop.
- **Structural validity.** A return that departs before the outbound lands is
  not a worse trip, it is not a trip.

`sweep()` deliberately returns **every** valid combination, not the cheapest.
Returning only the cheapest would be locally optimal — no single-node swap could
improve on it — and the re-planner would have nothing to find.

Deduplication happens after ranking:

- `best_per_destination(ranked, limit)` — one card per **city searched**.
- `best_per_shape(ranked, limit)` — one per `(destination, nights)`, where trip
  *length* is the comparison.

## 5. Fare evaluation — `src/discovery/score.py`

There is no price history, so the system cannot say "30% below normal". What one
sweep honestly supports is a comparator with a visible denominator.

```python
WEIGHTS = {
    "vibeScore": 0.55,   # semantic fit from vector retrieval
    "value":     0.25,   # the fare, normalised across today's sweep
    "headroom":  0.20,   # slack against the TIGHTEST ceiling in the group
}
```

Fit leads, price orders. Budget is unaffected either way — a ceiling is a hard
filter applied *before* any of this, and nothing in ranking can resurrect a trip
somebody cannot afford.

**`seatCount` is displayed but deliberately never scored.** Letting scarcity
raise a score means a dearer trip can outrank an identical cheaper one because it
is running out. That is pressure-selling, not ranking.

**An explicitly named place sorts in its own tier**, above inferred matches, with
price ordering within each tier. Naming a place leaves nothing to infer, so it is
not made to compete with a fare on the same axis.

Every scored trip carries the denominators that produced it, and the UI renders
them verbatim — never a bare score:

```python
comparators = {
  "median_fare_today": 411.20,
  "vs_median": -75.70,
  "headroom_vs_tightest_ceiling": 63.83,
  "seats_left": 9,
  "vibeScore": 0.86,
}
```

`score.explain(trip)` renders one readable line where every clause names its
denominator: `"-75.70 vs this sweep's median fare of 411.20 · 63.83 under the
tightest ceiling · 9 seat(s) left"`.

**A pitch must name its comparator.** "Saving 274.86" is only permitted beside
the trip it is measured against, that trip's fare, and that trip's `cost_ref`.
No comparator on the board ⇒ no saving claimed.

## 6. Multi-leg hub search

The same `search.do` engine, chained. Option 2 of the decision node is built
here; the behavioural contract is in
[Reconciliation Engine](#reconciliation-engine--multi-city-synthesis).

**Viability is established by probe before the engine is built.** Each
candidate stopover is searched on both legs and recorded:

| Leg | Routings | Cheapest |
|---|---|---|
| `origin → hub` | probed | recorded |
| `hub → destination` | probed | recorded |

11 of 12 probed hub legs return data — only `KUL-FUK` is empty. A virtual
multi-leg itinerary is **constructible from sandbox data**, not modelled.

**Leg chaining.** A multi-city itinerary is three or more searches, each a
separate `search.do` call under its own fixture key:

```
SIN → HUB    fromDate = D0          key: search.do:SIN-<HUB>@D0
HUB → DEST   fromDate = D0 + stay   key: search.do:<HUB>-<DEST>@D1
DEST → SIN   fromDate = D0 + total  key: search.do:<DEST>-SIN@D2
```

Each leg becomes a `FlightNode` and enters the **same `ItineraryGraph`** as the
round-trip case. Nothing new is invented: the existing dependency edges validate
the connections, and `structural_violations()` rejects any chain where a leg
departs before its predecessor lands.

**Constraints the chained search must satisfy — all hard filters:**

| Constraint | Rule |
|---|---|
| Stopover window | Validated by the temporal dependency edges. A stopover shorter than the minimum connection time is not a cheaper trip, it is not a trip. |
| Seats | `seatCount >= party_size` **on every leg**. One unseatable leg kills the chain. |
| Ceilings | The **combined** total is tested against every member's ceiling. A chain that clears leg by leg but breaches in total is rejected. |
| Traceability | Every leg carries its own `cost_ref`. The combined price is arithmetic over dereferenced figures — never a blended number without a denominator. |
| Comparator | Option 2's price is only ever displayed beside Option 1's price and Option 1's `cost_ref`. |

**Call-budget guardrail.** A hub chain multiplies searches. The engine evaluates
at most `MAX_HUB_CANDIDATES` hubs (default 3), selected before any call is made —
the same pre-filtering discipline retrieval applies to the destination sweep.

## 7. Post-confirmation re-price — `src/booking/reprice.py` *(gate file)*

The single most important sequencing decision in the product:

> **The price check runs AFTER the user's choice and BEFORE the order.**

A price check *before* confirmation tells you what the fare was when you asked.
The window that actually matters is between the human saying yes and the order
existing — and low-cost fares move in exactly that window.

`apiguide.md` names a "(verify/pricing step)" but publishes no path and no shape.
So re-price **re-runs `search.do`** — fully characterised — and matches the
candidate on `flightNumber` across segments. It never parses
`routingIdentifier`, which is documented as opaque, and it never invents a verify
endpoint.

| Status | Effect on the confirmation |
|---|---|
| `UNCHANGED` | stands |
| `CHEAPER` | stands — a cheaper fare cannot invalidate consent |
| `DEARER` | **void.** The choice authorised a price that no longer exists |
| `GONE` | **void.** The flights are no longer offered |

A `DEARER` or `GONE` result is not merely reported — it is fed into
`propagate.Change` and walked through the itinerary graph, so the system says
*what breaks and for whom*, then re-plans cheapest-change-first and returns to
the decision node with fresh options.

**Under autonomous execution this gate carries more weight, not less.** With one
choice authorising the whole downstream chain, the price binding on the
`Confirmation` is the only thing standing between the agent and an unapproved
charge — so it is enforced at **both** the booking gate and the payment gate.

**Every leg, every trip.** Re-price re-searches **all** legs under their own
keys and the verdict is the **worst** across them: any leg `DEARER` or `GONE`
voids the whole confirmation. This applies to a two-leg return trip exactly as
it does to a chain — a trip is one purchase decision and cannot be partially
re-confirmed. Re-pricing only the outbound leaves the return fare unguarded,
which is a silent hole in the one gate the product is built around.

**The staleness comparison must be dimensionally consistent.** The
`Confirmation` stores the whole-trip per-person price the human saw. The
executor must compare against the whole-trip per-person price now — the order
total divided by party size. Comparing an outbound-only figure against a
whole-trip figure does not merely weaken the check; it inverts it. The
outbound-only number is always the smaller one, so `now <= price_shown` holds
by construction and the guard passes on every fare rise it exists to catch.

## 8. Replay — a recording, never a curated universe

`AtlasClient(replay=True)` swaps exactly one thing: it reads
`fixtures/…/<KEY>.json` instead of POSTing. Everything downstream is identical —
same parsing, same `RESPONSE_CACHE`, same `cost_ref` dereferencing.

**The system runs live. Replay is a recording of a run that happened, and
nothing else.**

That distinction is load-bearing, because replay swaps the transport and can
silently swap something far more important: **the candidate universe.** The
`cost_ref` architecture guarantees no invented price. It says nothing about a
curated candidate set — so a hand-picked fixture directory moves the integrity
problem one layer earlier, to where none of the guarantees are looking.

A semantic layer choosing the best fit from a set somebody selected in advance
is not choosing. It is ratifying.

### The rule

> **No module may contain a hand-written list of destinations to capture.**

Every live response is written to `fixtures/live/` under its own key as it
arrives, so a live run is replayable *exactly as it happened*. Retrieval is
deterministic, so the same inputs reproduce the same shortlist — which makes the
recording faithful rather than approximate.

| Use | Verdict |
|---|---|
| Tests — deterministic, no credentials | **Keep.** `fixtures/test/`, a separate frozen set; needs stability, not breadth |
| Re-running a recorded session after a rate limit | **Keep** — it replays the run that actually happened |
| Constituting the pool the agent chooses from | **Forbidden.** This is the flaw |

The catalogue is the universe in both modes. If a run should cover more of it,
run it again live — never widen the pool by authoring one.

The honest mitigation for a rate limit or an outage mid-demo is *run it again*,
not *fall back to a prepared set*. That is a real operational cost, accepted
deliberately in exchange for the central claim being true.

Provenance is tracked and rendered: `_provenance()` returns `True` if *any*
response currently on screen is placeholder data, `False` if all are real, and
`None` before Atlas has been asked anything. Three states, not two — "we have not
asked yet" is not the same claim as "everything here is real".

---

# Agent Lifecycle & Autonomous Decision State Machine

## The stage machine — `src/party/orchestrator.py`

```
 1  DATE CONSENSUS    agents concede over private date windows      [Group 01]
 2  ATLAS DISCOVERY   sweep both directions; Atlas proposes destinations
 3  CONSTRAINT CHECK  seats, structure, and EVERY member's ceiling
 3b RECONCILIATION    find unsatisfied members; query hubs
 4  DECISION NODE     the single user choice: Option 1 vs Option 2
 5  CONFIRM           the choice IS the confirmation                [Group 02]
 6  RE-PRICE          check the fare again, AFTER the choice
 7  ORDER + PAY       autonomous, under the standing confirmation   [Group 02/03]

 — and then it does not stop —

 8  CHANGE            Atlas says something different about one node
 9  PROPAGATE         walk the graph: what breaks, and for whom
10  RE-PLAN           explore alternatives, cheapest-change-first
11  RE-NEGOTIATE      the agents choose again, ceilings still absolute
    → back to 4

 — and the order existing does not end it —

12  WATCH             re-shop the booked legs; the world keeps moving
    → an order-affecting change re-enters at 8            [Group 04]
```

Stages 8–11 are the difference between "the flight got more expensive" and
reasoning over a connected itinerary. Nothing in the orchestrator talks to Atlas
directly and nothing in it spends anything; stages 5–7 hand a proposal to the
executor, which applies its own gates regardless of what the orchestrator
believes.

## Stage 0 — Initialization

The party starts **empty on purpose**: nothing downstream means anything until
somebody is actually in the room. Each agent is spawned with:

| Parameter | Required | Notes |
|---|---|---|
| `name` | yes | Identity on screen |
| `budget` | yes | **Airfare ceiling.** Decision authority, not a wallet |
| `preferences` | yes | Free text — likes, dislikes, cuisines, vibes |
| `ics` | no | Calendar file, or one of four preset calendars |

Adding a member re-runs retrieval immediately (it makes no API calls, so it is
free) — a new person changes the group vibe the moment they walk in.

## Stage 1 — Date resolution by monotonic concession

`src/party/concession.py`. A real mechanism, not an animation over a solver:

1. Each agent holds its principal's ranking **privately**. `public_view()` never
   exposes `busy_days` or `date_ranking`, and a test enforces it.
2. Round 1: every agent names only its single favourite date.
3. No date named by everyone → each agent **concedes**: names the next date down
   its own ranking. **It may never go back up.**
4. Consensus is the first date every agent has named.
5. An agent that would concede past its reservation depth **withdraws**, and the
   group learns no date works.

Termination is guaranteed: each round every agent either reveals one new date or
withdraws, and both are finite. Monotonicity is what makes the transcript a
genuine record of narrowing positions.

Rounds are **synchronous and simultaneous** by design, and a test enforces
it.

`.ics` ingestion turns a calendar into a ranking: clashing days are dropped
entirely, and Fri/Sat starts are preferred.

## Stage 1.5 — Vector vibe matching: queries, not answers

Retrieval is the only thing between a settled date and a sweep, and it is
**deliberately not a decision** — it emits a list of **queries**.

```
generated global city dataset → retrieve(members, limit=14)
                              → 14 places to ask Atlas about
```

The cap is a rate-limit guardrail as much as a ranking one: a sweep is several
calls per place per date.

Three properties, each enforced by test:

- It **cannot see a price** — it runs before the first call. A `Match` has no
  price, fare, amount, cost, total, or saving field.
- Every candidate must exist in the **probed Atlas catalogue**. Retrieval ranks
  the whole dataset; it may only *propose* cities Atlas has answered for. A
  vector hit on an unreachable city is a query that wastes a call and returns a
  gap.
- Input it cannot place is **reported to the user, never silently ignored**.
  Dense retrieval always returns *something*, which makes silent failure easier,
  not harder — so a query where no clause clears `MIN_SIMILARITY` and no entity
  resolves is surfaced, not absorbed into a confident answer.

Full contract: [Vibe Matcher](#vibe-matcher--hybrid-vector-retrieval).

## Stages 2–3 — Atlas proposes, constraints filter

The agent **does not choose a destination**. It fixes a date and a party size,
sweeps `search.do` across the retrieved candidates in both directions, and
whatever comes back is the candidate set. Then:

- Seat count filters (hard).
- **Every** member's ceiling filters (hard). A member whose ceiling is exceeded
  **vetoes**. The group never out-votes a veto and no ceiling is ever rounded to
  make a deal work.
- Rejections are kept and rendered with reasons, plus `rejected_total` before
  deduplication — a filtered trip must not merely vanish.
- `state.missed` records places the group **asked for**, that Atlas **has**, and
  **nobody can afford**, quoting the closest miss for that city.

Agents negotiate the **date only**. Destination comes from Atlas inventory; price
and seats are filters, never trades.

## Stage 3b — Reconciliation and synthesis

Runs after the sweep completes and before the decision node opens. It answers one
question: **who did the winning trip leave out?**

```
ranked survivors
  → Option 1 = best-fit / cheapest survivor
  → gap analysis: per member, which stated preferences Option 1 does not satisfy
  → hub selection: which reachable hubs satisfy those gaps
  → chained search (≤ MAX_HUB_CANDIDATES) via search.do
  → Option 2 = cheapest viable chain that closes the most gaps
```

**The synthesis delay is the synthesis.** The pause between the ranked board and
the decision modal is this work actually running — hub queries against a live API
— surfaced as a status stream naming each call. It is never a cosmetic timer. If
reconciliation finds no viable chain, the modal opens with Option 2 disabled and
states why; it never fabricates a second option to fill the slot.

## Stage 4 — The decision node

The single interaction point. Two options, one selection event, no further
prompts. UI contract:
[Stage 2 — The decision surface](#stage-2--the-decision-surface).

## Stages 5–7 — The single confirmation and autonomous execution

**The selection event produces the `Confirmation`.** There is no separate confirm
click. The `Confirmation` is bound to an action, a target, **and the price on
screen at selection** — `price_shown` plus the `price_ref` that produced it.

Then, without further interaction:

```
selection
  → Confirmation { action, target, price_shown, price_ref, approved_by, at }
  → RE-PRICE       (search.do again; DEARER or GONE ⇒ confirmation void)
  → book_group     (Group 02 — RESERVES authority)
  → pay_group      (Group 03 — SETTLES authority)
  → receipt
```

What autonomy changes, and what it does not:

| | Behaviour |
|---|---|
| **Changes** | `GROUP_BOOK` and `GROUP_PAY` become autonomously executable **while a valid `Confirmation` is standing** — scoped to that exact target and that exact price. They are not autonomous in general. |
| **Unchanged** | Every ceiling still vetoes absolutely. |
| **Unchanged** | The post-selection re-price still runs, and a `DEARER` or `GONE` verdict still voids the confirmation and halts the chain. |
| **Unchanged** | Booking **reserves**; only payment **settles**. |
| **Unchanged** | All five executor gates run on every proposal. |
| **Strengthened** | The price binding is enforced at the payment gate as well as the booking gate. One choice authorising two money-moving actions must be re-checked at both, or the second is unbound. |

When the chain halts it halts **into the propagate/re-plan loop** and returns to
the decision node with fresh options — it does not dead-end. The user's next
selection is a new confirmation at the new price.

## Stages 8–11 — The repair loop

The stages that make this an agent rather than a search result. A booked trip is
not a finished document; it is a commitment with dependencies, and Atlas can
contradict any of them at any time.

### Four kinds of change, one loop

```python
PRICE     # Atlas returns a different fare for a leg
SCHEDULE  # the times moved
GONE      # the routing is no longer offered
CEILING   # the trip did not move - the CONSTRAINT did
```

`CEILING` is the one that has no analogue in a search product. A member
re-grants a lower airfare ceiling and the same fare is now measured against a
different limit, so a trip the group had already accepted is vetoed. Nothing was
re-searched and no price changed. **Delegated authority was withdrawn**, and the
system has to work out what that broke.

### Propagation — consequences, not deltas

```
Input   : ItineraryGraph, Change, mandates[]
Output  : (Impact, repaired_graph)
```

```python
@dataclass
class Impact:
    change: Change
    total_before: float = None
    total_after: float = None
    downstream: list = field(default_factory=list)     # node keys reached
    breached: list = field(default_factory=list)       # member names
    structural: list = field(default_factory=list)     # Violation
    replanable: list = field(default_factory=list)     # worth re-planning
    consensus_invalidated: bool = False
    still_feasible: bool = True

    def narrate(self) -> list:
        """The chain, in order, one line at a time."""
```

`narrate()` is the difference between noticing and reasoning:

```
outbound +42.00
fare 200.00 → 242.00
Marcus exceeds the ceiling he granted
group consensus invalidated
re-planning the dependent itinerary
```

**Two rules that make propagation trustworthy:**

**Edges are one-way by construction.** `downstream_of(node_key)` follows the
dependency direction, so a change to the return can never strand the outbound.
Reachability is structural, not a heuristic.

**A change is applied by swapping in the node Atlas returned — never by editing
a price in place.** Editing would leave the graph reporting one number while its
`cost_ref` resolved to another: the screen saying 151.50 and the pointer saying
96.00. The traceability claim would be quietly false. `apply_change()` returns a
**copy**, so before and after can be shown side by side and the new world can be
rejected.

### Re-planning — cheapest-change-first

When consensus is invalidated the system does not return to a blank search. It
walks the graph it already has and varies exactly one thing:

| Order | Alternative | Why this order |
|---|---|---|
| 1 | same place, different **return** | Moving one leg disturbs the group's agreement least |
| 2 | same place, different **outbound** | Still the same destination everyone agreed to |
| 3 | different **destination** | The largest repair, offered last |

Every alternative is a complete `ItineraryGraph`, re-checked against **every**
member's ceiling. **Nothing that breaches a ceiling is offered at all** — the
group never gets to vote on something one of them cannot afford, so a ceiling
cannot be eroded by a majority in a moment of inconvenience.

Alternatives carry `delta_vs_broken` (per-person, against the trip that broke)
and `rejected_for` (members who still cannot afford it), so a repair names what
it costs relative to what it replaces.

### Re-negotiation, and the loop closing

The agents choose again over the surviving alternatives. Ceilings remain
absolute; the concession protocol is unchanged. The loop returns to the decision
node — **stage 4, not a dead end** — and the user's next selection is a fresh
confirmation bound to the new price.

### The loop must be able to give up

A cycle with no exit condition is the most expensive failure an agent can have,
because it throws no error. Stages 8–11 form a genuine cycle — a change
invalidates consensus, re-planning produces alternatives, the agents
re-negotiate, the selection triggers a re-price, and a re-price can produce
another change.

**The dangerous case is not oscillation. It is a loop that looks like progress.**
Consider a party whose tightest ceiling sits just under every viable option.
Each round the re-planner legitimately finds new candidates, each is
ceiling-checked, each fails, and it widens to the next dimension. Every round
does real work, produces output, and spends an Atlas call — and the group is no
closer, because the binding constraint was never the itinerary.

Nothing about that state is detectably wrong from inside a single round.

#### A counter alone is not the fix

Halting at N rounds stops the spend and produces a worse artifact: no chosen
trip, no explanation, and a surface stuck mid-repair. That trades an expensive
failure for a confusing one. **The give-up path is the design work**, and it has
three parts.

**A terminal state that is a real answer.** The loop exits holding the best
surviving option and a plain statement of why it stopped:

> *After 4 rounds, nothing cleared Marcus's ceiling of 210.00. The closest was
> Penang at 268.17 — short by 58.17.*

That is [The Trade](#the-trade--what-the-groups-limits-cost-them) reached from a
different direction, and it is a **successful outcome**. "I could not solve this,
and here is precisely what blocked it" is the correct result for an unsolvable
constraint set. Looping until the budget is gone is not.

**Exhausted and blocked are different findings.**

| Reason | Meaning | What the user is told |
|---|---|---|
| `exhausted` | The re-planner ran out of dimensions to vary | A factual end. Here is the best of what existed. |
| `blocked` | One constraint killed every candidate, every round | **Name the constraint.** The useful output is not an itinerary. |
| `budget` | The round cap was reached with candidates still unexplored | Here is the best so far; searching further is possible. |

`blocked` is the most valuable of the three and the one a naive counter never
produces. A group whose every option dies on one member's ceiling does not need
a fifth round of itineraries — it needs to be told that one number is deciding
the trip.

**Progress, not just count.** Two early terminations, both independent of
rounds remaining:

- A round producing **zero new candidates** halts immediately. Nothing further
  can be found by continuing.
- A round where **every candidate breaches the same member** halts and reports
  `blocked`. The loop has diagnosed the problem; spending more calls only
  re-confirms it.

#### Budget shape

```
MAX_REPLAN_ROUNDS      = 4     # per change
MAX_SESSION_ROUNDS     = 12    # across the whole session
```

**Per-change, with a separate session cap.** A per-change budget bounds each
disruption independently, which is what makes a late disruption solvable at all;
a session-only budget means a change arriving near the end finds the allowance
already spent — at the worst possible moment. The session cap exists so total
spend is still bounded when many small changes arrive.

Reaching either cap yields `stopped_because = "budget"` and a stated remainder,
never a silent stop.

The terminal state is rendered as an **outcome, not an error**. A surface that
shows a spinner after the loop gave up is the same silent failure in a different
costume.

### The same loop serves deliberate changes

A user moving a constraint enters at the same place as an Atlas surprise:

- **Move the departure** — re-runs the sweep against a different date. Nothing is
  fabricated; the date change asks Atlas again.
- **Re-grant a ceiling** — emits a `CEILING` change, re-measures fares Atlas
  already returned, and re-runs propagation.

One mechanism handles "the world changed" and "we changed our minds", which is
why neither is a special case.

## Stage 12 — Aftercare: the loop does not end at the order

Most of the value in travel sits **after** the booking, and most systems stop
there. The order existing does not make the itinerary true — it makes it a
commitment that the world can now contradict at a higher cost.

Aftercare is **not a second engine.** It is the same repair loop
([stages 8–11](#stages-811--the-repair-loop)) entered from a post-order trigger.
If a separate post-order planner appears, the design has gone wrong.

### Re-shop is a search, not a guessed endpoint

`queryOrderDetails`, `void`, `refund` and `balance` are **undocumented**. None of
them may be invented.

So re-shopping an existing order **re-runs `search.do`** for the booked legs and
compares what Atlas returns now against what was actually paid — the same
substitution, and the same discipline, as the post-confirmation re-price. The
booked routing is matched on `flightNumber` across segments; `routingIdentifier`
is never parsed.

That yields three post-order findings, each a `Change` the existing propagation
already understands:

| Finding | Change kind | Meaning |
|---|---|---|
| The booked routing is no longer offered | `GONE` | The itinerary is broken and needs repair |
| Its times moved | `SCHEDULE` | Connections and dependency edges may no longer hold |
| An equivalent leg is now cheaper | `PRICE` | A repair may be **owed to the traveller**, not charged to them |

The last one is the one nobody builds. A fare that falls after booking is a
credit the traveller never learns about, and it is visible with exactly the
machinery already in place.

### The trigger is pluggable; the reasoning is not

No schedule-change webhook is documented, so **assume it never fires.** The
system must not depend on being told. Two supported triggers, both feeding the
same entry point:

- **Poll** — re-shop the booked legs on an interval. Read-only, and the only
  trigger that works without provider cooperation.
- **Injected event** — an operator-supplied event for rehearsal, **labelled as
  injected wherever it is rendered**. The payload shape is ours, not Atlas's,
  and the UI says so.

An injected event that renders identically to a real one is the same
misrepresentation as a stubbed call rendering as a success.

### Authority is decided per action, never per group

Group 04 contains one read and three writes, so a group-level autonomy rule is
wrong in one direction or the other:

| Action | Moves money | Authority |
|---|---|---|
| `reshop_order` | no | **Autonomous.** Read-only; safe to run on a timer |
| `change_order` | yes | Human confirmation, or a standing confirmation bound to the fare difference |
| `cancel_order` | yes | Human confirmation. Always |
| `refund_order` | credit | Autonomous to **request**; the credit is recorded, never spent |

`AUTONOMOUS_GROUPS` therefore holds `GROUP_SEARCH` only, and read-only aftercare
actions are named individually. Putting all of Group 04 in it would make
cancelling a booked trip an unattended action.

### Accounting after settlement

Pre-order, a repair changes what will be reserved. Post-order the money is
already committed, so the arithmetic is the **difference**, and it moves in both
directions:

- **A dearer repair** must fit the mandate's *remaining* authority. If it does
  not, the ceiling vetoes exactly as before — a member who granted 210 does not
  owe a change fee they never authorised.
- **A cheaper repair or a refund is a credit.** `mandate.credit()` returns
  authority rather than spending it, and the receipt shows it as money coming
  back with its own `cost_ref`.

**A repair is never applied because it is available.** It is applied because it
survives every ceiling — and if it does not, the group is told what it would
have cost and who it would have broken.

### What stays stubbed

Every order-mutating call. The re-shop is real, the propagation is real, the
ceiling arithmetic is real, and the receipt is real. `change_order`,
`cancel_order` and `refund_order` record `executed_stub`, because inventing a
request shape for an endpoint nobody has published is the precise failure this
system exists to avoid.

## The executor's five gates — `src/agent/executor.py` *(gate file)*

Every proposal passes all five in order. A failure at any one is **logged and
refused**, never worked around.

| # | Gate | Refuses when |
|---|---|---|
| 1 | **Schema** | not a well-formed `ActionProposal`, or carries an invented field |
| 2 | **Dereference** | any ref in `cost_refs` does not point at a figure Atlas returned **this run**; the list is empty on a money-moving action; or it does not carry one ref per leg of the target trip |
| 3 | **Mandate** | the summed order total across every leg does not fit under the granted ceiling |
| 4 | **Confirmation** | no standing confirmation, it does not match the target, or the whole-trip per-person price now exceeds what was displayed (`stale_confirmation`) |
| 5 | **Call** | — only now is Atlas touched |

## Authority accounting

Atlas tool groups per `availabletoolcalls.md`:

```python
GROUP_SEARCH    = "01"   # search / price
GROUP_BOOK      = "02"   # verify & book
GROUP_PAY       = "03"   # payment
GROUP_AFTERCARE = "04"   # change / void / refund
```

An action absent from `ACTION_GROUPS` is refused rather than defaulted to the
permissive group.

> **Booking RESERVES authority; only payment SPENDS it.** Committing at both
> points charges the party twice for one set of seats.
> `book_group` → `mandate.reserve()`. `pay_group` → `mandate.settle()`.

---

# Implemented Component Specifications

## Part A — UI layout & state machine

**Next.js App Router, built as a static export** (`output: 'export'`), served
from `web/out` by the same stdlib `ThreadingHTTPServer` that runs the
orchestrator. One command, no second process.

Static export is the requirement, not an optimisation. Next.js in server mode
would put a Node runtime in the demo path and make the stdlib-only claim false.
Python owns every API route, so nothing is lost: there is no server-side render
to do and no route handler to write. The client is a browser app that polls
`/api/state`, and Next.js supplies the App Router, file-based routing and
component conventions on top of that.

**State model:** every mutation returns the **whole** state object. The client
never reconciles a partial update against what it already had — a bug class that
would surface as agents disagreeing with the receipt.

### Stage 1 — Initialization view

**Landing view: the discovery feed.** Ranked Atlas inventory with **no party, no
ceilings and no agreed date**. Flights are visible before anybody has joined.
Runs on a worker thread so it never blocks `/api/state`.

**Passengers panel + `AddAgentModal`:**

| Control | Type | Binding |
|---|---|---|
| Name | text | `name` |
| Airfare budget | decimal, `$` prefixed | `budget` — validated `> 0`, numeric |
| Preferences | textarea, free text | `preferences` — likes, dislikes, cuisines, vibes |
| Calendar | `.ics` upload **or** one of four presets | `ics` — read client-side, **parsed server-side** |

A preset attaches a **calendar only** — the operator still names the agent and
sets their budget.

`state.can_add` is `false` once a date is agreed: the party is closed.

**Live retrieval feedback** while people are still joining — `state.vibe` (the
group vibe in words), `state.candidates` (each entry carrying `why` and its
`vibeScore`), and `state.unrecognised` (input nobody could place, shown not
swallowed).

### Stage 2 — The decision surface

A **two-phase** surface. Phase A demonstrates live API coverage; Phase B asks for
the one decision.

#### Phase A — live ranked board

Renders as sweep results land. `state.discovering` is `true` while in flight, so
the board says *"asking Atlas"* rather than looking like an empty result.

**Card board** — `state.cards`, one per city searched, each rendering:
destination, per-person fare, group total, seats left, the **comparator line**
from `score.explain()`, and feasibility.

Alongside it:

- **`state.rejections`** + `rejected_total` — what the constraints killed, with
  reasons.
- **The Trade** — `state.missed`, and it is a **first-class panel beside the
  board**, not a footnote under it. See below.
- **`state.gaps`** — pairs that errored or returned nothing, keyed by pair.

The board is **not** dismissed when Phase B opens. The modal overlays it, and the
board remains behind as the evidence the two options were drawn from.

#### The Trade — what the group's limits cost them

The most important panel on the screen, and the reason it exists is a real
failure this design produces by construction.

A member types *"kimchi and kpop"*. Retrieval correctly ranks Seoul and Busan
first. The tightest ceiling in the group then deletes every Korean and Japanese
option, and the board returns five cities nobody asked for. **Every rule behaved
correctly** — a ceiling is absolute and is never out-voted — and the experience
is still *"it ignored what I said."*

The fix is not to soften the constraint. It is to **render the collision**:

```
Seoul   matched on food, street food      cheapest 230.56
        over Marcus's ceiling of 210.00   short by 20.56     [cost_ref]

Taipei  matched on food, hiking, onsen    cheapest 249.15
        over Marcus's ceiling of 210.00   short by 39.15     [cost_ref]
```

Each row states the destination, **what it matched**, the cheapest fare Atlas
actually returned, **whose** ceiling it broke, and **by how much** — with the
`cost_ref` behind the fare. A veto with a number attached is a decision; a veto
without one is the system looking broken.

**The panel is actionable.** Each row offers the constraint change that would
resolve it — raise that member's ceiling, or move the departure — and routes
into `change_constraint`, which re-measures against fares Atlas already returned.
Nothing is fabricated to make the offer.

**Rules:**

- It is rendered **whenever it is non-empty**, at equal visual weight to the
  board — never collapsed, never below the fold, never behind a disclosure.
- It names the member. *"Over budget"* is not the finding; *"over Marcus's
  ceiling by 20.56"* is.
- An empty Trade renders nothing at all. A panel saying *"no trade-offs"* is
  noise on the run where everything fit.
- It never suggests a ceiling the member has not granted. It states the shortfall
  and offers the control; the human decides.

This is the clearest visible consequence of the ceiling architecture, and it is
the difference between a system that filters silently and one that negotiates in
the open.

#### Phase B — dual-option modal

Opens when reconciliation completes. Transition state:

```
sweeping → board_live → synthesizing → decision_open → committed
```

`synthesizing` renders the status stream — the actual hub queries, named as they
run.

**Card 1 — Cheapest / Primary Fit**

```
destination, nights
per-person fare + group total          [dereferenced]
seats left
comparator line (vs median, headroom, seats)
why this matched:  Match.why  ·  vibeScore
```

**Card 2 — Multi-City / Group-Optimized**

```
route:            SIN → HKG → KIX → SIN
stopover:         HKG, 31h
per-leg fares + combined total         [each leg dereferenced]
delta vs Card 1                        [named comparator + its cost_ref]
gaps closed:      "Marcus — street food; Ana — onsen"
seats left        (minimum across all legs)
```

Card 2 states **which members it satisfies that Card 1 does not**. That is its
entire justification for existing, and it is rendered, not implied.

**Disabled state.** If reconciliation finds no viable chain, Card 2 renders
disabled with the reason (`no hub closed a gap` / `chain breached a ceiling` /
`no seats on the connecting leg`). It is never filled with a fabricated
alternative.

**Selection:** `POST /api/decide { option: 1 | 2 }` — commits the choice,
produces the `Confirmation`, and resumes autonomous execution. This is the last
interaction.

### Stage 3 — Autonomous execution dashboard

**Status stream.** A live timeline of agent sub-tasks, each entry sourced from
the decision log — never a scripted sequence:

```
Querying Atlas · SIN→HKG @20260920 … 14 routings
Routing options resolved · 3 chains viable
Re-pricing confirmed itinerary …        fare held at 268.17
Reserving authority · 1072.68 of 1200.00 ceiling
Executing payment · [STUBBED — no documented endpoint]
```

**Other panels:**

- **Terminal** — the agents rendered as a group in one room. Renders
  `state.bubbles`: negotiation moves with **dereferenced figures only**. Any
  number in a speech bubble came from a `cost_ref` via `protocol.render()`.
- **Authority panel** — ceiling, spent, reserved, remaining, currency;
  per-member ceilings; `card_status` (masked).
- **Move the departure** — date chips; re-runs the sweep against a different
  departure. Nothing is fabricated: the date change asks Atlas again.
- **Change · Propagate · Re-plan** — `state.impact`, `state.narration`,
  `state.alternatives`, `replan_rounds`, `explored`.

**Final payload card** — confirmed itinerary (all legs), passenger specs,
transaction status, booking reference, and the itemized receipt: per-member
ceilings, decision log tail, and the provenance badge.

### HTTP contract — `src/ui/dashboard.py`

```
GET  /api/state                    → the whole state object
GET  /api/feed                     → { running, trips[], gaps{}, dates[] }
GET  /api/calendars                → the four preset calendars
GET  /api/receipt                  → rendered receipt

POST /api/feed        { dates? }   → starts the no-party sweep (worker thread)
POST /api/members     { name, budget, preferences, ics }
POST /api/members/remove { name }
POST /api/round       {}           → one concession round
POST /api/settle_date {}           → run rounds to consensus
POST /api/discover    {}           → starts the sweep (worker thread)
POST /api/synthesize  {}           → starts reconciliation (worker thread)
POST /api/decide      { option }   → the single decision; runs the chain
POST /api/renegotiate { index }
POST /api/constraint  { kind: "date"|"budget", date?, member?, ceiling? }
POST /api/reset       {}
```

Every POST returns the full state object. Errors return `{ error }` — a demo must
never die on a click, and a browser disconnect mid-response is not logged as a
crash.

## Part B — Engine contracts

### Vibe Matcher — hybrid vector retrieval

A dense vector search engine over a generated dataset of global cities, fused
with sparse keyword matching. Dense retrieval provides natural-language semantic
matching — `"beach and relax"` maps to Phuket and Bali without either phrase
appearing as a keyword — instead of relying on exact-text filters.

#### City dataset schema

Each destination entry carries explicit keywords, cultural vibes, and associated
phrase vectors:

```jsonc
{
  "cityId":   "HKT",
  "cityName": "Phuket",
  "country":  "Thailand",
  "keywords": ["beach", "island", "seafood", "diving", "nightlife"],
  "vibes":    ["laid-back", "tropical", "party", "budget-friendly"],
  "phrases":  [
    "long white-sand beaches and warm water",
    "cheap seafood grills on the sand",
    "island hopping and day boats"
  ],
  "vectors": {
    "keywords": [ ... ],      // dense embedding of the keyword set
    "vibes":    [ ... ],      // dense embedding of the vibe set
    "phrases":  [ [ ... ] ]   // one embedding per phrase
  },
  "aliases": ["thailand", "thai", "phuket", "andaman"],
  "atlasCoverage": "REACHABLE"   // REACHABLE | EMPTY | UNPROBED
}
```

`atlasCoverage` is derived from probe results and is **not editorial**.

#### Embedding strategy

The runtime is stdlib-only, so embeddings are **generated offline and shipped as
a static asset**:

1. **Build time.** City vectors and a token→vector table are computed once by the
   dataset generator and written to `data/vectors.json`. Nothing at runtime calls
   an embedding service.
2. **Query time.** A user's free text is embedded by token lookup against that
   table with mean pooling — pure arithmetic, no model, no dependency.
3. **Similarity.** Cosine, computed in stdlib, max-pooled per city across the
   keyword / vibe / phrase vectors.

This keeps the no-`pip install` constraint intact while giving true semantic
matching rather than exact-text filtering.

#### Retrieval contract

```
Input   : free-text preference string per member
          { likes, dislikes, vibes }

Process : 1. clause split + negation detection
          2. DENSE  — embed each clause; cosine against keyword / vibe /
                      phrase vectors; max-pool per city
          3. SPARSE — keyword token overlap against the entry's keyword set
          4. ENTITY — alias resolution, longest-match-first
                      ("kimchi" → {ICN, PUS}); exact, not similarity
          5. FUSE   — weighted combination, entity as an overriding tier
          6. FILTER — intersect with atlasCoverage == REACHABLE
          7. NEGATE — clauses under negation subtract; a negatively named
                      place is floored to 0.0

Output  : Match[] ranked by aggregate relevance
```

```jsonc
{
  "cityId": "ICN", "cityName": "Seoul", "country": "South Korea",
  "vibeScore": 0.9125,
  "dense": 0.71, "sparse": 0.50, "named": 1,
  "matched": ["food", "nightlife"],
  "why": "you named it · matches food, nightlife"
}
```

#### Rules the retrieval layer must hold

1. **Entity resolution outranks similarity.** Someone who writes "kimchi" has
   told you where they want to go; that is not a similarity question. Named
   places sort in their own tier, above anything dense retrieval infers —
   otherwise a cheap beach whose seafood vectors are brushed by "sushi"
   outranks the country the group actually named.
2. **Retrieval proposes queries, never answers.** `Match` carries no price,
   fare, amount, cost, total, or saving field. It runs before the first API call.
3. **Coverage filter is mandatory.** A city the vector index loves but Atlas has
   never answered for is a wasted call and an empty card.
4. **Unplaceable input is reported**, never absorbed.
5. **Preferences rank; they never filter.** A disliked vibe lowers a score. Only
   a ceiling or a seat count deletes a destination.
6. **`vibeScore` is always rendered with its `why`.** Never a bare number.

**Negation.** `"X but no Y"` is a like and a dislike, not two likes. `but` is a
clause separator everywhere else, so `"anywhere but Bangkok"` must not split into
an *endorsement* of Bangkok — the idiom is rewritten before the split. Clauses
are split and negation-tagged **before** embedding, so a negated clause subtracts
rather than adds.

### Date Scheduler Engine — `src/party/concession.py` + `ics.py`

```
Input   : per-member private date_ranking (from .ics, or a deterministic
          per-name offset into CANDIDATE_DATES)
Process : monotonic concession rounds, simultaneous
Output  : ConcessionState { round_no, named{}, withdrawn[], agreed_date,
                            settled, failed, moves[] }
          → resolved window: [{ departureDate, returnDate, duration }]
```

`return_dates_for(date, offsets=(2,3,4))` derives the return dates a departure
implies — **derived, never fixed**, so a group cannot agree a date and then be
shown returns that predate it.

Preferences are **declared, not private** — `public_view()` exposes what a member
typed. A calendar is uploaded; preferences are announced. They differ.

### Atlas Flight Search Engine — `sweep.py` + `score.py` + `feed.py`

```
Input   : origin, out_date, return_dates[], party_size,
          destinations[] (from retrieval), mandates[] (optional)
Process : search.do per (destination × direction × date)
          → FlightNode → ItineraryGraph (outbound × return)
          → structural + seat filters → ceiling filter → rank
Output  : (ranked_trips[], gaps{})
```

```python
ItineraryGraph.as_dict(mandates) → {
  "key", "destination", "destination_name", "nights", "ground_hours",
  "per_person", "group_total", "party_size",
  "nodes": [...], "dependencies": [...],
  "violations": [...], "feasible": bool,
  "cost_refs": [...],
}
```

`feed.cards(client, origin, dates, party_size=1, mandates=(), limit=8)` is the
same engine with no party: no ceilings, so `headroom()` returns `None` and
ranking falls back to value and fit — the honest behaviour before anyone has said
what they can afford. `DEFAULT_DATES = 3`, because each date costs a full sweep.

### Reconciliation Engine — multi-city synthesis

The engine behind Option 2. Option 1 optimises for the single best-fit or
lowest-cost destination; Option 2 asks **who that leaves out**, and whether a
stopover fixes it at a competitive overall price.

```
Input   : ranked survivors[], members[], mandates[], agreed_date, party_size
Output  : { option1, option2 | null, gaps[], reason_if_none }
```

#### Step 1 — Gap analysis

For each member, compute which stated preferences Option 1 does **not** satisfy:

```jsonc
{
  "member": "Marcus",
  "satisfied":   ["nightlife", "cheap"],
  "unsatisfied": ["streetfood", "ramen"],
  "weight": 0.5    // proportion of this member's stated preferences unmet
}
```

A member with **no** unmet preferences generates no gap. If nobody has a gap,
Option 2 is not constructed and the modal says so — a group that already agrees
does not need a second option invented for it.

#### Step 2 — Hub selection

Rank candidate hubs by **gap coverage**, not by fare — no fare is known yet, and
this runs before the chained calls:

```
hub_score = Σ (gap.weight × hub_satisfies(gap))  for every unmet gap
```

`hub_satisfies` reuses the retrieval scorer: the unmet preferences are embedded
and matched against the candidate city's vectors, so a "gap" and a "match" are
measured the same way.

**The candidate pool is every `REACHABLE` city except the Option 1 destination.
There is no hub list and no city carries a `hub` flag.** A hand-authored set of
"cities worth a layover" would pre-decide what the engine may route through -
the same curation removed from the ranking and the fixture set, one layer down.
It would also make the hub probe circular, able only to confirm cities somebody
already picked.

Viability is **measured, not assumed**, in two stages:

1. **Rank by gap coverage.** Zero Atlas calls - this runs before any fare is
   known, so nothing here can be swayed by price.
2. **Probe the top `MAX_HUB_CANDIDATES` (default 3) live.** A candidate whose
   `origin -> C` or `C -> destination` leg returns nothing drops out and the
   next-ranked candidate is tried. A city is a hub for this trip because both
   its legs answered, not because it was on a list.

**Detour is bounded by measurement, not opinion.** A chain whose total elapsed
time exceeds `MAX_DETOUR_FACTOR` x the direct trip's elapsed time is rejected -
derived from the durations Atlas returned, so "plausibly on the route" is a
number rather than a judgement about geography.

#### Step 3 — Chained search

For each candidate hub, three `search.do` calls
([mechanics](#6-multi-leg-hub-search)). Every leg is a `FlightNode` in one
`ItineraryGraph`, validated by the existing dependency edges.

#### Step 4 — Selection

Among viable chains, pick the one closing the most gap weight; ties break on
combined price.

```jsonc
{
  "route": ["SIN", "BKK", "CNX", "SIN"],
  "legs": [
    { "from": "SIN", "to": "BKK", "date": "20260920",
      "fare": 61.20,  "cost_ref": "search.do:SIN-BKK@20260920#routings[2].adultPrice" },
    { "from": "BKK", "to": "CNX", "date": "20260922",
      "fare": 33.85,  "cost_ref": "search.do:BKK-CNX@20260922#routings[0].adultPrice" },
    { "from": "CNX", "to": "SIN", "date": "20260927",
      "fare": 254.82, "cost_ref": "search.do:CNX-SIN@20260927#routings[4].adultPrice" }
  ],
  "stopover":   { "city": "BKK", "hours": 31 },
  "perPerson":  349.87,
  "groupTotal": 1399.48,
  "minSeats":   7,
  "gapsClosed": [ { "member": "Marcus", "closed": ["streetfood", "ramen"] } ],
  "comparator": { "against": "Chiang Mai direct", "delta": 81.70,
                  "cost_ref": "search.do:SIN-CNX@20260920#routings[0].adultPrice" }
}
```

#### Hard rules

- **The combined total faces every ceiling.** A chain cheaper per leg but dearer
  in total is rejected like any other breach.
- **Every leg carries its own `cost_ref`.** `perPerson` is arithmetic over
  dereferenced figures.
- **The comparator is named.** `delta` is only ever rendered beside Option 1's
  name, fare, and `cost_ref`.
- **Seats are the minimum across legs**, and `>= party_size` on every leg.
- **No viable chain ⇒ no Option 2.** The reason is rendered. Nothing is
  synthesised to fill the slot.

### Booking & Payment Executor — `src/agent/executor.py` + `mandate.py`

```
Input   : ActionProposal { action, target_leg, reason, cost_refs[] }  # no amount
          Confirmation   { action, target_leg, approved_by, at,
                           price_shown, price_refs[], ceiling_shown }
          payload        { adults }
Output  : ExecutionResult { accepted, proposal, amount, stage, reason,
                            atlas_response, remaining }
```

`cost_refs` is a **list, one ref per leg**, and the schema accepts no singular
form — a lone `cost_ref` is exactly the shape that reserves half a return trip,
so it is rejected rather than coerced. `amount` is the sum of
`resolve_group_total()` across every ref. `price_shown` is the whole-trip
per-person figure the human saw, and gate 4 compares `amount / adults` against
it — the same quantity on both sides.

`stage` on refusal is one of `schema`, `dereference`, `mandate`, `confirmation`,
`stale_confirmation`, `payment`, `atlas`. On success it is `atlas` or `stubbed`.

```python
ACTION_ENDPOINTS = {
  "book_group": "orderCommit.do",   # DOCUMENTED name, UNDOCUMENTED shape
  "pay_group":  "pay.do",           # NOT documented anywhere — placeholder path
}
UNCHARACTERISED = set(ACTION_ENDPOINTS.values())
```

Both are in `UNCHARACTERISED`, so **no HTTP request is fired for either**. The
executor records `executed_stub` rather than guessing a URL or faking a
successful Atlas response. See [External blockers](#external-blockers).

Transaction output:

```jsonc
{
  "status": "SUCCESS",           // SUCCESS | FAILED
  "bookingReference": "MOCK-…",  // orderCommit.do response shape unpublished
  "stubbed": true,
  "receipt": {
    "legs": [ { "route", "fare", "cost_ref" } ],
    "groupTotal": 1399.48,
    "perMemberCeilings": { "Marcus": 210.0, "Ana": 400.0 },
    "mandateRemaining": 127.32
  }
}
```

### Payment handling — `src/atlas/payment.py`

Card values come from `.env` (`ATLAS_TEST_CARD_*`) **only**: never in source,
never in a fixture, never in a prompt, never in the decision log, never
committed.

- `mask(pan)` → `"**** **** **** 1111"`. `TestCard.__repr__` is defensively
  masked so a stray traceback cannot leak a PAN.
- `redact(payload)` deep-copies with `cardNumber` masked and `cvv` blanked. It
  runs on every path that could print a payload.
- `describe()` is the only card data any UI sees — brand, masked PAN, expiry,
  `configured`, `missing[]`, and the payment-family disclosure.
- **`as_payload()` is the only function that returns full card data.** It is
  called at exactly one place: the moment the executor builds a Group 03 request
  under a standing confirmation.

| Family | Meaning |
|---|---|
| `MOR` | Atlas is merchant of record and settles. The agent selects a stored method and never handles the instrument. |
| `VCC` | Card data is passed through to the airline by this integration. **Weakens** the "never processes payments" claim — disclose it. |

---

# System Boundaries & Constraints

## Architectural refusals

Deliberate, and they hold across every component above.

| Refused | Why |
|---|---|
| **`amount` field on any agent message** | A model that can author a number can author a lie. `NegotiationMove` and `ActionProposal` have no such field, and the schema rejects one. |
| **A language model in the decision or pricing path** | A model may rewrite a `reason` into flavour text. It may never decide a move, price anything, or sit in the call path to Atlas. Generated flavour containing a digit is discarded and the bubble marked un-voiced. Vector retrieval does not change this: embeddings rank candidate **queries**; they never see a fare and never select an itinerary. |
| **Cost splitting, transfers, inter-member ledgers** | A member grants a **ceiling**, never a wallet and never a share. `Ceiling` must never grow a spend/charge/debit method, and a test enforces it. Atlas settles one order with one instrument; who reimburses whom afterwards is outside the system. |
| **Autonomy without a price binding** | Autonomous execution is scoped to a standing `Confirmation` bound to a specific target and price. It is not blanket authority. A fare rise voids it and returns control to the user. |
| **Reserving part of a trip** | Authority is reserved for the whole itinerary or the action is refused. A proposal carrying fewer refs than the trip has legs is a schema error, not a partial booking — under-reserving is indistinguishable from spending money nobody authorised. |
| **Comparing two different quantities in a guard** | Any check that compares a displayed figure against a recomputed one must compare the same quantity. A guard whose two sides have different dimensions does not fail loudly; it passes silently, which is worse than having no guard at all. |
| **A guessed verify endpoint** | `apiguide.md` names a pricing step with no path and no shape. Re-price re-runs `search.do` instead — a disclosed workaround, not a fabricated URL. |
| **Scarcity in the ranking** | `seatCount` is shown so the group can weigh it, never scored. |
| **A score without a denominator** | Every card renders its comparators verbatim. |
| **A fabricated second option** | If reconciliation finds no viable chain, Option 2 is disabled with a reason. Filling the slot to preserve a two-card layout would make the UI itself the liar. |
| **A cosmetic synthesis delay** | The pause before the decision modal is real work — live hub queries — surfaced as a status stream. |

## External blockers

Not design decisions. Facts about what Atlas publishes.

| Blocker | State |
|---|---|
| `orderCommit.do` request/response shape | Endpoint **named** in the booking flow; shape **not published**. Recorded as `executed_stub`. |
| Payment endpoint | **No payment endpoint is documented anywhere.** `pay.do` is a placeholder path. The card is provisioned in `.env` and **never transmitted**. |
| Verify / pricing step | Named without a path or a shape. Substituted by re-running `search.do`. |
| `supportPaymentMethods` int meaning | Undocumented. |

Autonomous execution therefore runs the full chain **through stubs**. The gates,
the accounting, the re-price and the receipt are all real; the two outbound calls
that would move money are not made, and the status stream labels those steps
explicitly.

## Out of scope

| Item | State |
|---|---|
| Per-member departure hubs | `MemberPreferences.origin` is collected and displayed, but the sweep uses a single party origin. Everyone departs `SIN`. |
| Ancillaries in the negotiation | `has_baggage`, `baggage_elements`, `through_checked_baggage()` are parsed but consumed nowhere. |
| Operational webhooks | Nothing mentions webhooks or incidents. |
| Currency selection | `USD` throughout; a currency mismatch raises `MandateError`. |

## Claims that must not be made

- **Not** "140+ LCC networks" — **44 reachable destinations** from one origin,
  measured.
- **Not** "fully autonomous end to end" — post-order repair and settlement do not
  exist, and both money-moving endpoints are stubbed.
- **Not** "asynchronous negotiation" — rounds are synchronous and simultaneous by
  design, and a test enforces the simultaneity.
- **Not** "changeable fare" — there is no `fareFamilies[]` array and no
  `priceDelta`; `fareFamily` is a string per segment, so changeability is an
  inference and is never stated as fact.
- **Not** "the agent booked and paid" while the endpoints are stubbed.

## Platform constraints

- **Stdlib only at runtime.** Python 3.14, `urllib` + `http.server`. No
  `pip install`. Embedding generation happens offline at dataset build time and
  ships as a static asset; query-time embedding is table lookup plus arithmetic.
- **Live is the product. Replay is a recording of it.** The deliverable is an
  agent that calls Atlas. `LIVE=1` gates live calls so a demo cannot be killed by
  a rate limit — it is a safety catch on the real thing, not the normal mode of
  operation. Any component whose only executed path is replay is **unbuilt**,
  however green its tests are.
- **All Atlas calls go through `src/atlas/client.py`.** Live calls only behind
  `LIVE=1`.
- **Unit tests run against `/fixtures`; acceptance runs live.** These are two
  different claims and neither substitutes for the other. Fixtures prove logic is
  deterministic. Only a live call proves the request shape is correct, because
  replay serves a fixture by key and **never reads the request payload** — a
  malformed request is indistinguishable from a well-formed one offline.
- **Every module that builds an Atlas request must be executed live at least
  once before it is called done.** Passing tests offline is not evidence that a
  request is well-formed. A capture made by a probe proves the *probe* is
  correct, never the application.
- **Gate files.** `src/party/protocol.py`, `src/agent/mandate.py`,
  `src/agent/executor.py`, `src/booking/reprice.py` carry the guarantees this
  document claims. They are short by design, every change to them is reviewed
  line by line, and none is ever bundled into an unrelated refactor — they are
  where every claim the product makes is either true or false.
