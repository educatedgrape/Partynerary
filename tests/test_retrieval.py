"""Phase 4 — Retrieval engine tests.

Verifies free-text → ranked city queries: fusion, tiering, negation,
alias resolution, and the no-price guarantee.
"""

import pathlib
import unittest

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.discovery import dataset, vectors
from src.discovery.retrieval import (
    Match, clauses, places_named, unrecognised, score_city,
    shortlist, group_vibe, describe, MIN_SIMILARITY, WEIGHTS,
)


DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
CITIES_PATH = DATA_DIR / "cities.json"
VECTORS_PATH = DATA_DIR / "vectors.json"


class TestMatchNoPrice(unittest.TestCase):
    """Match.as_dict() carries NO price/fare/amount field."""

    FORBIDDEN = ("price", "fare", "amount", "cost", "total", "saving")

    def test_as_dict_has_no_price_fields(self):
        m = Match(
            city_id="DPS", city_name="Bali", country="Indonesia",
            vibe_score=0.8, dense=0.7, sparse=0.5,
            named=0, matched=("beach", "temple"),
        )
        d = m.as_dict()
        for key in self.FORBIDDEN:
            self.assertNotIn(key, d,
                               "Match.as_dict() must not contain %r" % key)

    def test_as_dict_has_required_fields(self):
        m = Match(
            city_id="BKK", city_name="Bangkok", country="Thailand",
            vibe_score=0.9, dense=0.8, sparse=0.6,
            named=1, matched=("street food",),
        )
        d = m.as_dict()
        for key in ("cityId", "cityName", "country", "vibeScore", "why"):
            self.assertIn(key, d,
                           "Match.as_dict() must contain %r" % key)

    def test_why_is_string(self):
        m = Match(
            city_id="ICN", city_name="Seoul", country="South Korea",
            vibe_score=1.0, dense=0.7, sparse=0.5,
            named=1, matched=("kimchi",),
        )
        self.assertIsInstance(m.why, str)
        self.assertTrue(len(m.why) > 0)


class TestShortlistedReachable(unittest.TestCase):
    """Every shortlisted city is REACHABLE."""

    def setUp(self):
        dataset.clear_cache()
        vectors.clear_cache()

    def test_all_shortlisted_are_reachable(self):
        results = shortlist(["beach and relax"])
        reach_ids = {c.city_id for c in dataset.reachable(CITIES_PATH)}
        for m in results:
            self.assertIn(
                m.city_id, reach_ids,
                "%s shortlisted but not REACHABLE" % m.city_id)

    def test_empty_cities_never_shortlisted(self):
        results = shortlist(["bollywood and chai"])
        result_ids = {m.city_id for m in results}
        empty = [c for c in dataset.load(CITIES_PATH)
                 if c.atlas_coverage == dataset.EMPTY]
        for city in empty:
            self.assertNotIn(
                city.city_id, result_ids,
                "EMPTY city %s should not be shortlisted" % city.city_id)


class TestUnrecognised(unittest.TestCase):
    """A zero-vector query reports through unrecognised()."""

    def setUp(self):
        dataset.clear_cache()
        vectors.clear_cache()

    def test_gibberish_is_unrecognised(self):
        """Tokens nobody has heard of surface as unrecognised."""
        result = unrecognised("xyzzymumble blargleflonk")
        self.assertGreater(len(result), 0,
                           "Gibberish tokens should be reported as unrecognised")

    def test_known_words_not_unrecognised(self):
        """Known city keywords are not flagged as unrecognised."""
        result = unrecognised("beach and surfing")
        self.assertNotIn("beach", result)
        self.assertNotIn("surfing", result)

    def test_empty_text_returns_empty(self):
        result = unrecognised("")
        self.assertEqual(result, [])


class TestClauseNegation(unittest.TestCase):
    """great street food but no nightlife — one positive, one negated."""

    def setUp(self):
        dataset.clear_cache()
        vectors.clear_cache()

    def test_positive_and_negated_clauses(self):
        cls = clauses("great street food but no nightlife")
        # Should have at least 2 clauses
        self.assertGreaterEqual(len(cls), 2,
                                 "Expected at least 2 clauses")
        negated = [c for c in cls if c.negated]
        positive = [c for c in cls if not c.negated]
        self.assertGreater(len(negated), 0,
                           "Expected at least one negated clause")
        self.assertGreater(len(positive), 0,
                           "Expected at least one positive clause")

    def test_negated_clause_contains_nightlife(self):
        cls = clauses("great street food but no nightlife")
        negated = [c for c in cls if c.negated]
        texts = " ".join(c.text for c in negated)
        self.assertIn("nightlife", texts,
                      "The negated clause should mention nightlife")


class TestAnywhereButBangkok(unittest.TestCase):
    """'anywhere but bangkok' — Bangkok present, scored 0.0, not absent."""

    def setUp(self):
        dataset.clear_cache()
        vectors.clear_cache()

    def test_bangkok_present_but_zeroed(self):
        results = shortlist(["anywhere but bangkok"])
        bkk = [m for m in results if m.city_id == "BKK"]
        self.assertEqual(len(bkk), 1,
                         "Bangkok should be in the shortlist (scored 0.0)")
        self.assertEqual(bkk[0].vibe_score, 0.0,
                         "Bangkok vibe_score should be 0.0 when ruled out")
        self.assertEqual(bkk[0].named, -1,
                         "Bangkok should be marked as named=-1 (ruled out)")

    def test_bangkok_not_first(self):
        """Bangkok should not be first when ruled out."""
        results = shortlist(["anywhere but bangkok"])
        if len(results) > 1:
            self.assertNotEqual(results[0].city_id, "BKK",
                                "Ruled-out Bangkok should not be first")

    def test_places_named_detects_bangkok_negation(self):
        named = places_named("anywhere but bangkok")
        self.assertIn("BKK", named)
        self.assertEqual(named["BKK"], -1,
                         "Bangkok should be negatively named")


class TestNamedOutranksSimilarity(unittest.TestCase):
    """A named city outranks a higher-similarity unnamed one."""

    def setUp(self):
        dataset.clear_cache()
        vectors.clear_cache()

    def test_named_city_is_first(self):
        """Naming Seoul explicitly puts it at the top."""
        results = shortlist(["I want to visit seoul and maybe some beach"])
        if results:
            self.assertEqual(results[0].city_id, "ICN",
                             "Named Seoul should be first in the shortlist")
            self.assertEqual(results[0].named, 1)


class TestBeachAndRelax(unittest.TestCase):
    """'beach and relax' surfaces tropical-beach cities — semantic matching."""

    def setUp(self):
        dataset.clear_cache()
        vectors.clear_cache()

    def test_beach_cities_surface(self):
        results = shortlist(["beach and relax"])
        ids = [m.city_id for m in results]

        # At least some beach cities should appear
        beach_cities = {"DPS", "HKT", "CEB", "PER", "CMB"}
        found = beach_cities & set(ids)
        self.assertGreater(len(found), 0,
                           "Expected at least one beach city in results for "
                           "'beach and relax'. Got: %s" % ids)


class TestKimchiAndKpop(unittest.TestCase):
    """'kimchi and kpop' surfaces Korean cities with why == 'you named it'."""

    def setUp(self):
        dataset.clear_cache()
        vectors.clear_cache()

    def test_korean_cities_surface(self):
        results = shortlist(["kimchi and kpop"])
        ids = [m.city_id for m in results]

        # Seoul and/or Busan should be present
        korean = {"ICN", "PUS"}
        found = korean & set(ids)
        self.assertGreater(len(found), 0,
                           "Expected Korean cities for 'kimchi and kpop'. "
                           "Got: %s" % ids)

    def test_korean_cities_named(self):
        """kimchi and kpop are aliases for Seoul (ICN) — named, not inferred."""
        results = shortlist(["kimchi and kpop"])
        seoul = [m for m in results if m.city_id == "ICN"]
        self.assertEqual(len(seoul), 1, "Seoul should be in shortlist")
        self.assertEqual(seoul[0].named, 1,
                         "ICN should be named (kimchi/kpop are aliases)")
        self.assertIn("named", seoul[0].why.lower(),
                      "ICN.why should say 'you named it'")


class TestShortlistLimit(unittest.TestCase):
    """shortlist(limit=5) returns at most 5."""

    def setUp(self):
        dataset.clear_cache()
        vectors.clear_cache()

    def test_limit_respected(self):
        results = shortlist(["food and culture"], limit=5)
        self.assertLessEqual(len(results), 5)

    def test_limit_one(self):
        results = shortlist(["beach"], limit=1)
        self.assertLessEqual(len(results), 1)

    def test_empty_members_returns_empty(self):
        results = shortlist([])
        self.assertEqual(results, [])


class TestClauses(unittest.TestCase):
    """Clause splitting and embedding."""

    def setUp(self):
        vectors.clear_cache()

    def test_basic_split(self):
        cls = clauses("beach, temples, food")
        self.assertGreaterEqual(len(cls), 2,
                                 "Expected comma-separated clauses")

    def test_all_clauses_have_vectors(self):
        cls = clauses("beach and surfing")
        for c in cls:
            self.assertIsInstance(c.vector, list)
            self.assertGreater(len(c.vector), 0)

    def test_empty_text(self):
        cls = clauses("")
        # Empty text produces no clauses
        self.assertEqual(len(cls), 0)


class TestPlacesNamed(unittest.TestCase):
    """Entity resolution by alias."""

    def setUp(self):
        dataset.clear_cache()

    def test_city_name_resolved(self):
        named = places_named("I want to go to bangkok")
        self.assertIn("BKK", named)
        self.assertEqual(named["BKK"], 1)

    def test_alias_resolved(self):
        named = places_named("love thai food")
        self.assertIn("BKK", named)

    def test_country_resolved(self):
        named = places_named("visit japan")
        # Should resolve to at least one Japanese city
        japanese = {"NRT", "KIX", "FUK"}
        found = japanese & set(named.keys())
        self.assertGreater(len(found), 0,
                           "Expected Japanese city for 'japan'")

    def test_no_match_empty(self):
        named = places_named("zyxwvuts")
        self.assertEqual(len(named), 0)


class TestScoreCity(unittest.TestCase):
    """score_city returns (dense, sparse, matched) tuple."""

    def setUp(self):
        dataset.clear_cache()
        vectors.clear_cache()

    def test_returns_tuple(self):
        cities = dataset.load(CITIES_PATH)
        dps = [c for c in cities if c.city_id == "DPS"][0]
        cls = clauses("beach and surfing")
        result = score_city(dps, cls)
        self.assertEqual(len(result), 3)
        dense, sparse, matched = result
        self.assertIsInstance(dense, float)
        self.assertIsInstance(sparse, float)
        self.assertIsInstance(matched, tuple)


class TestGroupVibeAndDescribe(unittest.TestCase):
    """group_vibe and describe utilities."""

    def setUp(self):
        dataset.clear_cache()
        vectors.clear_cache()

    def test_group_vibe_returns_list(self):
        vibes = group_vibe(["beach and surfing", "street food and temples"])
        self.assertIsInstance(vibes, list)
        self.assertGreater(len(vibes), 0)

    def test_describe_returns_string(self):
        desc = describe(["beach and relax", "good food"])
        self.assertIsInstance(desc, str)
        self.assertGreater(len(desc), 0)

    def test_empty_members(self):
        vibes = group_vibe([])
        self.assertEqual(vibes, [])
        desc = describe([])
        self.assertEqual(desc, "no preferences stated")


class TestRankingDoesNotSaturate(unittest.TestCase):
    """Phase 5 proof: four conflicting members -> a board that still ranks.

    The old scoring summed per-clause contributions and overwrote named
    cities to 1.0, so the top of the board pinned at 1.0/1.0/1.0 with
    nothing left to order by. With mean normalisation the top 10 must be
    DISTINCT and strictly decreasing, and unnamed cities whose fused score
    sits below MIN_SIMILARITY must be dropped, not ranked as noise.
    """

    MEMBERS = [
        "beach and surfing and island hopping",
        "kpop and kimchi and night markets",
        "temples and quiet hiking",
        "ramen and cosy cafes",
    ]

    # Alias-free variants: nothing here names a city outright, so the board
    # is one unnamed tier and strict decrease across all ten is checkable.
    UNNAMED_MEMBERS = [
        "beach and surfing and island hopping",
        "live music and night markets",
        "temples and quiet hiking",
        "coffee and cosy cafes",
    ]

    def setUp(self):
        dataset.clear_cache()
        vectors.clear_cache()

    def test_top_ten_strictly_decreasing(self):
        results = shortlist(self.UNNAMED_MEMBERS, limit=10)
        self.assertGreaterEqual(len(results), 5,
                                "Expected a non-trivial shortlist")
        scores = [m.vibe_score for m in results]
        self.assertEqual(len(set(scores)), len(scores),
                         "Top-10 vibeScores must be distinct, got %s"
                         % scores)
        for higher, lower in zip(scores, scores[1:]):
            self.assertGreater(
                higher, lower,
                "vibeScores must be strictly decreasing, got %s" % scores)

    def test_named_city_keeps_real_score(self):
        """kimchi/kpop alias Seoul; its score is real, not overwritten 1.0."""
        results = shortlist(self.MEMBERS)
        seoul = [m for m in results if m.city_id == "ICN"]
        self.assertEqual(len(seoul), 1, "Seoul should be shortlisted")
        self.assertEqual(seoul[0].named, 1)
        self.assertLess(seoul[0].vibe_score, 1.0,
                        "Named tier must not overwrite vibe_score with 1.0")

    def test_below_floor_cities_excluded(self):
        """Unnamed cities with fused score < MIN_SIMILARITY are dropped."""
        results = shortlist(self.MEMBERS)
        result_ids = {m.city_id for m in results}

        all_clauses = []
        for text in self.MEMBERS:
            all_clauses.extend(clauses(text))

        below_floor = []
        for city in dataset.reachable():
            named_val = 0
            for text in self.MEMBERS:
                named_val = places_named(text).get(city.city_id, named_val)
            if named_val != 0:
                continue
            dense, sparse, _ = score_city(city, all_clauses)
            fused = WEIGHTS["dense"] * dense + WEIGHTS["sparse"] * sparse
            if fused < MIN_SIMILARITY:
                below_floor.append(city.city_id)

        self.assertGreater(
            len(below_floor), 0,
            "Expected at least one city below MIN_SIMILARITY to exist")
        for cid in below_floor:
            self.assertNotIn(
                cid, result_ids,
                "%s scored below MIN_SIMILARITY but was shortlisted" % cid)


class TestGroupPreferenceFairness(unittest.TestCase):
    """Conflicting single-word preferences must both reach the board.

    Regression: 'temples' + 'beaches' used to return an all-temple board.
    The beach clause was pooled with the temple clause and drowned out, and
    'beaches' (plural) never matched the dataset's singular 'beach' keyword
    so it earned no sparse credit either.
    """

    def setUp(self):
        dataset.clear_cache()
        vectors.clear_cache()

    def test_beach_member_represented(self):
        results = shortlist(["temples", "beaches"], limit=14)
        self.assertGreater(len(results), 0)
        beach_matches = [
            m for m in results
            if any("beach" in t for t in m.matched)
        ]
        self.assertGreater(
            len(beach_matches), 0,
            "Beach preference produced no beach-matching shortlist entry")
        # A beach city must rank in the top half, not just scrape in
        ids = [m.city_id for m in results]
        top_half = set(ids[: len(ids) // 2 + 1])
        self.assertTrue(
            top_half & {m.city_id for m in beach_matches},
            "No beach-matching city in the top half: %s" % ids)

    def test_plural_stems_to_keyword(self):
        # 'beaches' must earn sparse credit against the 'beach' keyword
        cities = dataset.load(CITIES_PATH)
        dps = [c for c in cities if c.city_id == "DPS"][0]
        _, sparse, matched = score_city(dps, clauses("beaches"))
        self.assertGreater(sparse, 0.0)
        self.assertIn("beaches", matched)

    def test_matched_tokens_use_member_wording(self):
        cities = dataset.load(CITIES_PATH)
        dps = [c for c in cities if c.city_id == "DPS"][0]
        _, _, matched = score_city(dps, clauses("temples"))
        # Reported wording is the member's own, not a crude stem
        for token in matched:
            self.assertNotEqual(token, "templ")


if __name__ == "__main__":
    unittest.main()
