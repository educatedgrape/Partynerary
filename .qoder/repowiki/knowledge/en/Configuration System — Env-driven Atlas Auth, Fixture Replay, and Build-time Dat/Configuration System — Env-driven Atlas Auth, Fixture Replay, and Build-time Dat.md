---
kind: configuration_system
name: Configuration System — Env-driven Atlas Auth, Fixture Replay, and Build-time Data Artifacts
category: configuration_system
scope:
    - '**'
source_files:
    - build-guide.md
    - project_scope.md
    - best-practices.md
    - tools/generate_dataset.py
    - tools/build_vectors.py
    - tools/requirements-build.txt
---

## What system/approach is used

The repository does not ship a dedicated configuration library. Configuration is handled through three lightweight mechanisms that are documented in the build guide and scope spec:

1. **Environment variables** for runtime switches and credentials.
2. **Fixture files on disk** (`fixtures/live/`, `fixtures/test/`) that replace live network calls when replaying or testing.
3. **Pre-generated data artifacts** under `data/` (cities dataset and vector embeddings) produced at build time by tools in `tools/` and consumed as static JSON by the runtime.

There is no `.env` file committed to the repo; the docs describe reading auth headers from `.env`. There is no YAML/TOML/JSON config file loaded at startup — the only structured data the application consumes at runtime is the committed `data/cities.json` and `data/vectors.json` artifacts.

## Key files and packages

- `build-guide.md` — documents every configuration surface: where `LIVE`, `x-atlas-client-id`, `x-atlas-client-secret` come from, how fixture paths are chosen, and how the build-time toolchain reads/writes `data/`.
- `project_scope.md` — restates the same configuration rules (Atlas client env vars, fixture key format, replay mode) as part of the architectural contract.
- `best-practices.md` — reinforces the rule that the runtime must be stdlib-only and that any external dependency belongs under `tools/` and runs at build time only.
- `tools/generate_dataset.py`, `tools/build_vectors.py`, `tools/requirements-build.txt` — the build-time configuration pipeline that turns findings into `data/cities.json` and `data/vectors.json`.
- `src/atlas/client.py` (referenced throughout the docs) — the single place that reads `LIVE`, the AK/SK headers, and toggles between live POST and fixture replay.
- `src/discovery/dataset.py` (referenced) — loads `data/cities.json` and `data/vectors.json` at runtime.

## Architecture and conventions

### Runtime environment variables

| Variable | Purpose | Where enforced | Enforced by |
|---|---|---|---|
| `LIVE=1` | Allows real Atlas calls; otherwise raises `LiveCallBlocked` | `AtlasClient` | Test asserting no live call without it | 
| `x-atlas-client-id` / `x-atlas-client-secret` | Atlas API keys read from `.env` | `AtlasClient.__init__` | "read from `.env`, never written at a call site" |
| `replay=True` (constructor flag) | Reads `fixtures/…/<KEY>.json` instead of POSTing | `AtlasClient` | Same transport path downstream |

The live gate is the hard boundary: without `LIVE=1`, the transport layer refuses to open a socket. This makes the entire codebase runnable offline once fixtures exist.

### Fixture-based replay

Two fixture directories serve different purposes and are kept separate so they cannot disturb each other:

- `fixtures/live/` — auto-captured responses from a live run, keyed by the canonical fixture key format `search.do:<ORIGIN>-<DESTINATION>@<DATE>`. Used for deterministic replay of an actual session.
- `fixtures/test/` — small frozen set of responses chosen for stability (not breadth), used exclusively by the test suite. A grep in Phase 2's gate forbids hand-written destination lists that would turn this into a curated candidate universe.

Every response is cached in-memory under its fixture key after a successful load, and `cost_ref.resolve()` dereferences figures only against that cache. A ref pointing at a response not received this run raises rather than returning a default.

### Build-time data artifacts

The runtime is stdlib-only. All non-stdlib work is fenced into `tools/` and executed before deployment:

- `tools/generate_dataset.py` reads probe findings and emits `data/cities.json`.
- `tools/build_vectors.py` installs `sentence-transformers==3.*` (pinned) from `tools/requirements-build.txt`, embeds city text with `all-MiniLM-L6-v2` (dim 384), and writes `data/vectors.json` containing model id, dimension, build date, and token vectors.
- The runtime imports nothing from `tools/`; it only reads the committed JSON artifacts.

Changing the embedding model requires regenerating both artifacts because `dim` propagates into every stored vector.

### Conventions and constraints

Observed conventions (descriptive):

- **One definition per key.** The fixture key format lives in one place; every module that searches builds its key there. Inventing a variant produces a cache miss that surfaces as empty results rather than an error.
- **No hand-authored destination lists anywhere except tests.** A grep gate in Phase 2 rejects any source containing `PLACES = [`, because a curated capture list becomes a hidden decision layer.
- **Runtime vs build-time separation is strict.** Anything that needs a third-party package goes under `tools/` and runs at build time; `src/` contains only stdlib Python.
- **Credentials are never embedded.** Auth headers are read from `.env` at process start inside the transport layer; no call site constructs them.
- **Replay swaps exactly one thing.** `AtlasClient(replay=True)` replaces the network call with filesystem reads; everything downstream (parsing, caching, `cost_ref` resolution) stays identical.
- **Test fixtures are frozen once and never regenerated.** They represent stable assertions, not a catalogue.

Enforced rules (with enforcement source):

- No live Atlas call unless `LIVE=1` — enforced by a test that asserts `LiveCallBlocked` is raised when the variable is unset.
- No `amount` field in negotiation messages — enforced by schema validation in `src/party/protocol.py` (`ALLOWED_KEYS`).
- No hand-written destination capture lists outside tests — enforced by a `grep -rn "PLACES = \[" probe/ src/` gate in Phase 2.
- Every figure rendered must resolve from a `cost_ref` against the current run's cache — enforced by `cost_ref.resolve()` raising when the pointed-at response was not received this run.
- Runtime must be stdlib-only — enforced by best practices and by keeping all non-stdlib dependencies under `tools/requirements-build.txt` for build-time use only.
- `Ceiling` has no spend/charge/debit method — enforced by a test pinning the object's interface.
- Re-price runs after user choice and before order — enforced as a gate in `src/agent/executor.py` and `src/booking/reprice.py`.