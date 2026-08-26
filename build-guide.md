# Partynerary — Build Guide

Step-by-step implementation of `project_scope.md`. The scope is the source of
truth; this guide is the order of operations and the code.

## Phase map

Each phase is independently verifiable and leaves the system in a working state.
Nothing depends on a phase that has not yet landed.

| Phase | Deliverable |
|---|---|
| 1 | Atlas transport, response contract, `cost_ref` resolution |
| 2 | Probe coverage — what the sandbox actually reaches |
| 3 | City dataset + offline vector build |
| 4 | Retrieval engine — dense vector, stdlib runtime |
| 5 | Itinerary graph, sweep, fare evaluation |
| 6 | Party — mandates, private rankings, date consensus |
| 7 | Executor, the five gates, re-price |
| 8 | Orchestrator, reconciliation, chained search |
| 9 | Server and UI — three stages, two-phase decision surface |

## Rules that apply to every phase

Repeated at the point they bite, but never negotiable:

- Every figure rendered is dereferenced from an Atlas response via `cost_ref`.
- `NegotiationMove` and `ActionProposal` carry **no `amount` field**.
- A ceiling deletes; nothing else does. Never out-voted, never rounded.
- `seatCount >= party_size` on **every leg**.
- Re-price runs after the user's choice, before the order.
- Booking RESERVES authority; only payment SETTLES it.
- `orderCommit.do` and `pay.do` stay stubbed. No guessed URLs.
- Runtime is stdlib-only. Build-time tooling is fenced into `tools/`.
- **Gate files** — `src/party/protocol.py`, `src/agent/mandate.py`,
  `src/agent/executor.py`, `src/booking/reprice.py` — carry the guarantees the
  product claims. Every change to them is reviewed line by line and never
  bundled into an unrelated refactor.

---

# Phase 1 — Atlas transport and the cost_ref substrate

**Goal:** One authenticated path to Atlas, one response cache, and one function
that turns a pointer into money.

**Files**

| Path | Action |
|---|---|
| `src/atlas/client.py` | `CREATE` |
| `src/atlas/models.py` | `CREATE` |
| `src/atlas/cache.py` | `CREATE` |
| `src/agent/cost_ref.py` | `CREATE` |
| `tests/helpers.py` | `CREATE` |
| `tests/test_transport.py` | `CREATE` |
| `fixtures/test/**` | `CREATE` — frozen once, see 1.4 |

## 1.1 — The client

`AtlasClient` is the only object in the system permitted to open a socket to
Atlas. Nothing else imports `urllib`.

```python
class AtlasClient:
    """POST to Atlas, or serve a frozen fixture when not live.

    fixture_key names the cache slot, e.g. "search.do:SIN-DPS@20260918". That
    key is the left-hand side of every cost_ref in the system.
    """

    def __init__(self, config=None, replay: bool = None,
                 fixtures: pathlib.Path = None):
        ...

    def post(self, endpoint: str, payload: dict, fixture_key: str = None,
             allow_error: bool = False):
        """Call Atlas (or serve the fixture) and record the response."""
```

Requirements, each one a test:

| Concern | Rule |
|---|---|
| Auth | Header AK/SK — `x-atlas-client-id` / `x-atlas-client-secret`, read from `.env`, never written at a call site |
| Live gate | No live call unless `LIVE=1`; otherwise raise `LiveCallBlocked` |
| Encoding | Responses are gzip and `urllib` does not auto-decompress — decode explicitly, and tolerate a mislabelled body |
| Errors | `AtlasHTTPError` carries status and body. **Never cache an error body and never return it as data** — otherwise a 401 renders as "this city has no flights" |
| Timeout | 12s. A sweep is a dozen sequential calls; a 30s timeout stalls the UI for half a minute on one dead pair |
| Capture | Write every live response to `fixtures/live/` under its own key, so any live run replays exactly as it happened |

**Fixture key — one definition, one place:**

```python
def fixture_key(origin: str, destination: str, date: str) -> str:
    """search.do:SIN-DPS@20260918 — the ONE key format."""
    return "search.do:%s-%s@%s" % (origin, destination, format_date(date))
```

Every module that searches builds its key here. A module that invents its own
variant will look for a cache slot that cannot exist, and the failure surfaces
as an empty result rather than an error.

## 1.2 — The response contract

Atlas returns `routings[]`, **not** `offers[]`. A priced itinerary is a
*routing*. Dates are `YYYYMMDD`; datetimes `YYYYMMDDHHmm`; no separators.

**There is no total price field.** Compute it:

```python
def total_price(self, adults: int = 1) -> float:
    """(adultPrice + adultTax) * adults + transactionFee."""
```

Parse per routing: `segments[]` (each with `flightNumber`, `seatCount`,
`fareFamily`), `min_seat_count`, `elapsed_hours`, `transit_hours`, `carriers`,
`is_multi_carrier`, `through_checked_baggage`.

**There is no `fareFamilies[]` array and no `priceDelta`.** `fareFamily` is a
string per segment, so "changeable" is an inference and must never be stated as
fact.

Every routing exposes its own ref builders — the only sanctioned way to name a
figure:

```python
def price_ref(self) -> str:
    return "%s#routings[%d].adultPrice" % (self.cache_key, self.index)

def tax_ref(self) -> str: ...
def fee_ref(self) -> str: ...
```

## 1.3 — cost_ref resolution

```python
"""Dereference a cost_ref into a real number that Atlas actually returned.

    <cache-key>#<dotted.path>[index].<field>
    search.do:KUL-DPS#routings[1].adultPrice

This module is the load-bearing half of the anti-hallucination guarantee. The
planner emits a POINTER; only this resolver turns a pointer into money, and it
can only do so if the pointed-at response is in the cache - i.e. if Atlas really
said it, during this run.
"""


def resolve(ref: str, cache=None) -> float:
    """The float this ref points at, or raise. NEVER returns a default."""


def sibling(ref: str, field: str) -> str:
    """Same routing, different leaf: '...adultPrice' -> '...adultTax'.

    Structural, not guessed. Walks WITHIN one routing - it cannot reach another
    response, and must not pretend to.
    """


def resolve_group_total(price_ref: str, adults: int, cache=None) -> tuple:
    """(adultPrice + adultTax) * adults + transactionFee, for ONE leg.

    Returns (total, refs_used). Every input is dereferenced; the arithmetic is
    the documented one. Nothing here is model-authored, which is why the
    executor is allowed to reserve against it.
    """
```

**A leg's siblings do not reach another leg.** The return leg lives under a
different cache key and is structurally unreachable from the outbound ref. That
is why a proposal carries one ref per leg (Phase 7) rather than one ref and a
derivation.

**Tests**

- A ref into a response not received this run raises, naming the known keys.
- A ref resolving to a non-number raises; a bool is not a number.
- An error response is not cached and not returned as data.
- gzip and non-gzip bodies both decode.
- `LIVE != 1` blocks a live call.
- `resolve_group_total` matches the documented formula for a party of 4.

## 1.4 — The test fixture set

Every later phase's gate runs the suite, and the suite must not need
credentials or a network. So `fixtures/test/` has to exist before the first
gate does — **create it in this phase or nothing downstream can be verified.**

```
fixtures/test/search.do/
  <PAIR>@<DATE>.json          # a pair that returned priced routings
  <PAIR>@<DATE>-empty.json    # a pair that answered with no routings
```

Populate it from the smoke call: run one live search, take the captured
response out of `fixtures/live/`, and freeze a copy here. Add an empty-routings
response so the "no routings returned" path is exercised, and hand-write a
minimal error body so `AtlasHTTPError` has something to parse.

**Rules — this set is different in kind from the demo fixtures:**

- **Frozen once, never regenerated.** A later probe run must not overwrite it.
  A test whose meaning changes with the weather is not a test.
- **Chosen for stability, never breadth.** Two or three pairs is right. This is
  not a destination catalogue and must never grow into one.
- **It never feeds the demo path.** `AtlasClient(fixtures=...)` points the suite
  here and the app somewhere else, so the two cannot disturb each other.
- Curating *this* set is legitimate precisely because nothing in it reaches a
  user: it fixes what the tests assert against, not what the agent may choose.

`tests/helpers.py` gives every test a client bound to this directory, so no
test constructs one by hand and drifts onto the demo fixtures.

**Gate**

```bash
LIVE=1 python -m probe.probe smoke
python -m unittest tests.test_transport -v
```

HTTP 200, contract fields present, the captured response replays offline, and
the suite passes **with `LIVE` unset** — that last part is what proves
`fixtures/test/` is real.

---

# Phase 2 — Probe coverage

**Goal:** Establish what the sandbox actually reaches, and build the catalogue
from what answers rather than from a guess.

**This phase must run before any destination list exists.** The published
documentation names only a handful of supported routes and warns that arbitrary
routes may return empty results. A catalogue assembled from intuition produces a
board full of gaps.

**Files**

| Path | Action |
|---|---|
| `probe/probe.py` | `CREATE` |
| `probe/breadth.py` | `CREATE` |
| `probe/hubs.py` | `CREATE` |
| `FINDINGS.md` | `CREATE` |

**Steps**

1. `probe.py smoke` — one documented route. Confirms auth, headers, gzip, and
   the field names in `models.py`.
2. `probe.py roundtrip` — outbound and several returns for one pair. Confirms
   the date arithmetic and that returns are searchable independently.
3. `breadth.py` — sweep a candidate list from the chosen origin on one date.
   Record three buckets:
   - **REACHABLE** — returned priced routings
   - **EMPTY** — answered, with nothing
   - **ERROR** — did not answer
4. `hubs.py` — a FEASIBILITY CHECK, not a hub list. Probe a handful of
   `origin → C` / `C → destination` pairs to confirm multi-leg chaining works
   at all, and record the result as evidence.

   **There is no hub table, and no city carries a `hub` flag.** Every reachable
   city is a candidate stopover; which ones are viable for a given trip is
   decided at synthesis time by gap coverage and a live leg probe (Phase 8).
   A hand-authored list of "cities worth a layover" would pre-decide what the
   engine is allowed to route through — the same curation removed from the
   ranking and the fixture set, reappearing one layer down. It also makes the
   probe circular: it can only ever confirm the cities somebody already chose.
5. Write every result to `FINDINGS.md` with the date and the command. **A number
   in the scope document must be traceable to a line in this file.**

**Rules**

- **Never add a destination on a hunch — probe it, or leave it out.** Record the
  `EMPTY` codes explicitly so nobody re-adds them hopefully.
- An error is a finding, not noise. Capture the body.
- `city ≠ airport`: a city-code search may return flights into a different
  airport in that city. Record the pairs where this happens; anything that
  re-queries later must re-run *the query that produced the offer*, never a new
  query built from the flown airport.

### Capture integrity — do not build a curated fixture set

The probe establishes what the catalogue *is*. It must not also decide what the
agent gets to *choose from*.

> **No module may contain a hand-written list of destinations to capture.**

`AtlasClient._capture()` already writes every live response to `fixtures/live/`
under its own key, so a live run is replayable exactly as it happened. That is
the only sanctioned way a replay set comes into existence.

**Do not write a `capture_places.py`.** A script holding a list of cities to
freeze looks like a storage optimisation and is actually a curated universe: the
semantic layer then picks the "best fit" from a pool a human shaped in advance,
which is ratification wearing the costume of retrieval. The `cost_ref`
architecture cannot catch this, because it guards prices, not candidate sets.

If a recorded run should cover more of the catalogue, **run it again live**.
Never widen the pool by authoring one.

The test suite is the one exception, and it is a different thing: `fixtures/test/`
is a small frozen set chosen for *stability*, never breadth, and nothing in it
feeds the demo path. A test whose meaning changes with the weather is not a test.

**Gate**

```bash
LIVE=1 python probe/breadth.py --origin SIN --date <YYYYMMDD>
LIVE=1 python probe/hubs.py
grep -rn "PLACES = \[" probe/ src/ && echo "FAIL: hand-written capture list" && exit 1
```

`FINDINGS.md` records the reachable/empty/error split and the hub-leg table.
Those counts are the only ones the scope may cite, and the `grep` returns
nothing.

---

# Phase 3 — City dataset and offline vector build

**Goal:** A dataset of global cities carrying keywords, cultural vibes, phrases
and aliases — with vectors generated offline so the runtime stays stdlib-only.

**Files**

| Path | Action |
|---|---|
| `tools/generate_dataset.py` | `CREATE` |
| `tools/build_vectors.py` | `CREATE` |
| `tools/requirements-build.txt` | `CREATE` |
| `data/cities.json` | `CREATE` (generated, committed) |
| `data/vectors.json` | `CREATE` (generated, committed) |
| `src/discovery/dataset.py` | `CREATE` |
| `tests/test_dataset.py`, `tests/test_vectors.py` | `CREATE` |

## 3.1 — Dataset schema

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
    "keywords": [ ... ],
    "vibes":    [ ... ],
    "phrases":  [ [ ... ] ]
  },
  "aliases": ["thailand", "thai", "phuket", "andaman"],
  "atlasCoverage": "REACHABLE"
}
```

`keywords` are concrete nouns. `vibes` are the register a person would use —
"laid-back", "buzzy", "old-world". `phrases` are full sentences a traveller
would write; three to six per city, and they carry most of the semantic signal.

`aliases` cover country, city, cuisine and cultural export — "thailand",
"korean", "kimchi", "kpop". This is the exact-match index, and it is what lets
someone name a place without describing it.

**`atlasCoverage` is derived from Phase 2, never editorial.** Emit `EMPTY`
entries too: they are how the system knows *not* to ask.

## 3.2 — The read side

```python
@dataclass(frozen=True)
class City:
    city_id: str
    city_name: str
    country: str
    keywords: tuple = ()
    vibes: tuple = ()
    phrases: tuple = ()
    aliases: tuple = ()
    atlas_coverage: str = UNPROBED

    @property
    def reachable(self) -> bool:
        return self.atlas_coverage == REACHABLE


def load(path: pathlib.Path = None) -> list: ...
def reachable(path: pathlib.Path = None) -> list: ...
def by_id(path: pathlib.Path = None) -> dict: ...
def alias_index(path: pathlib.Path = None) -> list:
    """[(alias, cityId)] sorted longest-first, so 'south korea' beats 'korea'."""
```

## 3.3 — Offline vector build

**This is the one deliberate exception to the no-dependency rule**, and it is
fenced: the dependency lives in `tools/`, runs at build time, and is never
imported by anything under `src/`.

```
# tools/requirements-build.txt
# BUILD TIME ONLY. Never imported from src/. Installed only when regenerating
# data/vectors.json - the app runs on the committed artifact, stdlib alone.
sentence-transformers==3.*
```

**Model: `sentence-transformers/all-MiniLM-L6-v2`, dim 384.** Pinned, not left
to the builder: `dim` propagates into every vector in the committed artifact, so
changing the model later means regenerating the whole dataset. It is small
(~80MB), Apache-2.0, and strong enough for short preference phrases.

A substitute is acceptable only if it is deterministic for identical input,
produces a fixed dimension, and can be run offline. Record the model id in
`vectors.json` so the artifact says what produced it.

**Artifact — `data/vectors.json`:**

```jsonc
{
  "model": "<model-id>",
  "dim": 384,
  "built": "<date>",
  "tokens": { "beach": [0.013, -0.221, ...], "relax": [0.087, 0.004, ...] }
}
```

`tokens` is the query-time table: every token appearing in any city's text,
**plus a curated expansion list** of travel vocabulary a user might type that no
city text contains — "chill", "unwind", "buzzy", "foodie", "hungover". Without
that expansion the query embedder has nothing to look up and silently degrades
to keyword matching.

```python
def build(dataset_path: pathlib.Path, out_path: pathlib.Path,
          model_id: str, expansion: list) -> dict:
    """Embed the dataset and the query token table. Returns the build report.

    Raises BuildError if any city yields an empty vector - a city that can never
    match is worse than a missing city, because nothing reports it.
    """
```

**Normalise every vector to unit length at build time.** Cosine then reduces to
a dot product, so the runtime is one loop with no square roots.

**Tests**

- `dim` consistent across every vector; all unit length within `1e-6`.
- Every city has `vectors.keywords`, `vectors.vibes`, and one entry in
  `vectors.phrases` per phrase.
- The token table covers every token in every city's text.
- Every `EMPTY` code from Phase 2 is present and marked `EMPTY`.
- `reachable()` excludes every `EMPTY` code.
- `alias_index()` is sorted longest-first; no duplicate `cityId`.
- Sanity: `cos(beach, seaside) > cos(beach, museum)`.

**Gate**

```bash
python tools/generate_dataset.py --findings FINDINGS.md --out data/cities.json
python tools/build_vectors.py --dataset data/cities.json --out data/vectors.json
python -m unittest tests.test_dataset tests.test_vectors -v
```

Build report prints; re-running is idempotent — byte-identical output for
identical input.

---

# Phase 4 — The retrieval engine

**Goal:** Free text in, ranked city **queries** out. Dense vector retrieval fused
with sparse keyword overlap and exact alias resolution, in pure stdlib.

**Files**

| Path | Action |
|---|---|
| `src/discovery/vectors.py` | `CREATE` |
| `src/discovery/retrieval.py` | `CREATE` |
| `tests/test_retrieval.py` | `CREATE` |

## 4.1 — Vector arithmetic

```python
"""Vector arithmetic in stdlib. No numpy, no model, no network.

Vectors are unit-normalised at build time, so cosine similarity is a dot product
and nothing here computes a magnitude.
"""


def load_tokens(path: pathlib.Path = None) -> tuple:
    """({token: vector}, dim). Cached after first read."""


def cosine(a: list, b: list) -> float:
    return sum(x * y for x, y in zip(a, b))


def embed(text: str, tokens: dict, dim: int) -> list:
    """Embed free text by token lookup and mean pooling, then normalise.

    Returns a ZERO VECTOR when no token is known. The caller MUST treat that as
    "I could not place this", never as a neutral query: a zero vector cosines to
    0.0 against everything, so every city would score identically and the board
    would fall through to fare order while looking like it had understood.
    """


def is_zero(vec: list) -> bool: ...
def max_pool(query: list, candidates: list) -> float: ...
```

## 4.2 — Retrieval

```python
"""Free text in, ranked city QUERIES out. The semantic layer.

    "beach and relax"  ->  Phuket, Bali, Koh Samui
    "kimchi and kpop"  ->  Seoul, Busan

WHY THIS IS NOT A MODEL DECIDING WHERE YOU GO
---------------------------------------------
  * It emits city QUERIES, never itineraries, prices, or dates. Atlas is asked
    afterwards, and Atlas's answer is what gets shown.
  * It never sees a price and never calls Atlas. It runs before the first call,
    so there is no fare in scope for it to leak or invent.
  * Embeddings are a static committed artifact. No model in the process, no
    network call, no per-run variation.

The worst a bad vector can do is cause the group to be shown flights to a city
they did not ask about - at real prices, with real seats, with the match reason
printed underneath. It cannot invent a fare or a route.
"""

WEIGHTS = {"dense": 0.70, "sparse": 0.30}

# Below this, a clause has not matched anything and must not be treated as a
# weak preference. Dense retrieval always returns *something*; this is what
# stops "something" becoming "a confident answer".
MIN_SIMILARITY = 0.35

NEGATORS = ("no ", "not ", "without ", "avoid ", "hate ", "dislike ",
            "rather not ", "anything but ", "except ")

_SPLIT = re.compile(r"[,;.!?]|\band\b|\bbut\b|\bthough\b|\bhowever\b|\bplus\b"
                    r"|\bwith\b|\balso\b")

# "anywhere but Bangkok" is one opinion, not two. `but` is a clause separator
# everywhere else, so splitting on it turns an exclusion into an endorsement:
# the trailing clause reads as a bare "bangkok" and the place being ruled OUT
# comes back top of the list. Rewrite the idiom BEFORE the split rather than
# teaching the splitter about context.
_EXCEPT = re.compile(r"\b(?:any|every|some)(?:where|thing|place)\s+"
                     r"(?:but|except|apart from|other than)\b")


@dataclass(frozen=True)
class Clause:
    text: str
    negated: bool
    vector: list


@dataclass
class Match:
    """One city the group's own words point at, and why.

    Carries NO price, fare, amount, cost, total, or saving field. It runs before
    the first Atlas call; if it cannot hold a fare it cannot leak an invented
    one, and a test enforces the absence.
    """
    city_id: str
    city_name: str
    country: str
    vibe_score: float
    dense: float
    sparse: float
    named: int              # +1 named outright, -1 ruled out, 0 inferred
    matched: tuple = ()

    @property
    def why(self) -> str:
        """The denominator, in words. Never a score on its own."""

    def as_dict(self) -> dict: ...


def clauses(text: str) -> list:
    """Split, detect negation, embed. Negation is tagged BEFORE embedding, so a
    negated clause subtracts rather than adds."""


def places_named(text: str) -> dict:
    """{cityId: +1|-1} by exact alias, longest-match-first.

    Separate from similarity on purpose. Somebody who writes "kimchi" has told
    you where they want to go; that is not a similarity question, and forcing it
    through one lets a cheap beach outrank a named country.
    """


def unrecognised(text: str) -> list:
    """Tokens that produced no vector and named no place."""


def score_city(city: dataset.City, clauses: list) -> tuple:
    """(dense, sparse, matched). Max-pooled across the city's vectors."""


def shortlist(members: list, limit: int = 14, pool: list = None) -> list:
    """The group's words -> the cities worth ASKING ATLAS ABOUT.

    `limit` is a rate-limit guardrail as much as a ranking one: a sweep is
    several calls per city per date. `pool` defaults to dataset.reachable() -
    never the full dataset, because proposing a city Atlas has never answered
    for spends a call to render a gap.
    """


def group_vibe(members: list) -> list: ...
def describe(members: list) -> str: ...
```

**Fusion and tiering — the part that must not be simplified**

```
vibe_score = 0.70 * dense + 0.30 * sparse      # inferred match

named > 0  ->  vibe_score = 1.0                # overriding tier
named < 0  ->  vibe_score = 0.0                # floored, never deleted
```

Sort on `(named > 0, vibe_score)` descending — a **tier**, not a weight. Naming
a place leaves nothing to infer, so it does not compete with similarity on the
same axis. Collapse this into a weight and "sushi and anime" ranks a cheap beach
first, because *sushi* brushes its seafood vectors.

**Negation subtracts, never deletes.** Only a ceiling or a seat count removes a
destination.

**Tests**

- `Match.as_dict()` contains none of `price`, `fare`, `amount`, `cost`, `total`,
  `saving`.
- Every shortlisted city is `REACHABLE`.
- A zero-vector query reports through `unrecognised()` and is not silently
  treated as "no preference".
- `"great street food but no nightlife"` — one positive clause, one negated.
- `"anywhere but bangkok"` — Bangkok present, scored `0.0`, not absent.
- A named city outranks a higher-similarity unnamed one.
- `"beach and relax"` surfaces tropical-beach cities though neither phrase
  appears verbatim in any keyword set — this is the test that proves the layer
  is semantic rather than literal.
- `"kimchi and kpop"` surfaces Korean cities with `why == "you named it"`.
- `shortlist(limit=5)` returns at most 5.

**Gate**

```bash
python -m unittest tests.test_retrieval -v
```

---

# Phase 5 — Itinerary graph, sweep, fare evaluation

**Goal:** Turn search responses into whole priced itineraries, filter them, and
rank them with a visible denominator on every card.

**Files**

| Path | Action |
|---|---|
| `src/itinerary/nodes.py` | `CREATE` |
| `src/itinerary/graph.py` | `CREATE` |
| `src/discovery/routes.py` | `CREATE` |
| `src/discovery/sweep.py` | `CREATE` |
| `src/discovery/score.py` | `CREATE` |
| `src/discovery/feed.py` | `CREATE` |
| `tests/test_graph.py`, `tests/test_sweep.py`, `tests/test_ranking.py` | `CREATE` |

## 5.1 — The graph is N-leg from the outset

Two legs is a round trip; three or more is a chain through a stopover. **Design
it for N from the start** — a two-node graph retrofitted later forces every
consumer to learn a second type.

```python
@dataclass
class ItineraryGraph:
    """GROUP GOAL -> leg -> leg -> ... -> home, with the edges between them.

    The dependency edges are derived pairwise, so a chain validates by exactly
    the rules a round trip does: a leg that departs before its predecessor lands
    is not a cheaper trip, it is not a trip.
    """
    legs: list
    party_size: int
    destination_name: str = ""
    # Semantic fit, carried from the Match that proposed this destination.
    # NOT an editorial desirability score - nothing in this system holds an
    # opinion about how nice a place is.
    vibe_score: float = 0.0
    dependencies: list = field(default_factory=list)

    @property
    def outbound(self):
        return self.legs[0]

    @property
    def inbound(self):
        """The leg home. `return` is a keyword."""
        return self.legs[-1]

    @property
    def is_chain(self) -> bool:
        return len(self.legs) > 2

    @property
    def stopovers(self) -> list:
        """[(cityId, hours)] per intermediate leg. Empty for a round trip."""

    @property
    def per_person(self) -> float:   # sum across legs
    @property
    def group_total(self) -> float:  # sum across legs
    @property
    def min_seats(self) -> int:      # MIN across legs
    @property
    def cost_refs(self) -> list:     # ONE PER LEG, in order


def build_chain(legs: list, party_size: int, destination_name: str = "",
                vibe_score: float = 0.0) -> ItineraryGraph:
    """Two legs or twenty. Raises ValueError below two."""
```

`_derive_dependencies()` walks consecutive pairs, emitting `PLACE`, `TEMPORAL`
and `DURATION` edges for each adjacent pair.

**`min_seats` is a minimum, not a first.** One unseatable leg makes the whole
itinerary unbookable.

## 5.2 — The sweep

Governing rule: **scan and filter, never construct.**

```python
def search_nodes(client, role, origin, destination, date, party_size,
                 drop_unseatable: bool = True) -> tuple:
    """Every priced routing for one direction on one date, as FlightNodes.

    Returns (nodes, error). A pair that errors or returns nothing is a data gap
    surfaced in the UI - never a crash, never silently dropped.
    """


def sweep(client, origin, out_date, return_dates, party_size,
          destinations=None, members=None) -> tuple:
    """Returns (all_valid_trips, errors).

    EVERY structurally valid outbound x return combination comes back. Returning
    only the cheapest would be locally optimal - no single-node swap could
    improve on it - and the re-planner would have nothing to find.
    """


def best_per_destination(ranked: list, limit: int = 8) -> list:
    """Top trip per CITY SEARCHED. Use for a board of places to go."""


def best_per_shape(ranked: list, limit: int = 8) -> list:
    """Top per (destination, nights). Use where trip LENGTH is the comparison."""
```

**Seat count is a hard filter, not a warning.** If every routing for a
destination fails it, report
`"N routing(s) returned but none seat a party of M"` — a gap, not a crash.

`return_dates_for(date, offsets=(2,3,4))` derives the return dates a departure
implies — **derived, never fixed**, or a group can agree a date and be shown
returns that predate it.

## 5.3 — Fare evaluation

There is no price history, so the system cannot say "30% below normal". What one
sweep honestly supports is a comparator with a visible denominator.

```python
WEIGHTS = {
    "vibeScore": 0.55,   # semantic fit from retrieval
    "value":     0.25,   # the fare, normalised across today's sweep
    "headroom":  0.20,   # slack against the TIGHTEST ceiling in the group
}
```

Fit leads, price orders. A ceiling is a hard filter applied *before* any of
this, and nothing in ranking can resurrect a trip somebody cannot afford.

**`seatCount` is displayed but never scored.** Letting scarcity raise a score
means a dearer trip outranks an identical cheaper one because it is running out.
That is pressure-selling, not ranking.

**Index scored trips by identity, not by a derived key.** A key like
`"<origin>-<dest>@<return-date>"` is shared by every outbound × return
combination on that date, so dozens of distinct trips collapse into one entry
and each is scored against whichever sibling was written last. Trips are
objects; index them as objects (`id(g)`).

Every scored trip carries its denominators, rendered verbatim:

```python
comparators = {
  "median_fare_today": 411.20,
  "vs_median": -75.70,
  "headroom_vs_tightest_ceiling": 63.83,
  "seats_left": 9,
  "vibeScore": 0.86,
}
```

**Tests**

- A three-leg chain derives 6 dependencies; a two-leg trip derives 3.
- A chain whose middle leg departs before the first lands is a structural
  violation.
- `min_seats` is the minimum across all legs.
- `group_total` equals the sum of every leg's group total.
- Two trips with the same derived key but different fares score differently.
- A destination where no routing seats the party reports the gap message.
- `headroom` is measured against the tightest ceiling, not the mean.

**Gate**

```bash
python -m unittest tests.test_graph tests.test_sweep tests.test_ranking -v
python -m src.discovery.feed --origin SIN --dates 3
```

Ranked trips print, each with its comparator line, with no party and no ceilings.

---

# Phase 6 — Party, mandates, and date consensus

**Goal:** Agents that hold a private calendar and a public ceiling, and reach a
date by monotonic concession.

**Files**

| Path | Action |
|---|---|
| `src/party/preferences.py` | `CREATE` |
| `src/party/protocol.py` | `CREATE` — **gate file** |
| `src/party/ics.py` | `CREATE` |
| `src/party/members.py` | `CREATE` |
| `src/party/concession.py` | `CREATE` |
| `src/agents/member_agent.py` | `CREATE` |
| `tests/test_concession.py`, `tests/test_agent_boundary.py` | `CREATE` |

## 6.1 — The move schema (gate file)

```python
# The complete set of keys a move may carry. Anything else is a hallucination.
ALLOWED_KEYS = {"move", "member", "subject", "reason", "cost_ref"}


@dataclass(frozen=True)
class NegotiationMove:
    move: str
    member: str
    subject: str
    reason: str           # the ONLY free-text field
    cost_ref: str = None  # pointer into an Atlas response, never a literal
    # deliberately NO amount field - structural, not a prompt instruction
```

`render()` dereferences the `cost_ref` at display time. **A speech bubble
carrying a digit in its `reason` is not the source of that number** — the figure
comes from the ref regardless of the words.

## 6.2 — Privacy boundary

**Two different objects, two different names.** `Mandate` (Phase 7) is the
party's stateful authority ledger — it accumulates, holds and settles.
`Ceiling` is a per-member threshold that **never depletes**. Naming both
"mandate" invites the reading that each member has a budget being drawn down,
when nothing is ever drawn down.

```python
@dataclass(frozen=True)
class Ceiling:
    """The entire money surface of a member agent. There is no more than this.

    AUTHORITY, not funds. Granted once by a human, consumed by nothing, and only
    ever tested against an Atlas-returned per-person price. Two answers: yes,
    and veto.

    It must NEVER grow a spend/charge/debit method - a test enforces that. The
    moment this object can be drawn down it stops being a delegated limit and
    becomes a wallet, which is the thing this system does not have.
    """
    member: str
    amount: float
    currency: str = "USD"

    def permits(self, per_person_price: float) -> bool:
        """The only question this object answers."""

    def shortfall(self, per_person_price: float) -> float:
        """How far over. Zero or negative means it fits."""
```

```python
@dataclass
class MemberPreferences:
    """Private to the member's agent. The orchestrator never reads this."""
    member: str
    origin: str
    ceiling: Ceiling
    date_ranking: list = field(default_factory=list)   # best first
    reservation_depth: int = None    # won't concede past this; None = all
    avatar: str = "🙂"
    preferences: str = ""            # verbatim, said out loud, so public
    clauses: tuple = ()              # embedded + negation-tagged, derived
    named_places: dict = field(default_factory=dict)
    unrecognised: tuple = ()
    busy_days: tuple = ()            # from their .ics; PRIVATE, never rendered
    calendar_note: str = ""
```

`public_view()` exposes the ceiling and what they typed. It must **never**
expose `busy_days` or `date_ranking`.

**`tests/test_agent_boundary.py` pins `member_agent.py`'s import set.** The
agent holds preferences as `self._prefs` and hands out one date per round;
anything else reaching into that dataclass is a bug. Clauses arrive
pre-computed, so the agent needs no retrieval import.

## 6.3 — Concession

```
1. Each agent holds its principal's ranking PRIVATELY.
2. Round 1: every agent names only its single favourite date.
3. No date named by everyone -> each agent CONCEDES: names the next date down
   its own ranking. It may NEVER go back up.
4. Consensus is the first date every agent has named.
5. An agent that would concede past its reservation depth WITHDRAWS.
```

Termination is guaranteed: each round every agent either reveals one new date or
withdraws, and both are finite. **Monotonicity is what makes the transcript a
record of narrowing positions** rather than a progress bar.

Rounds are **synchronous and simultaneous** by design — enforce it with a test,
and never describe the system as doing asynchronous negotiation.

`ics.preferences_from_ics()` turns a calendar into a ranking: clashing days are
dropped entirely, and Fri/Sat starts preferred.

**Members with no calendar must not all receive the identical ranking** — if
they do, consensus lands in round one with no concession and the mechanism is
invisible. Derive a deterministic per-name offset into the candidate window.

**Tests**

- Four agents with colliding calendars take 3+ rounds and produce visible
  concessions.
- `public_view()` leaks neither `busy_days` nor `date_ranking`.
- An agent never names a date above one it already conceded.
- A move with an `amount` key is rejected by the schema.
- A move whose `reason` contains a digit still renders its figure from the ref.
- `Ceiling` has no spend/charge/debit method.

**Gate**

```bash
python -m unittest tests.test_concession tests.test_agent_boundary -v
python -c "from src.party.members import demo_party; print([m.favourite() for m in demo_party()])"
```

Four distinct favourite dates, or the calendars are not colliding.

---

# Phase 7 — Executor, the five gates, re-price

**Goal:** The money path. Nothing reaches Atlas without passing five checks, and
authority is reserved for the whole itinerary or not at all.

`src/agent/executor.py`, `src/agent/mandate.py` and `src/booking/reprice.py` are
**gate files**. They are short, and they are where every claim the product makes
is either true or false. Review every line individually.

**Files**

| Path | Action |
|---|---|
| `src/agent/proposal.py` | `CREATE` |
| `src/agent/mandate.py` | `CREATE` — **gate file** |
| `src/agent/executor.py` | `CREATE` — **gate file** |
| `src/booking/reprice.py` | `CREATE` — **gate file** |
| `src/agent/decision_log.py` | `CREATE` |
| `src/atlas/payment.py` | `CREATE` |
| `src/ui/confirm.py`, `src/ui/receipt.py` | `CREATE` |
| `tests/test_gates.py`, `tests/test_whole_trip_reservation.py`, `tests/test_payment.py` | `CREATE` |

## 7.1 — The proposal schema

```python
ALLOWED_KEYS = {"action", "target", "reason", "cost_refs"}


@dataclass(frozen=True)
class ActionProposal:
    action: str            # one of ACTIONS
    target: str            # the itinerary key
    reason: str            # the ONLY free-text field
    cost_refs: tuple = ()  # ONE POINTER PER LEG; empty => zero-cost
    # deliberately NO amount field - structural, not a prompt instruction

    def __post_init__(self):
        if self.action not in ACTIONS:
            raise ProposalSchemaError("unknown action %r" % (self.action,))
        if not self.reason:
            raise ProposalSchemaError("every proposal must carry a reason")
        if isinstance(self.cost_refs, str):
            # A bare string is the shape that reserves half a return trip.
            # Coercing it to a one-element list would make that bug legal input.
            raise ProposalSchemaError(
                "cost_refs must be a sequence of refs, one per leg - got a "
                "single string. A return trip is two priced routings under two "
                "cache keys; one ref cannot address both.")
        object.__setattr__(self, "cost_refs", tuple(self.cost_refs))
```

**`cost_refs` is plural because a trip has legs.** A single ref addresses one
routing under one cache key; the other legs live under different keys and no
derivation reaches them. Reserving against one ref reserves one flight of a
return trip and under-counts the order — indistinguishable, downstream, from
spending money nobody authorised.

## 7.2 — The five gates

```
1. SCHEMA      - well-formed proposal, no invented fields?
2. DEREFERENCE - does every ref point at a figure Atlas returned THIS RUN?
3. MANDATE     - does the summed total fit under the granted ceiling?
4. CONFIRMATION- is there a standing confirmation, and is it still valid?
5. CALL        - only now does Atlas get touched.
```

Gate 2 sums across legs:

```python
        amount, amount_refs = 0.0, []
        adults = int((payload or {}).get("adults") or 1)
        expected = int((payload or {}).get("legs") or 0)

        if expected and len(proposal.cost_refs) != expected:
            return self._refuse(
                proposal, "dereference",
                "proposal carries %d cost_ref(s) for a %d-leg trip - reserving "
                "part of an itinerary is indistinguishable from spending money "
                "nobody authorised"
                % (len(proposal.cost_refs), expected))

        for ref in proposal.cost_refs:
            leg_total, leg_refs = resolve_group_total(ref, adults, self.cache)
            amount = round(amount + leg_total, 2)
            amount_refs.extend(leg_refs)
```

The executor cannot see the itinerary, so the caller passes `payload["legs"]`.
Without that count, a proposal with one ref for a two-leg trip is
indistinguishable from a legitimate one-leg proposal.

Gate 4 compares **the same quantity on both sides**:

```python
        # price_shown is the WHOLE-TRIP per-person figure the human saw, and
        # `amount` is the WHOLE-TRIP order total, so this divides to the same
        # quantity. Comparing an outbound-only figure here would not merely
        # weaken the check - it would invert it: the smaller number always
        # satisfies `now <= shown`, so the guard would pass on every fare rise
        # it exists to catch.
        per_person = round(amount / adults, 2) if adults else amount
        if not confirmation.still_valid_for(per_person):
            return self._refuse(proposal, "stale_confirmation", ...)
```

## 7.3 — Authority accounting

```python
AUTONOMOUS_GROUPS = {GROUP_SEARCH, GROUP_AFTERCARE}   # 01, 04
SETTLING_ACTIONS  = {"pay_group"}     # SPENDS
RESERVING_ACTIONS = {"book_group"}    # HOLDS
```

> **Booking RESERVES authority; only payment SPENDS it.** Committing at both
> points charges the party twice for one set of seats.

An action absent from `ACTION_GROUPS` is refused, never defaulted to the
permissive group.

## 7.4 — Re-price, every leg

```python
def check(client, card, confirmation, date, party_size, force_key=None):
    """The single-leg decision. UNCHANGED | CHEAPER | DEARER | GONE."""


def check_all(client, legs: list, confirmation, party_size: int,
              force_keys: dict = None) -> tuple:
    """Re-price EVERY leg. Returns (worst_result, per_leg_results).

    The verdict is the worst across legs: any leg DEARER or GONE voids the whole
    confirmation. A trip is one purchase decision and cannot be partially
    re-confirmed - re-pricing only the outbound leaves the return fare
    unguarded, which is a silent hole in the one gate this product is built
    around.
    """


SEVERITY = {GONE: 3, DEARER: 2, UNCHANGED: 1, CHEAPER: 0}
```

Severity is a module constant so the ordering is data rather than a chain of
comparisons. **A trip where one leg fell and another rose has not got cheaper.**

`check_all` compares the **summed** per-person across legs against
`price_shown`; individual legs report their own deltas for the UI but are not
each measured against the trip total.

**Why after the click:** a price check before confirmation tells you what the
fare was when you asked. The window that matters is between the human saying yes
and the order existing, and low-cost fares move in exactly that window.

**Do not invent a verify endpoint.** Re-run `search.do` and match the candidate
on `flightNumber` across segments. Never parse `routingIdentifier`, which is
documented as opaque.

## 7.5 — Payment and stubs

```python
ACTION_ENDPOINTS = {
  "book_group": "orderCommit.do",   # DOCUMENTED name, UNDOCUMENTED shape
  "pay_group":  "pay.do",           # NOT documented anywhere - placeholder path
}
UNCHARACTERISED = set(ACTION_ENDPOINTS.values())
```

Both are in `UNCHARACTERISED`, so **no HTTP request is fired for either**.
Record `executed_stub` rather than guessing a URL or faking a success response.

Card values come from `.env` only. `mask()` and `redact()` on every path that
could print a PAN; `TestCard.__repr__` is defensively masked so a stray
traceback cannot leak it. `as_payload()` is the **only** function returning full
card data, called at exactly one place under a standing confirmation.

**Tests**

- **Whole-trip reservation:** reserved amount equals `graph.group_total` for
  every trip on the board, to the cent.
- A proposal with one ref for a two-leg trip refuses at `dereference`.
- A bare string `cost_refs` raises `ProposalSchemaError`.
- **The inverted-guard case:** a *return* leg rising enough to raise the trip
  price refuses at `stale_confirmation`. An outbound-only check would pass this;
  it must not.
- One leg `CHEAPER` and one `DEARER` yields `DEARER`; one `GONE` yields `GONE`.
- `book_group` reserves and `pay_group` settles the same total — no double
  charge, no partial settle.
- A ref into a response not received this run refuses at `dereference`.
- Both money endpoints record `executed_stub` and attempt no HTTP.
- `redact()` masks `cardNumber` and blanks `cvv` anywhere they appear.

**Gate**

```bash
python -m unittest tests.test_gates tests.test_whole_trip_reservation tests.test_payment -v
```

Green. Then confirm the reservation identity holds on real data:

```bash
python -m src.demo --mode=replay 2>&1 | grep -E "reserved|group_total"
```

Reserved must equal the displayed group total, to the cent.

---

# Phase 8 — Orchestrator, reconciliation, and the repair loop

**Goal:** The stage machine, the engine that builds the multi-city option, and
the loop that keeps a booked commitment true when Atlas contradicts it.

**Files**

| Path | Action |
|---|---|
| `src/party/orchestrator.py` | `CREATE` |
| `src/discovery/reconcile.py` | `CREATE` |
| `src/itinerary/propagate.py` | `CREATE` |
| `src/itinerary/replan.py` | `CREATE` |
| `src/agents/pitch.py` | `CREATE` |
| `tests/test_reconcile.py`, `tests/test_chain.py`, `tests/test_propagation.py`, `tests/test_replan.py` | `CREATE` |

## 8.1 — The stage machine

```
 1  DATE CONSENSUS    agents concede over private date windows      [Group 01]
 2  ATLAS DISCOVERY   sweep both directions; Atlas proposes destinations
 3  CONSTRAINT CHECK  seats, structure, and EVERY member's ceiling
 3b RECONCILIATION    find unsatisfied members; query hubs
 4  DECISION NODE     the single user choice: Option 1 vs Option 2
 5  CONFIRM           the choice IS the confirmation                [Group 02]
 6  RE-PRICE          check every leg again, AFTER the choice
 7  ORDER + PAY       autonomous, under the standing confirmation   [Group 02/03]

 8  CHANGE / 9 PROPAGATE / 10 RE-PLAN / 11 RE-NEGOTIATE  -> back to 4
```

Nothing in the orchestrator talks to Atlas directly and nothing in it spends
anything; stages 5–7 hand a proposal to the executor, which applies its own
gates regardless of what the orchestrator believes.

**The booking proposal carries one ref per leg:**

```python
        return ActionProposal(
            action="book_group", target=trip.key,
            reason=("booking %s for %d — %d night(s), %s"
                    % (trip.destination_name, self.party_size, trip.nights or 0,
                       " + ".join(n.label for n in trip.nodes))),
            cost_refs=tuple(n.price_ref for n in trip.nodes))
```

and passes `payload={"adults": self.party_size, "legs": len(trip.nodes)}`.

## 8.2 — Chained search

```python
MAX_CHAIN_COMBOS = 200
MIN_STOPOVER_HOURS = 6      # below this it is a connection, not a stopover
MAX_STOPOVER_HOURS = 72     # above this it is two holidays


def chain_for(client, origin: str, hub: str, destination: str,
              out_date: str, stopover_nights: int, destination_nights: int,
              party_size: int, destination_name: str = "") -> tuple:
    """One three-leg itinerary through `hub`. Returns (graphs, error).

    Reuses search_nodes(), so the seat filter and gap reporting are inherited,
    not re-implemented. Every leg passes the seat filter independently - one
    unseatable leg kills the chain.

    The combinatorial product of three legs is large; cap at MAX_CHAIN_COMBOS by
    taking the cheapest N routings per leg before combining.
    """
```

Every combination goes through `graph.build_chain()`;
`structural_violations()` rejects the invalid ones. No bespoke validation.

Reservation and re-price need **no chain-specific code** — the proposal carries
three refs instead of two and `payload["legs"]` reads 3. If you find yourself
writing chain-specific money logic here, Phase 7 was done wrong.

## 8.3 — The repair loop

**This is what separates the product from a trip generator.** A generator
answers once. This holds a commitment and repairs it, so build it with the same
care as the money path — not as an afterthought bolted onto the end of a run.

### Change kinds

```python
PRICE     # Atlas returns a different fare for a leg
SCHEDULE  # the times moved
GONE      # the routing is no longer offered
CEILING   # the trip did not move - the CONSTRAINT did
```

`CEILING` has no analogue in a search product and is the one most likely to be
skipped. A member re-grants a lower airfare ceiling; no price changed and
nothing was re-searched, but a trip the group already accepted is now vetoed.
**Delegated authority was withdrawn**, and the loop has to work out what that
broke. Build it in the same pass as `PRICE`, or it will never be added.

### Propagation

```python
@dataclass
class Change:
    """Something Atlas now says that it did not say before."""
    node_key: str
    kind: str                    # PRICE | SCHEDULE | GONE | CEILING
    was: float = None            # the fare, for PRICE; the ceiling, for CEILING
    now: float = None
    member: str = None           # who moved their ceiling, for CEILING
    cost_ref: str = None
    replacement: object = None   # the FlightNode Atlas returned THIS TIME
    detail: str = ""


@dataclass
class Impact:
    change: Change
    total_before: float = None
    total_after: float = None
    downstream: list = field(default_factory=list)
    breached: list = field(default_factory=list)
    structural: list = field(default_factory=list)
    replanable: list = field(default_factory=list)
    consensus_invalidated: bool = False
    still_feasible: bool = True

    def narrate(self) -> list:
        """The chain, in order, for the UI to render one line at a time."""


def apply_change(graph, change) -> object:
    """A COPY of the graph with the change applied. The original is untouched.

    The change is applied by SWAPPING IN the node Atlas actually returned, never
    by editing a price in place. Editing would leave the graph reporting one
    number while its cost_ref resolved to another - the screen saying 151.50 and
    the pointer saying 96.00 - and the traceability claim would quietly be
    false. A change carrying no replacement leaves the graph alone; the caller
    learns that from `still_feasible`, not from a total that drifted.

    Returning a copy matters: the UI shows before and after side by side, and
    the orchestrator must be able to reject the new world and keep the old one.
    """


def propagate(graph, change, mandates) -> tuple:
    """Graph + change -> (Impact, repaired_graph)."""
```

`narrate()` must produce consequences, not a delta:

```
outbound +42.00
fare 200.00 -> 242.00
Marcus exceeds the ceiling he granted
group consensus invalidated
re-planning the dependent itinerary
```

**Edges are one-way by construction.** `downstream_of(node_key)` follows the
dependency direction, so a change to the return can never strand the outbound.
Reachability is structural — do not compute it with a heuristic.

**`replanable` includes the node that moved**, not only what is downstream of
it: swapping the dearer outbound for a different one is usually the cheapest
repair available.

### Re-planning, cheapest-change-first

```python
RETURN_SWAP      = "return"
OUTBOUND_SWAP    = "outbound"
DESTINATION_SWAP = "destination"

KIND_LABEL = {
    RETURN_SWAP:      "same place, different return",
    OUTBOUND_SWAP:    "same place, different outbound",
    DESTINATION_SWAP: "different destination",
}


@dataclass
class Alternative:
    kind: str
    graph: object
    delta_vs_broken: float = None   # per-person, against the trip that broke
    note: str = ""
    rejected_for: list = field(default_factory=list)


def explore(client, broken, mandates, outbound_date, return_dates,
            party_size) -> list:
    """Walk the graph and vary exactly ONE dimension at a time.

    The ordering is cheapest-change-first and it is deliberate: moving one leg
    disturbs the group's agreement less than moving the whole trip, so the
    agents are offered the smallest repair before the largest.
    """
```

**Nothing that breaches a ceiling is offered at all.** Filter before returning,
not in the UI — the group must never get to vote on something one of them cannot
afford, or a ceiling becomes erodable by majority in a moment of inconvenience.

Every alternative is a complete `ItineraryGraph`, priced from refs Atlas
returned this run, and re-checked against every mandate.

### Termination — build this with the loop, not after it

Stages 8–11 are a real cycle: change → propagate → re-plan → re-negotiate →
decision → re-price → possibly another change. **It has no natural exit, and a
cycle with no exit throws no error.** It just spends.

The dangerous case is not oscillation — it is a loop that looks like progress. A
party whose tightest ceiling sits just under every viable option produces new
candidates every round, all ceiling-checked, all failing, widening a dimension
each time. Real work, real output, real API calls, no progress. Nothing about
that is detectably wrong from inside one round.

```python
MAX_REPLAN_ROUNDS  = 4      # per change
MAX_SESSION_ROUNDS = 12     # across the whole session

EXHAUSTED = "exhausted"   # ran out of dimensions to vary
BLOCKED   = "blocked"     # one constraint killed every candidate
BUDGET    = "budget"      # round cap hit with candidates unexplored


@dataclass
class LoopOutcome:
    """Why the repair loop stopped, and the best thing it found.

    This is a RESULT, not an error. "I could not solve this, and here is exactly
    what blocked it" is a successful outcome for an unsolvable constraint set.
    """
    stopped_because: str            # EXHAUSTED | BLOCKED | BUDGET
    rounds_used: int
    best: object = None             # best surviving ItineraryGraph, or None
    blocking_member: str = None     # set when stopped_because == BLOCKED
    shortfall: float = None         # how far the best option missed, per person
    detail: str = ""

    def narrate(self) -> str:
        """One line a human can act on. Always names numbers when it has them."""
```

**Per-change budget with a separate session cap.** A per-change budget bounds
each disruption independently, which is what keeps a late disruption solvable;
a session-only budget means a change arriving near the end finds the allowance
already spent, at the worst possible moment. The session cap bounds total spend
when many small changes arrive.

**Two early terminations, both independent of rounds remaining:**

```python
def should_stop(round_result, previous, mandates) -> str:
    """Returns a stop reason, or None to continue.

    - Zero new candidates -> EXHAUSTED immediately. Nothing further exists.
    - Every candidate breaches the SAME member -> BLOCKED. The loop has
      diagnosed the problem; more rounds only re-confirm it at the cost of
      another sweep.
    """
```

`BLOCKED` is the most valuable of the three and the one a bare counter never
produces. A group whose every option dies on one ceiling does not need a fifth
round of itineraries — it needs to be told that one number is deciding the trip.

Render `LoopOutcome` through the same panel as
[The Trade](#the-trade--websrctradepaneljsx-create):

```
After 4 rounds, nothing cleared Marcus's ceiling of 210.00.
Closest was Penang at 268.17 — short by 58.17.
```

**The terminal state is an outcome, not an error.** A surface still showing a
spinner after the loop gave up is the same silent failure in a different
costume — assert this in the UI phase.

**Tests**

- A loop that cannot satisfy the tightest ceiling stops at `MAX_REPLAN_ROUNDS`
  and returns `BUDGET` with a non-null `best`.
- A round producing zero new candidates stops immediately with `EXHAUSTED`,
  **before** the round cap.
- Every candidate breaching the same member yields `BLOCKED` and sets
  `blocking_member`.
- `MAX_SESSION_ROUNDS` caps the total across several changes; a later change
  finding the session budget spent still returns a `LoopOutcome`, never a hang.
- `narrate()` on a `BLOCKED` outcome names the member and the shortfall.
- No terminal state leaves the surface in `synthesizing`.

### Closing the loop

Re-negotiation runs the unchanged concession protocol over the surviving
alternatives and returns to **stage 4, the decision node** — not a dead end. The
user's next selection is a fresh `Confirmation` bound to the new price.

**The same loop serves deliberate changes.** `change_constraint(kind, ...)`:

- `date` — re-runs the sweep against a different departure. Nothing is
  fabricated; it asks Atlas again.
- `budget` — emits a `CEILING` change and re-measures fares already returned.

One mechanism for "the world changed" and "we changed our minds". If you find
yourself writing a second path for the user-initiated case, stop — it is the
same loop with a different trigger.

### Tests

- A `PRICE` rise pushing the trip over the tightest ceiling sets
  `consensus_invalidated` and names the breached member.
- A `PRICE` rise the group can absorb leaves `still_feasible` true and narrates
  *"the trip still holds"*.
- **A `CEILING` re-grant with no price change vetoes a previously accepted
  trip.** This proves the loop reacts to authority, not only to fares.
- `apply_change` returns a copy — the original graph is unchanged after.
- A change carrying no `replacement` alters no total.
- A change to the return leg leaves the outbound out of `downstream`.
- After a swap, `graph.per_person` and every `cost_ref` resolve to the **same**
  figures — the traceability invariant.
- `explore()` returns return-swaps before outbound-swaps before
  destination-swaps.
- An alternative breaching any ceiling is absent from the list, not merely
  flagged.
- `delta_vs_broken` is measured against the trip that broke.

## 8.4 — Aftercare: entering the loop after the order

Most travel value sits after the booking, and most builds stop there. Aftercare
is **not a second engine** — it is §8.3's repair loop entered from a post-order
trigger. If you write a separate post-order planner, stop.

**Files**

| Path | Action |
|---|---|
| `src/booking/aftercare.py` | `CREATE` |
| `tests/test_aftercare.py` | `CREATE` |

### Re-shop with `search.do`, never a guessed endpoint

`queryOrderDetails`, `void`, `refund` and `balance` are **undocumented**. Do not
invent any of them.

```python
def reshop(client, order, party_size: int) -> list:
    """Re-run search.do for the booked legs; diff against what was paid.

    The same substitution the post-confirmation re-price makes, for the same
    reason: no reshop endpoint is published, search.do is fully characterised,
    and the booked routing is identifiable by flightNumber across segments.
    Never parses routingIdentifier.

    Returns a list of propagate.Change - GONE, SCHEDULE or PRICE - so everything
    downstream is the machinery that already exists.
    """
```

| Finding | Change kind | Meaning |
|---|---|---|
| Booked routing absent | `GONE` | Itinerary broken, needs repair |
| Times moved | `SCHEDULE` | Connections may no longer hold |
| Equivalent leg now cheaper | `PRICE` | A credit **owed to the traveller** |

Build the third case. A fare that falls after booking is money the traveller
never learns about, and it is visible with machinery already written.

### Triggers

No schedule-change webhook is documented. **Assume it never fires** — the system
must not depend on being told.

```python
POLL_INTERVAL_SECONDS = 900     # re-shop booked legs; read-only, safe on a timer


def watch(client, order, party_size, on_change=None) -> None:
    """Poll re-shop. The only trigger that works without provider cooperation."""


def inject(event: dict) -> list:
    """Operator-supplied event, for rehearsal only.

    The payload shape is OURS, not Atlas's. Every Change it produces carries
    source="injector", and the UI renders that label. An injected event that
    looks identical to a real one is the same misrepresentation as a stubbed
    call rendering as a success.
    """
```

### Authority is per action, not per group

Group 04 holds one read and three writes, so a group-level rule is wrong in one
direction or the other:

```python
AUTONOMOUS_GROUPS = {GROUP_SEARCH}        # NOT GROUP_AFTERCARE

# Read-only aftercare, named individually.
AUTONOMOUS_ACTIONS = {"reshop_order", "refund_order"}
```

| Action | Moves money | Authority |
|---|---|---|
| `reshop_order` | no | Autonomous; safe on a timer |
| `change_order` | yes | Confirmation, or a standing one bound to the fare difference |
| `cancel_order` | yes | Confirmation. Always |
| `refund_order` | credit | Autonomous to request; the credit is recorded, never spent |

Putting all of Group 04 in `AUTONOMOUS_GROUPS` makes cancelling a booked trip an
unattended action.

### Post-settlement accounting

The money is already committed, so the arithmetic is the **difference**, in both
directions:

```python
def settle_difference(executor, order, repair, mandates) -> object:
    """A dearer repair must fit REMAINING authority; a cheaper one is a credit.

    A member who granted 210 does not owe a change fee they never authorised, so
    the ceiling vetoes a repair exactly as it vetoes a trip. mandate.credit()
    returns authority rather than spending it.
    """
```

**A repair is never applied because it is available** — only because it survives
every ceiling. If it does not, the group is told what it would have cost and who
it would have broken.

### Stubs

`change_order`, `cancel_order` and `refund_order` join `UNCHARACTERISED` and
record `executed_stub`. The re-shop, the propagation, the ceiling arithmetic and
the receipt are all real.

### Tests

- A booked routing missing from a re-shop yields a `GONE` change.
- A cheaper equivalent leg yields a `PRICE` change with a **negative** delta and
  a resolvable `cost_ref`.
- A repair exceeding remaining authority is vetoed and names the member.
- `mandate.credit()` raises remaining authority; `spent` does not increase.
- An injected event produces changes tagged `source="injector"`.
- `reshop_order` executes with no confirmation; `cancel_order` refuses without
  one.
- All three order-mutating actions record `executed_stub` and attempt no HTTP.

**Gate**

```bash
python -m unittest tests.test_aftercare -v
python -m src.demo --mode=replay --aftercare
```

A booked order re-shopped, a change detected, propagation run, and either a
ceiling-checked repair or a stated reason it could not be made.

## 8.5 — Reconciliation

```python
"""Who did the winning trip leave out, and can a hub fix it?

Option 1 optimises for the single best-fit or lowest-cost destination. This
module asks the second question - and it is the only justification Option 2 has
for existing. A multi-city route that closes nobody's gap is just a longer
flight.
"""

MAX_HUB_CANDIDATES = 3
NO_GAPS   = "every member's stated preferences are already satisfied"
NO_HUB    = "no reachable hub closed a gap"
NO_SEATS  = "no seats on the connecting leg"
BREACHED  = "every viable chain breached a ceiling"


@dataclass
class Gap:
    member: str
    satisfied: tuple
    unsatisfied: tuple
    weight: float      # proportion of this member's preferences unmet


def gaps_for(option1, members: list) -> list: ...


def hub_satisfies(hub, gap: Gap) -> float:
    """How well one hub closes one gap. REUSES retrieval.score_city.

    A "gap" and a "match" must be measured by the same vectors, or Option 2's
    justification is unfalsifiable: it could claim to satisfy a preference by a
    metric nothing else in the system uses.
    """


MAX_DETOUR_FACTOR = 2.0   # chain elapsed vs the direct trip's elapsed


def rank_hubs(gaps: list, option1, pool: list = None) -> list:
    """Candidates by gap coverage. Runs BEFORE any chained call - no fare known.

        hub_score = Σ (gap.weight × hub_satisfies(hub, gap))

    `pool` defaults to EVERY reachable city except the Option 1 destination.
    There is no hub list and no city carries a `hub` flag: a hand-authored set
    of "cities worth a layover" would pre-decide what the engine may route
    through, and would make the probe circular. Viability is decided by the
    live leg probe in Step 3, not here - a candidate whose legs come back empty
    drops out and the next-ranked one is tried.
    """


def within_detour(chain, direct) -> bool:
    """Reject a chain that is not plausibly on the route.

    Measured, not judged: total elapsed vs the direct trip's elapsed, both from
    durations Atlas returned. "Plausibly on the route" is a number, or it is
    somebody's opinion about geography.
    """


def synthesize(client, option1, members, mandates, agreed_date, party_size,
               on_progress=None) -> dict:
    """The whole stage. `on_progress(message)` fires before each Atlas call so
    the UI can render the synthesis as it happens. The delay IS this work."""
```

**Hard rules, each a test**

| Rule | Failure it prevents |
|---|---|
| The **combined** total faces every ceiling | A chain clearing leg by leg but breaching in total |
| Every leg carries its own `cost_ref` | A blended number with no denominator |
| The comparator is named | "Saving 81.70" beside nothing |
| Seats are the minimum across legs | A party that cannot board the middle leg |
| No viable chain ⇒ `option2 is None` + a reason | A fabricated second option |

**Tests**

- A party with no unmet preferences yields `option2 is None`, `reason == NO_GAPS`.
- `hub_satisfies` and `retrieval.score_city` agree for the same input.
- `rank_hubs` makes zero Atlas calls (assert with a client that raises on `post`).
- **`rank_hubs` considers EVERY reachable city**, not a subset - assert the
  candidate pool size equals `len(dataset.reachable()) - 1`.
- A top-ranked candidate whose leg returns nothing **drops out and the next one
  is tried**, rather than failing the whole synthesis.
- A chain exceeding `MAX_DETOUR_FACTOR` x the direct elapsed time is rejected.
- No `City` attribute and no dataset key marks a city as a hub.
- A chain over the tightest ceiling is rejected though each leg is under it.
- `comparator.cost_ref` resolves against the cache.
- A stopover under `MIN_STOPOVER_HOURS` is rejected.

**Gate**

```bash
python -m unittest tests.test_reconcile tests.test_chain tests.test_propagation -v
python -m src.demo --mode=replay
```

Full run: consensus in 3+ rounds, a ranked board, a synthesis result, a receipt.

---

# Phase 9 — Server and UI

**Goal:** Three stages on screen, the single decision point, and an autonomous
run that is legible while it happens.

**Files**

| Path | Action |
|---|---|
| `src/ui/dashboard.py` | `CREATE` |
| `web/src/App.jsx`, `api.js`, `Panels.jsx`, `AddAgentModal.jsx` | `CREATE` |
| `web/src/DecisionModal.jsx`, `StatusStream.jsx`, `Terminal.jsx` | `CREATE` |

Single-page React app served from `web/dist` by the same stdlib
`ThreadingHTTPServer` that runs the orchestrator — one command, no second
process.

**Every mutation returns the whole state object.** The client never reconciles a
partial update against what it already had, a bug class that surfaces as agents
disagreeing with the receipt.

**Long work runs on a worker thread.** A sweep is a dozen sequential calls; held
under the request lock it freezes the status endpoint for the whole sweep, and
the UI goes dead exactly while the interesting thing is happening.

## 9.1 — HTTP contract

```
GET  /api/state                    → the whole state object
GET  /api/feed                     → { running, trips[], gaps{}, dates[] }
GET  /api/calendars                → preset calendars
GET  /api/receipt                  → rendered receipt

POST /api/feed        { dates? }   → starts the no-party sweep (worker)
POST /api/members     { name, budget, preferences, ics }
POST /api/members/remove { name }
POST /api/round       {}           → one concession round
POST /api/settle_date {}           → run rounds to consensus
POST /api/discover    {}           → starts the sweep (worker)
POST /api/synthesize  {}           → starts reconciliation (worker)
POST /api/decide      { option }   → the single decision; runs the chain
POST /api/renegotiate { index }
POST /api/constraint  { kind, date?, member?, ceiling? }
POST /api/reset       {}
```

Errors return `{ error }` — a running demo must never die on a click, and a
browser disconnect mid-response must not print a traceback.

## 9.2 — Stage 1: initialization

Landing view is the **discovery feed** — ranked Atlas inventory with no party,
no ceilings, no agreed date. Flights are visible before anybody has joined.

Spawn form: name, airfare budget (numeric, `> 0`), free-text preferences,
optional `.ics` upload or a preset calendar. A preset attaches a **calendar
only** — the operator still names the agent and sets their budget.

Live retrieval feedback while people join: the group vibe in words, the
candidate list with each entry's `why`, and any input nobody could place.

## 9.3 — Stage 2: the decision surface

```js
function surfacePhase(state) {
  if (state.discovering) return 'sweeping'
  if (state.decision) return 'committed'
  if (state.synthesis) return 'decision_open'
  if (state.synthesizing) return 'synthesizing'
  if (state.cards?.length) return 'board_live'
  return 'sweeping'
}
```

Derive the phase from state, **never from a local timer**.

**Phase A — the live ranked board.** One card per city searched: destination,
per-person fare, group total, seats left, the comparator line, feasibility. The
board is evidence, not a control surface — it has no pick handler, and it stays
mounted behind the modal.

#### The Trade — `web/src/TradePanel.jsx` `CREATE`

**A first-class panel beside the board, at equal visual weight — not a footnote
under it.** This is what stops a correct system from feeling broken.

The failure it exists for is produced by construction: someone types *"kimchi and
kpop"*, retrieval correctly ranks Seoul and Busan first, the tightest ceiling
then deletes every Korean and Japanese option, and the board returns five cities
nobody asked for. Every rule behaved correctly. The experience is still *"it
ignored what I said."*

Render the collision:

```
Seoul   matched on food, street food      cheapest 230.56
        over Marcus's ceiling of 210.00   short by 20.56     [cost_ref]
```

Each row: destination · **what it matched** · the cheapest fare Atlas returned ·
**whose** ceiling it broke · **by how much** · the `cost_ref` behind the fare.

```jsx
export function TradePanel({ missed, members, busy, onConstraintChange }) {
  if (!missed?.length) return null      // an empty Trade renders NOTHING
  ...
}
```

Rules, each a review point:

- Rendered **whenever `state.missed` is non-empty** — never collapsed, never
  below the fold, never behind a disclosure.
- **Names the member.** "Over budget" is not the finding; "over Marcus's ceiling
  by 20.56" is.
- **Empty renders nothing.** A panel announcing "no trade-offs" is noise on the
  run where everything fit.
- **Actionable.** Each row offers the constraint change that resolves it — raise
  that member's ceiling, or move the departure — routed into `/api/constraint`,
  which re-measures against fares Atlas already returned. Nothing is fabricated
  to make the offer.
- **Never proposes a ceiling the member has not granted.** State the shortfall,
  offer the control, let the human decide.

**Phase B — the dual-option modal.**

```
Card 1 — Cheapest / Primary Fit
    destination, nights
    per-person fare + group total          [dereferenced]
    seats left
    comparator line (vs median, headroom, seats)
    why this matched:  Match.why  ·  vibeScore

Card 2 — Multi-City / Group-Optimized
    route:            SIN → HKG → KIX → SIN
    stopover:         HKG, 31h
    per-leg fares + combined total         [each leg dereferenced]
    delta vs Card 1                        [named comparator + its cost_ref]
    gaps closed:      "Marcus — street food; Ana — onsen"
    seats left        (minimum across all legs)
```

Card 2 states **which members it satisfies that Card 1 does not**. That is its
entire justification, and it is rendered, not implied.

**Disabled state.** No viable chain ⇒ Card 2 renders disabled with the reason.
Filling that slot with a fabricated alternative to preserve the layout would
make the UI itself the liar.

Selection calls `/api/decide` once and disables both cards immediately — a
double-click must not produce two confirmations.

## 9.4 — Stage 3: autonomous execution

**Every stream entry is sourced from the decision log**, never a scripted
sequence. If a step is not in the log it did not happen, and the UI must not
claim it did.

```
Querying Atlas · SIN→HKG @20260920 … 14 routings
Routing options resolved · 3 chains viable
Re-pricing confirmed itinerary · 2 legs …  fare held at 268.17
Reserving authority · 1072.68 of 1200.00 ceiling
Executing payment · [STUBBED — no documented endpoint]
```

**The stubbed steps are labelled.** A run rendering "Payment complete" over a
stub is the one claim in this product that could actually mislead someone.

Final payload card: confirmed itinerary (all legs, each with fare and
`cost_ref`), passenger specs, transaction status, booking reference, per-member
ceilings, and the provenance badge.

**Provenance has three states, not two:** any placeholder data on screen, all
real, or nothing asked yet. "We have not asked Atlas yet" is not the same claim
as "everything here is real".

**Gate**

```bash
cd web && npm run build && cd ..
python -m src.ui.dashboard --mode=replay
```

Drive one full run: spawn four agents with conflicting preferences → settle →
discover → synthesize → decide → receipt. Confirm by reading the DOM that the
board sits behind the modal, both cards show dereferenced fares, Card 2 names
the members whose gaps it closes, and a no-gap party renders Card 2 disabled
with its reason.

---

# Final verification

```bash
python -m unittest discover -s tests -t .
cd web && npm run build && cd ..
python -m src.demo --mode=replay
python -m src.demo --mode=replay --breach
python -m src.ui.dashboard --mode=replay
```

Against the running app:

| Check | Expected |
|---|---|
| `"beach and relax"` | Tropical-beach cities shortlisted, though neither phrase appears in any keyword set |
| `"kimchi and kpop"` | Korean cities top, `why` reads "you named it" |
| `"anywhere but bangkok"` | Bangkok present, scored `0.0`, not hidden |
| A word in no vocabulary | Reported as unplaceable, not silently ignored |
| Party with all preferences met | Card 2 disabled, reason rendered |
| Party with an unmet preference | Card 2 names the member and the preferences closed |
| Every figure on both cards | Traceable to a `cost_ref` in the response cache |
| Reserved authority | Equals the displayed group total **to the cent**, every trip and every chain |
| A return-leg fare rise | Voids the confirmation at `stale_confirmation` — not just an outbound rise |
| A ceiling breach | Vetoes, and is never out-voted or rounded |
| Payment step | Labelled stubbed |

**Live smoke** — requires `ATLAS_BASE_URL`, `ATLAS_CLIENT_ID`,
`ATLAS_CLIENT_SECRET` in `.env` (names only; never commit values):

```bash
LIVE=1 python -m src.ui.dashboard --mode=live
```

One full run against the sandbox. Provenance must read *all real* throughout — a
placeholder badge means synthetic data reached the screen.
