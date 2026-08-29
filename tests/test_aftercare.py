"""Phase 8.4 — Aftercare tests.

Verifies re-shop detection (GONE, PRICE), executor credit recording, inject
tagging, executor authority for aftercare actions, and stub recording.

Settlement lives on the executor, never on the mandate: a refund is
RECORDED as owed, never spent.
"""

import unittest
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests.helpers import make_client
from src.booking.aftercare import (
    reshop, inject, settle_difference, Order, BookedLeg, RepairOutcome,
    AUTONOMOUS_ACTIONS,
)
from src.itinerary.propagate import GONE, PRICE, SCHEDULE
from src.itinerary.nodes import FlightNode
from src.agent.proposal import ActionProposal
from src.agent.mandate import Mandate
from src.agent.executor import Executor, Confirmation
from src.agent.decision_log import DecisionLog
from src.discovery.routes import search_nodes
from src.itinerary.graph import build_chain
from src.party.preferences import Ceiling
from src.atlas import cache as response_cache


OUT_KEY = "search.do:SIN-DPS@20260918"
RET_KEY = "search.do:DPS-SIN@20260922"


class TestReshopGone(unittest.TestCase):
    """A booked routing missing from a re-shop yields a GONE change."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()

    def test_booked_routing_missing_yields_gone(self):
        out_nodes, _ = search_nodes(
            self.client, "outbound", "SIN", "DPS", "20260918", 2)
        ret_nodes, _ = search_nodes(
            self.client, "inbound", "DPS", "SIN", "20260922", 2)

        # Book a routing index beyond what the fixture provides
        order = Order(
            legs=[
                BookedLeg(
                    role="outbound", origin="SIN", destination="DPS",
                    date="20260918", cache_key=OUT_KEY,
                    routing_index=99, per_person=57.80,
                    flight_numbers=("XX001",)),
                BookedLeg(
                    role="inbound", origin="DPS", destination="SIN",
                    date="20260922", cache_key=RET_KEY,
                    routing_index=0, per_person=ret_nodes[0].per_person,
                    flight_numbers=ret_nodes[0].flight_numbers),
            ],
            party_size=2)

        changes = reshop(self.client, order, 2)

        gone_changes = [c for c in changes if c.kind == GONE]
        self.assertGreater(len(gone_changes), 0,
                           "expected at least one GONE change")

    def tearDown(self):
        response_cache.clear()


class TestReshopPrice(unittest.TestCase):
    """A cheaper equivalent leg yields a PRICE change with negative delta."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()

    def test_cheaper_leg_yields_price_change(self):
        out_nodes, _ = search_nodes(
            self.client, "outbound", "SIN", "DPS", "20260918", 2)
        ret_nodes, _ = search_nodes(
            self.client, "inbound", "DPS", "SIN", "20260922", 2)

        # Book at routing[1] (more expensive)
        booked_out = out_nodes[1]

        # Simulate a price drop: current routing[1] has the cheaper price
        # from routing[0]. Swap the cheaper node into position 1.
        cheaper = out_nodes[0]
        swapped = FlightNode(
            role=cheaper.role, origin=cheaper.origin,
            destination=cheaper.destination, date=cheaper.date,
            cache_key=cheaper.cache_key, routing_index=1,
            flight_numbers=cheaper.flight_numbers, carriers=cheaper.carriers,
            elapsed_hours=cheaper.elapsed_hours,
            adult_price=cheaper.adult_price, adult_tax=cheaper.adult_tax,
            transaction_fee=cheaper.transaction_fee,
            min_seat_count=cheaper.min_seat_count,
            price_ref=cheaper.price_ref, tax_ref=cheaper.tax_ref,
            fee_ref=cheaper.fee_ref)

        # Build current_routings with the swapped node at index 1
        modified_out = [n for n in out_nodes if n.routing_index != 1]
        modified_out.append(swapped)
        modified_out.sort(key=lambda n: n.routing_index)
        current_routings = {OUT_KEY: modified_out, RET_KEY: ret_nodes}

        order = Order(
            legs=[
                BookedLeg(
                    role="outbound", origin="SIN", destination="DPS",
                    date="20260918", cache_key=OUT_KEY,
                    routing_index=1, per_person=booked_out.per_person,
                    flight_numbers=booked_out.flight_numbers),
                BookedLeg(
                    role="inbound", origin="DPS", destination="SIN",
                    date="20260922", cache_key=RET_KEY,
                    routing_index=ret_nodes[0].routing_index,
                    per_person=ret_nodes[0].per_person,
                    flight_numbers=ret_nodes[0].flight_numbers),
            ],
            party_size=2)

        changes = reshop(self.client, order, 2, current_routings)

        price_changes = [c for c in changes if c.kind == PRICE]
        self.assertGreater(len(price_changes), 0,
                           "expected at least one PRICE change")

        for c in price_changes:
            if c.cost_ref:
                delta = c.now - c.was
                self.assertLess(delta, 0,
                                "cheaper leg should have negative delta")
                # cost_ref should be resolvable
                from src.agent.cost_ref import resolve
                value = resolve(c.cost_ref, response_cache.RESPONSE_CACHE)
                self.assertIsNotNone(value)

    def tearDown(self):
        response_cache.clear()


def _simulate_payment(executor, mandate, amount):
    """Mirror what the call gate does for pay_group: release the hold,
    record the settlement in the executor's own books."""
    mandate.release(amount)
    executor._settled = round(executor._settled + amount, 2)


class TestExecutorCredit(unittest.TestCase):
    """A refund is recorded as owed — remaining rises, spent never grows.

    The mandate itself has no credit/settle method: settlement and refunds
    are the executor's own accounting.
    """

    def test_credit_raises_remaining(self):
        m = Mandate(1000.0)
        executor = Executor(m)
        m.reserve(500.0)
        _simulate_payment(executor, m, 500.0)
        remaining_before = executor.remaining
        settled_before = executor.settled

        executor.record_credit(100.0)

        self.assertGreater(executor.remaining, remaining_before,
                           "credit should raise remaining authority")
        self.assertEqual(executor.settled, settled_before,
                         "credit must not alter what was spent")

    def test_credit_is_never_spent(self):
        m = Mandate(1000.0)
        executor = Executor(m)
        m.reserve(500.0)
        _simulate_payment(executor, m, 500.0)

        executor.record_credit(50.0)

        self.assertEqual(executor.credits, 50.0)
        self.assertEqual(executor.settled, 500.0,
                         "settled must never change because of a credit")

    def test_mandate_has_no_ledger_methods(self):
        """By introspection: the mandate exposes nothing that moves money."""
        m = Mandate(1000.0)
        forbidden = ("spend", "charge", "debit", "credit",
                     "settle", "transfer", "balance")
        for name in dir(m):
            for word in forbidden:
                self.assertNotIn(
                    word, name.lower(),
                    "Mandate exposes %r — a ceiling is authority, not funds"
                    % name)


class TestInject(unittest.TestCase):
    """An injected event produces changes tagged with [injector]."""

    def test_inject_tags_source(self):
        changes = inject({"kind": "PRICE", "node_key": "test#0",
                          "was": 100.0, "now": 90.0,
                          "detail": "fare dropped"})
        self.assertEqual(len(changes), 1)
        self.assertIn("[injector]", changes[0].detail)
        self.assertEqual(changes[0].kind, "PRICE")
        self.assertEqual(changes[0].was, 100.0)
        self.assertEqual(changes[0].now, 90.0)

    def test_inject_gone(self):
        changes = inject({"kind": "GONE", "node_key": "test#0",
                          "detail": "route cancelled"})
        self.assertEqual(changes[0].kind, "GONE")
        self.assertIn("[injector]", changes[0].detail)


class TestExecutorAftercare(unittest.TestCase):
    """reshop_order executes with no confirmation; cancel_order refuses."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()

    def _book_trip(self):
        out_nodes, _ = search_nodes(
            self.client, "outbound", "SIN", "DPS", "20260918", 2)
        ret_nodes, _ = search_nodes(
            self.client, "inbound", "DPS", "SIN", "20260922", 2)
        trip = build_chain(
            [out_nodes[0], ret_nodes[0]],
            party_size=2, destination_name="Bali")
        return trip, out_nodes, ret_nodes

    def test_reshop_order_autonomous_no_confirmation(self):
        """reshop_order executes with no confirmation — it's read-only."""
        mandate = Mandate(1000.0)
        executor = Executor(mandate)  # no confirmation

        proposal = ActionProposal(
            action="reshop_order", target="test-trip",
            reason="periodic reshop check", cost_refs=())

        result = executor.execute(proposal, {"adults": 2, "legs": 0})
        self.assertTrue(result.accepted,
                        "reshop_order should execute without confirmation: %s"
                        % result.reason)

    def test_cancel_order_requires_confirmation(self):
        """cancel_order refuses without a standing confirmation."""
        trip, out_nodes, ret_nodes = self._book_trip()
        mandate = Mandate(1000.0)
        mandate.reserve(trip.group_total)
        executor = Executor(mandate)  # no confirmation
        _simulate_payment(executor, mandate, trip.group_total)

        proposal = ActionProposal(
            action="cancel_order", target=trip.key,
            reason="group cancelled trip",
            cost_refs=tuple(leg.price_ref for leg in trip.legs))

        result = executor.execute(proposal, {"adults": 2, "legs": 2})
        self.assertFalse(result.accepted,
                         "cancel_order should refuse without confirmation")
        self.assertIn("confirmation", result.stage)

    def test_change_order_requires_confirmation(self):
        """change_order refuses without a standing confirmation."""
        trip, out_nodes, ret_nodes = self._book_trip()
        mandate = Mandate(1000.0)
        mandate.reserve(trip.group_total)
        executor = Executor(mandate)
        _simulate_payment(executor, mandate, trip.group_total)

        proposal = ActionProposal(
            action="change_order", target=trip.key,
            reason="cheaper routing found",
            cost_refs=tuple(leg.price_ref for leg in trip.legs))

        result = executor.execute(proposal, {"adults": 2, "legs": 2})
        self.assertFalse(result.accepted)
        self.assertIn("confirmation", result.stage)

    def test_three_mutating_actions_record_stub(self):
        """change_order, cancel_order, refund_order all record executed_stub."""
        trip, out_nodes, ret_nodes = self._book_trip()
        cost_refs = tuple(leg.price_ref for leg in trip.legs)

        confirmation = Confirmation(
            action="change_order", target=trip.key,
            approved_by="operator", at="2026-09-19",
            price_shown=round(trip.group_total / trip.party_size, 2))

        for action in ("change_order", "cancel_order", "refund_order"):
            mandate = Mandate(1000.0)
            mandate.reserve(trip.group_total)
            log = DecisionLog()
            executor = Executor(mandate, confirmation=confirmation, log=log)
            _simulate_payment(executor, mandate, trip.group_total)

            proposal = ActionProposal(
                action=action, target=trip.key,
                reason="test %s" % action, cost_refs=cost_refs)

            result = executor.execute(proposal, {"adults": 2, "legs": 2})
            self.assertTrue(result.accepted,
                            "%s should be accepted with confirmation" % action)
            self.assertIn("stub", result.reason.lower(),
                          "%s should record executed_stub" % action)

    def tearDown(self):
        response_cache.clear()


class TestSettleDifference(unittest.TestCase):
    """Post-settlement accounting — the DIFFERENCE, not the total."""

    def test_cheaper_repair_credits(self):
        m = Mandate(1000.0)
        executor = Executor(m)
        m.reserve(500.0)
        _simulate_payment(executor, m, 500.0)

        outcome = settle_difference(executor, 250.0, 230.0, 2)
        self.assertTrue(outcome.accepted)
        self.assertGreater(outcome.credit, 0)
        self.assertLess(outcome.difference, 0)
        self.assertEqual(executor.credits, outcome.credit,
                         "the refund is recorded as owed, never spent")

    def test_dearer_repair_vetoed_when_over_authority(self):
        m = Mandate(500.0)
        executor = Executor(m)
        m.reserve(500.0)
        _simulate_payment(executor, m, 500.0)
        # remaining is now 0.0

        outcome = settle_difference(executor, 250.0, 260.0, 2)
        # diff = (260-250)*2 = 20.0 > remaining 0.0
        self.assertFalse(outcome.accepted)

    def test_dearer_repair_vetoed_by_ceiling(self):
        m = Mandate(1000.0)
        executor = Executor(m)
        m.reserve(500.0)
        _simulate_payment(executor, m, 500.0)

        ceiling = Ceiling(member="Alice", amount=255.0)
        # new_per_person=260 > ceiling=255
        outcome = settle_difference(executor, 250.0, 260.0, 2,
                                    ceilings=[ceiling])
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.vetoed_by, "Alice")

    def test_no_difference_accepted(self):
        m = Mandate(1000.0)
        executor = Executor(m)
        outcome = settle_difference(executor, 250.0, 250.0, 2)
        self.assertTrue(outcome.accepted)
        self.assertEqual(outcome.difference, 0.0)


if __name__ == "__main__":
    unittest.main()
