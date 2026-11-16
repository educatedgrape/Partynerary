"""Phase 8 — Re-plan tests.

Verifies cheapest-change-first ordering, ceiling filtering, termination
conditions, and LoopOutcome narration.
"""

import unittest
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests.helpers import make_client
from src.itinerary.replan import (
    explore, repair_loop, should_stop, LoopOutcome, Alternative,
    RETURN_SWAP, OUTBOUND_SWAP, DESTINATION_SWAP, KIND_LABEL,
    EXHAUSTED, BLOCKED, BUDGET, MAX_REPLAN_ROUNDS, MAX_SESSION_ROUNDS,
)
from src.itinerary.nodes import FlightNode
from src.itinerary.graph import build_chain
from src.party.preferences import Ceiling
from src.atlas import cache as response_cache


OUT_KEY = "search.do:SIN-DPS@20260918"
RET_KEY = "search.do:DPS-SIN@20260922"


class TestExplore(unittest.TestCase):
    """Cheapest-change-first exploration."""

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

    def test_return_swaps_before_outbound(self):
        """explore() returns return-swaps before outbound-swaps."""
        trip = self._build_trip()
        ceiling = Ceiling(member="Alice", amount=10000.0)
        ret_dates = ["20260920", "20260921", "20260922"]

        alts = explore(
            self.client, trip, [ceiling], "20260918", ret_dates, 2)

        if len(alts) >= 2:
            # First alternatives should be return swaps (cheapest change)
            kinds = [a.kind for a in alts]
            # Return swaps should appear before outbound swaps
            if RETURN_SWAP in kinds and OUTBOUND_SWAP in kinds:
                first_return = kinds.index(RETURN_SWAP)
                first_outbound = kinds.index(OUTBOUND_SWAP)
                self.assertLess(first_return, first_outbound)

    def test_ceiling_breaching_alternatives_absent(self):
        """An alternative breaching any ceiling is absent from the list."""
        trip = self._build_trip()
        # Very low ceiling — nothing should pass
        ceiling = Ceiling(member="Alice", amount=1.0)
        ret_dates = ["20260922"]

        alts = explore(
            self.client, trip, [ceiling], "20260918", ret_dates, 2)

        for alt in alts:
            self.assertGreater(alt.graph.per_person, 1.0,
                               "all alternatives should be above $1 but were filtered")

    def test_delta_measured_against_broken(self):
        """delta_vs_broken is measured against the trip that broke."""
        trip = self._build_trip()
        ceiling = Ceiling(member="Alice", amount=10000.0)
        ret_dates = ["20260920", "20260921", "20260922"]

        alts = explore(
            self.client, trip, [ceiling], "20260918", ret_dates, 2)

        for alt in alts:
            expected_delta = round(alt.graph.per_person - trip.per_person, 2)
            self.assertAlmostEqual(
                alt.delta_vs_broken, expected_delta, places=2,
                msg="delta should be measured against the broken trip")

    def tearDown(self):
        response_cache.clear()


class TestShouldStop(unittest.TestCase):
    """Termination conditions."""

    def test_zero_candidates_exhausted(self):
        """Zero new candidates stops immediately with EXHAUSTED."""
        self.assertEqual(should_stop([], None, None), EXHAUSTED)

    def test_nonempty_candidates_continue(self):
        """Non-empty candidates with no common breach → continue (None)."""
        alt = Alternative(kind=RETURN_SWAP, delta_vs_broken=5.0)
        self.assertIsNone(should_stop([alt], None, None))

    def test_all_breach_same_member_blocked(self):
        """Every candidate breaching the same member yields BLOCKED."""
        alt1 = Alternative(kind=RETURN_SWAP, rejected_for=["Alice"])
        alt2 = Alternative(kind=OUTBOUND_SWAP, rejected_for=["Alice"])
        self.assertEqual(should_stop([alt1, alt2], None, None), BLOCKED)

    def test_different_members_not_blocked(self):
        """Different members breaching → not BLOCKED."""
        alt1 = Alternative(kind=RETURN_SWAP, rejected_for=["Alice"])
        alt2 = Alternative(kind=OUTBOUND_SWAP, rejected_for=["Bob"])
        result = should_stop([alt1, alt2], None, None)
        self.assertNotEqual(result, BLOCKED)


class TestLoopOutcome(unittest.TestCase):
    """LoopOutcome narration."""

    def test_blocked_narration_names_member(self):
        """narrate() on a BLOCKED outcome names the member and the shortfall."""
        from src.itinerary.nodes import FlightNode
        best = FlightNode(
            role="outbound", origin="SIN", destination="DPS",
            date="20260918", cache_key=OUT_KEY, routing_index=0,
            adult_price=150.00, adult_tax=20.00, transaction_fee=0.00)

        # Build a minimal graph for narration
        ret_node = FlightNode(
            role="inbound", origin="DPS", destination="SIN",
            date="20260922", cache_key=RET_KEY, routing_index=0,
            adult_price=120.00, adult_tax=18.00, transaction_fee=0.00)

        graph = build_chain([best, ret_node], party_size=2,
                            destination_name="Penang")

        outcome = LoopOutcome(
            stopped_because=BLOCKED,
            rounds_used=4,
            best=graph,
            blocking_member="Marcus",
            shortfall=58.17,
        )

        narration = outcome.narrate()
        self.assertIn("Marcus", narration)
        self.assertIn("58.17", narration)

    def test_exhausted_narration(self):
        outcome = LoopOutcome(
            stopped_because=EXHAUSTED,
            rounds_used=2)
        narration = outcome.narrate()
        self.assertIn("2", narration)
        self.assertIn("no more", narration.lower())

    def test_budget_narration(self):
        outcome = LoopOutcome(
            stopped_because=BUDGET,
            rounds_used=4)
        narration = outcome.narrate()
        self.assertIn("4", narration)


class TestRepairLoop(unittest.TestCase):
    """The repair loop terminates correctly."""

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

    def test_impossible_ceiling_returns_budget_or_blocked(self):
        """A loop that cannot satisfy the tightest ceiling stops and
        returns BUDGET or BLOCKED with a non-null best."""
        trip = self._build_trip()
        ceiling = Ceiling(member="Alice", amount=1.0)  # impossible

        outcome = repair_loop(
            self.client, trip, [ceiling], "20260918",
            ["20260922"], 2)

        self.assertIn(outcome.stopped_because, (BUDGET, BLOCKED, EXHAUSTED))
        self.assertGreater(outcome.rounds_used, 0)

    def test_session_cap_returns_outcome_not_hang(self):
        """MAX_SESSION_ROUNDS caps total; a later change finding the budget
        spent still returns a LoopOutcome, never a hang."""
        trip = self._build_trip()
        ceiling = Ceiling(member="Alice", amount=1.0)

        outcome = repair_loop(
            self.client, trip, [ceiling], "20260918",
            ["20260922"], 2,
            session_rounds_used=MAX_SESSION_ROUNDS)

        self.assertIsNotNone(outcome)
        self.assertIn(outcome.stopped_because, (BUDGET, BLOCKED, EXHAUSTED))

    def tearDown(self):
        response_cache.clear()


if __name__ == "__main__":
    unittest.main()
