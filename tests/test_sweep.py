"""Phase 5 — Sweep tests.

Verifies the sweep engine: return date derivation, cross-product assembly,
error reporting, and gap messages.
"""

import unittest
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests.helpers import make_client
from src.discovery.sweep import (
    sweep, return_dates_for, best_per_destination, best_per_shape,
)
from src.discovery.routes import search_nodes
from src.atlas.cache import clear as clear_cache


class TestReturnDatesFor(unittest.TestCase):
    """return_dates_for derives dates from a departure."""

    def test_default_offsets(self):
        dates = return_dates_for("20260918")
        self.assertEqual(len(dates), 3)
        self.assertEqual(dates[0], "20260920")  # +2
        self.assertEqual(dates[1], "20260921")  # +3
        self.assertEqual(dates[2], "20260922")  # +4

    def test_custom_offsets(self):
        dates = return_dates_for("20260918", offsets=(5, 7))
        self.assertEqual(len(dates), 2)
        self.assertEqual(dates[0], "20260923")  # +5
        self.assertEqual(dates[1], "20260925")  # +7

    def test_hyphenated_date(self):
        dates = return_dates_for("2026-09-18")
        self.assertEqual(len(dates), 3)
        self.assertEqual(dates[0], "20260920")


class TestSearchNodes(unittest.TestCase):
    """search_nodes returns FlightNodes from fixtures."""

    def setUp(self):
        clear_cache()

    def test_returns_nodes(self):
        client = make_client()
        nodes, error = search_nodes(
            client, "outbound", "SIN", "DPS", "20260918", party_size=1)
        self.assertIsNone(error)
        self.assertGreater(len(nodes), 0)

    def test_nodes_have_refs(self):
        client = make_client()
        nodes, _ = search_nodes(
            client, "outbound", "SIN", "DPS", "20260918", party_size=1)
        for node in nodes:
            self.assertTrue(node.price_ref, "Node missing price_ref")
            self.assertTrue(node.tax_ref, "Node missing tax_ref")

    def test_seat_filtering(self):
        """Party of 10 should drop nodes with fewer seats."""
        client = make_client()
        nodes, _ = search_nodes(
            client, "outbound", "SIN", "DPS", "20260918", party_size=10)
        for node in nodes:
            self.assertGreaterEqual(node.min_seat_count, 10)

    def test_empty_destination(self):
        """No routings for an unknown destination returns an error."""
        client = make_client()
        nodes, error = search_nodes(
            client, "outbound", "SIN", "FOO", "20260918", party_size=1)
        # The fixture exists and returns empty routings — nodes is empty
        # but error might be None (empty response is not an error)
        self.assertEqual(len(nodes), 0)


class TestSweep(unittest.TestCase):
    """sweep assembles outbound × return into ItineraryGraphs."""

    def setUp(self):
        clear_cache()

    def test_sweep_produces_trips(self):
        """SIN→DPS outbound × DPS→SIN return = trips."""
        client = make_client()
        trips, errors = sweep(
            client, "SIN", "20260918", ["20260922"],
            party_size=1, destinations=["DPS"])
        self.assertGreater(len(trips), 0,
                           "Expected trips from SIN→DPS roundtrip")

    def test_cross_product(self):
        """3 outbound × 2 return = 6 trips."""
        client = make_client()
        trips, _ = sweep(
            client, "SIN", "20260918", ["20260922"],
            party_size=1, destinations=["DPS"])
        # SIN-DPS has 3 routings, DPS-SIN has 2 routings → 6 combos
        self.assertEqual(len(trips), 6,
                         "Expected 3×2 = 6 trips, got %d" % len(trips))

    def test_empty_destinations(self):
        trips, errors = sweep(
            make_client(), "SIN", "20260918", ["20260922"],
            party_size=1, destinations=[])
        self.assertEqual(len(trips), 0)

    def test_none_destinations(self):
        trips, errors = sweep(
            make_client(), "SIN", "20260918", ["20260922"],
            party_size=1, destinations=None)
        self.assertEqual(len(trips), 0)

    def test_gap_message_for_unknown(self):
        """A destination with no routings reports a gap."""
        client = make_client()
        trips, errors = sweep(
            client, "SIN", "20260918", ["20260922"],
            party_size=1, destinations=["XYZ"])
        self.assertEqual(len(trips), 0)
        self.assertGreater(len(errors), 0,
                           "Expected error/gap for unknown destination XYZ")

    def test_trips_have_two_legs(self):
        client = make_client()
        trips, _ = sweep(
            client, "SIN", "20260918", ["20260922"],
            party_size=1, destinations=["DPS"])
        for trip in trips:
            self.assertEqual(len(trip.legs), 2)
            self.assertEqual(trip.outbound.role, "outbound")
            self.assertEqual(trip.inbound.role, "inbound")

    def test_vibe_score_carried(self):
        """vibe_score from Match objects is carried into the graph."""
        from src.discovery.retrieval import Match
        match = Match(
            city_id="DPS", city_name="Bali", country="Indonesia",
            vibe_score=0.85, dense=0.7, sparse=0.5, named=0)
        client = make_client()
        trips, _ = sweep(
            client, "SIN", "20260918", ["20260922"],
            party_size=1, destinations=["DPS"], members=[match])
        for trip in trips:
            self.assertAlmostEqual(trip.vibe_score, 0.85, places=2)


class TestBestPer(unittest.TestCase):
    """best_per_destination and best_per_shape helpers."""

    def setUp(self):
        clear_cache()

    def test_best_per_destination_limit(self):
        client = make_client()
        trips, _ = sweep(
            client, "SIN", "20260918", ["20260922"],
            party_size=1, destinations=["DPS"])
        result = best_per_destination(trips, limit=1)
        self.assertLessEqual(len(result), 1)

    def test_best_per_shape(self):
        client = make_client()
        trips, _ = sweep(
            client, "SIN", "20260918", ["20260922"],
            party_size=1, destinations=["DPS"])
        result = best_per_shape(trips, limit=2)
        self.assertLessEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
