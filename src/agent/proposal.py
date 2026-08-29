"""ActionProposal — the schema every booking action must satisfy.

No amount field. No price, fare, total, cost, or saving. Structural, not a
prompt instruction. If a proposal could carry a number, a model could author
one, and the anti-hallucination guarantee would be void.

cost_refs is plural because a trip has legs. A single ref addresses one
routing under one cache key; the other legs live under different keys and no
derivation reaches them.
"""

from dataclasses import dataclass


# The only valid action verbs. Anything else is refused at the schema gate.
ACTIONS = {"book_group", "pay_group", "search_group", "aftercare",
          "reshop_order", "change_order", "cancel_order", "refund_order"}

# Group classification for authority accounting.
GROUP_SEARCH = 1     # autonomous — costs nothing, reserves nothing
GROUP_RESERVE = 2    # holds authority — booking
GROUP_SETTLE = 3     # spends authority — payment
GROUP_AFTERCARE = 4  # autonomous — post-order
GROUP_AFTERWRITE = 5 # confirmation-required — money-moving aftercare

ACTION_GROUPS = {
    "search_group": GROUP_SEARCH,
    "book_group": GROUP_RESERVE,
    "pay_group": GROUP_SETTLE,
    "aftercare": GROUP_AFTERCARE,
    "reshop_order": GROUP_AFTERCARE,
    "refund_order": GROUP_AFTERCARE,
    "change_order": GROUP_AFTERWRITE,
    "cancel_order": GROUP_AFTERWRITE,
}


class ProposalSchemaError(Exception):
    """A proposal failed schema validation — bad action, missing reason, or
    a cost_refs shape that would reserve part of a trip."""


ALLOWED_KEYS = {"action", "target", "reason", "cost_refs"}


@dataclass(frozen=True)
class ActionProposal:
    """One action the agent proposes to take.

    Deliberately NO amount field — structural, not a prompt instruction.
    The executor dereferences cost_refs to compute the amount; if the
    proposal carried one, a model could author a lie and the gate would
    compare its own fiction against itself.
    """
    action: str            # one of ACTIONS
    target: str            # the itinerary key
    reason: str            # the ONLY free-text field
    cost_refs: tuple = ()  # ONE POINTER PER LEG; empty => zero-cost

    def __post_init__(self):
        if self.action not in ACTIONS:
            raise ProposalSchemaError(
                "unknown action %r — valid actions: %s" % (
                    self.action, sorted(ACTIONS)))
        if not self.reason:
            raise ProposalSchemaError(
                "every proposal must carry a reason")
        if isinstance(self.cost_refs, str):
            # A bare string is the shape that reserves half a return trip.
            # Coercing it to a one-element list would make that bug legal input.
            raise ProposalSchemaError(
                "cost_refs must be a sequence of refs, one per leg — got a "
                "single string. A return trip is two priced routings under two "
                "cache keys; one ref cannot address both.")
        object.__setattr__(self, "cost_refs", tuple(self.cost_refs))

    def as_dict(self):
        return {
            "action": self.action,
            "target": self.target,
            "reason": self.reason,
            "cost_refs": list(self.cost_refs),
        }
