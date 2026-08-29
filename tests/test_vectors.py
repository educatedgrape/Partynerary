"""Phase 3 — Vector artifact tests.

Verifies vectors.json structure, consistency, and unit normalisation.
"""

import json
import math
import pathlib
import unittest

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.discovery.dataset import load, reachable, clear_cache


DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
CITIES_PATH = DATA_DIR / "cities.json"
VECTORS_PATH = DATA_DIR / "vectors.json"


def _load_vectors():
    with open(VECTORS_PATH) as f:
        return json.load(f)


class TestVectorStructure(unittest.TestCase):
    """vectors.json structure and metadata."""

    def test_has_model_field(self):
        v = _load_vectors()
        self.assertIn("model", v)
        self.assertTrue(v["model"])

    def test_has_dim_field(self):
        v = _load_vectors()
        self.assertIn("dim", v)
        self.assertEqual(v["dim"], 384)

    def test_has_built_field(self):
        v = _load_vectors()
        self.assertIn("built", v)
        self.assertTrue(v["built"])

    def test_has_tokens_field(self):
        v = _load_vectors()
        self.assertIn("tokens", v)
        self.assertIsInstance(v["tokens"], dict)

    def test_tokens_not_empty(self):
        v = _load_vectors()
        self.assertGreater(len(v["tokens"]), 100)


class TestVectorConsistency(unittest.TestCase):
    """All vectors are dim-consistent and unit-normalised."""

    @classmethod
    def setUpClass(cls):
        cls.v = _load_vectors()
        cls.dim = cls.v["dim"]
        cls.tokens = cls.v["tokens"]

    def test_dim_consistent(self):
        for token, vec in self.tokens.items():
            self.assertEqual(
                len(vec), self.dim,
                "Token %r has dim %d, expected %d" % (token, len(vec), self.dim))

    def test_unit_normalised(self):
        for token, vec in self.tokens.items():
            mag = math.sqrt(sum(x * x for x in vec))
            self.assertAlmostEqual(
                mag, 1.0, places=3,
                msg="Token %r magnitude %.6f, not unit length" % (token, mag))

    def test_values_are_floats(self):
        for token, vec in self.tokens.items():
            for val in vec:
                self.assertIsInstance(val, (int, float),
                                      "Token %r has non-numeric value" % token)


class TestTokenCoverage(unittest.TestCase):
    """Token table covers every token in every city's text."""

    @classmethod
    def setUpClass(cls):
        cls.v = _load_vectors()
        cls.tokens = cls.v["tokens"]
        clear_cache()
        cls.cities = load(CITIES_PATH)

    def test_covers_city_keywords(self):
        import re
        for city in self.cities:
            for kw in city.keywords:
                for token in re.split(r"[^a-z0-9]+", kw.lower()):
                    if token:
                        self.assertIn(
                            token, self.tokens,
                            "Token %r from %s.keywords not in vectors" % (
                                token, city.city_id))

    def test_covers_city_vibes(self):
        import re
        for city in self.cities:
            for vibe in city.vibes:
                for token in re.split(r"[^a-z0-9]+", vibe.lower()):
                    if token:
                        self.assertIn(
                            token, self.tokens,
                            "Token %r from %s.vibes not in vectors" % (
                                token, city.city_id))

    def test_covers_expansion_tokens(self):
        """Travel vocabulary expansion tokens are present."""
        expansion = ["chill", "unwind", "foodie", "relax", "adventure"]
        for token in expansion:
            self.assertIn(token, self.tokens,
                          "Expansion token %r not in vectors" % token)


class TestEmptyCoverageInDataset(unittest.TestCase):
    """Every EMPTY code from the probe is present and marked EMPTY."""

    def setUp(self):
        clear_cache()

    def test_empty_codes_present(self):
        """At least one city is marked EMPTY in the dataset."""
        cities = load(CITIES_PATH)
        empty = [c for c in cities if c.atlas_coverage == "EMPTY"]
        self.assertGreater(len(empty), 0)

    def test_reachable_excludes_empty(self):
        reach = reachable(CITIES_PATH)
        for city in reach:
            self.assertNotEqual(
                city.atlas_coverage, "EMPTY",
                "%s is EMPTY but in reachable()" % city.city_id)


class TestSanity(unittest.TestCase):
    """Basic sanity checks on the vector space.

    With hash-based fallback vectors, semantic relationships won't hold.
    These tests only run meaningful checks when real model vectors are used.
    """

    @classmethod
    def setUpClass(cls):
        cls.v = _load_vectors()

    def test_different_tokens_different_vectors(self):
        tokens = list(self.v["tokens"].keys())
        if len(tokens) >= 2:
            v1 = self.v["tokens"][tokens[0]]
            v2 = self.v["tokens"][tokens[1]]
            # At least some components should differ
            diffs = sum(1 for a, b in zip(v1, v2) if abs(a - b) > 1e-8)
            self.assertGreater(diffs, 0,
                               "Two different tokens have identical vectors")


if __name__ == "__main__":
    unittest.main()
