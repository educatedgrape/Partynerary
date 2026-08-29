"""Multi-leg DAG search tests — Option B with real dependency edges.

Covers:
  - 3-leg DAG construction: origin → stopover → final → origin
  - build_chain dependency edges (PLACE, TEMPORAL, DURATION)
  - MultilegOption as_ui_dict() backward compatibility
  - Empty results when Atlas returns nothing for a leg
  - Infeasible graphs filtered out
  - STOPOVER_SWAP dimension in replan.explore()
"""

import os
import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

os.environ.pop("LIVE", None)

from src.atlas import cache as response_cache
from src.atlas.models import fixture_key
from src.itinerary.graph import build_chain, ItineraryGraph, PLACE, TEMPORAL
from src.itinerary.nodes import FlightNode
from src.discovery.multileg import (
    search_multileg_routes, MultilegOption, _add_days, _fmt_date_display,
    hub_candidates,
)
from src.itinerary.replan import explore, STOPOVER_SWAP
from tests.helpers import make_client, TEST_CONFIG


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _make_node(role, origin, dest, date, price=50.0, flight="TR100",
               carrier="TR", segments=None):
    """Build a FlightNode matching what search_nodes returns."""
    if segments is None:
        segments = ({
            "flight_number": flight,
            "dep_airport": origin,
            "arr_airport": dest,
            "dep_time": "%s 08:00" % date,
            "arr_time": "%s 10:00" % date,
            "carrier": carrier,
        },)
    cache_key = fixture_key(origin, dest, date)
    return FlightNode(
        role=role,
        origin=origin,
        destination=dest,
        date=date,
        cache_key=cache_key,
        routing_index=0,
        flight_numbers=(flight,),
        carriers=(carrier,),
        elapsed_hours=2.5,
        adult_price=price,
        adult_tax=price * 0.2,
        transaction_fee=0.0,
        min_seat_count=9,
        price_ref="%s#routings[0].adultPrice" % cache_key,
        tax_ref="%s#routings[0].adultTax" % cache_key,
        fee_ref="%s#routings[0].transactionFee" % cache_key,
        segments=segments,
    )


def _make_atlas_response(flight_number, carrier, price, origin, dest):
    """Build a synthetic Atlas search.do response."""
    return {
        "success": True,
        "routings": [{
            "fromSegments": [{
                "segmentIndex": 1,
                "carrier": carrier,
                "flightNumber": flight_number,
                "depAirport": origin,
                "depTime": "202610100820",
                "arrAirport": dest,
                "arrTime": "202610101055",
                "stopCities": "",
                "duration": 155,
                "codeShare": False,
                "cabin": "M",
                "cabinClass": 1,
                "seatCount": 9,
                "fareFamily": "Fly",
            }],
            "adultPrice": price,
            "adultTax": round(price * 0.2, 2),
            "transactionFee": 0.0,
            "currency": "USD",
            "riskSellout": False,
        }],
    }


def _mock_client_with_routes(route_responses):
    """Create a mock client that returns specific responses per fixture key."""
    client = MagicMock()

    def mock_post(endpoint, payload, fixture_key=None, allow_error=False):
        if fixture_key and fixture_key in route_responses:
            resp = route_responses[fixture_key]
            response_cache.put(fixture_key, resp)
            return resp
        return None

    client.post = MagicMock(side_effect=mock_post)
    return client


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

class TestDateHelpers(unittest.TestCase):

    def test_add_days(self):
        self.assertEqual(_add_days("20261010", 3), "20261013")
        self.assertEqual(_add_days("20261030", 2), "20261101")

    def test_add_days_hyphenated(self):
        self.assertEqual(_add_days("2026-10-10", 3), "20261013")

    def test_fmt_date_display(self):
        self.assertEqual(_fmt_date_display("20261010"), "2026-10-10")


# ---------------------------------------------------------------------------
# DAG construction — 3-leg chain
# ---------------------------------------------------------------------------

class TestMultilegDAG(unittest.TestCase):
    """search_multileg_routes builds real 3-leg ItineraryGraph chains."""

    def setUp(self):
        response_cache.clear()

    def test_returns_multileg_options(self):
        routes = {
            fixture_key("SIN", "HKT", "20261010"):
                _make_atlas_response("TR652", "TR", 45.0, "SIN", "HKT"),
            fixture_key("HKT", "BKK", "20261012"):
                _make_atlas_response("TR201", "TR", 30.0, "HKT", "BKK"),
            fixture_key("BKK", "SIN", "20261014"):
                _make_atlas_response("TR600", "TR", 55.0, "BKK", "SIN"),
        }
        client = _mock_client_with_routes(routes)

        options, errors = search_multileg_routes(
            client, "SIN",
            stopover_candidates=[{"cityId": "HKT", "cityName": "Phuket"}],
            final_dest="BKK",
            out_date="20261010",
            return_dates=["20261014"],
            party_size=2,
            min_stopover_days=2,
        )

        self.assertGreater(len(options), 0)
        opt = options[0]
        self.assertIsInstance(opt, MultilegOption)
        self.assertIsInstance(opt.graph, ItineraryGraph)

    def test_graph_has_three_legs(self):
        routes = {
            fixture_key("SIN", "HKT", "20261010"):
                _make_atlas_response("TR652", "TR", 45.0, "SIN", "HKT"),
            fixture_key("HKT", "BKK", "20261012"):
                _make_atlas_response("TR201", "TR", 30.0, "HKT", "BKK"),
            fixture_key("BKK", "SIN", "20261014"):
                _make_atlas_response("TR600", "TR", 55.0, "BKK", "SIN"),
        }
        client = _mock_client_with_routes(routes)

        options, _ = search_multileg_routes(
            client, "SIN",
            [{"cityId": "HKT", "cityName": "Phuket"}],
            "BKK", "20261010", ["20261014"], 2,
        )

        self.assertGreater(len(options), 0)
        graph = options[0].graph
        self.assertEqual(len(graph.legs), 3)
        self.assertTrue(graph.is_chain)

    def test_graph_has_dependency_edges(self):
        routes = {
            fixture_key("SIN", "HKT", "20261010"):
                _make_atlas_response("TR652", "TR", 45.0, "SIN", "HKT"),
            fixture_key("HKT", "BKK", "20261012"):
                _make_atlas_response("TR201", "TR", 30.0, "HKT", "BKK"),
            fixture_key("BKK", "SIN", "20261014"):
                _make_atlas_response("TR600", "TR", 55.0, "BKK", "SIN"),
        }
        client = _mock_client_with_routes(routes)

        options, _ = search_multileg_routes(
            client, "SIN",
            [{"cityId": "HKT", "cityName": "Phuket"}],
            "BKK", "20261010", ["20261014"], 2,
        )

        graph = options[0].graph
        # 3 legs → 2 pairs → 6 edges (PLACE + TEMPORAL + DURATION × 2)
        self.assertEqual(len(graph.dependencies), 6)
        # Check PLACE edges
        place_edges = [d for d in graph.dependencies if d.kind == PLACE]
        self.assertEqual(len(place_edges), 2)
        # All edges should be satisfied for correct routing
        self.assertTrue(graph.feasible)

    def test_price_is_sum_of_legs(self):
        routes = {
            fixture_key("SIN", "HKT", "20261010"):
                _make_atlas_response("TR652", "TR", 45.0, "SIN", "HKT"),
            fixture_key("HKT", "BKK", "20261012"):
                _make_atlas_response("TR201", "TR", 30.0, "HKT", "BKK"),
            fixture_key("BKK", "SIN", "20261014"):
                _make_atlas_response("TR600", "TR", 55.0, "BKK", "SIN"),
        }
        client = _mock_client_with_routes(routes)

        options, _ = search_multileg_routes(
            client, "SIN",
            [{"cityId": "HKT", "cityName": "Phuket"}],
            "BKK", "20261010", ["20261014"], 2,
        )

        opt = options[0]
        # per_person = (45 + 9) + (30 + 6) + (55 + 11) = 156
        expected = (45.0 + 9.0) + (30.0 + 6.0) + (55.0 + 11.0)
        self.assertAlmostEqual(opt.per_person, expected, places=2)


# ---------------------------------------------------------------------------
# Empty results — leg missing
# ---------------------------------------------------------------------------

class TestMultilegEmpty(unittest.TestCase):
    """When Atlas returns nothing for a leg, that stopover is skipped."""

    def setUp(self):
        response_cache.clear()

    def test_missing_leg2_skips_stopover(self):
        routes = {
            fixture_key("SIN", "HKT", "20261010"):
                _make_atlas_response("TR652", "TR", 45.0, "SIN", "HKT"),
            # No response for HKT→BKK (leg 2)
            fixture_key("BKK", "SIN", "20261014"):
                _make_atlas_response("TR600", "TR", 55.0, "BKK", "SIN"),
        }
        client = _mock_client_with_routes(routes)

        options, errors = search_multileg_routes(
            client, "SIN",
            [{"cityId": "HKT", "cityName": "Phuket"}],
            "BKK", "20261010", ["20261014"], 2,
        )

        self.assertEqual(len(options), 0)
        self.assertGreater(len(errors), 0)

    def test_empty_stopover_candidates(self):
        client = MagicMock()
        options, errors = search_multileg_routes(
            client, "SIN", [], "BKK", "20261010", ["20261014"], 2)
        self.assertEqual(len(options), 0)
        self.assertEqual(len(errors), 0)


# ---------------------------------------------------------------------------
# MultilegOption.as_ui_dict() — backward compatibility
# ---------------------------------------------------------------------------

class TestMultilegOptionUIDict(unittest.TestCase):
    """as_ui_dict() produces dicts compatible with the frontend."""

    def setUp(self):
        response_cache.clear()

    def _make_option(self):
        routes = {
            fixture_key("SIN", "HKT", "20261010"):
                _make_atlas_response("TR652", "TR", 45.0, "SIN", "HKT"),
            fixture_key("HKT", "BKK", "20261012"):
                _make_atlas_response("TR201", "TR", 30.0, "HKT", "BKK"),
            fixture_key("BKK", "SIN", "20261014"):
                _make_atlas_response("TR600", "TR", 55.0, "BKK", "SIN"),
        }
        client = _mock_client_with_routes(routes)
        options, _ = search_multileg_routes(
            client, "SIN",
            [{"cityId": "HKT", "cityName": "Phuket"}],
            "BKK", "20261010", ["20261014"], 2,
        )
        return options[0]

    def test_has_frontend_fields(self):
        opt = self._make_option()
        d = opt.as_ui_dict(direct_per_person=200.0)

        self.assertIn("label", d)
        self.assertIn("stopover", d)
        self.assertIn("final", d)
        self.assertIn("per_person", d)
        self.assertIn("group_total", d)
        self.assertIn("outbound", d)
        self.assertIn("inbound", d)
        self.assertIn("why", d)
        self.assertIn("cost_ref", d)

    def test_stopover_fields(self):
        opt = self._make_option()
        d = opt.as_ui_dict()

        self.assertEqual(d["stopover"]["city_id"], "HKT")
        self.assertEqual(d["stopover"]["name"], "Phuket")
        self.assertEqual(d["stopover"]["days"], 2)

    def test_outbound_has_combined_segments(self):
        opt = self._make_option()
        d = opt.as_ui_dict()

        # Outbound segments should include leg1 + leg2 segments
        segs = d["outbound"]["segments"]
        self.assertGreaterEqual(len(segs), 2)

    def test_graph_metadata_present(self):
        opt = self._make_option()
        d = opt.as_ui_dict()

        self.assertIn("graph_key", d)
        self.assertIn("feasible", d)
        self.assertTrue(d["feasible"])
        self.assertIn("is_chain", d)
        self.assertTrue(d["is_chain"])
        self.assertIn("dependencies", d)
        self.assertIn("cost_refs", d)

    def test_savings_computed(self):
        opt = self._make_option()
        d = opt.as_ui_dict(direct_per_person=200.0)
        # Baseline = separate round trip to the stopover (symmetric-fare
        # fallback: 54 + 54 = 108) + direct round trip (200) = 308,
        # minus the multi-leg loop price (54 + 36 + 66 = 156).
        self.assertGreater(d["savings"], 0)
        self.assertEqual(d["separate_flights_pp"], 308.0)
        self.assertEqual(d["savings"], 152.0)

    def test_savings_uses_stopover_return_fare(self):
        # When the stopover → origin return leg is found, its real fare is
        # used in the separate-flights baseline instead of the fallback.
        routes = {
            fixture_key("SIN", "HKT", "20261010"):
                _make_atlas_response("TR652", "TR", 45.0, "SIN", "HKT"),
            fixture_key("HKT", "BKK", "20261012"):
                _make_atlas_response("TR201", "TR", 30.0, "HKT", "BKK"),
            fixture_key("BKK", "SIN", "20261014"):
                _make_atlas_response("TR600", "TR", 55.0, "BKK", "SIN"),
            fixture_key("HKT", "SIN", "20261012"):
                _make_atlas_response("TR653", "TR", 40.0, "HKT", "SIN"),
        }
        client = _mock_client_with_routes(routes)
        options, _ = search_multileg_routes(
            client, "SIN",
            [{"cityId": "HKT", "cityName": "Phuket"}],
            "BKK", "20261010", ["20261014"], 2,
        )
        opt = options[0]
        # stopover RT = 54 + 48 = 102; separate = 102 + 200 = 302;
        # multi-leg loop = 156 → savings = 146.
        d = opt.as_ui_dict(direct_per_person=200.0)
        self.assertEqual(d["separate_flights_pp"], 302.0)
        self.assertEqual(d["savings"], 146.0)

    def test_carriers_list(self):
        opt = self._make_option()
        d = opt.as_ui_dict()
        self.assertIn("TR", d["carriers"])


# ---------------------------------------------------------------------------
# STOPOVER_SWAP in replan.explore()
# ---------------------------------------------------------------------------

class TestStopoverSwap(unittest.TestCase):
    """explore() with STOPOVER_SWAP dimension for 3-leg chains."""

    def setUp(self):
        response_cache.clear()

    def test_stopover_swap_not_triggered_for_2leg(self):
        """STOPOVER_SWAP only applies to is_chain graphs (3+ legs)."""
        # Build a 2-leg graph (round trip)
        out_node = _make_node("outbound", "SIN", "BKK", "20261010",
                              flight="TR100")
        ret_node = _make_node("inbound", "BKK", "SIN", "20261014",
                              flight="TR200")
        graph = build_chain([out_node, ret_node], party_size=2)
        self.assertFalse(graph.is_chain)

        client = MagicMock()
        alts = explore(
            client, graph, ceilings=[], outbound_date="20261010",
            return_dates=["20261014"], party_size=2,
            stopover_candidates=[{"cityId": "HKT", "cityName": "Phuket"}],
        )

        # No STOPOVER_SWAP alternatives for a 2-leg graph
        stopover_alts = [a for a in alts if a.kind == STOPOVER_SWAP]
        self.assertEqual(len(stopover_alts), 0)


# ---------------------------------------------------------------------------
# Sort order — cheapest first
# ---------------------------------------------------------------------------

class TestMultilegSortOrder(unittest.TestCase):
    """Options are sorted by per_person price ascending."""

    def setUp(self):
        response_cache.clear()

    def test_cheapest_first(self):
        # Two stopover cities with different prices
        routes = {
            # Cheap stopover
            fixture_key("SIN", "HKT", "20261010"):
                _make_atlas_response("TR652", "TR", 20.0, "SIN", "HKT"),
            fixture_key("HKT", "BKK", "20261012"):
                _make_atlas_response("TR201", "TR", 15.0, "HKT", "BKK"),
            fixture_key("BKK", "SIN", "20261014"):
                _make_atlas_response("TR600", "TR", 25.0, "BKK", "SIN"),
            # Expensive stopover
            fixture_key("SIN", "MNL", "20261010"):
                _make_atlas_response("PR501", "PR", 80.0, "SIN", "MNL"),
            fixture_key("MNL", "BKK", "20261012"):
                _make_atlas_response("TG301", "TG", 60.0, "MNL", "BKK"),
            # BKK→SIN already cached above
        }
        client = _mock_client_with_routes(routes)

        options, _ = search_multileg_routes(
            client, "SIN",
            [
                {"cityId": "MNL", "cityName": "Manila"},
                {"cityId": "HKT", "cityName": "Phuket"},
            ],
            "BKK", "20261010", ["20261014"], 2, limit=5,
        )

        if len(options) >= 2:
            self.assertLessEqual(
                options[0].per_person, options[1].per_person)


# ---------------------------------------------------------------------------
# Hub mining — dead-end destinations
# ---------------------------------------------------------------------------

class TestHubCandidates(unittest.TestCase):
    """Gateway cities mined from real return routings.

    Regression: a dead-end destination (Langkawi — reachable only via
    Kuala Lumpur) left Option B empty because no shortlist city had
    leg-2 connectivity. The intermediate airports of the final→origin
    routings are the proven gateways.
    """

    @staticmethod
    def _node(*hops):
        """Fake node: hops is a chain like ('LGK','KUL','SIN')."""
        segs = [{"dep_airport": hops[i], "arr_airport": hops[i + 1]}
                for i in range(len(hops) - 1)]
        return MagicMock(segments=segs)

    def test_mines_intermediate_gateway(self):
        nodes = [
            self._node("LGK", "KUL", "SIN"),
            self._node("LGK", "KUL", "SIN"),
            self._node("LGK", "SIN"),  # direct — no intermediate
        ]
        cities = {"KUL": MagicMock(city_name="Kuala Lumpur"),
                  "LGK": MagicMock(city_name="Langkawi"),
                  "SIN": MagicMock(city_name="Singapore")}
        with patch("src.discovery.multileg.search_nodes",
                   return_value=(nodes, None)), \
             patch("src.discovery.multileg.dataset.by_id",
                   return_value=cities):
            hubs = hub_candidates(MagicMock(), "SIN", "LGK", "20261012", 2)
        self.assertEqual([h["cityId"] for h in hubs], ["KUL"])
        self.assertEqual(hubs[0]["cityName"], "Kuala Lumpur")

    def test_excludes_origin_destination_and_unknown_codes(self):
        nodes = [
            self._node("LGK", "KUL", "SIN"),
            self._node("LGK", "XXX", "SIN"),  # XXX not in dataset
        ]
        cities = {"KUL": MagicMock(city_name="Kuala Lumpur"),
                  "LGK": MagicMock(city_name="Langkawi"),
                  "SIN": MagicMock(city_name="Singapore")}
        with patch("src.discovery.multileg.search_nodes",
                   return_value=(nodes, None)), \
             patch("src.discovery.multileg.dataset.by_id",
                   return_value=cities):
            hubs = hub_candidates(MagicMock(), "SIN", "LGK", "20261012", 2)
        ids = [h["cityId"] for h in hubs]
        self.assertNotIn("XXX", ids)
        self.assertNotIn("SIN", ids)
        self.assertNotIn("LGK", ids)

    def test_ranks_by_frequency(self):
        nodes = [
            self._node("LGK", "PEN", "SIN"),
            self._node("LGK", "KUL", "SIN"),
            self._node("LGK", "KUL", "SIN"),
        ]
        cities = {"KUL": MagicMock(city_name="Kuala Lumpur"),
                  "PEN": MagicMock(city_name="Penang"),
                  "LGK": MagicMock(city_name="Langkawi"),
                  "SIN": MagicMock(city_name="Singapore")}
        with patch("src.discovery.multileg.search_nodes",
                   return_value=(nodes, None)), \
             patch("src.discovery.multileg.dataset.by_id",
                   return_value=cities):
            hubs = hub_candidates(MagicMock(), "SIN", "LGK", "20261012", 2)
        self.assertEqual([h["cityId"] for h in hubs], ["KUL", "PEN"])


if __name__ == "__main__":
    unittest.main()
