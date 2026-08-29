---
kind: build_system
name: Partynerary Phased Build Guide — Spec-Driven Python/React Build & Artifact Pipeline
category: build_system
scope:
    - '**'
source_files:
    - build-guide.md
    - project_scope.md
    - best-practices.md
    - tools/requirements-build.txt
---

## What system/approach is used

This repository does not contain a traditional Makefile, Dockerfile, or CI pipeline. The build system is **spec-driven and phase-gated**, documented entirely in `build-guide.md` (the authoritative step-by-step implementation plan) and `project_scope.md` (architectural spec). Each of the nine phases is independently verifiable and leaves the system in a working state; nothing depends on a phase that has not yet landed. The project targets **Python 3.14 + React 18** with an Atlas sandbox API (`https://sandbox.atriptech.com`).

Build-time tooling is fenced into `tools/` and explicitly excluded from runtime: `tools/generate_dataset.py`, `tools/build_vectors.py`, and `tools/requirements-build.txt` install `sentence-transformers==3.*` only for generating `data/cities.json` and `data/vectors.json`. The runtime itself is stdlib-only — no numpy, no model import, no network call at execution time.

The test runner is `python -m unittest` across `tests/`, invoked per phase as the gate command. There is no package manager manifest shown in this snapshot; the repo appears to be a specification/plan repo awaiting code generation per the phased guide.

## Key files and packages

- `build-guide.md` — the canonical build order, phase deliverables, per-phase file inventories, and gate commands.
- `project_scope.md` — architectural spec defining contracts (Atlas transport, cost_ref substrate, retrieval engine, executor gates, orchestrator stages).
- `best-practices.md` — engineering discipline governing money-path safety, replay semantics, and agent lifecycle.
- `README.md` — minimal root entry point.

## Architecture and conventions

### Phase-gated delivery
Each phase declares its files under a `Files` table and ends with a `Gate` shell snippet. Phases are:
1. Atlas transport, response contract, `cost_ref` resolution
2. Probe coverage
3. City dataset + offline vector build
4. Retrieval engine (dense vector, stdlib runtime)
5. Itinerary graph, sweep, fare evaluation
6. Party — mandates, private rankings, date consensus
7. Executor, five gates, re-price
8. Orchestrator, reconciliation, chained search
9. Server and UI — three stages, two-phase decision surface

### Runtime vs build-time boundary
- Runtime: `src/`, `tests/`, `probe/` — pure stdlib Python, no external imports.
- Build-time: `tools/` installs `sentence-transformers==3.*` via `tools/requirements-build.txt` solely to produce `data/cities.json` and `data/vectors.json`, which are committed artifacts.
- Data: `data/` holds generated JSON (cities, vectors); `fixtures/test/` holds frozen test fixtures; `fixtures/live/` captures live responses during probe runs.

### Test and replay strategy
- `LIVE=1` gates all live Atlas calls; without it, `LiveCallBlocked` is raised.
- `AtlasClient(replay=True)` swaps transport to read `fixtures/<KEY>.json` — same parsing, cache, and dereferencing downstream.
- `fixtures/test/` is frozen once and never regenerated; `fixtures/live/` is captured live and replayed exactly.
- A grep gate forbids hand-written destination lists: `grep -rn "PLACES = \[" probe/ src/ && echo "FAIL: hand-written capture list" && exit 1`.

### Gate files and immutable boundaries
Four files carry product guarantees and must be reviewed line-by-line, never bundled into unrelated refactors: `src/party/protocol.py`, `src/agent/mandate.py`, `src/agent/executor.py`, `src/booking/reprice.py`. They define the schema, mandate accounting, the five-executor gates, and post-confirmation re-price logic respectively.

### Money-path discipline
- Every rendered figure is a `cost_ref` pointer into an Atlas response, dereferenced at render time — never a literal number authored by the model.
- `NegotiationMove` and `ActionProposal` carry no `amount` field; schema rejection prevents invented prices.
- Booking RESERVES authority; only payment SETTLES it — committing at both points would charge twice.
- Re-price runs after user choice, before order; `DEARER` or `GONE` voids the confirmation.

### Dependency and artifact rules
- Vectors are unit-normalized at build time so cosine reduces to dot products at runtime.
- `data/vectors.json` records `model`, `dim`, `built`, and `tokens`; changing the model requires regenerating the whole dataset.
- Index trips by object identity (`id(g)`), not derived keys like `<origin>-<dest>@<date>`.
- Seat count is a hard filter (`seatCount >= party_size`) applied before any ranking.

### Conventions and constraints
- **Runtime is stdlib-only.** Build-time tooling is fenced into `tools/` and never imported from `src/`.
- **Every change to gate files is reviewed line by line and never bundled into an unrelated refactor.**
- **A ceiling deletes; nothing else does. Never out-voted, never rounded.**
- **`seatCount >= party_size` on every leg.**
- **Re-price runs after the user's choice, before the order.**
- **Booking RESERVES authority; only payment SETTLES it.**
- **`orderCommit.do` and `pay.do` stay stubbed. No guessed URLs.**
- **No module may contain a hand-written list of destinations to capture.** Coverage comes from live probe sweeps recorded to `fixtures/live/`.
- **Tests run with `LIVE` unset** — they must pass against frozen fixtures, proving `fixtures/test/` is real.
- **Zero-vector queries are surfaced as unrecognised input, never silently absorbed into a confident answer.**
- **Seat count is displayed but deliberately never scored** — scarcity must not raise a trip's rank.