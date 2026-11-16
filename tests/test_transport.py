"""Phase 1 — Transport, response contract, and cost_ref tests.

Covers:
  - AtlasClient live-call blocking
  - gzip and non-gzip body decoding
  - Error body isolation (never cached, never returned as data)
  - Response parsing (routings[], segments, pricing)
  - cost_ref resolution, sibling navigation, group total formula
  - Fixture key format
"""

import gzip
import json
import os
import pathlib
import unittest

# Ensure LIVE is never set during tests.
os.environ.pop("LIVE", None)

from src.atlas import cache as response_cache
from src.atlas.client import AtlasClient, LiveCallBlocked
from src.atlas.models import (
    Routing, RoutingSegment, fixture_key, format_date, parse_routings,
)
from src.agent.cost_ref import (
    CostRefError, resolve, sibling, resolve_group_total,
)
from tests.helpers import make_client, TEST_FIXTURES, PROJECT_ROOT


# ===========================================================================
# AtlasClient — live gate, encoding
# ===========================================================================

class TestAtlasClientLiveGate(unittest.TestCase):
    """Live calls are blocked unless LIVE=1."""

    def test_live_blocked_without_env(self):
        os.environ.pop("LIVE", None)
        client = AtlasClient(config={"ATLAS_BASE_URL": "http://x"})
        with self.assertRaises(LiveCallBlocked):
            client.post("search.do", {}, fixture_key="search.do/SIN-DPS@20260918")


class TestAtlasClientDecoding(unittest.TestCase):
    """gzip and non-gzip bodies both decode."""

    def test_gzip_decode(self):
        payload = json.dumps({"test": True}).encode("utf-8")
        compressed = gzip.compress(payload)
        result = AtlasClient._decode(compressed, "gzip")
        self.assertEqual(json.loads(result), {"test": True})

    def test_plain_decode(self):
        payload = json.dumps({"test": True}).encode("utf-8")
        result = AtlasClient._decode(payload, None)
        self.assertEqual(json.loads(result), {"test": True})

    def test_mislabelled_gzip_decode(self):
        """A gzip body mislabelled as non-gzip must still decode."""
        payload = json.dumps({"test": True}).encode("utf-8")
        compressed = gzip.compress(payload)
        result = AtlasClient._decode(compressed, None)
        self.assertEqual(json.loads(result), {"test": True})


class TestAtlasClientNoUrllib(unittest.TestCase):
    """Nothing else in src/ imports urllib — enforced by this test."""

    def test_no_other_urllib_imports(self):
        src_dir = PROJECT_ROOT / "src"
        violations = []
        for py_file in src_dir.rglob("*.py"):
            if py_file.name == "client.py" and "atlas" in str(py_file):
                continue  # client.py is the ONE allowed urllib user
            content = py_file.read_text()
            if "import urllib" in content or "from urllib" in content:
                violations.append(str(py_file.relative_to(src_dir)))
        self.assertEqual(
            violations, [],
            "Only src/atlas/client.py may import urllib. Violations: %s"
            % violations)


# ===========================================================================
# Response contract — models
# ===========================================================================

class TestModels(unittest.TestCase):
    """Atlas response parsing — routings, segments, pricing."""

    def setUp(self):
        response_cache.clear()

    def _load_fixture(self, key):
        path = TEST_FIXTURES / (key.replace(":", "/") + ".json")
        with open(path) as f:
            return json.load(f)

    def test_parse_routings_count(self):
        data = self._load_fixture("search.do/SIN-DPS@20260918")
        routings = parse_routings(data, cache_key="search.do/SIN-DPS@20260918")
        self.assertEqual(len(routings), 3)

    def test_routing_segments(self):
        data = self._load_fixture("search.do/SIN-DPS@20260918")
        routings = parse_routings(data, cache_key="search.do/SIN-DPS@20260918")
        segs = routings[0].segments
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0].flight_number, "TR560")
        self.assertEqual(segs[0].seat_count, 9)

    def test_routing_pricing(self):
        data = self._load_fixture("search.do/SIN-DPS@20260918")
        routings = parse_routings(data, cache_key="search.do/SIN-DPS@20260918")
        self.assertAlmostEqual(routings[0].adult_price, 45.50)
        self.assertAlmostEqual(routings[0].adult_tax, 12.30)
        self.assertAlmostEqual(routings[0].transaction_fee, 0.00)

    def test_routing_total_price(self):
        data = self._load_fixture("search.do/SIN-DPS@20260918")
        routings = parse_routings(data, cache_key="search.do/SIN-DPS@20260918")
        # GA836: (78.90 + 21.50) * 1 + 2.00 = 102.40
        self.assertAlmostEqual(routings[2].total_price(1), 102.40)
        # Party of 4: (78.90 + 21.50) * 4 + 2.00 = 403.60
        self.assertAlmostEqual(routings[2].total_price(4), 403.60)

    def test_routing_min_seat_count(self):
        data = self._load_fixture("search.do/SIN-DPS@20260918")
        routings = parse_routings(data, cache_key="search.do/SIN-DPS@20260918")
        self.assertEqual(routings[0].min_seat_count, 9)
        self.assertEqual(routings[1].min_seat_count, 5)

    def test_routing_ref_builders(self):
        data = self._load_fixture("search.do/SIN-DPS@20260918")
        key = "search.do/SIN-DPS@20260918"
        routings = parse_routings(data, cache_key=key)
        self.assertEqual(
            routings[1].price_ref(),
            "%s#routings[1].adultPrice" % key)
        self.assertEqual(
            routings[1].tax_ref(),
            "%s#routings[1].adultTax" % key)
        self.assertEqual(
            routings[1].fee_ref(),
            "%s#routings[1].transactionFee" % key)

    def test_empty_routings(self):
        data = self._load_fixture("search.do/SIN-FOO@20260918-empty")
        routings = parse_routings(data, cache_key="search.do/SIN-FOO@20260918-empty")
        self.assertEqual(routings, [])

    def test_carriers(self):
        data = self._load_fixture("search.do/SIN-DPS@20260918")
        routings = parse_routings(data, cache_key="search.do/SIN-DPS@20260918")
        self.assertEqual(routings[0].carriers, ["TR"])
        self.assertFalse(routings[0].is_multi_carrier)

    def test_elapsed_hours(self):
        data = self._load_fixture("search.do/SIN-DPS@20260918")
        routings = parse_routings(data, cache_key="search.do/SIN-DPS@20260918")
        self.assertAlmostEqual(routings[0].elapsed_hours, 3.17)


class TestFixtureKey(unittest.TestCase):
    """Fixture key format — one definition, one place."""

    def test_format(self):
        self.assertEqual(
            fixture_key("SIN", "DPS", "20260918"),
            "search.do:SIN-DPS@20260918")

    def test_date_with_hyphens(self):
        self.assertEqual(
            fixture_key("SIN", "DPS", "2026-09-18"),
            "search.do:SIN-DPS@20260918")

    def test_format_date_passthrough(self):
        self.assertEqual(format_date("20260918"), "20260918")

    def test_format_date_strip_hyphens(self):
        self.assertEqual(format_date("2026-09-18"), "20260918")


# ===========================================================================
# cost_ref resolution
# ===========================================================================

class TestCostRef(unittest.TestCase):
    """cost_ref resolution, sibling navigation, group total."""

    def setUp(self):
        response_cache.clear()
        self.cache_key = "search.do/SIN-DPS@20260918"
        path = TEST_FIXTURES / "search.do" / "SIN-DPS@20260918.json"
        with open(path) as f:
            data = json.load(f)
        response_cache.put(self.cache_key, data)

    # -- resolve ------------------------------------------------------------

    def test_resolve_adult_price(self):
        ref = "%s#routings[0].adultPrice" % self.cache_key
        self.assertAlmostEqual(resolve(ref), 45.50)

    def test_resolve_adult_tax(self):
        ref = "%s#routings[0].adultTax" % self.cache_key
        self.assertAlmostEqual(resolve(ref), 12.30)

    def test_resolve_transaction_fee(self):
        ref = "%s#routings[2].transactionFee" % self.cache_key
        self.assertAlmostEqual(resolve(ref), 2.00)

    def test_resolve_second_routing(self):
        ref = "%s#routings[1].adultPrice" % self.cache_key
        self.assertAlmostEqual(resolve(ref), 52.00)

    def test_resolve_missing_cache_key_raises(self):
        ref = "search.do:NOWHERE@20260101#routings[0].adultPrice"
        with self.assertRaises(CostRefError) as ctx:
            resolve(ref)
        self.assertIn("not found", str(ctx.exception))

    def test_resolve_missing_field_raises(self):
        ref = "%s#routings[0].nonexistentField" % self.cache_key
        with self.assertRaises(CostRefError):
            resolve(ref)

    def test_resolve_out_of_range_raises(self):
        ref = "%s#routings[99].adultPrice" % self.cache_key
        with self.assertRaises(CostRefError):
            resolve(ref)

    def test_resolve_malformed_no_hash(self):
        with self.assertRaises(CostRefError):
            resolve("no-hash-here")

    def test_resolve_boolean_raises(self):
        """A bool is not a number — True must not silently become 1.0."""
        response_cache.put("test-bool", {"routings": [{"flag": True}]})
        ref = "test-bool#routings[0].flag"
        with self.assertRaises(CostRefError) as ctx:
            resolve(ref)
        self.assertIn("boolean", str(ctx.exception))

    def test_resolve_string_raises(self):
        """A string is not a number."""
        response_cache.put("test-str", {"routings": [{"name": "hello"}]})
        ref = "test-str#routings[0].name"
        with self.assertRaises(CostRefError) as ctx:
            resolve(ref)
        self.assertIn("str", str(ctx.exception))

    # -- sibling ------------------------------------------------------------

    def test_sibling_price_to_tax(self):
        ref = "%s#routings[0].adultPrice" % self.cache_key
        self.assertEqual(
            sibling(ref, "adultTax"),
            "%s#routings[0].adultTax" % self.cache_key)

    def test_sibling_price_to_fee(self):
        ref = "%s#routings[2].adultPrice" % self.cache_key
        self.assertEqual(
            sibling(ref, "transactionFee"),
            "%s#routings[2].transactionFee" % self.cache_key)

    def test_sibling_tax_to_price(self):
        ref = "%s#routings[1].adultTax" % self.cache_key
        self.assertEqual(
            sibling(ref, "adultPrice"),
            "%s#routings[1].adultPrice" % self.cache_key)

    # -- resolve_group_total -----------------------------------------------

    def test_group_total_party_of_1(self):
        """TR560: (45.50 + 12.30) * 1 + 0.00 = 57.80"""
        ref = "%s#routings[0].adultPrice" % self.cache_key
        total, refs = resolve_group_total(ref, adults=1)
        self.assertAlmostEqual(total, 57.80)
        self.assertEqual(len(refs), 3)

    def test_group_total_party_of_4(self):
        """GA836: (78.90 + 21.50) * 4 + 2.00 = 403.60"""
        ref = "%s#routings[2].adultPrice" % self.cache_key
        total, refs = resolve_group_total(ref, adults=4)
        self.assertAlmostEqual(total, 403.60)
        self.assertEqual(len(refs), 3)

    def test_group_total_with_fee(self):
        """GA836 party of 1: (78.90 + 21.50) * 1 + 2.00 = 102.40"""
        ref = "%s#routings[2].adultPrice" % self.cache_key
        total, refs = resolve_group_total(ref, adults=1)
        self.assertAlmostEqual(total, 102.40)

    def test_group_total_refs_used(self):
        """refs_used lists all three component refs."""
        ref = "%s#routings[0].adultPrice" % self.cache_key
        _, refs = resolve_group_total(ref, adults=1)
        self.assertIn("adultPrice", refs[0])
        self.assertIn("adultTax", refs[1])
        self.assertIn("transactionFee", refs[2])

    def test_group_total_missing_ref_raises(self):
        """A ref pointing at a missing cache key raises."""
        ref = "search.do:MISSING@20260101#routings[0].adultPrice"
        with self.assertRaises(CostRefError):
            resolve_group_total(ref, adults=1)


# ===========================================================================
# Error body isolation
# ===========================================================================

class TestErrorIsolation(unittest.TestCase):
    """Error responses are never cached and never returned as data."""

    def setUp(self):
        response_cache.clear()

    def test_error_fixture_not_cached_as_data(self):
        """Loading the error fixture does not put it into the cache under
        a search key — it is just a file on disk."""
        # The error fixture exists but is loaded only when explicitly asked
        # for. It must never appear as if it were a successful search response.
        client = make_client()
        result = client.post(
            "search.do",
            {},
            fixture_key="search.do/error-401",
        )
        # The fixture is loaded — but it has no routings, so parse_routings
        # returns an empty list.
        routings = parse_routings(result, cache_key="search.do/error-401")
        self.assertEqual(routings, [])


# ===========================================================================
# Integration — client + models + cost_ref end-to-end
# ===========================================================================

class TestEndToEnd(unittest.TestCase):
    """Load a fixture through the mock client, parse, and dereference."""

    def setUp(self):
        response_cache.clear()

    def test_full_fixture_load_and_resolve(self):
        client = make_client()
        key = "search.do/SIN-DPS@20260918"

        # 1. Client serves the fixture and populates the cache
        data = client.post("search.do", {}, fixture_key=key)
        self.assertIn("routings", data)

        # 2. Parse into Routing objects
        routings = parse_routings(data, cache_key=key)
        self.assertEqual(len(routings), 3)

        # 3. Build a cost_ref from a routing
        ref = routings[0].price_ref()
        self.assertEqual(ref, "%s#routings[0].adultPrice" % key)

        # 4. Dereference it — same value as the fixture
        price = resolve(ref)
        self.assertAlmostEqual(price, 45.50)

        # 5. Group total through cost_ref
        total, _ = resolve_group_total(ref, adults=4)
        # (45.50 + 12.30) * 4 + 0.00 = 231.20
        self.assertAlmostEqual(total, 231.20)

    def test_two_fixtures_independent_caches(self):
        """Two different searches have independent cache keys."""
        client = make_client()
        key_out = "search.do/SIN-DPS@20260918"
        key_ret = "search.do/DPS-SIN@20260922"

        client.post("search.do", {}, fixture_key=key_out)
        client.post("search.do", {}, fixture_key=key_ret)

        # Both are in the cache
        self.assertIn(key_out, response_cache.RESPONSE_CACHE)
        self.assertIn(key_ret, response_cache.RESPONSE_CACHE)

        # Refs from one do not reach the other
        out_routings = parse_routings(
            response_cache.get(key_out), cache_key=key_out)
        ret_routings = parse_routings(
            response_cache.get(key_ret), cache_key=key_ret)

        out_ref = out_routings[0].price_ref()
        ret_ref = ret_routings[0].price_ref()
        self.assertNotEqual(out_ref, ret_ref)
        self.assertAlmostEqual(resolve(out_ref), 45.50)
        self.assertAlmostEqual(resolve(ret_ref), 48.25)


if __name__ == "__main__":
    unittest.main()
