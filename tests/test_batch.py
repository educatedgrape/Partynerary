"""Batch search tests — pre-sales fare lookup and comparison.

Covers:
  - Batch success: multiple queries return normalised records
  - Empty result: query with no routings produces error entry
  - Auth failure: 401 from Atlas surfaces as error, not crash
  - Timeout: URLError surfaces as error entry
  - Malformed response: missing routings key → empty, not crash
  - FareRecord normalisation: currency, baggage, sellable fields
  - Comparison report: table output, JSON serialisation
  - Retry: 429 triggers backoff, eventual success returns record
"""

import json
import os
import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

os.environ.pop("LIVE", None)

from src.atlas import cache as response_cache
from src.atlas.client import AtlasClient, AtlasHTTPError, LiveCallBlocked
from src.atlas.models import (
    BaggageOption, Routing, RoutingSegment, parse_routings, fixture_key,
)
from src.discovery.batch import (
    SearchQuery, FareRecord, BatchReport, batch_search, _routing_to_record,
)
from tests.helpers import make_client, TEST_CONFIG


# ---------------------------------------------------------------------------
# Fixture builders — realistic Atlas responses for batch scenarios
# ---------------------------------------------------------------------------

def _make_routing_dict(index=0, price=100.0, tax=20.0, carrier="TR",
                       flight="TR100", currency="USD", sellout=False,
                       baggage_kg=20, baggage_price=30.0):
    """Build a synthetic routing dict matching Atlas response shape."""
    seg = {
        "segmentIndex": 1,
        "carrier": carrier,
        "flightNumber": flight,
        "depAirport": "SIN",
        "depTime": "202610100820",
        "arrAirport": "BKK",
        "arrTime": "202610100955",
        "stopCities": "",
        "duration": 155,
        "codeShare": False,
        "cabin": "M",
        "cabinClass": 1,
        "seatCount": 5,
        "fareFamily": "Fly",
    }
    routing = {
        "fromSegments": [seg],
        "adultPrice": price,
        "adultTax": tax,
        "transactionFee": 0.0,
        "currency": currency,
        "riskSellout": sellout,
        "ancillaryProductElements": [
            {
                "productCode": "SCI_BAG_%dKG" % baggage_kg,
                "productName": "StandardCheckInBaggage",
                "productType": 1,
                "price": baggage_price,
                "currency": currency,
                "auxBaggageElement": {
                    "weight": baggage_kg,
                    "piece": 0,
                    "isAllWeight": True,
                },
            }
        ],
    }
    return routing


def _make_response(routings):
    """Wrap routing list into an Atlas search.do response."""
    return {"success": True, "routings": routings}


# ---------------------------------------------------------------------------
# Batch success — multiple queries return normalised records
# ---------------------------------------------------------------------------

class TestBatchSuccess(unittest.TestCase):
    """batch_search returns records for successful queries."""

    def setUp(self):
        response_cache.clear()

    def test_single_query_returns_records(self):
        client = MagicMock()
        resp = _make_response([
            _make_routing_dict(0, 100.0, 20.0, "TR", "TR100"),
            _make_routing_dict(1, 120.0, 25.0, "GA", "GA800"),
        ])
        key = fixture_key("SIN", "BKK", "20261010")
        client.post = MagicMock(return_value=resp)
        response_cache.put(key, resp)

        queries = [SearchQuery("SIN", "BKK", "20261010", adults=2)]
        report = batch_search(client, queries)

        self.assertEqual(report.total_queries, 1)
        self.assertEqual(report.successful_queries, 1)
        self.assertEqual(report.total_errors, 0)
        self.assertGreater(len(report.records), 0)

    def test_multiple_queries(self):
        client = MagicMock()

        def mock_post(endpoint, payload, fixture_key=None, allow_error=False):
            dest = payload.get("toCity", "")
            if dest == "BKK":
                resp = _make_response([
                    _make_routing_dict(0, 100.0, 20.0, "TR", "TR100"),
                ])
            elif dest == "HKT":
                resp = _make_response([
                    _make_routing_dict(0, 80.0, 15.0, "SQ", "SQ500"),
                ])
            else:
                return None
            if fixture_key:
                response_cache.put(fixture_key, resp)
            return resp

        client.post = MagicMock(side_effect=mock_post)
        queries = [
            SearchQuery("SIN", "BKK", "20261010", adults=2),
            SearchQuery("SIN", "HKT", "20261010", adults=2),
        ]
        report = batch_search(client, queries)

        self.assertEqual(report.total_queries, 2)
        self.assertEqual(report.total_errors, 0)
        self.assertEqual(report.successful_queries, 2)

    def test_records_have_normalized_fields(self):
        client = MagicMock()
        resp = _make_response([
            _make_routing_dict(0, 104.39, 33.98, "TR", "TR624",
                               currency="USD", baggage_kg=40,
                               baggage_price=41.85),
        ])
        key = fixture_key("SIN", "BKK", "20261010")
        client.post = MagicMock(return_value=resp)
        response_cache.put(key, resp)

        queries = [SearchQuery("SIN", "BKK", "20261010", adults=1)]
        report = batch_search(client, queries)

        self.assertGreater(len(report.records), 0)
        r = report.records[0]
        self.assertEqual(r.origin, "SIN")
        self.assertEqual(r.destination, "BKK")
        self.assertEqual(r.airline, "TR")
        self.assertAlmostEqual(r.fare, 104.39)
        self.assertAlmostEqual(r.taxes, 33.98)
        self.assertAlmostEqual(r.total_per_person, 104.39 + 33.98)
        self.assertEqual(r.currency, "USD")
        self.assertEqual(r.baggage_kg, 40)
        self.assertAlmostEqual(r.baggage_price, 41.85)
        self.assertTrue(r.sellable)
        self.assertEqual(r.fare_family, "Fly")


# ---------------------------------------------------------------------------
# Empty result — no routings returned
# ---------------------------------------------------------------------------

class TestBatchEmptyResult(unittest.TestCase):
    """batch_search handles empty routing lists gracefully."""

    def setUp(self):
        response_cache.clear()

    def test_empty_routings(self):
        client = MagicMock()
        resp = {"success": True, "routings": []}
        client.post = MagicMock(return_value=resp)

        queries = [SearchQuery("SIN", "XYZ", "20261010", adults=1)]
        report = batch_search(client, queries)

        self.assertEqual(report.total_records, 0)
        self.assertEqual(report.total_errors, 1)
        self.assertIn("No routings", report.errors[0]["error"])

    def test_missing_routings_key(self):
        client = MagicMock()
        resp = {"success": True}  # no 'routings' key at all
        client.post = MagicMock(return_value=resp)

        queries = [SearchQuery("SIN", "XYZ", "20261010", adults=1)]
        report = batch_search(client, queries)

        self.assertEqual(report.total_records, 0)
        # Should not crash — graceful degradation
        self.assertEqual(report.total_queries, 1)


# ---------------------------------------------------------------------------
# Auth failure — 401 from Atlas
# ---------------------------------------------------------------------------

class TestBatchAuthFailure(unittest.TestCase):
    """401 from Atlas is an error, not a crash or empty result."""

    def setUp(self):
        response_cache.clear()

    def test_auth_failure_surfaces_error(self):
        client = MagicMock()
        client.post = MagicMock(return_value=None)
        client.last_error = {
            "code": 401,
            "body": "Unauthorized",
            "url": "https://sandbox.atriptech.com/search.do",
        }

        queries = [SearchQuery("SIN", "BKK", "20261010", adults=1)]
        report = batch_search(client, queries)

        self.assertEqual(report.total_records, 0)
        self.assertEqual(report.total_errors, 1)
        self.assertIn("401", report.errors[0]["error"])

    def test_auth_failure_does_not_block_other_queries(self):
        """One query's auth failure does not prevent other queries."""
        client = MagicMock()
        call_count = [0]

        def mock_post(endpoint, payload, fixture_key=None, allow_error=False):
            call_count[0] += 1
            dest = payload.get("toCity", "")
            if dest == "FAIL":
                return None  # simulate error
            resp = _make_response([
                _make_routing_dict(0, 100.0, 20.0),
            ])
            if fixture_key:
                response_cache.put(fixture_key, resp)
            return resp

        client.post = MagicMock(side_effect=mock_post)
        client.last_error = {"code": 401, "body": "Unauthorized", "url": ""}

        queries = [
            SearchQuery("SIN", "FAIL", "20261010", adults=1),
            SearchQuery("SIN", "BKK", "20261010", adults=1),
        ]
        report = batch_search(client, queries)

        # First query failed, second succeeded
        self.assertEqual(report.total_errors, 1)
        self.assertGreater(len(report.records), 0)


# ---------------------------------------------------------------------------
# Timeout — URLError from network
# ---------------------------------------------------------------------------

class TestBatchTimeout(unittest.TestCase):
    """Network timeout surfaces as error entry, not crash."""

    def setUp(self):
        response_cache.clear()

    def test_timeout_surfaces_error(self):
        client = MagicMock()
        client.post = MagicMock(side_effect=Exception("Timeout: connection timed out"))

        queries = [SearchQuery("SIN", "BKK", "20261010", adults=1)]
        report = batch_search(client, queries)

        self.assertEqual(report.total_records, 0)
        self.assertEqual(report.total_errors, 1)
        self.assertIn("Timeout", report.errors[0]["error"])


# ---------------------------------------------------------------------------
# Malformed response — missing required fields
# ---------------------------------------------------------------------------

class TestBatchMalformedResponse(unittest.TestCase):
    """Malformed Atlas response does not crash the batch."""

    def setUp(self):
        response_cache.clear()

    def test_routings_is_string_not_list(self):
        client = MagicMock()
        resp = {"success": True, "routings": "not a list"}
        client.post = MagicMock(return_value=resp)

        queries = [SearchQuery("SIN", "BKK", "20261010", adults=1)]
        # Should not crash even if routings is not iterable
        report = batch_search(client, queries)
        self.assertEqual(report.total_queries, 1)

    def test_routing_missing_price_fields(self):
        """Routing with missing price fields defaults to 0.0."""
        client = MagicMock()
        resp = _make_response([{
            "fromSegments": [{
                "flightNumber": "TR999",
                "seatCount": 5,
                "depAirport": "SIN",
                "arrAirport": "BKK",
                "depTime": "202610100820",
                "arrTime": "202610100955",
                "duration": 155,
                "carrier": "TR",
            }],
            # No adultPrice, adultTax, etc.
        }])
        key = fixture_key("SIN", "BKK", "20261010")
        client.post = MagicMock(return_value=resp)
        response_cache.put(key, resp)

        queries = [SearchQuery("SIN", "BKK", "20261010", adults=1)]
        report = batch_search(client, queries)

        # Should produce records with 0.0 prices, not crash
        self.assertGreater(len(report.records), 0)
        r = report.records[0]
        self.assertEqual(r.fare, 0.0)
        self.assertEqual(r.taxes, 0.0)


# ---------------------------------------------------------------------------
# FareRecord normalisation — models.py new fields
# ---------------------------------------------------------------------------

class TestFareRecordNormalisation(unittest.TestCase):
    """Routing → FareRecord normalises all required fields."""

    def test_currency_from_routing(self):
        data = _make_routing_dict(0, currency="SGD")
        routing = Routing(_data=data, index=0, cache_key="test")
        self.assertEqual(routing.currency, "SGD")

    def test_baggage_options_parsed(self):
        data = _make_routing_dict(0, baggage_kg=40, baggage_price=41.85)
        routing = Routing(_data=data, index=0, cache_key="test")
        opts = routing.baggage_options
        self.assertEqual(len(opts), 1)
        self.assertEqual(opts[0].weight_kg, 40)
        self.assertAlmostEqual(opts[0].price, 41.85)

    def test_baggage_empty_when_no_type1(self):
        data = _make_routing_dict(0)
        data["ancillaryProductElements"] = [
            {"productType": 2, "productCode": "SEAT_12A", "price": 15.0}
        ]
        routing = Routing(_data=data, index=0, cache_key="test")
        self.assertEqual(len(routing.baggage_options), 0)

    def test_sellable_true_when_no_risk(self):
        data = _make_routing_dict(0, sellout=False)
        routing = Routing(_data=data, index=0, cache_key="test")
        self.assertTrue(routing.sellable)

    def test_sellable_false_when_risk_sellout(self):
        data = _make_routing_dict(0, sellout=True)
        routing = Routing(_data=data, index=0, cache_key="test")
        self.assertFalse(routing.sellable)


# ---------------------------------------------------------------------------
# Comparison report — table and JSON output
# ---------------------------------------------------------------------------

class TestBatchReport(unittest.TestCase):
    """BatchReport table and JSON output."""

    def setUp(self):
        response_cache.clear()

    def _make_report(self):
        """Build a report with two queries and some records."""
        client = MagicMock()

        def mock_post(endpoint, payload, fixture_key=None, allow_error=False):
            dest = payload.get("toCity", "")
            if dest == "BKK":
                resp = _make_response([
                    _make_routing_dict(0, 100.0, 20.0, "TR", "TR100"),
                    _make_routing_dict(1, 120.0, 25.0, "GA", "GA800"),
                ])
            else:
                resp = _make_response([
                    _make_routing_dict(0, 80.0, 15.0, "SQ", "SQ500"),
                ])
            if fixture_key:
                response_cache.put(fixture_key, resp)
            return resp

        client.post = MagicMock(side_effect=mock_post)
        queries = [
            SearchQuery("SIN", "BKK", "20261010", adults=2),
            SearchQuery("SIN", "HKT", "20261010", adults=2),
        ]
        return batch_search(client, queries)

    def test_table_output_does_not_crash(self):
        report = self._make_report()
        # Should not raise
        report.print_table()

    def test_json_serialisable(self):
        report = self._make_report()
        d = report.to_dict()
        # Must be JSON-serialisable
        s = json.dumps(d)
        self.assertIsInstance(s, str)
        parsed = json.loads(s)
        self.assertIn("records", parsed)
        self.assertIn("errors", parsed)
        self.assertIn("summary", parsed)

    def test_cheapest_per_route(self):
        report = self._make_report()
        best = report.cheapest_per_route()
        self.assertGreater(len(best), 0)
        for key, rec in best.items():
            self.assertIsInstance(rec, FareRecord)

    def test_cheapest_per_airline(self):
        report = self._make_report()
        best = report.cheapest_per_airline()
        self.assertGreater(len(best), 0)

    def test_coverage_summary(self):
        report = self._make_report()
        d = report.to_dict()
        coverage = d["summary"]["coverage_by_route"]
        self.assertIn("SIN-BKK", coverage)
        self.assertGreater(coverage["SIN-BKK"], 0)


# ---------------------------------------------------------------------------
# SearchQuery validation
# ---------------------------------------------------------------------------

class TestSearchQuery(unittest.TestCase):
    """SearchQuery normalises dates and accepts all optional fields."""

    def test_hyphenated_date_stripped(self):
        q = SearchQuery("SIN", "BKK", "2026-10-10")
        self.assertEqual(q.departure_date, "20261010")

    def test_return_date_optional(self):
        q = SearchQuery("SIN", "BKK", "20261010")
        self.assertEqual(q.return_date, "")

    def test_return_date_normalised(self):
        q = SearchQuery("SIN", "BKK", "20261010",
                        return_date="2026-10-15")
        self.assertEqual(q.return_date, "20261015")

    def test_airlines_tuple(self):
        q = SearchQuery("SIN", "BKK", "20261010", airlines=("TR", "SQ"))
        self.assertEqual(q.airlines, ("TR", "SQ"))

    def test_currency(self):
        q = SearchQuery("SIN", "BKK", "20261010", currency="SGD")
        self.assertEqual(q.currency, "SGD")


# ---------------------------------------------------------------------------
# Retry logic in AtlasClient
# ---------------------------------------------------------------------------

class TestAtlasClientRetry(unittest.TestCase):
    """AtlasClient retries on 429 and 5xx."""

    def test_retry_on_429_eventually_succeeds(self):
        """A 429 followed by a 200 should return the 200 response."""
        os.environ["LIVE"] = "1"
        try:
            client = AtlasClient(config=TEST_CONFIG)
            call_count = [0]

            with patch("urllib.request.urlopen") as mock_urlopen:
                def side_effect(req, timeout=None):
                    call_count[0] += 1
                    if call_count[0] == 1:
                        exc = __import__("urllib.error", fromlist=["HTTPError"]).HTTPError
                        raise exc(
                            "https://test", 429, "Rate limited",
                            {}, None)
                    # Second call succeeds
                    resp = MagicMock()
                    resp.read.return_value = b'{"success": true, "routings": []}'
                    resp.headers = {"Content-Encoding": None}
                    resp.__enter__ = lambda s: s
                    resp.__exit__ = MagicMock(return_value=False)
                    return resp

                mock_urlopen.side_effect = side_effect

                result = client.post("search.do", {}, fixture_key="test")
                self.assertIsNotNone(result)
                self.assertEqual(call_count[0], 2)
        finally:
            os.environ.pop("LIVE", None)

    def test_non_retryable_4xx_raises_immediately(self):
        """401 should NOT be retried."""
        os.environ["LIVE"] = "1"
        try:
            client = AtlasClient(config=TEST_CONFIG)
            call_count = [0]

            with patch("urllib.request.urlopen") as mock_urlopen:
                def side_effect(req, timeout=None):
                    call_count[0] += 1
                    exc = __import__("urllib.error", fromlist=["HTTPError"]).HTTPError
                    raise exc(
                        "https://test", 401, "Unauthorized",
                        {}, None)

                mock_urlopen.side_effect = side_effect

                with self.assertRaises(AtlasHTTPError):
                    client.post("search.do", {})
                self.assertEqual(call_count[0], 1)  # Only one attempt
        finally:
            os.environ.pop("LIVE", None)

    def test_request_log_populated(self):
        """Every call should log a request_id."""
        os.environ["LIVE"] = "1"
        try:
            client = AtlasClient(config=TEST_CONFIG)

            with patch("urllib.request.urlopen") as mock_urlopen:
                resp = MagicMock()
                resp.read.return_value = b'{"success": true}'
                resp.headers = {"Content-Encoding": None}
                resp.__enter__ = lambda s: s
                resp.__exit__ = MagicMock(return_value=False)
                mock_urlopen.return_value = resp

                client.post("search.do", {}, fixture_key="test")
                self.assertEqual(len(client.request_log), 1)
                self.assertIn("request_id", client.request_log[0])
                self.assertEqual(client.request_log[0]["status"], "ok")
        finally:
            os.environ.pop("LIVE", None)


if __name__ == "__main__":
    unittest.main()
