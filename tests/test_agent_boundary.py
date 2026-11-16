"""Phase 6 — Agent boundary tests.

Pins member_agent.py's import set. The agent holds preferences as self._prefs
and hands out one date per round; anything else reaching into that dataclass
is a bug. Clauses arrive pre-computed, so the agent needs no retrieval import.
"""

import ast
import pathlib
import unittest
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.agents.member_agent import MemberAgent
from src.agent.mandate import Mandate, ceiling_total_from_members
from src.party.preferences import Ceiling, MemberPreferences


AGENT_FILE = pathlib.Path(__file__).resolve().parent.parent / "src" / "agents" / "member_agent.py"

# Imports the agent is allowed to have at module level
ALLOWED_IMPORTS = {
    "src.party.preferences",  # for public_view (lazy import inside method)
}


class TestAgentImports(unittest.TestCase):
    """The agent's import set is pinned — no retrieval, no atlas, no sweep."""

    def test_no_retrieval_import(self):
        """The agent must not import from discovery.retrieval."""
        source = AGENT_FILE.read_text()
        self.assertNotIn("from src.discovery", source,
                         "Agent must not import from src.discovery")
        self.assertNotIn("import retrieval", source,
                         "Agent must not import retrieval")

    def test_no_atlas_import(self):
        """The agent must not import from atlas."""
        source = AGENT_FILE.read_text()
        self.assertNotIn("from src.atlas", source,
                         "Agent must not import from src.atlas")

    def test_no_sweep_import(self):
        """The agent must not import from discovery.sweep."""
        source = AGENT_FILE.read_text()
        self.assertNotIn("sweep", source.split("#")[0] if "#" in source else source,
                         "Agent must not reference sweep")

    def test_module_level_imports_minimal(self):
        """Parse the AST and verify module-level imports are minimal."""
        source = AGENT_FILE.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    # No third-party imports
                    self.assertFalse(
                        alias.name.startswith("numpy") or
                        alias.name.startswith("pandas"),
                        "Agent imports %s" % alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    # Top-level imports should be minimal
                    self.assertIn(
                        node.module, ALLOWED_IMPORTS,
                        "Agent has unexpected top-level import: %s" % node.module)


class TestAgentPrivacy(unittest.TestCase):
    """The agent exposes only what the protocol needs."""

    def _make_agent(self):
        prefs = MemberPreferences(
            member="TestAgent", origin="SIN",
            ceiling=Ceiling(member="TestAgent", amount=300.0),
            date_ranking=["20260905", "20260912", "20260919"],
            reservation_depth=2,
            avatar="🧪",
            preferences="beach and relax",
            busy_days=("20260904",),
            calendar_note="conference",
        )
        return MemberAgent(prefs)

    def test_holds_prefs_as_private(self):
        agent = self._make_agent()
        self.assertTrue(hasattr(agent, "_prefs"),
                        "Agent should store prefs as _prefs")

    def test_favourite_returns_first_date(self):
        agent = self._make_agent()
        self.assertEqual(agent.favourite(), "20260905")

    def test_concede_advances(self):
        agent = self._make_agent()
        # Round 1: favourite
        self.assertEqual(agent.favourite(), "20260905")
        # Concede: next in ranking
        next_date = agent.concede()
        self.assertEqual(next_date, "20260912")

    def test_never_goes_back_up(self):
        agent = self._make_agent()
        agent.favourite()  # position 0
        dates = [agent.favourite()]
        dates.append(agent.concede())  # position 1
        dates.append(agent.concede())  # position 2
        dates.append(agent.concede())  # exhausted → None
        # None means exhausted, not a date going back up
        real_dates = [d for d in dates if d is not None]
        # All real dates should be from the ranking in order
        self.assertEqual(real_dates, ["20260905", "20260912", "20260919"])

    def test_public_view_no_busy_days(self):
        agent = self._make_agent()
        view = agent.public_view()
        self.assertNotIn("busy_days", view)
        self.assertNotIn("date_ranking", view)

    def test_public_view_has_ceiling(self):
        agent = self._make_agent()
        view = agent.public_view()
        self.assertIn("ceiling", view)
        self.assertEqual(view["ceiling"]["amount"], 300.0)

    def test_permits_within_ceiling(self):
        agent = self._make_agent()
        self.assertTrue(agent.permits(250.0))
        self.assertFalse(agent.permits(350.0))


class TestMandateIsNotALedger(unittest.TestCase):
    """A ceiling is AUTHORITY, not funds.

    The mandate answers two questions — does this price fit, and how much
    authority is unheld. It exposes no attribute that moves money.
    """

    FORBIDDEN = ("spend", "charge", "debit", "credit",
                 "settle", "transfer", "balance")

    def test_no_money_moving_attributes(self):
        m = Mandate(1000.0)
        for name in dir(m):
            lowered = name.lower()
            for word in self.FORBIDDEN:
                self.assertNotIn(
                    word, lowered,
                    "Mandate exposes %r — a ceiling is authority, not a "
                    "wallet" % name)


class TestCeilingTotalAtPartySizeFour(unittest.TestCase):
    """The mandate carries min(ceilings) * party size — the group total the
    executor reserves, with every member able to afford their share.

    Asserted as a literal, never recomputed from the same expression the
    code uses.
    """

    def _members(self):
        prefs = []
        for i, amount in enumerate((200.0, 210.0, 300.0, 400.0)):
            prefs.append(MemberPreferences(
                member="M%d" % i, origin="SIN",
                ceiling=Ceiling(member="M%d" % i, amount=amount),
                preferences=""))
        return prefs

    def test_party_of_four(self):
        # Tightest ceiling 200, four travellers -> 800
        total = ceiling_total_from_members(self._members(), party_size=4)
        self.assertEqual(total, 800.0)

    def test_without_party_size_returns_per_person(self):
        total = ceiling_total_from_members(self._members())
        self.assertEqual(total, 200.0)


if __name__ == "__main__":
    unittest.main()
