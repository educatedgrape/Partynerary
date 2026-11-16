"""Member agent — holds preferences privately and hands out one date per round.

The agent holds preferences as self._prefs and exposes only what the protocol
needs. Clauses arrive pre-computed, so the agent needs no retrieval import.

The privacy boundary is enforced by test: test_agent_boundary.py pins the
import set and verifies that nothing outside the agent reaches into
MemberPreferences.
"""


class MemberAgent:
    """One group member's agent.

    Holds a MemberPreferences privately. The orchestrator communicates through
    the public interface only:
      - name:        who this agent represents
      - ceiling:     their spending limit
      - public_view: what the group can see
      - favourite:   their current best date
      - concede:     reveal the next date down their ranking
    """

    def __init__(self, prefs):
        """Wrap a MemberPreferences object.

        Args:
            prefs: MemberPreferences (stored as self._prefs, private)
        """
        self._prefs = prefs
        self._position = 0

    @property
    def name(self):
        return self._prefs.member

    @property
    def ceiling(self):
        return self._prefs.ceiling

    @property
    def date_ranking(self):
        """The agent's date ranking — accessed by the concession engine."""
        return self._prefs.date_ranking

    @property
    def reservation_depth(self):
        """How far down the ranking the agent will concede. None = all."""
        return self._prefs.reservation_depth

    def favourite(self):
        """Their single best date — round 1 answer."""
        ranking = self._prefs.date_ranking
        if ranking:
            return ranking[0]
        return ""

    def concede(self):
        """Reveal the next date down the ranking. NEVER goes back up.

        Returns the next date, or None if the ranking is exhausted.
        """
        ranking = self._prefs.date_ranking
        self._position += 1
        if self._position < len(ranking):
            return ranking[self._position]
        return None

    def current_date(self):
        """The date the agent is currently holding."""
        ranking = self._prefs.date_ranking
        if self._position < len(ranking):
            return ranking[self._position]
        return None

    def public_view(self):
        """What the group can see. Never exposes busy_days or date_ranking."""
        from src.party.preferences import public_view
        return public_view(self._prefs)

    def permits(self, per_person_price):
        """Does this member's ceiling allow this price?"""
        if self._prefs.ceiling is None:
            return True
        return self._prefs.ceiling.permits(per_person_price)

    def __repr__(self):
        return "MemberAgent(%s, favourite=%s)" % (
            self.name, self.favourite())
