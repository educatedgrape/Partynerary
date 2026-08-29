# Remediation pass — Partynerary

You are fixing an existing implementation, not building one. The build compiles
and 307 tests pass. **Do not treat that as evidence of anything.** The suite
asserts shapes — vector length, dict keys, exception types — and every defect
below passes it. Your first duty is to make the tests measure behaviour.

Work the phases in order. Each has a **Proof** command. Do not proceed to the
next phase until the proof prints what it says it must. Do not report a phase
done on the strength of the existing suite going green.

Read `project_scope.md`, `build-guide.md` and the non-negotiables before you
start. Where this document and those disagree, those win — except where this
document names a defect, which means the current code disagrees with them.

---

## Phase 0 — Install the rules that were never installed

`.qoder/rules.md` does not exist. `.qoder/` contains only `repowiki/`. The
non-negotiables never governed the build, which is the root cause of everything
below, not a separate issue.

Create `.qoder/rules.md` now and load it before any other work. It must carry,
at minimum:

- `NegotiationMove` and `ActionProposal` have no `amount` field.
- A ceiling is AUTHORITY, not funds. No spend / charge / debit / credit / settle
  method. No ledger. No balance that money moves through.
- The re-price gate matches on `flightNumber`. Never on list position. Never by
  parsing `routingIdentifier`.
- Every figure rendered anywhere is dereferenced from a `cost_ref`.
- No hand-authored field may narrow what the engine can choose.
- Stdlib only at runtime. Build-time tooling is separate and never imported
  from `src/`.

**Proof:** the file exists and is non-empty.

---

## Phase 1 — P0: the semantic engine is not semantic

`data/vectors.json` carries `"model": "hash-fallback"`. `tools/build_vectors.py`
took its `_hash_vector` branch because `sentence-transformers` was not
installed, and the result was committed as if it were real.

Hashed vectors are 384-dim, unit-normalised and deterministic, so every existing
vector test passes. They also carry **zero** semantic relationship. Measured on
the committed artifact:

```
beach vs coast     -0.0962
beach vs sand      -0.0582
beach vs temples    0.1184
```

`beach` is closer to `temples` than to `coast`. The dense half of the retrieval
score is noise, and the shortlist that noise produces is what the entire product
is then built on top of.

Compounding it: the token table holds **340 tokens**. `ramen`, `noodles`,
`surf`, `seoul` are absent, so they embed to the zero vector and — per
`vectors.embed`'s own docstring — contribute nothing while looking like they
were understood.

### Do this

1. Create and use a build-time virtualenv. It is build-time only and must never
   be imported from `src/`:

   ```
   python -m venv .venv-build
   .venv-build/Scripts/pip install -r tools/requirements-build.txt
   ```

2. Run `tools/build_vectors.py` **with the real model**,
   `sentence-transformers/all-MiniLM-L6-v2`, dim 384.
3. **Delete the `_hash_vector` fallback and the `--fallback` flag entirely.** Do
   not keep it behind a warning. It caused this defect precisely because it was a
   silent, plausible-looking success path. If the model is unavailable, the build
   must fail loudly.
4. Extend the token table to cover every word appearing in `data/cities.json`
   (keywords, vibes, phrases, aliases) **plus** the query-side expansion
   vocabulary — the words people type that appear in no city text: `chill`,
   `unwind`, `foodie`, `kpop`, `nightlife`, `diving`, `surf`, `noodles`,
   `hiking`, `shrine`, and the rest of that class. Expect well over 340 tokens.
5. Assert at load time that `vectors.json`'s `model` field equals the real model
   id, and refuse to run otherwise.

### Proof

Add `tests/test_semantic_sanity.py` — the test that was missing and would have
caught this on day one. It must assert **relative ordering**, not thresholds:

```
sim(beach, coast)   > sim(beach, temples)
sim(ramen, noodles) > sim(ramen, volcano)
sim(kpop, seoul)    > sim(kpop, jakarta)
```

and that no word in the expansion vocabulary embeds to the zero vector.

Every ordering assertion must pass **on the committed artifact**. This test is
the acceptance criterion for the phase.

---

## Phase 2 — P0: the re-price gate checks the wrong flight

`src/booking/reprice.py` names `flightNumber` in its module docstring and in
`check()`'s docstring. **The code never reads it.** `check()` calls
`_extract_routing_index(price_ref)`, pulls `routings[N]` out of the fresh
response by list position, and prices that.

Atlas reorders results when fares move. Reordering is the normal case in exactly
the window this gate exists to police — between the human's click and the order.
So the flagship guarantee routinely compares the confirmed flight against an
unrelated one, and reports `UNCHANGED` with total confidence.

### Do this

1. Resolve the original routing from the cache via the `cost_ref` and read its
   segment `flightNumber` values, in order.
2. In the fresh `search.do` response, find the routing whose segment
   `flightNumber` sequence matches. Match on the **full ordered sequence** — one
   flight number can appear in several routings under different fare families.
3. No match ⇒ `GONE`. That is the correct verdict, and it must not fall back to
   index, to nearest price, or to the cheapest remaining routing.
4. Delete `_extract_routing_index`. Leaving it available invites its reuse.
5. Keep `check_all`'s worst-verdict-across-legs logic — that part is right.

### Proof

Add a fixture pair for the same route and date where the fresh response holds
the **same routings in a different order** and the confirmed flight is dearer.
Assert `DEARER`, and assert the matched routing's flight numbers equal the
original's. Then add one where the confirmed flight is absent entirely; assert
`GONE`.

A test that passes under the current index-based code has not tested this.
Verify your new test **fails** against the old implementation before you accept
it.

---

## Phase 2b — P0: the application's flight search cannot call Atlas

This is the defect that makes the flagship feature look unattempted, and it is
invisible from inside the test suite by construction.

The **probe** scripts send the documented request shape:

```python
# probe/probe.py, probe/breadth.py, probe/hubs.py
{"fromCity": origin, "toCity": destination, "fromDate": date, "adultNum": 1}
```

That is why `fixtures/live/search.do/` holds 197 real responses. The probe talks
to Atlas correctly.

The **application** sends something else entirely:

```python
# src/discovery/routes.py:32  and  src/booking/reprice.py:102
{"originAirportCode": origin,
 "destinationAirportCode": destination,
 "departureDate": str(date)}
```

Wrong field names on all three, and `adultNum` is absent, so party size never
reaches Atlas at all. The Atlas contract is explicit: **request fields are
`fromCity` / `toCity` / `fromDate`.**

### Why nothing caught it

`AtlasClient.post()` in replay mode calls `_serve_replay(fixture_key,
allow_error)` and **never looks at `payload`**. The fixture is located by
`fixture_key` alone. Every one of the 307 tests runs in replay. So the request
body is constructed, passed down, and discarded unread — in tests, in the demo,
in every code path that has ever executed.

The result: `src/discovery/routes.py` is the only search the product actually
uses, and **it has never once been executed against Atlas.** Under `LIVE=1` it
sends fields the API does not recognise. The 197 captures prove the sandbox
works; they prove nothing about the code that ships.

### Do this

1. Fix the payload in `src/discovery/routes.py` and `src/booking/reprice.py` to
   `fromCity` / `toCity` / `fromDate`, and pass `adultNum` = party size.
2. Extract the payload construction into **one** function in `src/atlas/` and
   call it from routes, reprice and the probe scripts alike. Three call sites
   drifted apart precisely because there were three.
3. Make the client validate the payload **in replay too**. Serving a fixture
   while ignoring a malformed request is what let this survive to completion.
   Reject a `search.do` payload missing any required field, whether or not a
   live call follows.

### Proof

Run one search live and confirm it returns routings:

```
LIVE=1 python -c "from src.atlas.client import AtlasClient; from src.discovery.routes import search_nodes; n,e=search_nodes(AtlasClient(),'outbound','SIN','DPS','20260920',4); print(len(n), e)"
```

Non-zero nodes and `None` error. Then add a replay test asserting the client
**raises** on a payload built with the old field names — so the next drift is
caught by the suite that missed this one.

---

## Phase 3 — P1: the ceiling grew into a wallet

`src/agent/mandate.py` defines `Mandate` with `_settled`, `settle()` and
`credit()`. `credit()`'s docstring reads *"money owed back to the traveller"*.
That is a balance with money moving through it — a ledger, and the exact
construct the non-negotiables forbid. `src/party/preferences.py` has the correct
`Ceiling`; the violation is the layer built on top of it.

### Do this

- A ceiling answers two questions and no others: **does this per-person price
  fit** (`permits`), and **by how much does it miss** (`shortfall`). Booking
  RESERVES authority; nothing in this module spends, settles, credits or
  refunds.
- Remove `settle()` and `credit()`. Reservation release, if the flow needs it,
  belongs to the executor's own accounting and must never be phrased as money
  returning to a member.
- Keep booking-reserves / payment-settles as a distinction the **executor**
  tracks. It is not a balance the ceiling owns.

Then fix the adjacent defect: `ceiling_total_from_members` has a docstring
saying *"the MINIMUM per-member ceiling times the party size"* and a body that
returns `min(ceilings)` with no multiplication. One of those is wrong, and both
callers — `src/party/orchestrator.py:212` and `src/ui/dashboard.py:373` — test
the result against group totals. Decide which is correct against the scope, make
code and docstring agree, and pin it with a test at party size 4.

### Proof

Extend `tests/test_agent_boundary.py` to assert by introspection that `Mandate`
exposes no attribute whose name contains `spend`, `charge`, `debit`, `credit`,
`settle`, `transfer` or `balance`; and that a four-member party with ceilings
200/210/300/400 produces the figure the scope specifies — asserted as a literal,
not recomputed from the same expression the code uses.

---

## Phase 4 — P1: the dataset was trimmed after capture, and ships UNPROBED

- `data/cities.json` holds **27** cities. The seed was 42. Nothing records what
  removed 15 or why, and a semantic engine choosing from 27 options is a
  different product from one choosing from 42.
- **2 cities ship as `UNPROBED`.** Coverage must be measured, never assumed;
  `UNPROBED` in a shipped artifact means the sweep can spend calls on a city
  Atlas has never answered for.
- `fixtures/live/search.do/` holds **197 real captures, 62 MB** — the probe ran
  and it worked. This is the one part of the build that is genuinely done. Do
  **not** re-run the capture. Note that 197 captures against 27 catalogue cities
  means the probe reached cities the dataset then dropped.

### Do this

1. Re-run the breadth probe over the full candidate set in `probe/breadth.py`
   and regenerate `data/cities.json` from its measured output.
2. Every city ships `REACHABLE` or `EMPTY`. `UNPROBED` is a build failure —
   assert this at load.
3. If the catalogue is genuinely 27 after probing, that is a finding: record it
   in `FINDINGS.md` with the probe evidence. Do not pad it back to 42.
4. The captures already exist. Reconcile `data/cities.json` against
   `fixtures/live/search.do/` — every captured pair should correspond to a
   catalogue entry with measured coverage. A 104-byte capture (`{"routings": []}`)
   is a real `EMPTY` result and is evidence, not a failure.

### Proof

```
python -c "import json,collections;c=json.load(open('data/cities.json'))['cities'];print(len(c),collections.Counter(x['atlasCoverage'] for x in c))"
```

Zero `UNPROBED`, and every `REACHABLE` city backed by a non-empty capture.

---

## Phase 5 — P2: the ranking saturates

In `retrieval.score_city`, per-clause dense and sparse scores are **summed
across clauses** and only then clamped to `[0, 1]`. With four members
contributing several clauses each, most cities reach the 1.0 ceiling and the
ranking flattens into a tie broken by iteration order.

`shortlist` then sets `vibe_score = 1.0` for any named city, so every named city
ties at exactly 1.0 with every saturated one.

### Do this

- Normalise by the number of positive clauses — mean, not sum — so the score
  stays comparable as party size grows. Negated clauses still subtract, then
  clamp once at the end.
- Keep the named tier as a **tier**: sort by `(named > 0, vibe_score)` as it does
  now, but stop overwriting `vibe_score` with 1.0. A named city should keep its
  real similarity so the board can order named cities against each other.
- Honour `MIN_SIMILARITY = 0.35`. It is defined and never read. A clause below it
  has not matched, and must not be presented as a weak preference.

### Proof

Score four members with genuinely different preferences and assert the top 10
`vibeScore` values are **distinct** and strictly decreasing, and that at least
one candidate city falls below `MIN_SIMILARITY` and is excluded.

---

## Phase 6 — Re-verify the whole path, and say what is still unproven

Run, in order:

```
python -m unittest discover -s tests -t .
```

Then start the dashboard, spawn four members with conflicting preferences, and
confirm by reading the rendered output rather than by assertion:

- The shortlist reflects what they typed. `ramen` reaches Japan; `beach` reaches
  the beach cities; a negated clause removes its city.
- Consensus takes **more than one round** and shows visible concession.
- Every figure on screen traces to a `cost_ref` in the decision log.
- A ceiling breach vetoes, and is not out-voted or rounded away.
- Raising a fare between confirmation and order produces `DEARER` and forces a
  fresh click.

Finally, append to `FINDINGS.md`: what each phase changed, what the proof
printed, and — explicitly — **anything still unverified**. Do not describe a
phase as complete because its tests pass. State what was measured.

---

## Phase 7 — P0: the UI is a step-runner, not the product

This is the largest phase. Budget accordingly.

### 7a. Port to Next.js static export

`project_scope.md` and `build-guide.md` have been **updated** and now specify
Next.js. The current Vite app matched the OLD text, so do not treat the port as
correcting a mistake — read the revised sections before starting.

- Next.js App Router, `output: 'export'` in `next.config.mjs`,
  `images: { unoptimized: true }`.
- Build output goes to `web/out`. Update `src/ui/dashboard.py` — three places
  reference `web/dist` (module docstring, `_serve_static`, and the startup
  check at the bottom).
- **No `app/api/**` route handlers.** Python owns every endpoint. A route
  handler needs a Node runtime at demo time and breaks the single-process story
  that is the whole reason for static export.
- Every component that reads state is a client component (`'use client'`)
  polling `/api/state`. There is no server-side data fetching — the Python
  server is not running at build time.

**Proof:** `npm run build` produces `web/out/index.html`; `python -m
src.ui.dashboard` serves the app with no Node process running.

### 7b. The state object is a stub — fix the API before the UI

The 14 endpoints in `src/ui/dashboard.py` all exist, which reads as complete.
The state object they return is not. I checked every key the scope requires
against both the API and the frontend:

```
vibe · candidates · unrecognised · rejections · rejected_total · bubbles
impact · narration · alternatives · replan_rounds · explored · can_add
reserved · card_status                    api=0   ui=0
```

**Fourteen of seventeen specified keys exist nowhere** — not emitted, not
consumed. The engines beneath them were built; their output is computed and
discarded at the HTTP boundary. There is no UI work to do on these until the
state object carries them, so do 7b first.

Emit every key named in `project_scope.md` Part A. `state.unrecognised` in
particular is a non-negotiable: input the system could not place is REPORTED,
never silently dropped.

### 7c. Build the surfaces the scope specifies

What exists is a toolbar of four buttons clicked in sequence — `Add Agent` →
`Settle Date` → `Discover` → `Synthesize`. The scope's phrase is **"Autonomous
after the single click."** The current UI is the opposite: a manual driver for
the Python API, with the operator performing the orchestration the agent is
supposed to perform.

Rebuild against Part A, in this order of importance:

1. **The Terminal / arena.** `state.bubbles` — the agents rendered as a group in
   one room, negotiation moves appearing as they happen, with **dereferenced
   figures only**. `Terminal.jsx` is currently 30 lines and renders no bubbles at
   all. This is the headline beat of the entire product and it has no UI.
2. **The Trade panel.** Specified as *"the most important panel on the screen"*,
   rendered at equal visual weight to the board, never collapsed. Currently 29
   lines listing a destination and a fare. It must state: what the city matched,
   the cheapest fare Atlas returned, **whose** ceiling it broke, by how much, and
   the `cost_ref` behind the fare.
3. **The discovery feed as the landing view.** Ranked inventory with no party, no
   ceilings, no agreed date. Flights visible before anybody joins. `/api/feed`
   exists; nothing renders it.
4. **The ranked board.** `Panels.jsx` is 28 lines of bare divs. It needs the
   comparator line from `score.explain()`, seats left, feasibility, plus
   `state.rejections` with reasons and `state.gaps`.
5. **Live retrieval feedback** during joining — `state.vibe`, `state.candidates`
   with `why` and `vibeScore`, `state.unrecognised`.
6. **The status stream** — sourced from the decision log, never a scripted
   sequence.
7. **The authority panel** — ceiling, reserved, remaining, per-member ceilings,
   `card_status` masked.
8. **Phase B dual-option modal** — Card 2 must state which members it satisfies
   that Card 1 does not, and render **disabled with its reason** when
   reconciliation finds no viable chain. Never filled with a fabricated
   alternative.

Drive the phase transitions (`sweeping → board_live → synthesizing →
decision_open → committed`) from state, not from operator clicks. After the
single decision click, execution continues on its own.

### 7d. One live violation to fix now

`web/src/TradePanel.jsx` computes:

```js
ceiling: m.cheapest + 50
```

A number invented in the browser, in the product whose entire thesis is that
every figure is dereferenced. The Trade panel offers a **control**; the human
sets the value. It never proposes a ceiling the member has not granted — the
scope says this explicitly. Remove the arithmetic.

### Proof for Phase 7

Add a UI test asserting no rendered numeric string reaches the DOM without a
corresponding `cost_ref` in the same payload. Then run the demo and confirm, by
watching rather than asserting: the feed renders with zero agents; four agents
join and their vibes appear live; consensus visibly concedes over more than one
round in the Terminal; the Trade panel names a member and a shortfall; the modal
opens with Card 2 justified or disabled-with-reason; and after the decision click
**nothing further is clicked** until the receipt.

---

## Phase 8 — the documentation taught the build to prefer replay

Fix this or the next pass reproduces Phase 2b exactly.

`project_scope.md` and `build-guide.md` have been **updated** — read the revised
"Platform constraints" and the standing rule under Phase 1's gate before you
start. What follows is why they changed.

The old text pointed the whole build at replay:

| | `project_scope.md` | `build-guide.md` |
|---|---|---|
| "fixture" | 11 | 21 |
| "replay" | 7 | 12 |
| `LIVE=1` | 2 | **5** |

Of those five `LIVE=1` occurrences in a 1995-line build guide, **four are probe
scripts** — `probe.probe smoke`, `probe/breadth.py`, `probe/hubs.py`. The fifth
is `LIVE=1 python -m src.ui.dashboard --mode=live`, on the **last line of the
document**. The application is exercised live exactly once, at the very end,
after every phase has already been signed off.

Worse, Phase 1's gate previously read:

> the suite passes **with `LIVE` unset** — that last part is what proves
> `fixtures/test/` is real.

The offline pass was named as the proof. Combined with the non-negotiable
"Every test runs against `/fixtures`", the documented definition of done was
*passes offline*. An implementer following it correctly produces exactly what
was produced: a probe that talks to Atlas, and an application that has never
tried.

`project_scope.md` §8 always said the right thing — *"The system runs live.
Replay is a recording of a run that happened, and nothing else."* That doctrine
sat in one prose section while every gate, command and checklist defaulted to
replay. **Mechanics beat doctrine.** State a principle once and contradict it in
every verification step, and the verification steps win.

### What changed

- Live is now stated as the product and replay as its recording, in
  `project_scope.md`'s platform constraints where the other non-negotiables live.
- "Every test runs against `/fixtures`" is now **"Unit tests run against
  `/fixtures`; acceptance runs live"** — two claims, neither substituting for the
  other.
- A new constraint: **every module that builds an Atlas request must be executed
  live at least once before it is called done.** A capture made by a probe proves
  the probe is correct, never the application.
- A standing rule in `build-guide.md`: any phase that adds or changes code
  building an Atlas request carries a **live acceptance check** in its gate.

### Do this

Add the live half to the gate of every phase that touches an Atlas request —
transport, probe coverage, sweep and ranking, executor, re-price, orchestrator.
One live call each, asserting the response carries `routings`. That is the check
that would have caught `originAirportCode` on the day it was written.

### Proof

```
grep -c "LIVE=1" build-guide.md
```

Materially more than 5, and the majority pointing at `src/` rather than
`probe/`.

---

## Standing rule for this pass

Every defect above shares one cause: a silent fallback produced a
plausible-looking artifact, and a test checked that artifact's shape instead of
its meaning. When you hit a missing dependency, an unreachable endpoint or an
unmatched lookup, **fail loudly**. Do not synthesise a stand-in and carry on.

If you cannot complete a phase, stop and say so. A phase reported done on a
fallback is worse than a phase reported blocked.
