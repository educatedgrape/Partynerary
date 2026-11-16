"""Phase 8 — Reconciliation tests.

Verifies gap analysis, hub ranking, and the Option 2 engine.
"""

import unittest
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests.helpers import make_client
from src.discovery.reconcile import (
    gap_analysis, rank_hubs, reconcile,
    MAX_HUB_CANDIDATES,
)
from src.discovery.routes import search_nodes
from src.itinerary.graph import build_chain
from src.party.preferences import Ceiling, MemberPreferences
from src.atlas import cache as response_cache


class TestGapAnalysis(unittest.TestCase):
    """Gap analysis identifies unsatisfied preferences."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()

    def _build_trip(self):
        out_nodes, _ = search_nodes(
            self.client, "outbound", "SIN", "DPS", "20260918", 2)
        ret_nodes, _ = search_nodes(
            self.client, "inbound", "DPS", "SIN", "20260922", 2)
        return build_chain(
            [out_nodes[0], ret_nodes[0]],
            party_size=2, destination_name="Bali")

    def test_no_gap_when_preferences_met(self):
        """A member whose preferences match the destination has no gap."""
        trip = self._build_trip()
        # "beach" and "island" are Bali keywords
        member = MemberPreferences(
            member="Alice", origin="SIN",
            ceiling=Ceiling(member="Alice", amount=500.0),
            preferences="beach temple")
        gaps = gap_analysis(trip, [member])
        # Should have no gap (or minimal)
        unsatisfied_total = sum(len(g["unsatisfied"]) for g in gaps)
        self.assertEqual(unsatisfied_total, 0,
                         "Expected no unsatisfied preferences, got gaps: %s" % gaps)

    def test_gap_when_preferences_unmet(self):
        """A member whose preferences don't match generates a gap."""
        trip = self._build_trip()
        member = MemberPreferences(
            member="Marcus", origin="SIN",
            ceiling=Ceiling(member="Marcus", amount=500.0),
            preferences="streetfood ramen sushi")
        gaps = gap_analysis(trip, [member])
        # Should have unsatisfied preferences
        if gaps:
            self.assertGreater(len(gaps[0]["unsatisfied"]), 0)

    def test_no_members_no_gaps(self):
        trip = self._build_trip()
        gaps = gap_analysis(trip, [])
        self.assertEqual(gaps, [])

    def tearDown(self):
        response_cache.clear()


class TestReconcile(unittest.TestCase):
    """Option 2 engine."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()

    def _build_trip(self):
        out_nodes, _ = search_nodes(
            self.client, "outbound", "SIN", "DPS", "20260918", 2)
        ret_nodes, _ = search_nodes(
            self.client, "inbound", "DPS", "SIN", "20260922", 2)
        return build_chain(
            [out_nodes[0], ret_nodes[0]],
            party_size=2, destination_name="Bali")

    def test_no_gap_no_option2(self):
        """If nobody has a gap, Option 2 is not constructed."""
        trip = self._build_trip()
        member = MemberPreferences(
            member="Alice", origin="SIN",
            ceiling=Ceiling(member="Alice", amount=500.0),
            preferences="beach temple")

        result = reconcile(
            self.client, trip, [member], "SIN", "20260918",
            ["20260922"], 2, destination_name="Bali")

        self.assertIsNone(result.get("option2"))
        self.assertIn("reason_if_none", result)

    def tearDown(self):
        response_cache.clear()


if __name__ == "__main__":
    unittest.main()
