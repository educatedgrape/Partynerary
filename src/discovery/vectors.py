"""Vector arithmetic in stdlib. No numpy, no model, no network.

Vectors are unit-normalised at build time, so cosine similarity is a dot product
and nothing here computes a magnitude.
"""

import json
import math
import pathlib
import re


# Default path — project_root/data/vectors.json
_DEFAULT_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "vectors.json"

# The artifact must carry real embeddings. A placeholder artifact once
# shipped under the name "hash-fallback" and every retrieval score was noise;
# refuse to run on anything else.
REQUIRED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Module-level cache
_cache = {}


def load_tokens(path=None):
    """({token: vector}, dim). Cached after first read.

    Asserts at load time that the artifact was built with the real embedding
    model — a wrong or missing model field is a build failure, not a warning.
    """
    if path is None:
        path = _DEFAULT_PATH
    path = pathlib.Path(path)
    key = str(path)

    if key in _cache:
        return _cache[key]

    if not path.is_file():
        raise FileNotFoundError("Vectors not found: %s" % path)

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    model = data.get("model")
    if model != REQUIRED_MODEL:
        raise ValueError(
            "vectors.json carries model %r, expected %r. Regenerate with "
            "tools/build_vectors.py inside the build-time virtualenv — the "
            "runtime refuses placeholder embeddings." % (model, REQUIRED_MODEL))

    tokens = data.get("tokens", {})
    dim = data.get("dim", 0)
    result = (tokens, dim)
    _cache[key] = result
    return result


def cosine(a, b):
    """Dot product of two unit-normalised vectors = cosine similarity."""
    return sum(x * y for x, y in zip(a, b))


def embed(text, tokens, dim):
    """Embed free text by token lookup and mean pooling, then normalise.

    Returns a ZERO VECTOR when no token is known. The caller MUST treat that as
    "I could not place this", never as a neutral query: a zero vector cosines to
    0.0 against everything, so every city would score identically and the board
    would fall through to fare order while looking like it had understood.
    """
    if not text or not text.strip():
        return [0.0] * dim

    words = [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]
    matched = [tokens[w] for w in words if w in tokens]

    if not matched:
        return [0.0] * dim

    # Mean pool
    pooled = [0.0] * dim
    for vec in matched:
        for i, v in enumerate(vec):
            pooled[i] += v
    n = len(matched)
    pooled = [v / n for v in pooled]

    # Normalise to unit length
    mag = math.sqrt(sum(v * v for v in pooled))
    if mag > 0:
        pooled = [v / mag for v in pooled]

    return pooled


def is_zero(vec):
    """True when every component is zero — embed() returned no match."""
    return all(v == 0.0 for v in vec)


def max_pool(query, candidates):
    """Best cosine from a query vector against a list of candidate vectors.

    Returns the maximum similarity across all candidate vectors. Returns 0.0
    when the candidate list is empty or the query is zero.
    """
    if not candidates or is_zero(query):
        return 0.0
    return max(cosine(query, c) for c in candidates)


def clear_cache():
    """Clear the module-level cache. Used by tests."""
    _cache.clear()
