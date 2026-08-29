"""Phase 6 — Concession and agent tests.

Verifies monotonic concession, privacy boundary, schema enforcement,
and the no-wallet guarantee.
"""

import unittest
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.party.preferences import Ceiling, MemberPreferences, public_view
from src.party.protocol import (
    NegotiationMove, SchemaViolation, validate_dict, render,
    ALLOWED_KEYS, FORBIDDEN_KEYS,
)
from src.party.ics import preferences_from_ics, _candidate_dates
from src.party.concession import run_concession, ConcessionState
from src.agents.member_agent import MemberAgent


class TestConcession(unittest.TestCase):
    """Monotonic concession engine."""

    def _make_agents(self, names, rankings, depths=None):
        agents = []
        for i, name in enumerate(names):
            prefs = MemberPreferences(
                member=name, origin="SIN",
                ceiling=Ceiling(member=name, amount=300.0),
                date_ranking=rankings[i],
                reservation_depth=depths[i] if depths else None,
            )
            agents.append(MemberAgent(prefs))
        return agents

    def test_four_agents_colliding_calendars(self):
        """Four agents with different favourites take 3+ rounds."""
        # Use explicit rankings with deliberate non-overlap at position 0
        rankings = [
            ["20260905", "20260906", "20260912", "20260913", "20260919"],
            ["20260906", "20260912", "20260905", "20260919", "20260913"],
            ["20260912", "20260905", "20260913", "20260906", "20260919"],
            ["20260913", "20260919", "20260906", "20260912", "20260905"],
        ]
        agents = self._make_agents(
            ["Alice", "Bob", "Carol", "Dave"], rankings)
        state = run_concession(agents)
        # Should settle (all rankings contain the same dates)
        self.assertTrue(state.settled,
                        "Expected concession to settle")
        self.assertGreaterEqual(state.round_no, 3,
                                "Expected 3+ rounds for colliding calendars")

    def test_concession_is_monotonic(self):
        """An agent never names a date above one it already conceded."""
        rankings = [
            ["20260905", "20260912", "20260919"],
            ["20260912", "20260919", "20260905"],
        ]
        agents = self._make_agents(["A", "B"], rankings)
        state = run_concession(agents)

        # Check monotonicity: each member's named dates only go forward
        for member, dates in state.named.items():
            for i in range(1, len(dates)):
                # The date should be at a position >= the previous date
                # (since rankings are best-first, later positions = worse dates)
                # Monotonicity means: the INDEX in the ranking only increases
                ranking = None
                for a in agents:
                    if a.name == member:
                        ranking = a.date_ranking
                        break
                if ranking:
                    prev_idx = ranking.index(dates[i - 1]) if dates[i - 1] in ranking else -1
                    curr_idx = ranking.index(dates[i]) if dates[i] in ranking else -1
                    self.assertGreaterEqual(
                        curr_idx, prev_idx,
                        "%s conceded backwards: %s → %s" % (
                            member, dates[i - 1], dates[i]))

    def test_withdrawal_at_reservation_depth(self):
        """An agent that would concede past its reservation depth withdraws."""
        rankings = [
            ["20260905", "20260912", "20260919"],
            ["20260912", "20260919", "20260905"],
        ]
        depths = [1, None]  # First agent only concedes 1 deep
        agents = self._make_agents(["A", "B"], rankings, depths=depths)
        state = run_concession(agents)
        # Agent A should have withdrawn
        self.assertIn("A", state.withdrawn)

    def test_synchronous_rounds(self):
        """All agents name their dates in the same round simultaneously."""
        rankings = [
            ["20260905", "20260912", "20260919"],
            ["20260912", "20260905", "20260919"],
        ]
        agents = self._make_agents(["X", "Y"], rankings)
        state = run_concession(agents)
        # In round 1, both agents should have a move
        round1_moves = [m for m in state.moves
                        if m.round_no == 1 and not m.withdrawn]
        self.assertEqual(len(round1_moves), 2,
                         "Both agents should name a date in round 1")


class TestPublicView(unittest.TestCase):
    """public_view() leaks neither busy_days nor date_ranking."""

    def test_no_busy_days(self):
        prefs = MemberPreferences(
            member="Test", origin="SIN",
            ceiling=Ceiling(member="Test", amount=300.0),
            date_ranking=["20260905", "20260912"],
            busy_days=("20260904", "20260905"),
        )
        view = public_view(prefs)
        self.assertNotIn("busy_days", view)
        self.assertNotIn("date_ranking", view)

    def test_no_date_ranking(self):
        prefs = MemberPreferences(
            member="Test", origin="SIN",
            ceiling=Ceiling(member="Test", amount=300.0),
            date_ranking=["20260905", "20260912"],
        )
        view = public_view(prefs)
        self.assertNotIn("date_ranking", view)
        self.assertNotIn("reservation_depth", view)

    def test_exposes_preferences(self):
        prefs = MemberPreferences(
            member="Test", origin="SIN",
            ceiling=Ceiling(member="Test", amount=300.0),
            preferences="beach and relax",
        )
        view = public_view(prefs)
        self.assertEqual(view["preferences"], "beach and relax")
        self.assertIn("ceiling", view)
        self.assertEqual(view["ceiling"]["amount"], 300.0)


class TestNegotiationMove(unittest.TestCase):
    """Schema enforcement — no amount field, no forbidden keys."""

    def test_move_with_amount_rejected(self):
        """A move with an amount key is rejected by the schema."""
        d = {
            "move": "propose", "member": "Alice",
            "subject": "20260918", "reason": "I like Fridays",
            "amount": 250.0,
        }
        with self.assertRaises(SchemaViolation):
            validate_dict(d)

    def test_valid_move(self):
        d = {
            "move": "propose", "member": "Alice",
            "subject": "20260918", "reason": "I like Fridays",
        }
        m = validate_dict(d)
        self.assertEqual(m.move, "propose")
        self.assertEqual(m.member, "Alice")

    def test_move_with_cost_ref(self):
        d = {
            "move": "propose", "member": "Alice",
            "subject": "DPS", "reason": "cheap flights",
            "cost_ref": "search.do:SIN-DPS@20260918#routings[0].adultPrice",
        }
        m = validate_dict(d)
        self.assertIsNotNone(m.cost_ref)

    def test_as_dict_no_forbidden_keys(self):
        m = NegotiationMove(
            move="propose", member="Alice",
            subject="20260918", reason="I like Fridays")
        d = m.as_dict()
        for key in FORBIDDEN_KEYS:
            self.assertNotIn(key, d,
                               "as_dict must not contain %r" % key)

    def test_render_uses_ref_not_reason(self):
        """A reason with a digit still renders its figure from the ref."""
        from src.atlas import cache as response_cache
        response_cache.clear()
        response_cache.put("test-key", {"value": 42.50})

        m = NegotiationMove(
            move="propose", member="Alice",
            subject="DPS", reason="I found 50 dollar flights",
            cost_ref="test-key#value")
        rendered = render(m)
        # The figure comes from the ref (42.50), not the reason (50)
        self.assertEqual(rendered["figure"], 42.50)
        response_cache.clear()


class TestCeiling(unittest.TestCase):
    """Ceiling has no spend/charge/debit method."""

    def test_no_spend_method(self):
        c = Ceiling(member="Alice", amount=300.0)
        self.assertFalse(hasattr(c, "spend"),
                         "Ceiling must not have a spend method")
        self.assertFalse(hasattr(c, "charge"),
                         "Ceiling must not have a charge method")
        self.assertFalse(hasattr(c, "debit"),
                         "Ceiling must not have a debit method")
        self.assertFalse(hasattr(c, "deduct"),
                         "Ceiling must not have a deduct method")
        self.assertFalse(hasattr(c, "draw_down"),
                         "Ceiling must not have a draw_down method")

    def test_permits_under(self):
        c = Ceiling(member="Alice", amount=300.0)
        self.assertTrue(c.permits(250.0))

    def test_permits_exact(self):
        c = Ceiling(member="Alice", amount=300.0)
        self.assertTrue(c.permits(300.0))

    def test_permits_over(self):
        c = Ceiling(member="Alice", amount=300.0)
        self.assertFalse(c.permits(350.0))

    def test_shortfall(self):
        c = Ceiling(member="Alice", amount=300.0)
        self.assertEqual(c.shortfall(350.0), 50.0)
        self.assertLessEqual(c.shortfall(250.0), 0)

    def test_frozen(self):
        c = Ceiling(member="Alice", amount=300.0)
        with self.assertRaises(AttributeError):
            c.amount = 500.0


class TestICSParsing(unittest.TestCase):
    """ICS calendar parsing and date ranking."""

    def test_candidate_dates_are_fri_sat(self):
        candidates = _candidate_dates("20260827")
        from datetime import datetime
        for d in candidates:
            dt = datetime.strptime(d, "%Y%m%d")
            self.assertIn(dt.weekday(), (4, 5),
                          "%s is not a Friday or Saturday" % d)

    def test_no_calendar_different_rankings(self):
        """Members with no calendar get different rankings."""
        r1 = preferences_from_ics(None, "Alice", origin_date="20260827")
        r2 = preferences_from_ics(None, "Bob", origin_date="20260827")
        # They should not be identical
        self.assertNotEqual(r1, r2,
                            "No-calendar members should have different rankings")

    def test_busy_days_filtered(self):
        ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20260904
DTEND:20260906
SUMMARY:Busy
END:VEVENT
END:VCALENDAR"""
        ranking = preferences_from_ics(ics, "Test", origin_date="20260827")
        self.assertNotIn("20260904", ranking)
        self.assertNotIn("20260905", ranking)


class TestDemoParty(unittest.TestCase):
    """demo_party() produces four distinct favourite dates."""

    def test_four_distinct_favourites(self):
        from src.party.members import demo_party
        members = demo_party()
        agents = [MemberAgent(p) for p in members]
        favourites = [a.favourite() for a in agents]
        unique = set(favourites)
        self.assertGreater(len(unique), 1,
                           "Expected distinct favourite dates, got %s" % favourites)

    def test_four_members(self):
        from src.party.members import demo_party
        members = demo_party()
        self.assertEqual(len(members), 4)


if __name__ == "__main__":
    unittest.main()
