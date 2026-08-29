"""Phase 8 — Propagation tests.

Verifies change impact analysis, graph copy semantics, downstream tracing,
and the ceiling-breaching detection.
"""

import unittest
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests.helpers import make_client
from src.itinerary.propagate import (
    Change, Impact, apply_change, propagate, downstream_of,
    PRICE, SCHEDULE, GONE, CEILING,
)
from src.itinerary.nodes import FlightNode
from src.itinerary.graph import build_chain
from src.party.preferences import Ceiling
from src.atlas import cache as response_cache


OUT_KEY = "search.do:SIN-DPS@20260918"
RET_KEY = "search.do:DPS-SIN@20260922"


class TestApplyChange(unittest.TestCase):
    """apply_change returns a copy — the original graph is unchanged."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()

    def _build_trip(self):
        from src.discovery.routes import search_nodes
        out_nodes, _ = search_nodes(
            self.client, "outbound", "SIN", "DPS", "20260918", 2)
        ret_nodes, _ = search_nodes(
            self.client, "inbound", "DPS", "SIN", "20260922", 2)
        return build_chain(
            [out_nodes[0], ret_nodes[0]],
            party_size=2, destination_name="Bali")

    def test_copy_not_mutated(self):
        """apply_change returns a copy; the original graph is unchanged."""
        trip = self._build_trip()
        original_per_person = trip.per_person
        original_key = trip.key

        # Create a replacement node with a different price
        replacement = FlightNode(
            role="outbound", origin="SIN", destination="DPS",
            date="20260918", cache_key=OUT_KEY, routing_index=99,
            flight_numbers=("XX999",), carriers=("XX",),
            adult_price=999.99, adult_tax=50.00, transaction_fee=5.00,
            min_seat_count=10,
            price_ref="%s#routings[99].adultPrice" % OUT_KEY)

        change = Change(
            node_key=trip.outbound.key, kind=PRICE,
            was=trip.outbound.adult_price, now=999.99,
            replacement=replacement)

        repaired = apply_change(trip, change)

        # Original is unchanged
        self.assertAlmostEqual(trip.per_person, original_per_person, places=2)
        self.assertEqual(trip.key, original_key)

        # Repaired is different
        self.assertNotAlmostEqual(repaired.per_person, original_per_person,
                                  places=2)

    def test_no_replacement_no_change(self):
        """A change carrying no replacement alters no total."""
        trip = self._build_trip()
        change = Change(
            node_key=trip.outbound.key, kind=PRICE,
            was=45.50, now=55.50, replacement=None)

        repaired = apply_change(trip, change)
        # Should be the same graph object (no change applied)
        self.assertAlmostEqual(repaired.per_person, trip.per_person, places=2)

    def tearDown(self):
        response_cache.clear()


class TestDownstream(unittest.TestCase):
    """A change to the return leg leaves the outbound out of downstream."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()

    def _build_trip(self):
        from src.discovery.routes import search_nodes
        out_nodes, _ = search_nodes(
            self.client, "outbound", "SIN", "DPS", "20260918", 2)
        ret_nodes, _ = search_nodes(
            self.client, "inbound", "DPS", "SIN", "20260922", 2)
        return build_chain(
            [out_nodes[0], ret_nodes[0]],
            party_size=2, destination_name="Bali")

    def test_return_change_no_outbound_downstream(self):
        """A change to the return leg does not include the outbound."""
        trip = self._build_trip()
        ds = downstream_of(trip, trip.inbound.key)
        self.assertNotIn(trip.outbound.key, ds)

    def test_outbound_change_has_return_downstream(self):
        """A change to the outbound includes the return in downstream."""
        trip = self._build_trip()
        ds = downstream_of(trip, trip.outbound.key)
        self.assertIn(trip.inbound.key, ds)

    def tearDown(self):
        response_cache.clear()


class TestPropagate(unittest.TestCase):
    """Impact tracing through the dependency graph."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()

    def _build_trip(self):
        from src.discovery.routes import search_nodes
        out_nodes, _ = search_nodes(
            self.client, "outbound", "SIN", "DPS", "20260918", 2)
        ret_nodes, _ = search_nodes(
            self.client, "inbound", "DPS", "SIN", "20260922", 2)
        return build_chain(
            [out_nodes[0], ret_nodes[0]],
            party_size=2, destination_name="Bali")

    def test_price_rise_over_ceiling_breaches(self):
        """A PRICE rise pushing per_person over the ceiling breaches."""
        trip = self._build_trip()

        # Create a replacement with much higher price
        replacement = FlightNode(
            role="inbound", origin="DPS", destination="SIN",
            date="20260922", cache_key=RET_KEY, routing_index=99,
            flight_numbers=("XX999",), carriers=("XX",),
            adult_price=500.00, adult_tax=50.00, transaction_fee=10.00,
            min_seat_count=10,
            price_ref="%s#routings[99].adultPrice" % RET_KEY)

        change = Change(
            node_key=trip.inbound.key, kind=PRICE,
            was=trip.inbound.adult_price, now=500.00,
            replacement=replacement)

        ceiling = Ceiling(member="Alice", amount=200.0)
        impact, repaired = propagate(trip, change, ceilings=[ceiling])

        self.assertIn("Alice", impact.breached)
        self.assertTrue(impact.consensus_invalidated)

    def test_absorbable_price_rise_still_holds(self):
        """A PRICE rise the group can absorb leaves still_feasible true."""
        trip = self._build_trip()

        # Small price increase
        replacement = FlightNode(
            role="inbound", origin="DPS", destination="SIN",
            date="20260922", cache_key=RET_KEY, routing_index=99,
            flight_numbers=("XX888",), carriers=("XX",),
            adult_price=50.00, adult_tax=14.00, transaction_fee=0.00,
            min_seat_count=10,
            price_ref="%s#routings[99].adultPrice" % RET_KEY)

        change = Change(
            node_key=trip.inbound.key, kind=PRICE,
            was=trip.inbound.adult_price, now=50.00,
            replacement=replacement)

        # High ceiling
        ceiling = Ceiling(member="Alice", amount=10000.0)
        impact, repaired = propagate(trip, change, ceilings=[ceiling])

        self.assertTrue(impact.still_feasible)
        self.assertEqual(impact.breached, [])
        narration = impact.narrate()
        self.assertTrue(
            any("still holds" in line for line in narration),
            "Expected 'still holds' in narration: %s" % narration)

    def test_ceiling_regrant_vetoes(self):
        """A CEILING re-grant with no price change vetoes a previously
        accepted trip."""
        trip = self._build_trip()

        # The trip's per_person is around 119.05. Re-grant a ceiling below that.
        change = Change(
            node_key="", kind=CEILING,
            was=300.0, now=50.0,
            member="Bob")

        ceiling = Ceiling(member="Bob", amount=50.0)
        impact, repaired = propagate(trip, change, ceilings=[ceiling])

        self.assertIn("Bob", impact.breached)
        self.assertTrue(impact.consensus_invalidated)

    def test_gone_invalidates(self):
        """A GONE change invalidates the trip."""
        trip = self._build_trip()
        change = Change(
            node_key=trip.outbound.key, kind=GONE)

        impact, repaired = propagate(trip, change)
        self.assertFalse(impact.still_feasible)
        self.assertTrue(impact.consensus_invalidated)

    def tearDown(self):
        response_cache.clear()


class TestNarrate(unittest.TestCase):
    """Impact narration produces consequences, not deltas."""

    def test_narrate_price_rise(self):
        trip_total_before = 238.10
        trip_total_after = 338.10
        change = Change(kind=PRICE, was=45.50, now=95.50)
        impact = Impact(
            change=change,
            total_before=trip_total_before,
            total_after=trip_total_after)
        lines = impact.narrate()
        self.assertTrue(any("$" in line for line in lines))

    def test_narrate_still_holds(self):
        impact = Impact(still_feasible=True)
        lines = impact.narrate()
        self.assertTrue(any("still holds" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
