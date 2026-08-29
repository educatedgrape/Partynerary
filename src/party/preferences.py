"""Member preferences — the privacy boundary.

Two different objects, two different names:
  - Ceiling: per-member threshold that NEVER depletes (authority, not funds)
  - MemberPreferences: private to the agent; the orchestrator never reads this

public_view() exposes the ceiling and what they typed. It must NEVER expose
busy_days or date_ranking.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Ceiling:
    """The entire money surface of a member agent. There is no more than this.

    AUTHORITY, not funds. Granted once by a human, consumed by nothing, and only
    ever tested against an Atlas-returned per-person price. Two answers: yes,
    and veto.

    It must NEVER grow a spend/charge/debit method — a test enforces that. The
    moment this object can be drawn down it stops being a delegated limit and
    becomes a wallet, which is the thing this system does not have.
    """
    member: str
    amount: float
    currency: str = "USD"

    def permits(self, per_person_price):
        """The only question this object answers."""
        return per_person_price <= self.amount

    def shortfall(self, per_person_price):
        """How far over. Zero or negative means it fits."""
        return per_person_price - self.amount


# Fields that public_view must NEVER expose
_PRIVATE_FIELDS = {"busy_days", "date_ranking", "reservation_depth",
                   "calendar_note"}


@dataclass
class MemberPreferences:
    """Private to the member's agent. The orchestrator never reads this."""
    member: str
    origin: str
    ceiling: Ceiling = None
    date_ranking: list = field(default_factory=list)   # best first
    reservation_depth: int = None    # won't concede past this; None = all
    avatar: str = "🙂"
    preferences: str = ""            # verbatim, said out loud, so public
    clauses: tuple = ()              # embedded + negation-tagged, derived
    named_places: dict = field(default_factory=dict)
    unrecognised: tuple = ()
    busy_days: tuple = ()            # from their .ics; PRIVATE, never rendered
    calendar_note: str = ""


def public_view(prefs):
    """Expose only what the group can see.

    Returns a dict with: member, avatar, preferences, ceiling, named_places,
    unrecognised. Must NEVER expose busy_days or date_ranking.
    """
    result = {
        "member": prefs.member,
        "avatar": prefs.avatar,
        "preferences": prefs.preferences,
        "named_places": dict(prefs.named_places),
        "unrecognised": list(prefs.unrecognised),
    }
    if prefs.ceiling is not None:
        result["ceiling"] = {
            "amount": prefs.ceiling.amount,
            "currency": prefs.ceiling.currency,
        }
    # Verify no private fields leakeded
    for key in result:
        if key in _PRIVATE_FIELDS:
            raise RuntimeError(
                "public_view leaked private field %r" % key)
    return result
