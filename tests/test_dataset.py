"""Phase 3 — Dataset tests.

Verifies the city dataset structure, loading, filtering, and alias index.
"""

import json
import math
import pathlib
import unittest

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.discovery.dataset import (
    City, load, reachable, by_id, alias_index, clear_cache,
    REACHABLE, EMPTY, UNPROBED,
)


DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
CITIES_PATH = DATA_DIR / "cities.json"
VECTORS_PATH = DATA_DIR / "vectors.json"


class TestDatasetLoad(unittest.TestCase):
    """Dataset loading and structure."""

    def setUp(self):
        clear_cache()

    def test_load_returns_list(self):
        cities = load(CITIES_PATH)
        self.assertIsInstance(cities, list)
        self.assertGreater(len(cities), 10)

    def test_every_city_has_required_fields(self):
        for city in load(CITIES_PATH):
            self.assertIsInstance(city, City)
            self.assertTrue(city.city_id, "%s missing city_id" % city)
            self.assertTrue(city.city_name, "%s missing city_name" % city)
            self.assertTrue(city.country, "%s missing country" % city)

    def test_keywords_are_tuples(self):
        for city in load(CITIES_PATH):
            self.assertIsInstance(city.keywords, tuple,
                                 "%s.keywords should be tuple" % city.city_id)

    def test_phrases_not_empty(self):
        for city in load(CITIES_PATH):
            self.assertGreater(len(city.phrases), 0,
                               "%s has no phrases" % city.city_id)

    def test_aliases_not_empty(self):
        for city in load(CITIES_PATH):
            self.assertGreater(len(city.aliases), 0,
                               "%s has no aliases" % city.city_id)

    def test_coverage_values_valid(self):
        """Coverage is measured: only REACHABLE or EMPTY ever ships."""
        valid = {REACHABLE, EMPTY}
        for city in load(CITIES_PATH):
            self.assertIn(city.atlas_coverage, valid,
                          "%s has invalid coverage: %s" % (
                              city.city_id, city.atlas_coverage))

    def test_zero_unprobed_in_committed_artifact(self):
        """UNPROBED in a shipped artifact means the sweep can spend calls
        on a city Atlas has never answered for. It must never ship."""
        raw = json.loads(CITIES_PATH.read_text(encoding="utf-8"))
        entries = raw["cities"] if isinstance(raw, dict) else raw
        unprobed = [e["cityId"] for e in entries
                    if e.get("atlasCoverage") not in (REACHABLE, EMPTY)]
        self.assertEqual(unprobed, [],
                         "Shipped dataset carries unmeasured cities: %s"
                         % unprobed)

    def test_caching(self):
        """load() returns the same list on second call (cached)."""
        a = load(CITIES_PATH)
        b = load(CITIES_PATH)
        self.assertIs(a, b)


class TestReachable(unittest.TestCase):
    """reachable() filtering."""

    def setUp(self):
        clear_cache()

    def test_reachable_excludes_empty(self):
        all_cities = load(CITIES_PATH)
        reach = reachable(CITIES_PATH)
        for city in reach:
            self.assertNotEqual(city.atlas_coverage, EMPTY,
                                "%s is EMPTY but in reachable()" % city.city_id)

    def test_reachable_excludes_unprobed(self):
        reach = reachable(CITIES_PATH)
        for city in reach:
            self.assertNotEqual(city.atlas_coverage, UNPROBED,
                                "%s is UNPROBED but in reachable()" % city.city_id)

    def test_reachable_only_contains_reachable(self):
        for city in reachable(CITIES_PATH):
            self.assertTrue(city.reachable,
                            "%s not reachable" % city.city_id)

    def test_reachable_is_smaller_than_full(self):
        all_cities = load(CITIES_PATH)
        reach = reachable(CITIES_PATH)
        self.assertLess(len(reach), len(all_cities))

    def test_has_some_reachable(self):
        """At least some cities should be marked REACHABLE."""
        reach = reachable(CITIES_PATH)
        self.assertGreater(len(reach), 5)


class TestById(unittest.TestCase):
    """by_id() lookup."""

    def setUp(self):
        clear_cache()

    def test_lookup_exists(self):
        idx = by_id(CITIES_PATH)
        self.assertIn("DPS", idx)
        self.assertEqual(idx["DPS"].city_name, "Bali")

    def test_lookup_count_matches_load(self):
        cities = load(CITIES_PATH)
        idx = by_id(CITIES_PATH)
        self.assertEqual(len(idx), len(cities))

    def test_no_duplicate_ids(self):
        """Every city_id appears exactly once."""
        cities = load(CITIES_PATH)
        ids = [c.city_id for c in cities]
        self.assertEqual(len(ids), len(set(ids)),
                         "Duplicate city_id(s) in dataset")


class TestAliasIndex(unittest.TestCase):
    """alias_index() — sorted longest-first."""

    def setUp(self):
        clear_cache()

    def test_returns_list_of_pairs(self):
        pairs = alias_index(CITIES_PATH)
        self.assertIsInstance(pairs, list)
        self.assertGreater(len(pairs), 10)
        for alias, city_id in pairs:
            self.assertIsInstance(alias, str)
            self.assertIsInstance(city_id, str)

    def test_sorted_longest_first(self):
        pairs = alias_index(CITIES_PATH)
        lengths = [len(alias) for alias, _ in pairs]
        for i in range(len(lengths) - 1):
            self.assertGreaterEqual(lengths[i], lengths[i + 1],
                                    "alias_index not sorted longest-first at index %d" % i)

    def test_south_korea_beats_korea(self):
        """'south korea' must appear before 'korea' in the index."""
        pairs = alias_index(CITIES_PATH)
        aliases = [a for a, _ in pairs]
        if "south korea" in aliases and "korea" in aliases:
            self.assertLess(
                aliases.index("south korea"),
                aliases.index("korea"),
                "'south korea' should come before 'korea'")

    def test_no_duplicate_pairs(self):
        pairs = alias_index(CITIES_PATH)
        self.assertEqual(len(pairs), len(set(pairs)))


class TestEmptyCoverage(unittest.TestCase):
    """EMPTY codes are explicitly present in the dataset."""

    def setUp(self):
        clear_cache()

    def test_empty_cities_exist(self):
        """At least one city is marked EMPTY."""
        all_cities = load(CITIES_PATH)
        empty = [c for c in all_cities if c.atlas_coverage == EMPTY]
        self.assertGreater(len(empty), 0,
                           "No EMPTY cities — the probe should record them")

    def test_empty_not_in_reachable(self):
        reach = reachable(CITIES_PATH)
        reach_ids = {c.city_id for c in reach}
        all_cities = load(CITIES_PATH)
        for city in all_cities:
            if city.atlas_coverage == EMPTY:
                self.assertNotIn(city.city_id, reach_ids,
                                 "%s is EMPTY but in reachable()" % city.city_id)


class TestUnprobedFailsToLoad(unittest.TestCase):
    """A dataset carrying UNPROBED refuses to load — fail loudly."""

    def setUp(self):
        clear_cache()

    def _write_tmp(self, coverage_value):
        import tempfile
        entries = [{
            "cityId": "TST", "cityName": "Testville", "country": "Testland",
            "keywords": ["test"], "vibes": ["test"],
            "phrases": ["a city for testing"], "aliases": ["testville"],
            "atlasCoverage": coverage_value,
        }]
        fd, path = tempfile.mkstemp(suffix=".json")
        import os
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"cities": entries}, f)
        return pathlib.Path(path)

    def test_unprobed_raises(self):
        path = self._write_tmp(UNPROBED)
        try:
            with self.assertRaises(ValueError):
                load(path)
        finally:
            path.unlink()
            clear_cache()

    def test_missing_coverage_raises(self):
        import tempfile, os
        entries = [{"cityId": "TST", "cityName": "Testville",
                    "country": "Testland", "phrases": ["p"], "aliases": ["a"]}]
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"cities": entries}, f)
        try:
            with self.assertRaises(ValueError):
                load(pathlib.Path(path))
        finally:
            pathlib.Path(path).unlink()
            clear_cache()

    def test_reachable_and_empty_load_fine(self):
        for cov in (REACHABLE, EMPTY):
            path = self._write_tmp(cov)
            try:
                cities = load(path)
                self.assertEqual(cities[0].atlas_coverage, cov)
            finally:
                path.unlink()
                clear_cache()


if __name__ == "__main__":
    unittest.main()
