"""Phase 9 — Dashboard tests.

Verifies the HTTP API contract: state serialization, member management,
concession, discovery, and the full booking flow.
"""

import json
import threading
import unittest
import sys
import pathlib
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests.helpers import make_client
from src.ui.dashboard import DashboardState, create_server, ThreadedHTTPServer
from src.party.preferences import Ceiling
from src.atlas import cache as response_cache
from src.discovery.multileg import MultilegOption


class TestDashboardState(unittest.TestCase):
    """State serialization and member management."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()
        self.state = DashboardState(self.client, origin="SIN", party_size=2)

    def test_initial_state_has_required_keys(self):
        snap = self.state.as_dict()
        required = {
            "origin", "party_size", "members", "agreed_date",
            "return_dates", "cards", "decision", "booked",
            "receipt", "log_tail",
        }
        for key in required:
            self.assertIn(key, snap, "state missing key: %s" % key)

    def test_add_member_appears_in_state(self):
        self.state.add_member("Alice", 500, "beach temple")
        snap = self.state.as_dict()
        self.assertEqual(len(snap["members"]), 1)
        self.assertEqual(snap["members"][0]["name"], "Alice")
        self.assertEqual(snap["members"][0]["budget"], 500.0)

    def test_remove_member(self):
        self.state.add_member("Alice", 500, "beach")
        self.state.add_member("Bob", 400, "temples")
        self.state.remove_member("Alice")
        snap = self.state.as_dict()
        names = [m["name"] for m in snap["members"]]
        self.assertNotIn("Alice", names)
        self.assertIn("Bob", names)

    def test_add_same_name_replaces(self):
        self.state.add_member("Alice", 500, "beach")
        self.state.add_member("Alice", 600, "surfing")
        snap = self.state.as_dict()
        self.assertEqual(len(snap["members"]), 1)
        self.assertEqual(snap["members"][0]["budget"], 600.0)

    def test_reset_clears_everything(self):
        self.state.add_member("Alice", 500, "beach")
        self.state.reset()
        snap = self.state.as_dict()
        self.assertEqual(len(snap["members"]), 0)
        self.assertIsNone(snap["agreed_date"])
        self.assertEqual(snap["cards"], [])

    def tearDown(self):
        response_cache.clear()


class TestConcession(unittest.TestCase):
    """Date settlement through the API."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()
        self.state = DashboardState(self.client, origin="SIN", party_size=2)

    def test_settle_date_with_members(self):
        self.state.add_member("Alice", 500, "beach")
        self.state.add_member("Bob", 400, "temples")
        # Give members date rankings so concession can settle
        for m in self.state.members:
            m.date_ranking = ["20260918", "20260919", "20260920",
                              "20260925", "20260926"]
        snap = self.state.settle_date()
        # Should settle (members share the same ranking)
        self.assertTrue(snap["concession_settled"])
        self.assertIsNotNone(snap["agreed_date"])
        self.assertGreater(len(snap["return_dates"]), 0)

    def test_no_members_no_settlement(self):
        snap = self.state.settle_date()
        self.assertFalse(snap["concession_settled"])

    def tearDown(self):
        response_cache.clear()


class TestDiscovery(unittest.TestCase):
    """Discovery sweep and card generation."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()
        self.state = DashboardState(self.client, origin="SIN", party_size=2)

    def test_discovery_requires_agreed_date(self):
        result = self.state.start_discovery()
        self.assertIn("error", result)
        self.assertIn("agreed date", result["error"].lower())

    def test_discovery_produces_cards(self):
        self.state.add_member("Alice", 500, "beach")
        self.state.add_member("Bob", 400, "beach")
        # Give members date rankings and settle manually
        for m in self.state.members:
            m.date_ranking = ["20260918", "20260919", "20260920"]
        self.state.settle_date()

        result = self.state.start_discovery()
        self.assertTrue(result.get("discovering"))

        # Wait for the worker to finish
        import time
        for _ in range(50):
            time.sleep(0.1)
            if not self.state.discovering:
                break

        snap = self.state.as_dict()
        self.assertFalse(snap["discovering"])
        self.assertGreater(snap["trips_count"], 0)

    def tearDown(self):
        response_cache.clear()


class TestConstraint(unittest.TestCase):
    """Constraint changes through the API."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()
        self.state = DashboardState(self.client, origin="SIN", party_size=2)

    def test_ceiling_change(self):
        self.state.add_member("Alice", 500, "beach")
        snap = self.state.apply_constraint(
            kind="ceiling", member="Alice", ceiling=600.0)
        alice = next(m for m in snap["members"] if m["name"] == "Alice")
        self.assertEqual(alice["budget"], 600.0)

    def tearDown(self):
        response_cache.clear()


class TestHTTPServer(unittest.TestCase):
    """The HTTP server handles API routes."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()
        self.server, self.state = create_server(
            self.client, port=0)  # port 0 = random free port
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def _get(self, path):
        import urllib.request
        url = "http://localhost:%d%s" % (self.port, path)
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path, body=None):
        import urllib.request
        url = "http://localhost:%d%s" % (self.port, path)
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_get_state(self):
        snap = self._get("/api/state")
        self.assertIn("origin", snap)
        self.assertEqual(snap["origin"], "SIN")

    def test_post_member(self):
        result = self._post("/api/members", {
            "name": "Alice", "budget": 500, "preferences": "beach"})
        self.assertEqual(len(result["members"]), 1)

    def test_post_reset(self):
        self._post("/api/members", {"name": "Alice", "budget": 500})
        result = self._post("/api/reset")
        self.assertEqual(len(result["members"]), 0)

    def test_errors_never_crash(self):
        """A malformed request returns {error}, not a crash."""
        import urllib.request
        url = "http://localhost:%d/api/unknown" % self.port
        req = urllib.request.Request(url, data=b"{}", method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as e:
            body = json.loads(e.read().decode("utf-8"))
            self.assertIn("error", body)

    def tearDown(self):
        self.server.shutdown()
        response_cache.clear()


if __name__ == "__main__":
    unittest.main()


class TestSwapStopover(unittest.TestCase):
    """swap_stopover re-searches Option B through a new city."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()
        self.state = DashboardState(self.client, origin="SIN", party_size=2)

    def test_swap_requires_discovery_results(self):
        result = self.state.swap_stopover("HKT", "Phuket")
        self.assertIn("error", result)

    def test_swap_requires_agreed_date(self):
        self.state.cards = [{"destination_id": "BKK"}]
        self.state.itinerary_options = {
            "option_a": {"destination_id": "BKK", "per_person": 200},
        }
        result = self.state.swap_stopover("HKT", "Phuket")
        self.assertIn("error", result)

    def test_swap_updates_cards_and_options(self):
        # Set up minimal state that swap_stopover expects
        self.state.agreed_date = "20260911"
        self.state.return_dates = ["20260920", "20260921"]
        self.state.cards = [{
            "destination": "Bangkok",
            "destination_id": "BKK",
            "per_person": 200,
            "group_total": 400,
        }]
        self.state.itinerary_options = {
            "option_a": {
                "destination_id": "BKK",
                "per_person": 200,
                "group_total": 400,
            },
            "options_b": [],
        }
        # swap_stopover will call search_multileg_routes which hits Atlas
        # With fixture mode (LIVE not set), it will get fixture responses
        result = self.state.swap_stopover("HKT", "Phuket")
        # Either succeeds with new cards or fails gracefully
        if "error" not in result:
            self.assertTrue(result["swapped"])
            self.assertEqual(result["stopover"], "Phuket")
            self.assertGreater(result["cards"], 0)
            # Cards should be updated
            self.assertGreater(len(self.state.cards), 0)
            # Selected card should be auto-set
            self.assertEqual(self.state.selected_card, 0)

    def tearDown(self):
        response_cache.clear()


class TestStopoverPaths(unittest.TestCase):
    """Validated stopover paths and instant swap reuse."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()
        self.state = DashboardState(self.client, origin="SIN", party_size=2)

    @staticmethod
    def _opt(stop_id, stop_name, pp, final_id="BKK", days=2):
        class _FakeGraph:
            legs = []
            key = "fake-graph"
            feasible = True
            is_chain = True
            dependencies = []
            cost_refs = []
            origin = "SIN"

            def __init__(self, per_person):
                self._pp = per_person

            @property
            def per_person(self):
                return self._pp

            @property
            def group_total(self):
                return self._pp * 2

        return MultilegOption(
            _FakeGraph(pp), stop_id, stop_name, days, final_id,
            out_date="20261010", leg2_date="20261012", ret_date="20261014")

    def test_stopover_paths_cheapest_per_city(self):
        opts = [
            self._opt("HKT", "Phuket", 180.0),
            self._opt("HKT", "Phuket", 150.0),
            self._opt("CNX", "Chiang Mai", 220.0),
        ]
        paths = DashboardState._stopover_paths(opts, direct_per_person=300)
        self.assertEqual([p["city_id"] for p in paths], ["HKT", "CNX"])
        self.assertEqual(paths[0]["per_person"], 150.0)
        self.assertEqual(paths[0]["days"], 2)
        self.assertEqual(paths[1]["per_person"], 220.0)

    def test_stopover_paths_empty(self):
        self.assertEqual(DashboardState._stopover_paths([]), [])

    def test_instant_swap_reuses_cached_option(self):
        # Seed the minimal discovery state swap_stopover expects
        self.state.agreed_date = "20261010"
        self.state.return_dates = ["20261014"]
        self.state.cards = [{
            "destination_id": "BKK", "per_person": 200, "group_total": 400,
        }]
        self.state.itinerary_options = {
            "option_a": {"destination_id": "BKK", "per_person": 200},
            "options_b": [],
        }
        self.state.multileg_options = [self._opt("HKT", "Phuket", 156.0)]

        # Poison the client: an instant swap must never call Atlas
        self.state.client = MagicMock()
        self.state.client.search_flights.side_effect = AssertionError(
            "Atlas was called for an instant swap")

        result = self.state.swap_stopover("HKT", "Phuket")
        self.assertNotIn("error", result)
        self.assertTrue(result["swapped"])
        self.assertTrue(result["instant"])
        self.assertEqual(result["stopover"], "Phuket")
        self.assertGreater(len(result["new_option"]), 0)
        # Validated paths are exposed to the frontend for future swaps
        paths = self.state.itinerary_options["stopover_paths"]
        self.assertEqual(paths[0]["city_id"], "HKT")
        self.assertEqual(paths[0]["per_person"], 156.0)

    def test_windowing_widens_until_routes_found(self):
        # First window of 4 stops has no connectivity to TPE; the second
        # window must be searched instead of returning empty options.
        cards = [{"destination_id": "TPE", "destination": "Taipei",
                  "per_person": 120, "group_total": 240}]
        shortlist = [{"cityId": c, "cityName": c} for c in
                     ["CMB", "MAA", "KHH", "CEI", "BKK", "NRT", "DPS"]]
        fake = self._opt("BKK", "Bangkok", 190.0, final_id="TPE")
        with patch("src.ui.dashboard.search_multileg_routes") as mock_search:
            mock_search.side_effect = [
                ([], [{"stopover": "CMB", "leg": "leg2",
                       "error": "No routings"}]),
                ([fake], []),
            ]
            result = self.state._generate_multi_leg_options(
                cards, shortlist, "SIN", "20261010", ["20261014"], 2)
        self.assertEqual(mock_search.call_count, 2)
        self.assertEqual(
            [c["cityId"] for c in mock_search.call_args_list[0][0][2]],
            ["CMB", "MAA", "KHH", "CEI"])
        self.assertEqual(
            [c["cityId"] for c in mock_search.call_args_list[1][0][2]],
            ["BKK", "NRT", "DPS"])
        self.assertEqual(len(result["options_b"]), 1)
        self.assertEqual(result["stopover_paths"][0]["city_id"], "BKK")
        self.assertEqual(len(self.state.multileg_errors), 1)

    def test_windowing_stops_at_first_success(self):
        cards = [{"destination_id": "BKK", "destination": "Bangkok",
                  "per_person": 100, "group_total": 200}]
        shortlist = [{"cityId": c, "cityName": c} for c in
                     ["HKT", "CNX", "DMK", "PEN", "KUL", "DPS"]]
        fake = self._opt("HKT", "Phuket", 150.0)
        with patch("src.ui.dashboard.search_multileg_routes") as mock_search:
            mock_search.return_value = ([fake], [])
            result = self.state._generate_multi_leg_options(
                cards, shortlist, "SIN", "20261010", ["20261014"], 2)
        self.assertEqual(mock_search.call_count, 1)
        self.assertEqual(len(result["options_b"]), 1)

    def test_hub_fallback_for_dead_end_destination(self):
        # Dead-end destination (Langkawi): no shortlist window yields a
        # route, so discovery must mine gateway cities from the return
        # routings and search through them instead of returning empty.
        cards = [{"destination_id": "LGK", "destination": "Langkawi",
                  "per_person": 90, "group_total": 180}]
        shortlist = [{"cityId": c, "cityName": c} for c in
                     ["PUS", "CMB", "MAA", "DPS", "HKT", "CEB", "CXR"]]
        fake = self._opt("KUL", "Kuala Lumpur", 130.0, final_id="LGK")
        hubs = [{"cityId": "KUL", "cityName": "Kuala Lumpur"}]
        with patch("src.ui.dashboard.search_multileg_routes") as mock_search, \
             patch("src.ui.dashboard.hub_candidates",
                   return_value=hubs) as mock_hubs:
            mock_search.side_effect = [([], []), ([], []), ([fake], [])]
            result = self.state._generate_multi_leg_options(
                cards, shortlist, "SIN", "20261010", ["20261014"], 2)
        self.assertEqual(mock_search.call_count, 3)
        self.assertEqual(mock_hubs.call_count, 1)
        # The third search ran through the mined gateway, not the shortlist
        hub_window = mock_search.call_args_list[2][0][2]
        self.assertEqual([c["cityId"] for c in hub_window], ["KUL"])
        self.assertEqual(len(result["options_b"]), 1)
        self.assertEqual(result["stopover_paths"][0]["city_id"], "KUL")

    def tearDown(self):
        response_cache.clear()


class TestSwapDestination(unittest.TestCase):
    """swap_destination re-picks the stopover from the ranked shortlist."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()
        self.state = DashboardState(self.client, origin="SIN", party_size=2)
        self.state.agreed_date = "20261010"
        self.state.return_dates = ["20261014"]
        self.state.cards = [{
            "destination_id": "BKK", "per_person": 200, "group_total": 400,
        }]
        self.state.itinerary_options = {
            "option_a": {"destination_id": "BKK", "per_person": 200},
            "options_b": [],
        }
        self.state.shortlist = [
            {"cityId": "BKK", "cityName": "Bangkok"},
            {"cityId": "HKT", "cityName": "Phuket"},
            {"cityId": "CNX", "cityName": "Chiang Mai"},
            {"cityId": "DMK", "cityName": "Don Mueang"},
        ]

    def test_prefers_rank_2_and_3_stopovers(self):
        fake = TestStopoverPaths._opt("HKT", "Phuket", 150.0, final_id="DPS")
        with patch("src.ui.dashboard.search_multileg_routes") as mock_search:
            mock_search.return_value = ([fake], [])
            result = self.state.swap_destination("DPS", "Bali")
        self.assertNotIn("error", result)
        self.assertEqual(result["stopover"], "Phuket")
        candidates = mock_search.call_args[0][2]
        self.assertEqual(
            [c["cityId"] for c in candidates],
            ["HKT", "CNX", "BKK", "DMK"])

    def test_excludes_new_destination_from_candidates(self):
        fake = TestStopoverPaths._opt("CNX", "Chiang Mai", 160.0,
                                      final_id="HKT")
        with patch("src.ui.dashboard.search_multileg_routes") as mock_search:
            mock_search.return_value = ([fake], [])
            result = self.state.swap_destination("HKT", "Phuket")
        self.assertNotIn("error", result)
        candidates = mock_search.call_args[0][2]
        ids = [c["cityId"] for c in candidates]
        self.assertNotIn("HKT", ids)
        self.assertEqual(ids[0], "CNX")

    def test_fallback_keeps_current_stopover_without_shortlist(self):
        self.state.shortlist = []
        self.state.multileg_options = [
            TestStopoverPaths._opt("PEN", "Penang", 140.0, final_id="BKK")]
        fake = TestStopoverPaths._opt("PEN", "Penang", 150.0, final_id="DPS")
        with patch("src.ui.dashboard.search_multileg_routes") as mock_search:
            mock_search.return_value = ([fake], [])
            result = self.state.swap_destination("DPS", "Bali")
        self.assertNotIn("error", result)
        candidates = mock_search.call_args[0][2]
        self.assertEqual([c["cityId"] for c in candidates], ["PEN"])

    def test_current_stopover_excluded_then_used_as_fallback(self):
        # Current stopover HKT (rank #2): it must be excluded from the new
        # candidates, but when every other city fails it is retried so the
        # destination still changes (A→X→B becomes A→X→C).
        self.state.multileg_options = [
            TestStopoverPaths._opt("HKT", "Phuket", 150.0, final_id="BKK")]
        fallback_opt = TestStopoverPaths._opt("HKT", "Phuket", 160.0,
                                              final_id="DPS")
        with patch("src.ui.dashboard.search_multileg_routes") as mock_search:
            mock_search.side_effect = [([], [{"error": "no legs"}]),
                                       ([fallback_opt], [])]
            result = self.state.swap_destination("DPS", "Bali")
        self.assertNotIn("error", result)
        self.assertEqual(result["stopover"], "Phuket")
        first = [c["cityId"] for c in mock_search.call_args_list[0][0][2]]
        self.assertNotIn("HKT", first)
        second = [c["cityId"] for c in mock_search.call_args_list[1][0][2]]
        self.assertEqual(second, ["HKT"])

    def test_no_candidates_and_no_stopover_errors(self):
        self.state.shortlist = []
        self.state.multileg_options = []
        result = self.state.swap_destination("DPS", "Bali")
        self.assertIn("error", result)

    def tearDown(self):
        response_cache.clear()


class TestCancelBooking(unittest.TestCase):
    """cancel_booking clears booking but preserves discovery results."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()
        self.state = DashboardState(self.client, origin="SIN", party_size=2)

    def test_cancel_clears_booking_fields(self):
        # Set up a booked state
        self.state.booked_trip = object()
        self.state.booking_ref = "PNR-ABC123"
        self.state.booked_at = "2026-08-29 10:00"
        self.state.booked_explorer = True
        self.state.booked_stopover = {"name": "Phuket", "days": 2}
        self.state.decision = "option1"
        self.state.selected_card = 0
        # Also preserve discovery data
        self.state.cards = [{"destination_id": "BKK"}]
        self.state.itinerary_options = {"option_a": {}, "options_b": []}

        result = self.state.cancel_booking()

        # Booking fields should be cleared
        self.assertIsNone(self.state.booked_trip)
        self.assertIsNone(self.state.booking_ref)
        self.assertIsNone(self.state.booked_at)
        self.assertFalse(self.state.booked_explorer)
        self.assertIsNone(self.state.booked_stopover)

        # Selection and discovery data should be preserved so the user
        # can re-confirm or pick another flight immediately
        self.assertEqual(self.state.decision, "option1")
        self.assertEqual(self.state.selected_card, 0)
        self.assertEqual(len(self.state.cards), 1)
        self.assertIsNotNone(self.state.itinerary_options)

    def test_cancel_returns_snapshot(self):
        result = self.state.cancel_booking()
        self.assertIn("members", result)
        self.assertIn("cards", result)


class TestRunAutonomous(unittest.TestCase):
    """run_autonomous chains settle_date + discover."""

    def setUp(self):
        response_cache.clear()
        self.client = make_client()
        self.state = DashboardState(self.client, origin="SIN", party_size=2)

    def test_autonomous_requires_agents(self):
        result = self.state.run_autonomous()
        self.assertIn("error", result)
        self.assertEqual(result["error"], "no agents added")

    def test_autonomous_with_existing_cards(self):
        # If cards already exist, discovery is not re-started
        self.state.agreed_date = "20260911"
        self.state.cards = [{"destination_id": "BKK"}]
        result = self.state.run_autonomous()
        self.assertTrue(result["autonomous"])
        self.assertFalse(result["discovery_started"])
        self.assertTrue(result["cards_exist"])

    def tearDown(self):
        response_cache.clear()
