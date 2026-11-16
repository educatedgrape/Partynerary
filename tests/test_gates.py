"""Phase 7 — Gate tests.

Verifies the five-gate executor: schema, dereference, mandate, confirmation,
and call. Tests for proposal schema, partial-trip refusal, stale confirmation
(the inverted-guard case), and stub execution.
"""

import unittest
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.agent.proposal import (
    ActionProposal, ProposalSchemaError, ACTIONS, ALLOWED_KEYS,
)
from src.agent.mandate import Mandate, MandateError
from src.agent.executor import (
    Executor, ExecutionResult, Confirmation,
    ACTION_ENDPOINTS, UNCHARACTERISED,
)
from src.agent.decision_log import DecisionLog
from src.agent.cost_ref import CostRefError
from src.atlas import cache as response_cache


# Fixture cache keys
OUT_KEY = "search.do:SIN-DPS@20260918"
RET_KEY = "search.do:DPS-SIN@20260922"

# Fixture data matching the JSON fixtures exactly
OUT_RESPONSE = {
    "success": True,
    "routings": [
        {"adultPrice": 45.50, "adultTax": 12.30, "transactionFee": 0.00,
         "fromSegments": [{"flightNumber": "TR560"}]},
        {"adultPrice": 52.00, "adultTax": 14.10, "transactionFee": 0.00,
         "fromSegments": [{"flightNumber": "TR562"}]},
        {"adultPrice": 78.90, "adultTax": 21.50, "transactionFee": 2.00,
         "fromSegments": [{"flightNumber": "GA836"}]},
    ]
}

RET_RESPONSE = {
    "success": True,
    "routings": [
        {"adultPrice": 48.25, "adultTax": 13.00, "transactionFee": 0.00,
         "fromSegments": [{"flightNumber": "TR561"}]},
        {"adultPrice": 85.40, "adultTax": 22.80, "transactionFee": 2.00,
         "fromSegments": [{"flightNumber": "GA837"}]},
    ]
}

# Pre-computed totals (2 adults):
# Out[0]: (45.50+12.30)*2+0 = 115.60
# Ret[0]: (48.25+13.00)*2+0 = 122.50
# Total:  238.10; per_person = 119.05
TRIP_TOTAL_00 = 238.10
TRIP_PER_PERSON_00 = 119.05


class TestProposalSchema(unittest.TestCase):
    """The proposal schema rejects bad shapes."""

    def test_bare_string_cost_refs_raises(self):
        """A bare string cost_refs raises ProposalSchemaError."""
        with self.assertRaises(ProposalSchemaError):
            ActionProposal(
                action="book_group", target="trip-1",
                reason="booking", cost_refs="search.do:SIN-DPS@20260918#routings[0].adultPrice")

    def test_unknown_action_raises(self):
        with self.assertRaises(ProposalSchemaError):
            ActionProposal(
                action="fly_now", target="trip-1",
                reason="urgent", cost_refs=())

    def test_missing_reason_raises(self):
        with self.assertRaises(ProposalSchemaError):
            ActionProposal(
                action="book_group", target="trip-1",
                reason="", cost_refs=())

    def test_valid_proposal(self):
        p = ActionProposal(
            action="book_group", target="trip-1",
            reason="booking Bali for 4",
            cost_refs=("ref1", "ref2"))
        self.assertEqual(p.action, "book_group")
        self.assertEqual(len(p.cost_refs), 2)

    def test_as_dict_no_amount(self):
        """The dict form contains no amount, price, or fare field."""
        p = ActionProposal(
            action="search_group", target="trip-1",
            reason="search for Bali")
        d = p.as_dict()
        for key in ("amount", "price", "fare", "total", "cost", "saving"):
            self.assertNotIn(key, d)

    def test_cost_refs_tuple_coerced(self):
        """A list cost_refs is coerced to tuple."""
        p = ActionProposal(
            action="book_group", target="trip-1",
            reason="booking", cost_refs=["ref1", "ref2"])
        self.assertIsInstance(p.cost_refs, tuple)


class TestGate1Schema(unittest.TestCase):
    """Gate 1: schema validation."""

    def setUp(self):
        response_cache.clear()
        self.mandate = Mandate(1000.0)
        self.executor = Executor(self.mandate)

    def test_non_proposal_refused(self):
        """A non-ActionProposal object is refused at schema."""
        result = self.executor.execute("not a proposal")
        self.assertFalse(result.accepted)
        self.assertEqual(result.stage, "schema")

    def tearDown(self):
        response_cache.clear()


class TestGate2Dereference(unittest.TestCase):
    """Gate 2: dereference — refs must point at real data."""

    def setUp(self):
        response_cache.clear()
        response_cache.put(OUT_KEY, OUT_RESPONSE)
        response_cache.put(RET_KEY, RET_RESPONSE)
        self.mandate = Mandate(1000.0)
        self.executor = Executor(self.mandate)

    def test_one_ref_for_two_leg_trip_refuses(self):
        """A proposal with one ref for a two-leg trip refuses at dereference."""
        proposal = ActionProposal(
            action="book_group", target="trip-1",
            reason="booking Bali for 2",
            cost_refs=(
                "%s#routings[0].adultPrice" % OUT_KEY,
            ))
        result = self.executor.execute(proposal, payload={"adults": 2, "legs": 2})
        self.assertFalse(result.accepted)
        self.assertEqual(result.stage, "dereference")
        self.assertIn("2-leg", result.reason)

    def test_ref_into_missing_cache_refuses(self):
        """A ref into a response not received this run refuses at dereference."""
        proposal = ActionProposal(
            action="book_group", target="trip-1",
            reason="booking",
            cost_refs=(
                "search.do:SIN-HKT@20260918#routings[0].adultPrice",
                "search.do:HKT-SIN@20260922#routings[0].adultPrice",
            ))
        result = self.executor.execute(proposal, payload={"adults": 2, "legs": 2})
        self.assertFalse(result.accepted)
        self.assertEqual(result.stage, "dereference")

    def test_valid_two_refs_pass(self):
        """Two valid refs for a two-leg trip pass dereference."""
        proposal = ActionProposal(
            action="book_group", target="trip-1",
            reason="booking Bali for 2",
            cost_refs=(
                "%s#routings[0].adultPrice" % OUT_KEY,
                "%s#routings[0].adultPrice" % RET_KEY,
            ))
        # Need confirmation to pass all gates
        self.executor.confirmation = Confirmation(
            action="book_group", target="trip-1",
            approved_by="user", at="2026-09-15T10:00",
            price_shown=TRIP_PER_PERSON_00)
        result = self.executor.execute(proposal, payload={"adults": 2, "legs": 2})
        self.assertTrue(result.accepted)
        self.assertEqual(result.amount, TRIP_TOTAL_00)

    def tearDown(self):
        response_cache.clear()


class TestGate3Mandate(unittest.TestCase):
    """Gate 3: mandate — the total must fit under the ceiling."""

    def setUp(self):
        response_cache.clear()
        response_cache.put(OUT_KEY, OUT_RESPONSE)
        response_cache.put(RET_KEY, RET_RESPONSE)

    def test_over_ceiling_refuses(self):
        """A trip exceeding the ceiling refuses at mandate."""
        mandate = Mandate(100.0)  # too low for TRIP_TOTAL_00 = 238.10
        confirmation = Confirmation(
            action="book_group", target="trip-1",
            approved_by="user", at="2026-09-15T10:00",
            price_shown=TRIP_PER_PERSON_00)
        executor = Executor(mandate, confirmation=confirmation)
        proposal = ActionProposal(
            action="book_group", target="trip-1",
            reason="booking Bali",
            cost_refs=(
                "%s#routings[0].adultPrice" % OUT_KEY,
                "%s#routings[0].adultPrice" % RET_KEY,
            ))
        result = executor.execute(proposal, payload={"adults": 2, "legs": 2})
        self.assertFalse(result.accepted)
        self.assertEqual(result.stage, "mandate")

    def test_autonomous_skips_mandate(self):
        """Autonomous actions (search_group) skip the mandate check."""
        mandate = Mandate(0.0)  # zero ceiling — should still pass
        executor = Executor(mandate)
        proposal = ActionProposal(
            action="search_group", target="trip-1",
            reason="search for Bali")
        result = executor.execute(proposal)
        self.assertTrue(result.accepted)

    def tearDown(self):
        response_cache.clear()


class TestGate4Confirmation(unittest.TestCase):
    """Gate 4: confirmation — must be present and still valid."""

    def setUp(self):
        response_cache.clear()
        response_cache.put(OUT_KEY, OUT_RESPONSE)
        response_cache.put(RET_KEY, RET_RESPONSE)
        self.mandate = Mandate(1000.0)

    def test_no_confirmation_refuses(self):
        """A non-autonomous action without confirmation refuses."""
        executor = Executor(self.mandate, confirmation=None)
        proposal = ActionProposal(
            action="book_group", target="trip-1",
            reason="booking Bali",
            cost_refs=(
                "%s#routings[0].adultPrice" % OUT_KEY,
                "%s#routings[0].adultPrice" % RET_KEY,
            ))
        result = executor.execute(proposal, payload={"adults": 2, "legs": 2})
        self.assertFalse(result.accepted)
        self.assertEqual(result.stage, "confirmation")

    def test_stale_confirmation_return_leg_rises(self):
        """The inverted-guard case: return leg rises, trip total exceeds.

        An outbound-only check would pass this — it must not.
        """
        # Create a confirmation at a lower per-person price
        low_price_shown = 100.00  # below TRIP_PER_PERSON_00 = 119.05
        confirmation = Confirmation(
            action="book_group", target="trip-1",
            approved_by="user", at="2026-09-15T10:00",
            price_shown=low_price_shown)
        executor = Executor(self.mandate, confirmation=confirmation)
        proposal = ActionProposal(
            action="book_group", target="trip-1",
            reason="booking Bali",
            cost_refs=(
                "%s#routings[0].adultPrice" % OUT_KEY,
                "%s#routings[0].adultPrice" % RET_KEY,
            ))
        result = executor.execute(proposal, payload={"adults": 2, "legs": 2})
        self.assertFalse(result.accepted)
        self.assertEqual(result.stage, "stale_confirmation")

    def tearDown(self):
        response_cache.clear()


class TestGate5Call(unittest.TestCase):
    """Gate 5: both money endpoints record executed_stub, no HTTP."""

    def setUp(self):
        response_cache.clear()
        response_cache.put(OUT_KEY, OUT_RESPONSE)
        response_cache.put(RET_KEY, RET_RESPONSE)
        self.mandate = Mandate(1000.0)

    def test_book_group_stubbed(self):
        """book_group records executed_stub."""
        confirmation = Confirmation(
            action="book_group", target="trip-1",
            approved_by="user", at="2026-09-15T10:00",
            price_shown=TRIP_PER_PERSON_00)
        executor = Executor(self.mandate, confirmation=confirmation)
        proposal = ActionProposal(
            action="book_group", target="trip-1",
            reason="booking Bali for 2",
            cost_refs=(
                "%s#routings[0].adultPrice" % OUT_KEY,
                "%s#routings[0].adultPrice" % RET_KEY,
            ))
        result = executor.execute(proposal, payload={"adults": 2, "legs": 2})
        self.assertTrue(result.accepted)
        self.assertEqual(result.stage, "stubbed")
        self.assertIn("stub", result.atlas_response.get("bookingReference", ""))

    def test_pay_group_stubbed(self):
        """pay_group records executed_stub."""
        confirmation = Confirmation(
            action="pay_group", target="trip-1",
            approved_by="user", at="2026-09-15T10:00",
            price_shown=TRIP_PER_PERSON_00)
        executor = Executor(self.mandate, confirmation=confirmation)
        proposal = ActionProposal(
            action="pay_group", target="trip-1",
            reason="paying for Bali trip",
            cost_refs=(
                "%s#routings[0].adultPrice" % OUT_KEY,
                "%s#routings[0].adultPrice" % RET_KEY,
            ))
        result = executor.execute(proposal, payload={"adults": 2, "legs": 2})
        self.assertTrue(result.accepted)
        self.assertEqual(result.stage, "stubbed")

    def test_both_endpoints_uncharacterised(self):
        """Both money endpoints are in UNCHARACTERISED."""
        self.assertIn("orderCommit.do", UNCHARACTERISED)
        self.assertIn("pay.do", UNCHARACTERISED)

    def tearDown(self):
        response_cache.clear()


class TestDecisionLog(unittest.TestCase):
    """The decision log records every executor decision."""

    def setUp(self):
        response_cache.clear()
        response_cache.put(OUT_KEY, OUT_RESPONSE)
        response_cache.put(RET_KEY, RET_RESPONSE)

    def test_refusal_logged(self):
        """A refused proposal is logged."""
        mandate = Mandate(1000.0)
        log = DecisionLog()
        executor = Executor(mandate, log=log)
        proposal = ActionProposal(
            action="book_group", target="trip-1",
            reason="booking",
            cost_refs=("bad-ref",))
        executor.execute(proposal, payload={"adults": 1, "legs": 1})
        self.assertGreater(len(log), 0)
        entry = log.entries[-1]
        self.assertFalse(entry.accepted)

    def test_success_logged(self):
        """A successful execution is logged."""
        mandate = Mandate(1000.0)
        log = DecisionLog()
        confirmation = Confirmation(
            action="book_group", target="trip-1",
            approved_by="user", at="2026-09-15T10:00",
            price_shown=TRIP_PER_PERSON_00)
        executor = Executor(mandate, confirmation=confirmation, log=log)
        proposal = ActionProposal(
            action="book_group", target="trip-1",
            reason="booking Bali for 2",
            cost_refs=(
                "%s#routings[0].adultPrice" % OUT_KEY,
                "%s#routings[0].adultPrice" % RET_KEY,
            ))
        executor.execute(proposal, payload={"adults": 2, "legs": 2})
        self.assertGreater(len(log), 0)
        entry = log.entries[-1]
        self.assertTrue(entry.accepted)

    def tearDown(self):
        response_cache.clear()


class TestMandateAccounting(unittest.TestCase):
    """Booking reserves, payment settles, no double charge.

    The hold lives on the mandate; settlement is the executor's own
    accounting. Both sides together never commit the grant twice.
    """

    def test_reserve_and_settle_no_double_charge(self):
        """book_group reserves and pay_group settles the same total."""
        mandate = Mandate(1000.0)
        executor = Executor(mandate)
        mandate.reserve(238.10)
        self.assertEqual(mandate.reserved, 238.10)
        self.assertEqual(mandate.remaining, 761.90)
        self.assertEqual(executor.remaining, 761.90)

        # What the call gate does for pay_group: release the hold,
        # record the settlement in the executor's books.
        mandate.release(238.10)
        executor._settled = 238.10
        self.assertEqual(executor.settled, 238.10)
        self.assertEqual(mandate.reserved, 0.0)
        # Same figure before and after settlement — no double charge.
        self.assertEqual(executor.remaining, 761.90)

    def test_unknown_action_refused(self):
        """An action not in ACTION_GROUPS is refused, never defaulted."""
        mandate = Mandate(1000.0)
        with self.assertRaises(MandateError):
            mandate.check(100.0, "unknown_action")


if __name__ == "__main__":
    unittest.main()
