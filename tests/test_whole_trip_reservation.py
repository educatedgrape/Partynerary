"""Phase 7 — Whole-trip reservation tests.

Verifies that the reserved amount equals graph.group_total for every trip
on the board, to the cent. The executor compares the same quantity on both
sides of every guard.
"""

import unittest
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests.helpers import make_client
from src.agent.proposal import ActionProposal
from src.agent.mandate import Mandate
from src.agent.executor import Executor, Confirmation
from src.agent.decision_log import DecisionLog
from src.atlas import cache as response_cache
from src.discovery.routes import search_nodes
from src.itinerary.graph import build_chain


# Fixture cache keys
OUT_KEY = "search.do:SIN-DPS@20260918"
RET_KEY = "search.do:DPS-SIN@20260922"


class TestWholeTripReservation(unittest.TestCase):
    """Reserved amount equals graph.group_total, to the cent."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()
        # Pre-load the cache by searching both directions
        self.out_nodes, _ = search_nodes(
            self.client, "outbound", "SIN", "DPS", "20260918", 2)
        self.ret_nodes, _ = search_nodes(
            self.client, "inbound", "DPS", "SIN", "20260922", 2)

    def _build_trip(self, out_idx=0, ret_idx=0, party_size=2):
        """Build an ItineraryGraph from specific routing indices."""
        out_node = self.out_nodes[out_idx]
        ret_node = self.ret_nodes[ret_idx]
        return build_chain(
            [out_node, ret_node],
            party_size=party_size,
            destination_name="Bali")

    def _reserve(self, trip, ceiling=5000.0):
        """Run a book_group proposal for the trip and return the result."""
        mandate = Mandate(ceiling)
        log = DecisionLog()
        # price_shown is the WHOLE-TRIP per-person figure the human saw —
        # group_total / adults, which matches what the executor computes.
        per_person = round(trip.group_total / trip.party_size, 2)
        confirmation = Confirmation(
            action="book_group", target=trip.key,
            approved_by="user", at="2026-09-15T10:00",
            price_shown=per_person)
        executor = Executor(mandate, confirmation=confirmation, log=log)

        # Build the proposal with one ref per leg
        cost_refs = tuple(leg.price_ref for leg in trip.legs)
        proposal = ActionProposal(
            action="book_group", target=trip.key,
            reason="booking %s for %d" % (trip.destination_name, trip.party_size),
            cost_refs=cost_refs)

        payload = {"adults": trip.party_size, "legs": len(trip.legs)}
        return executor.execute(proposal, payload=payload)

    def test_reserved_equals_group_total_r0_r0(self):
        """Out[0] + Ret[0]: reserved amount = group_total to the cent."""
        trip = self._build_trip(0, 0)
        result = self._reserve(trip)
        self.assertTrue(result.accepted, "reservation refused: %s" % result.reason)
        self.assertAlmostEqual(result.amount, trip.group_total, places=2,
                               msg="reserved $%.2f ≠ group_total $%.2f" % (
                                   result.amount, trip.group_total))

    def test_reserved_equals_group_total_r0_r1(self):
        """Out[0] + Ret[1]: reserved amount = group_total to the cent."""
        trip = self._build_trip(0, 1)
        result = self._reserve(trip)
        self.assertTrue(result.accepted, "reservation refused: %s" % result.reason)
        self.assertAlmostEqual(result.amount, trip.group_total, places=2,
                               msg="reserved $%.2f ≠ group_total $%.2f" % (
                                   result.amount, trip.group_total))

    def test_reserved_equals_group_total_r2_r0(self):
        """Out[2] + Ret[0]: reserved amount = group_total to the cent."""
        trip = self._build_trip(2, 0)
        result = self._reserve(trip)
        self.assertTrue(result.accepted, "reservation refused: %s" % result.reason)
        self.assertAlmostEqual(result.amount, trip.group_total, places=2,
                               msg="reserved $%.2f ≠ group_total $%.2f" % (
                                   result.amount, trip.group_total))

    def test_book_reserves_then_pay_settles(self):
        """book_group reserves and pay_group settles the same total —
        no double charge, no partial settle."""
        trip = self._build_trip(0, 0)
        mandate = Mandate(5000.0)
        log = DecisionLog()
        per_person = round(trip.group_total / trip.party_size, 2)

        # Step 1: Book (reserve)
        book_confirm = Confirmation(
            action="book_group", target=trip.key,
            approved_by="user", at="2026-09-15T10:00",
            price_shown=per_person)
        executor = Executor(mandate, confirmation=book_confirm, log=log)

        cost_refs = tuple(leg.price_ref for leg in trip.legs)
        book_proposal = ActionProposal(
            action="book_group", target=trip.key,
            reason="booking Bali", cost_refs=cost_refs)
        book_result = executor.execute(
            book_proposal,
            payload={"adults": trip.party_size, "legs": len(trip.legs)})
        self.assertTrue(book_result.accepted)
        self.assertAlmostEqual(mandate.reserved, trip.group_total, places=2)

        # Step 2: Pay (settle)
        pay_confirm = Confirmation(
            action="pay_group", target=trip.key,
            approved_by="user", at="2026-09-15T10:01",
            price_shown=per_person)
        executor.confirmation = pay_confirm
        pay_proposal = ActionProposal(
            action="pay_group", target=trip.key,
            reason="paying for Bali", cost_refs=cost_refs)
        pay_result = executor.execute(
            pay_proposal,
            payload={"adults": trip.party_size, "legs": len(trip.legs)})
        self.assertTrue(pay_result.accepted)

        # After settle: reserved is 0; settlement is the executor's own
        # accounting, not a balance on the mandate.
        self.assertAlmostEqual(mandate.reserved, 0.0, places=2)
        self.assertAlmostEqual(executor.settled, trip.group_total, places=2)

    def test_group_total_per_trip_identity(self):
        """Every trip has a unique identity key and its own group total."""
        trip1 = self._build_trip(0, 0)
        trip2 = self._build_trip(0, 1)
        self.assertNotEqual(trip1.key, trip2.key)
        self.assertNotAlmostEqual(trip1.group_total, trip2.group_total)

    def tearDown(self):
        response_cache.clear()


class TestPartialTripRefused(unittest.TestCase):
    """A proposal with fewer refs than legs is refused."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()
        self.out_nodes, _ = search_nodes(
            self.client, "outbound", "SIN", "DPS", "20260918", 2)
        self.ret_nodes, _ = search_nodes(
            self.client, "inbound", "DPS", "SIN", "20260922", 2)

    def test_one_ref_for_two_legs(self):
        """One ref for a two-leg trip refuses at dereference."""
        trip = build_chain(
            [self.out_nodes[0], self.ret_nodes[0]],
            party_size=2, destination_name="Bali")
        mandate = Mandate(5000.0)
        confirmation = Confirmation(
            action="book_group", target=trip.key,
            approved_by="user", at="2026-09-15T10:00",
            price_shown=round(trip.group_total / trip.party_size, 2))
        executor = Executor(mandate, confirmation=confirmation)

        # Only one ref — missing the return leg
        proposal = ActionProposal(
            action="book_group", target=trip.key,
            reason="booking half a trip",
            cost_refs=(self.out_nodes[0].price_ref,))
        result = executor.execute(
            proposal,
            payload={"adults": 2, "legs": 2})
        self.assertFalse(result.accepted)
        self.assertEqual(result.stage, "dereference")

    def tearDown(self):
        response_cache.clear()


if __name__ == "__main__":
    unittest.main()
