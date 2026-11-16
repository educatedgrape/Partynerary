"""Mandate — authority accounting gate file.

Booking RESERVES authority; only payment SPENDS it. Committing at both points
charges the party twice for one set of seats. That reservation/settlement
distinction is tracked by the EXECUTOR — this module only grants and holds
authority. It carries no ledger: nothing here spends, settles, credits or
refunds.

An action absent from ACTION_GROUPS is refused, never defaulted to the
permissive group.
"""

from src.agent.proposal import (
    ACTION_GROUPS, GROUP_SEARCH, GROUP_RESERVE, GROUP_SETTLE, GROUP_AFTERCARE,
)


class MandateError(Exception):
    """The proposal exceeds the granted authority."""


# Actions the executor may run without explicit confirmation.
AUTONOMOUS_GROUPS = {GROUP_SEARCH, GROUP_AFTERCARE}

# Actions that HOLD authority (reserve, but do not deplete).
RESERVING_ACTIONS = {"book_group"}

# Aftercare actions that run without confirmation.
AUTONOMOUS_ACTIONS = {"reshop_order", "refund_order"}


class Mandate:
    """The authority granted by the party's ceilings.

    Answers two questions only — does this amount fit, and how much of the
    grant is still unheld. Booking reserves authority; nothing here spends,
    settles, credits or refunds. The reservation/settlement bookkeeping lives
    on the executor, never on this object.
    """

    def __init__(self, ceiling_total):
        self._ceiling_total = float(ceiling_total)
        self._reserved = 0.0

    @property
    def ceiling_total(self):
        return self._ceiling_total

    @property
    def reserved(self):
        return self._reserved

    @property
    def remaining(self):
        """Authority granted but not yet held by a booking."""
        return round(self._ceiling_total - self._reserved, 2)

    def check(self, amount, action):
        """Does this amount fit under the remaining authority?

        Returns True if the action is permitted. Raises MandateError if not.

        - Autonomous actions (search, aftercare) always pass.
        - Money-moving actions check that the amount fits within the
          ceiling minus existing reservations.
        """
        group = ACTION_GROUPS.get(action)
        if group is None:
            raise MandateError(
                "action %r has no authority group — refused, never defaulted"
                % action)

        if group in AUTONOMOUS_GROUPS:
            return True

        available = self._ceiling_total - self._reserved
        if amount > round(available, 2) + 0.001:  # small tolerance for rounding
            raise MandateError(
                "amount %.2f exceeds remaining authority %.2f "
                "(ceiling=%.2f, reserved=%.2f)" % (
                    amount, available, self._ceiling_total,
                    self._reserved))
        return True

    def reserve(self, amount):
        """Hold authority for a booking. Does not spend it."""
        self._reserved = round(self._reserved + amount, 2)

    def release(self, amount):
        """Unhold authority once the executor settles a payment.

        A hold released is not money moving — settlement itself is the
        executor's own accounting, never a mutation of the grant.
        """
        amount = min(round(amount, 2), self._reserved)
        self._reserved = round(self._reserved - amount, 2)


def ceiling_total_from_members(members_preferences, party_size=None):
    """The mandate's ceiling: the MINIMUM per-member ceiling times party size.

    Every member grants a PER-PERSON ceiling, and the executor reserves the
    GROUP total — so the mandate must carry min(ceilings) * party_size,
    otherwise a trip each member can individually afford is refused for the
    group. With no party size given the per-person figure is returned.
    """
    if not members_preferences:
        return float("inf")
    ceilings = []
    for prefs in members_preferences:
        if hasattr(prefs, "ceiling") and prefs.ceiling:
            ceilings.append(prefs.ceiling.amount)
    if not ceilings:
        return float("inf")
    tightest = min(ceilings)
    if party_size:
        return round(tightest * party_size, 2)
    return tightest
