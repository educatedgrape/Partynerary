"""City dataset — the read side.

A dataset of global cities carrying keywords, cultural vibes, phrases and
aliases. Vectors are generated offline and shipped as a static asset; this
module is the stdlib-only runtime reader.

Coverage values (atlasCoverage) are derived from Phase 2 probe results,
never editorial.
"""

import json
import pathlib
from dataclasses import dataclass, field


# Coverage constants
REACHABLE = "REACHABLE"
EMPTY = "EMPTY"
UNPROBED = "UNPROBED"

# Coverage must be MEASURED. A dataset that ships anything other than an
# answer from Atlas — reached, or answered-empty — is a build failure.
VALID_COVERAGE = (REACHABLE, EMPTY)

# Default path — project_root/data/cities.json
_DEFAULT_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "cities.json"

# Module-level cache
_cache = {}


@dataclass(frozen=True)
class City:
    """One destination in the catalogue.

    Frozen — the dataset is read-only at runtime. atlasCoverage comes from
    the probe, never from editorial choice.
    """
    city_id: str
    city_name: str
    country: str
    keywords: tuple = ()
    vibes: tuple = ()
    phrases: tuple = ()
    aliases: tuple = ()
    atlas_coverage: str = UNPROBED
    vectors: dict = field(default_factory=dict, compare=False)

    @property
    def reachable(self):
        return self.atlas_coverage == REACHABLE

    def __repr__(self):
        return "City(%s, %s, %s)" % (self.city_id, self.city_name, self.country)


def load(path=None):
    """Load the full city dataset from JSON.

    Returns a list of City objects. Cached after first load.
    """
    if path is None:
        path = _DEFAULT_PATH
    path = pathlib.Path(path)
    key = str(path)

    if key in _cache:
        return _cache[key]

    if not path.is_file():
        raise FileNotFoundError("Dataset not found: %s" % path)

    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    cities = []
    entries = raw if isinstance(raw, list) else raw.get("cities", [])
    for entry in entries:
        city = _parse_city(entry)
        if city.atlas_coverage not in VALID_COVERAGE:
            raise ValueError(
                "%s ships with coverage %r. Coverage must be measured by "
                "the breadth probe — REACHABLE or EMPTY, never %r. "
                "Re-run the probe and regenerate the dataset." % (
                    city.city_id, city.atlas_coverage, UNPROBED))
        cities.append(city)

    _cache[key] = cities
    return cities


def _parse_city(entry):
    """Parse one city entry dict into a City dataclass."""
    return City(
        city_id=entry["cityId"],
        city_name=entry["cityName"],
        country=entry["country"],
        keywords=tuple(entry.get("keywords", [])),
        vibes=tuple(entry.get("vibes", [])),
        phrases=tuple(entry.get("phrases", [])),
        aliases=tuple(entry.get("aliases", [])),
        atlas_coverage=entry.get("atlasCoverage", UNPROBED),
        vectors=entry.get("vectors", {}),
    )


def reachable(path=None):
    """Only cities Atlas has answered for.

    Proposing a city Atlas has never answered for spends a call to render
    a gap. This filter is mandatory for the retrieval pool.
    """
    return [c for c in load(path) if c.reachable]


def by_id(path=None):
    """{cityId: City} lookup dict."""
    return {c.city_id: c for c in load(path)}


def alias_index(path=None):
    """[(alias, cityId)] sorted longest-first.

    Longest-first so 'south korea' beats 'korea' in exact matching.
    No duplicate cityId per alias.
    """
    pairs = []
    seen = set()
    for city in load(path):
        for alias in city.aliases:
            key = (alias.lower(), city.city_id)
            if key not in seen:
                seen.add(key)
                pairs.append((alias.lower(), city.city_id))

    # Sort longest alias first; break ties alphabetically
    pairs.sort(key=lambda p: (-len(p[0]), p[0]))
    return pairs


def clear_cache():
    """Clear the module-level cache. Used by tests."""
    _cache.clear()
