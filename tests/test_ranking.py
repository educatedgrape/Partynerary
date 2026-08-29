"""Phase 5 — Ranking tests.

Verifies fare evaluation: scoring, ceiling filtering, identity-based indexing,
and the no-seatCount-scoring guarantee.
"""

import unittest
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests.helpers import make_client
from src.discovery.sweep import sweep
from src.discovery.score import (
    score_trip, score_sweep, apply_ceilings, ScoredTrip, WEIGHTS,
)
from src.itinerary.nodes import FlightNode
from src.itinerary.graph import build_chain
from src.atlas.cache import clear as clear_cache


def _make_trip(destination="DPS", price_out=50.0, price_ret=60.0,
               vibe=0.8, party_size=1, out_index=0, ret_index=0):
    out = FlightNode(
        role="outbound", origin="SIN", destination=destination,
        date="20260918", cache_key="search.do:SIN-%s@20260918" % destination,
        routing_index=out_index, flight_numbers=("TR560",), carriers=("TR",),
        elapsed_hours=3.17, adult_price=price_out, adult_tax=10.0,
        transaction_fee=0.0, min_seat_count=9,
        price_ref="search.do:SIN-%s@20260918#routings[0].adultPrice" % destination,
        tax_ref="search.do:SIN-%s@20260918#routings[0].adultTax" % destination,
        fee_ref="search.do:SIN-%s@20260918#routings[0].transactionFee" % destination,
    )
    ret = FlightNode(
        role="inbound", origin=destination, destination="SIN",
        date="20260922", cache_key="search.do:%s-SIN@20260922" % destination,
        routing_index=ret_index, flight_numbers=("TR561",), carriers=("TR",),
        elapsed_hours=3.17, adult_price=price_ret, adult_tax=13.0,
        transaction_fee=0.0, min_seat_count=7,
        price_ref="search.do:%s-SIN@20260922#routings[0].adultPrice" % destination,
        tax_ref="search.do:%s-SIN@20260922#routings[0].adultTax" % destination,
        fee_ref="search.do:%s-SIN@20260922#routings[0].transactionFee" % destination,
    )
    return build_chain([out, ret], party_size=party_size,
                       destination_name=destination, vibe_score=vibe)


class TestScoreTrip(unittest.TestCase):
    """score_trip produces comparators and a rank_score."""

    def test_returns_scored_trip(self):
        trip = _make_trip()
        st = score_trip(trip, median_fare=120.0)
        self.assertIsInstance(st, ScoredTrip)
        self.assertGreater(st.rank_score, 0)

    def test_comparators_present(self):
        trip = _make_trip()
        st = score_trip(trip, median_fare=120.0)
        self.assertIn("median_fare_today", st.comparators)
        self.assertIn("vs_median", st.comparators)
        self.assertIn("seats_left", st.comparators)
        self.assertIn("vibeScore", st.comparators)

    def test_cheaper_than_median_positive_vs(self):
        trip = _make_trip(price_out=30.0, price_ret=30.0)
        st = score_trip(trip, median_fare=120.0)
        self.assertGreater(st.comparators["vs_median"], 0)

    def test_dearer_than_median_negative_vs(self):
        trip = _make_trip(price_out=100.0, price_ret=100.0)
        st = score_trip(trip, median_fare=120.0)
        self.assertLess(st.comparators["vs_median"], 0)


class TestScoreSweep(unittest.TestCase):
    """score_sweep ranks the entire sweep."""

    def test_returns_sorted(self):
        trips = [
            _make_trip("DPS", price_out=50.0, price_ret=60.0, vibe=0.8),
            _make_trip("BKK", price_out=40.0, price_ret=45.0, vibe=0.9),
        ]
        scored = score_sweep(trips)
        self.assertEqual(len(scored), 2)
        # Higher score first
        self.assertGreaterEqual(scored[0].rank_score, scored[1].rank_score)

    def test_empty_input(self):
        self.assertEqual(score_sweep([]), [])

    def test_with_ceilings(self):
        trips = [
            _make_trip("DPS", price_out=50.0, price_ret=60.0, vibe=0.8),
        ]
        scored = score_sweep(trips, ceilings=[200.0])
        self.assertEqual(len(scored), 1)
        self.assertIn("headroom_vs_tightest_ceiling", scored[0].comparators)


class TestIdentityScoring(unittest.TestCase):
    """Two trips with the same derived key but different fares score differently."""

    def test_different_fares_different_scores(self):
        trip1 = _make_trip("DPS", price_out=45.50, price_ret=48.25, vibe=0.8)
        trip2 = _make_trip("DPS", price_out=78.90, price_ret=85.40, vibe=0.8)
        st1 = score_trip(trip1, median_fare=120.0)
        st2 = score_trip(trip2, median_fare=120.0)
        self.assertNotEqual(st1.rank_score, st2.rank_score,
                            "Same destination, different fares → different scores")

    def test_identity_keys_differ(self):
        trip1 = _make_trip("DPS", price_out=45.50, price_ret=48.25, out_index=0, ret_index=0)
        trip2 = _make_trip("DPS", price_out=78.90, price_ret=85.40, out_index=1, ret_index=1)
        self.assertNotEqual(trip1.key, trip2.key)


class TestCeilingFilter(unittest.TestCase):
    """Ceiling is a hard filter — trips above the tightest ceiling are vetoed."""

    def test_ceiling_vetoes_expensive(self):
        cheap = _make_trip("DPS", price_out=30.0, price_ret=30.0, vibe=0.8)
        dear = _make_trip("BKK", price_out=100.0, price_ret=100.0, vibe=0.9)
        scored = score_sweep([cheap, dear])
        survivors, vetoed = apply_ceilings(scored, ceilings=[100.0])
        # cheap: (30+10) + (30+13) = 83 per_person → survives
        # dear: (100+10) + (100+13) = 223 per_person → vetoed
        self.assertGreater(len(survivors), 0)
        self.assertGreater(len(vetoed), 0)

    def test_headroom_measured_against_tightest(self):
        """headroom is against the TIGHTEST ceiling, not the mean."""
        trip = _make_trip("DPS", price_out=50.0, price_ret=60.0, vibe=0.8)
        scored = score_sweep([trip], ceilings=[150.0, 300.0])
        # Tightest is 150.0
        st = scored[0]
        headroom = st.comparators.get("headroom_vs_tightest_ceiling")
        self.assertIsNotNone(headroom)
        # per_person = (50+10) + (60+13) = 133
        # headroom = 150 - 133 = 17
        self.assertAlmostEqual(headroom, 17.0, places=0)

    def test_no_ceilings_no_veto(self):
        trip = _make_trip("DPS")
        scored = score_sweep([trip])
        survivors, vetoed = apply_ceilings(scored, ceilings=None)
        self.assertEqual(len(survivors), len(scored))
        self.assertEqual(len(vetoed), 0)

    def test_vetoed_carries_gap_info(self):
        trip = _make_trip("DPS", price_out=100.0, price_ret=100.0, vibe=0.8)
        scored = score_sweep([trip])
        _, vetoed = apply_ceilings(scored, ceilings=[50.0])
        self.assertEqual(len(vetoed), 1)
        v = vetoed[0]
        self.assertIn("destination", v)
        self.assertIn("per_person", v)
        self.assertIn("tightest_ceiling", v)
        self.assertIn("over_by", v)
        self.assertGreater(v["over_by"], 0)


class TestNoSeatCountScoring(unittest.TestCase):
    """seatCount is displayed but never scored."""

    def test_seats_left_in_comparators_not_score(self):
        """seats_left is in comparators, not in the scoring formula."""
        trip = _make_trip("DPS", price_out=50.0, price_ret=60.0, vibe=0.8)
        st = score_trip(trip, median_fare=120.0)
        # seats_left is displayed
        self.assertIn("seats_left", st.comparators)
        # But the scoring weights don't include it
        self.assertNotIn("seats", WEIGHTS)
        self.assertNotIn("scarcity", WEIGHTS)


class TestLiveSweepRanking(unittest.TestCase):
    """Integration: sweep from fixtures, score, and verify."""

    def setUp(self):
        clear_cache()

    def test_fixture_sweep_scores(self):
        client = make_client()
        trips, _ = sweep(
            client, "SIN", "20260918", ["20260922"],
            party_size=1, destinations=["DPS"])
        scored = score_sweep(trips)
        self.assertEqual(len(scored), len(trips))
        for st in scored:
            self.assertGreater(st.rank_score, 0)
            self.assertIn("median_fare_today", st.comparators)

    def test_scored_trip_as_dict(self):
        client = make_client()
        trips, _ = sweep(
            client, "SIN", "20260918", ["20260922"],
            party_size=1, destinations=["DPS"])
        scored = score_sweep(trips)
        d = scored[0].as_dict()
        self.assertIn("rank_score", d)
        self.assertIn("comparators", d)


if __name__ == "__main__":
    unittest.main()
