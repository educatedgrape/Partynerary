"""Response cache — the substrate every cost_ref dereferences against.

Every successful Atlas response lands here under its fixture key. The left-hand
side of every cost_ref in the system is one of these keys.
"""

RESPONSE_CACHE = {}


def put(key, data):
    """Store a parsed response under its fixture key."""
    RESPONSE_CACHE[key] = data


def get(key):
    """Return the parsed response, or None if absent."""
    return RESPONSE_CACHE.get(key)


def keys():
    """Return all cached keys."""
    return list(RESPONSE_CACHE.keys())


def clear():
    """Empty the cache. Used by the test suite between cases."""
    RESPONSE_CACHE.clear()
