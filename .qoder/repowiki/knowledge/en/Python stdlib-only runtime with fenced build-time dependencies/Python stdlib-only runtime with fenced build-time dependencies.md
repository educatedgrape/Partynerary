---
kind: dependency_management
name: Python stdlib-only runtime with fenced build-time dependencies
category: dependency_management
scope:
    - '**'
source_files:
    - build-guide.md
    - project_scope.md
    - best-practices.md
    - tools/requirements-build.txt
---

## What system/approach is used

The Partynerary project enforces a **stdlib-only runtime** for the application code under `src/` and `tests/`. No third-party Python packages are imported at runtime — network calls go through `urllib`, vector arithmetic is hand-written, and there is no `requirements.txt`, `pyproject.toml`, `go.mod`, or `package.json` in the repository root. The only declared dependency file is `tools/requirements-build.txt`, which pins `sentence-transformers==3.*` exclusively for offline dataset/vector generation.

Build-time tooling is explicitly fenced into the `tools/` directory: `tools/generate_dataset.py` and `tools/build_vectors.py` install from `requirements-build.txt` to produce `data/cities.json` and `data/vectors.json`, which are then committed as frozen artifacts. The runtime reads these artifacts and never imports anything from `tools/`.

The React 18 + Vite frontend (referenced as `web/dist`) is not present in this snapshot of the repo; the visible codebase is purely Python documentation and spec files.

## Key files and packages

- `build-guide.md` — declares the stdlib-only rule and fences all non-stdlib dependencies into `tools/`; documents `tools/requirements-build.txt` as the single dependency manifest.
- `project_scope.md` — reiterates the no-`pip install` constraint for runtime and describes embedding generation as an offline build step that produces committed artifacts.
- `best-practices.md` — reinforces keeping models out of the decision path and preferring deterministic, model-free core logic.
- `tools/requirements-build.txt` — the only dependency declaration in the repo, pinning `sentence-transformers==3.*` with the explicit comment "BUILD TIME ONLY. Never imported from src/. Installed only when regenerating data/vectors.json".
- `data/cities.json` and `data/vectors.json` — committed artifacts produced by the build tools; they carry provenance metadata (`model`, `dim`, `built`) so downstream consumers know what generated them.

## Architecture and conventions

1. **Runtime isolation**: Code under `src/` must import only Python standard library modules. This is enforced by design rather than by a linter in this snapshot — the convention is documented repeatedly and tested via assertions that certain modules do not import forbidden libraries.
2. **Build-time exception**: Vector embedding is the one deliberate exception to the no-dependency rule. It is isolated in `tools/`, requires manual installation of `requirements-build.txt`, and produces static JSON artifacts that are checked into version control.
3. **Artifact immutability**: Once `data/vectors.json` is built, it is treated as immutable input to the runtime. Changing the embedding model means regenerating the artifact and committing the new file — there is no runtime download of models.
4. **No lockfile**: There is no `Pipfile.lock`, `poetry.lock`, or equivalent. The build dependency is pinned loosely (`sentence-transformers==3.*`) rather than locked to a specific patch version.
5. **No vendoring**: No `vendor/` directory or vendored packages are present. Dependencies are installed on-demand into the active environment.
6. **Private registry / GOPRIVATE**: Not applicable — there is no Go module graph and no private registry configuration in this snapshot.
7. **Dependency update policy**: The build dependency is pinned to a major-version range (`3.*`). Updating means changing the wildcard to a newer major and regenerating the committed artifacts; the runtime itself has no dependency surface to update.

## Conventions and constraints

- **Rule (documented)**: Runtime is stdlib-only. Build-time tooling is fenced into `tools/`. (Source: `build-guide.md`, Phase rules)
- **Rule (documented)**: `tools/requirements-build.txt` is build-time only — never imported from `src/`, installed only when regenerating `data/vectors.json`. (Source: `build-guide.md`, lines 406–410)
- **Rule (documented)**: Embedding model is pinned (`sentence-transformers==3.*`) because the `dim` propagates into every vector in the committed artifact; changing the model requires regenerating the whole dataset. (Source: `build-guide.md`, lines 412–419)
- **Rule (documented)**: Every figure rendered anywhere in the product is a pointer into an Atlas response (`cost_ref`), dereferenced at render time — no numbers authored by the model. (Source: `project_scope.md`, executive summary)
- **Convention**: Third-party dependencies are avoided entirely in production code; when unavoidable, they are confined to build-time scripts and their outputs are committed as static data.
- **Constraint observed**: No package manager lockfile exists; dependency versions are expressed as loose major-version wildcards in a single text file.