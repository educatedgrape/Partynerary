"""Phase 5 — ItineraryGraph tests.

Verifies the graph structure: N-leg support, dependency derivation,
min_seats, group_total, and identity keys.
"""

import unittest
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.itinerary.nodes import FlightNode
from src.itinerary.graph import (
    ItineraryGraph, build_chain, Dependency,
    PLACE, TEMPORAL, DURATION,
)


def _make_node(role="outbound", origin="SIN", destination="DPS",
               date="20260918", price=50.0, tax=10.0, fee=0.0,
               seats=9, cache_key="test", index=0):
    return FlightNode(
        role=role, origin=origin, destination=destination,
        date=date, cache_key=cache_key, routing_index=index,
        flight_numbers=("TR560",), carriers=("TR",),
        elapsed_hours=3.17, adult_price=price, adult_tax=tax,
        transaction_fee=fee, min_seat_count=seats,
        price_ref="%s#routings[%d].adultPrice" % (cache_key, index),
        tax_ref="%s#routings[%d].adultTax" % (cache_key, index),
        fee_ref="%s#routings[%d].transactionFee" % (cache_key, index),
    )


class TestGraphStructure(unittest.TestCase):
    """ItineraryGraph basic properties."""

    def test_two_leg_roundtrip(self):
        out = _make_node("outbound", "SIN", "DPS", "20260918")
        ret = _make_node("inbound", "DPS", "SIN", "20260922")
        g = build_chain([out, ret])
        self.assertEqual(len(g.legs), 2)
        self.assertFalse(g.is_chain)
        self.assertEqual(g.outbound, out)
        self.assertEqual(g.inbound, ret)

    def test_three_leg_chain(self):
        leg1 = _make_node("outbound", "SIN", "BKK", "20260918")
        leg2 = _make_node("stopover", "BKK", "DPS", "20260920")
        leg3 = _make_node("inbound", "DPS", "SIN", "20260924")
        g = build_chain([leg1, leg2, leg3])
        self.assertTrue(g.is_chain)
        self.assertEqual(len(g.legs), 3)

    def test_below_two_legs_raises(self):
        leg = _make_node("outbound")
        with self.assertRaises(ValueError):
            build_chain([leg])

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            build_chain([])


class TestDependencies(unittest.TestCase):
    """Dependency derivation between consecutive legs."""

    def test_two_leg_derives_3_deps(self):
        """A two-leg trip derives 3 dependencies (PLACE, TEMPORAL, DURATION)."""
        out = _make_node("outbound", "SIN", "DPS", "20260918")
        ret = _make_node("inbound", "DPS", "SIN", "20260922")
        g = build_chain([out, ret])
        self.assertEqual(len(g.dependencies), 3)

    def test_three_leg_derives_6_deps(self):
        """A three-leg chain derives 6 dependencies (3 per adjacent pair)."""
        leg1 = _make_node("outbound", "SIN", "BKK", "20260918")
        leg2 = _make_node("stopover", "BKK", "DPS", "20260920")
        leg3 = _make_node("inbound", "DPS", "SIN", "20260924")
        g = build_chain([leg1, leg2, leg3])
        self.assertEqual(len(g.dependencies), 6)

    def test_temporal_violation(self):
        """A chain where the middle leg departs before the first lands."""
        leg1 = _make_node("outbound", "SIN", "BKK", "20260920")
        leg2 = _make_node("stopover", "BKK", "DPS", "20260918", index=1)
        leg3 = _make_node("inbound", "DPS", "SIN", "20260924", index=2)
        g = build_chain([leg1, leg2, leg3])
        temporal_deps = [d for d in g.dependencies if d.kind == TEMPORAL]
        violated = [d for d in temporal_deps if not d.satisfied]
        self.assertGreater(len(violated), 0,
                           "Expected a TEMPORAL violation")

    def test_place_violation(self):
        """Arrival city of leg N != departure city of leg N+1."""
        leg1 = _make_node("outbound", "SIN", "BKK", "20260918")
        leg2 = _make_node("inbound", "HKT", "SIN", "20260922", index=1)
        g = build_chain([leg1, leg2])
        place_deps = [d for d in g.dependencies if d.kind == PLACE]
        violated = [d for d in place_deps if not d.satisfied]
        self.assertGreater(len(violated), 0,
                           "Expected a PLACE violation")

    def test_valid_trip_is_feasible(self):
        out = _make_node("outbound", "SIN", "DPS", "20260918")
        ret = _make_node("inbound", "DPS", "SIN", "20260922")
        g = build_chain([out, ret], party_size=1)
        self.assertTrue(g.feasible)


class TestMinSeats(unittest.TestCase):
    """min_seats is MIN across all legs."""

    def test_min_across_two_legs(self):
        out = _make_node("outbound", seats=9)
        ret = _make_node("inbound", "DPS", "SIN", "20260922", seats=3, index=1)
        g = build_chain([out, ret])
        self.assertEqual(g.min_seats, 3)

    def test_min_across_three_legs(self):
        leg1 = _make_node("outbound", "SIN", "BKK", "20260918", seats=12)
        leg2 = _make_node("stopover", "BKK", "DPS", "20260920", seats=2, index=1)
        leg3 = _make_node("inbound", "DPS", "SIN", "20260924", seats=8, index=2)
        g = build_chain([leg1, leg2, leg3])
        self.assertEqual(g.min_seats, 2)

    def test_insufficient_seats_not_feasible(self):
        out = _make_node("outbound", seats=5)
        ret = _make_node("inbound", "DPS", "SIN", "20260922", seats=5, index=1)
        g = build_chain([out, ret], party_size=6)
        self.assertFalse(g.feasible,
                         "Party of 6 can't be seated with 5 seats")


class TestGroupTotal(unittest.TestCase):
    """group_total equals the sum of every leg's group total."""

    def test_two_leg_group_total(self):
        out = _make_node("outbound", price=50.0, tax=10.0, fee=0.0)
        ret = _make_node("inbound", "DPS", "SIN", "20260922",
                         price=60.0, tax=15.0, fee=2.0, index=1)
        g = build_chain([out, ret], party_size=2)
        # Out: (50+10)*2 + 0 = 120
        # Ret: (60+15)*2 + 2 = 152
        expected = 120.0 + 152.0
        self.assertAlmostEqual(g.group_total, expected, places=2)

    def test_per_person_sum(self):
        out = _make_node("outbound", price=50.0, tax=10.0, fee=0.0)
        ret = _make_node("inbound", "DPS", "SIN", "20260922",
                         price=60.0, tax=15.0, fee=2.0, index=1)
        g = build_chain([out, ret])
        expected = (50 + 10) + (60 + 15)
        self.assertAlmostEqual(g.per_person, expected, places=2)


class TestIdentityKey(unittest.TestCase):
    """Two trips with the same derived key but different fares have
    different identity keys."""

    def test_different_fares_different_keys(self):
        out1 = _make_node("outbound", price=50.0, index=0)
        ret1 = _make_node("inbound", "DPS", "SIN", "20260922", price=60.0, index=0)
        g1 = build_chain([out1, ret1])

        out2 = _make_node("outbound", price=52.0, index=1)
        ret2 = _make_node("inbound", "DPS", "SIN", "20260922", price=60.0, index=1)
        g2 = build_chain([out2, ret2])

        self.assertNotEqual(g1.key, g2.key,
                            "Same derived key, different fares → different identity")

    def test_cost_refs_per_leg(self):
        out = _make_node("outbound")
        ret = _make_node("inbound", "DPS", "SIN", "20260922", index=1)
        g = build_chain([out, ret])
        # 3 refs per leg × 2 legs = 6 refs total
        self.assertEqual(len(g.cost_refs), 6)


class TestAsDict(unittest.TestCase):
    """as_dict() produces a valid serialisable dict."""

    def test_has_required_fields(self):
        out = _make_node("outbound")
        ret = _make_node("inbound", "DPS", "SIN", "20260922")
        g = build_chain([out, ret], party_size=2, destination_name="Bali")
        d = g.as_dict()
        for key in ("key", "destination", "per_person", "group_total",
                     "min_seats", "feasible", "legs", "dependencies",
                     "cost_refs"):
            self.assertIn(key, d, "as_dict missing %r" % key)

    def test_legs_have_refs(self):
        out = _make_node("outbound")
        ret = _make_node("inbound", "DPS", "SIN", "20260922")
        g = build_chain([out, ret])
        d = g.as_dict()
        for leg in d["legs"]:
            self.assertIn("price_ref", leg)


if __name__ == "__main__":
    unittest.main()
