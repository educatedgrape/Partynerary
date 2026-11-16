"""Dereference a cost_ref into a real number that Atlas actually returned.

    <cache-key>#<dotted.path>[index].<field>
    search.do:KUL-DPS#routings[1].adultPrice

This module is the load-bearing half of the anti-hallucination guarantee.
The planner emits a POINTER; only this resolver turns a pointer into money,
and it can only do so if the pointed-at response is in the cache — i.e. if
Atlas really said it, during this run.
"""

import re

from src.atlas import cache as response_cache


class CostRefError(Exception):
    """A cost_ref could not be resolved — cache miss, bad path, or non-number."""


# Pre-compiled: splits the fragment on '.', preserving bracket indices.
_PATH_RE = re.compile(r"\.")
_INDEX_RE = re.compile(r"^(\w+)\[(\d+)\]$")


def _resolve_path(data, fragment):
    """Walk a dotted path into a dict/list structure.

    Handles dotted keys (routings.0.adultPrice) and bracket notation
    (routings[0].adultPrice). Raises CostRefError on any navigation failure.
    """
    current = data
    for part in _PATH_RE.split(fragment):
        m = _INDEX_RE.match(part)
        if m:
            key, idx = m.group(1), int(m.group(2))
            if not isinstance(current, dict):
                raise CostRefError(
                    "Expected dict at '%s', got %s" % (part, type(current).__name__))
            if key not in current:
                raise CostRefError("Key '%s' not found in response" % key)
            current = current[key]
            if not isinstance(current, (list, tuple)):
                raise CostRefError(
                    "Expected list at '%s', got %s" % (key, type(current).__name__))
            if idx >= len(current):
                raise CostRefError(
                    "Index [%d] out of range for '%s' (length %d)" % (
                        idx, key, len(current)))
            current = current[idx]
        elif part.isdigit() and isinstance(current, (list, tuple)):
            idx = int(part)
            if idx >= len(current):
                raise CostRefError(
                    "Index [%d] out of range (length %d)" % (idx, len(current)))
            current = current[idx]
        else:
            if not isinstance(current, dict):
                raise CostRefError(
                    "Expected dict at '%s', got %s" % (part, type(current).__name__))
            if part not in current:
                raise CostRefError("Key '%s' not found in response" % part)
            current = current[part]
    return current


def resolve(ref, cache=None):
    """The float the ref points at, or raise. NEVER returns a default.

    cache defaults to the module-level RESPONSE_CACHE.
    """
    if cache is None:
        cache = response_cache.RESPONSE_CACHE

    if "#" not in ref:
        raise CostRefError("Malformed cost_ref (no '#'): %r" % ref)

    cache_key, fragment = ref.split("#", 1)
    data = cache.get(cache_key)
    if data is None:
        raise CostRefError(
            "Cache key %r not found. Known keys: %s" % (
                cache_key, list(cache.keys())))

    value = _resolve_path(data, fragment)

    # A bool is not a number — True/False must not silently become 1.0/0.0.
    if isinstance(value, bool):
        raise CostRefError(
            "cost_ref resolved to a boolean (%s), not a number" % value)

    if not isinstance(value, (int, float)):
        raise CostRefError(
            "cost_ref resolved to %s (%s), not a number" % (
                type(value).__name__, value))

    return float(value)


def sibling(ref, field):
    """Same routing, different leaf: '...adultPrice' -> '...adultTax'.

    Structural, not guessed. Walks WITHIN one routing — it cannot reach
    another response, and must not pretend to. The return leg lives under
    a different cache key and is structurally unreachable from here.
    """
    if "#" not in ref:
        raise CostRefError("Malformed cost_ref (no '#'): %r" % ref)
    cache_key, fragment = ref.split("#", 1)
    # Replace the leaf field (everything after the last '.')
    parts = fragment.rsplit(".", 1)
    if len(parts) < 2:
        raise CostRefError("Cannot find sibling of %r — no dotted path" % ref)
    return "%s#%s.%s" % (cache_key, parts[0], field)


def resolve_group_total(price_ref, adults, cache=None):
    """(adultPrice + adultTax) * adults + transactionFee, for ONE leg.

    Returns (total, refs_used). Every input is dereferenced; the arithmetic
    is the documented one. Nothing here is model-authored, which is why the
    executor is allowed to reserve against it.

    A leg's siblings do not reach another leg — the return leg lives under
    a different cache key and is structurally unreachable from the outbound
    ref. That is why a proposal carries one ref per leg, not one ref and
    a derivation.
    """
    price = resolve(price_ref, cache)
    tax = resolve(sibling(price_ref, "adultTax"), cache)
    fee = resolve(sibling(price_ref, "transactionFee"), cache)

    total = round((price + tax) * adults + fee, 2)
    refs_used = [
        price_ref,
        sibling(price_ref, "adultTax"),
        sibling(price_ref, "transactionFee"),
    ]
    return total, refs_used
