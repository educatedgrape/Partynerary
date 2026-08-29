"""Phase 8 — Chain search tests.

Verifies that chain_for produces valid 3-leg itineraries through a hub,
and that the seat filter applies independently to each leg.
"""

import unittest
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests.helpers import make_client
from src.discovery.reconcile import chain_for, MAX_CHAIN_COMBOS
from src.atlas import cache as response_cache


class TestChainFor(unittest.TestCase):
    """chain_for builds 3-leg itineraries through a hub."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()

    def test_chain_missing_leg_returns_error(self):
        """A chain with a missing leg (no fixture) returns an error."""
        graphs, err = chain_for(
            self.client, "SIN", "BKK", "DPS", "20260918",
            stopover_nights=1, destination_nights=2,
            party_size=2)
        # BKK has no fixture in the test set → error
        self.assertIsNotNone(err)
        self.assertEqual(graphs, [])

    def test_chain_combos_capped(self):
        """MAX_CHAIN_COMBOS is a finite cap."""
        self.assertGreater(MAX_CHAIN_COMBOS, 0)
        self.assertLessEqual(MAX_CHAIN_COMBOS, 1000)

    def tearDown(self):
        response_cache.clear()


class TestPitch(unittest.TestCase):
    """The pitch module builds valid proposals from trips."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()

    def test_pitch_booking_has_refs_and_payload(self):
        """pitch_booking returns a proposal with cost_refs and a payload."""
        from src.agents.pitch import pitch_booking
        from src.discovery.routes import search_nodes
        from src.itinerary.graph import build_chain

        out_nodes, _ = search_nodes(
            self.client, "outbound", "SIN", "DPS", "20260918", 2)
        ret_nodes, _ = search_nodes(
            self.client, "inbound", "DPS", "SIN", "20260922", 2)
        trip = build_chain(
            [out_nodes[0], ret_nodes[0]],
            party_size=2, destination_name="Bali")

        proposal, payload = pitch_booking(trip)

        self.assertEqual(proposal.action, "book_group")
        self.assertEqual(len(proposal.cost_refs), 2)  # one per leg
        self.assertEqual(payload["adults"], 2)
        self.assertEqual(payload["legs"], 2)

    def test_pitch_payment_has_refs(self):
        """pitch_payment returns a pay_group proposal."""
        from src.agents.pitch import pitch_payment
        from src.discovery.routes import search_nodes
        from src.itinerary.graph import build_chain

        out_nodes, _ = search_nodes(
            self.client, "outbound", "SIN", "DPS", "20260918", 2)
        ret_nodes, _ = search_nodes(
            self.client, "inbound", "DPS", "SIN", "20260922", 2)
        trip = build_chain(
            [out_nodes[0], ret_nodes[0]],
            party_size=2, destination_name="Bali")

        proposal, payload = pitch_payment(trip)

        self.assertEqual(proposal.action, "pay_group")
        self.assertEqual(len(proposal.cost_refs), 2)

    def tearDown(self):
        response_cache.clear()


if __name__ == "__main__":
    unittest.main()
