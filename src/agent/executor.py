"""Executor — the five gates. Gate file.

Nothing reaches Atlas without passing five checks, and authority is reserved
for the whole itinerary or not at all.

Gates:
  1. SCHEMA       — well-formed proposal, no invented fields?
  2. DEREFERENCE  — does every ref point at a figure Atlas returned THIS RUN?
  3. MANDATE      — does the summed total fit under the granted ceiling?
  4. CONFIRMATION — is there a standing confirmation, and is it still valid?
  5. CALL         — only now does Atlas get touched.

The executor cannot see the itinerary, so the caller passes payload["legs"].
Without that count, a proposal with one ref for a two-leg trip is
indistinguishable from a legitimate one-leg proposal.
"""

from src.agent.proposal import (
    ActionProposal, ProposalSchemaError, ACTION_GROUPS,
    GROUP_SEARCH, GROUP_AFTERCARE, GROUP_AFTERWRITE, ALLOWED_KEYS,
)
from src.agent.mandate import Mandate, MandateError, AUTONOMOUS_GROUPS, AUTONOMOUS_ACTIONS
from src.agent.cost_ref import resolve, resolve_group_total, CostRefError
from src.agent.decision_log import DecisionLog
from src.atlas import cache as response_cache
from src.atlas.payment import TestCard, redact


# Endpoints for money-moving actions. Both UNCHARACTERISED — no HTTP fires.
ACTION_ENDPOINTS = {
    "book_group": "orderCommit.do",   # DOCUMENTED name, UNDOCUMENTED shape
    "pay_group":  "pay.do",           # NOT documented anywhere — placeholder path
    "change_order": "change_order.do",    # NOT documented
    "cancel_order": "cancel_order.do",    # NOT documented
    "refund_order": "refund_order.do",    # NOT documented
}
UNCHARACTERISED = set(ACTION_ENDPOINTS.values())


class ExecutionResult:
    """The outcome of running a proposal through the five gates."""

    def __init__(self, accepted, proposal, amount=0.0, stage="",
                 reason="", atlas_response=None, remaining=None,
                 amount_refs=None):
        self.accepted = accepted
        self.proposal = proposal
        self.amount = round(amount, 2)
        self.stage = stage
        self.reason = reason
        self.atlas_response = atlas_response
        self.remaining = remaining
        self.amount_refs = amount_refs or []

    def as_dict(self):
        return {
            "accepted": self.accepted,
            "action": self.proposal.action if self.proposal else "",
            "target": self.proposal.target if self.proposal else "",
            "amount": self.amount,
            "stage": self.stage,
            "reason": self.reason,
            "remaining": self.remaining,
            "stubbed": self.stage in ("stubbed",),
        }

    def __repr__(self):
        status = "OK" if self.accepted else "REFUSED"
        return "ExecutionResult(%s %s at %s: $%.2f)" % (
            status, self.proposal.action if self.proposal else "?",
            self.stage, self.amount)


class Confirmation:
    """Standing confirmation from the human — bound to a target and price.

    Not blanket authority. A fare rise voids it and returns control to the
    user.
    """

    def __init__(self, action, target, approved_by, at, price_shown,
                 price_refs=None, ceiling_shown=None):
        self.action = action
        self.target = target
        self.approved_by = approved_by
        self.at = at
        self.price_shown = float(price_shown)
        self.price_refs = price_refs or []
        self.ceiling_shown = ceiling_shown

    def still_valid_for(self, per_person_now):
        """Is this confirmation still valid for the current per-person price?

        Compares the SAME QUANTITY on both sides: the displayed per-person
        figure and the recomputed per-person figure. Comparing an
        outbound-only figure here would not merely weaken the check — it
        would invert it.
        """
        return per_person_now <= self.price_shown + 0.001  # rounding tolerance


class Executor:
    """Runs proposals through the five gates.

    Holds the mandate, the confirmation, and the decision log. The cache
    parameter defaults to the module-level RESPONSE_CACHE.

    Booking-reserves / payment-settled is the executor's own bookkeeping —
    the mandate only grants and holds authority, so the settled figure lives
    here and never on the ceiling.
    """

    def __init__(self, mandate, confirmation=None, card=None,
                 log=None, cache=None):
        self.mandate = mandate
        self.confirmation = confirmation
        self.card = card if card is not None else TestCard(env={})
        self.log = log if log is not None else DecisionLog()
        self.cache = cache if cache is not None else response_cache.RESPONSE_CACHE
        self._settled = 0.0
        self._credits = 0.0

    @property
    def settled(self):
        """What payment has committed — the executor's own accounting."""
        return self._settled

    @property
    def credits(self):
        """Recorded refunds — owed back, never spent. Displayed on the
        receipt with its own cost_ref; never a mutation of the grant."""
        return self._credits

    @property
    def remaining(self):
        """Authority left after holds and settlements, with recorded
        credits given back."""
        return round(
            self.mandate.remaining - self._settled + self._credits, 2)

    def execute(self, proposal, payload=None):
        """Run a proposal through all five gates.

        payload may carry:
          - adults: int — number of travellers (default 1)
          - legs: int — expected number of legs (for ref count check)
        """
        payload = payload or {}

        # Gate 1: SCHEMA
        result = self._gate_schema(proposal)
        if result:
            return result

        # Gate 2: DEREFERENCE
        result = self._gate_dereference(proposal, payload)
        if result:
            return result

        # We now have the dereferenced amount — stored in _last_amount
        amount = self._last_amount
        amount_refs = self._last_amount_refs

        # Gate 3: MANDATE
        result = self._gate_mandate(proposal, amount)
        if result:
            return result

        # Gate 4: CONFIRMATION (for non-autonomous actions)
        result = self._gate_confirmation(proposal, amount, payload)
        if result:
            return result

        # Gate 5: CALL (or stub)
        return self._gate_call(proposal, amount, amount_refs, payload)

    # -- Gate 1: SCHEMA -----------------------------------------------------

    def _gate_schema(self, proposal):
        """Well-formed proposal, no invented fields?"""
        if not isinstance(proposal, ActionProposal):
            return self._refuse(
                proposal, "schema",
                "expected ActionProposal, got %s" % type(proposal).__name__)

        # Check for invented fields in the dict representation
        d = proposal.as_dict()
        extra = set(d.keys()) - ALLOWED_KEYS
        if extra:
            return self._refuse(
                proposal, "schema",
                "proposal carries invented fields: %s" % sorted(extra))

        return None  # pass

    # -- Gate 2: DEREFERENCE ------------------------------------------------

    def _gate_dereference(self, proposal, payload):
        """Does every ref point at a figure Atlas returned THIS RUN?

        Sums across legs. The executor checks the leg count from payload.
        """
        amount, amount_refs = 0.0, []
        adults = int((payload or {}).get("adults") or 1)
        expected = int((payload or {}).get("legs") or 0)

        if expected and len(proposal.cost_refs) != expected:
            return self._refuse(
                proposal, "dereference",
                "proposal carries %d cost_ref(s) for a %d-leg trip — reserving "
                "part of an itinerary is indistinguishable from spending money "
                "nobody authorised"
                % (len(proposal.cost_refs), expected))

        for ref in proposal.cost_refs:
            try:
                leg_total, leg_refs = resolve_group_total(
                    ref, adults, self.cache)
                amount = round(amount + leg_total, 2)
                amount_refs.extend(leg_refs)
            except CostRefError as exc:
                return self._refuse(
                    proposal, "dereference",
                    "cost_ref %r could not be dereferenced: %s" % (ref, exc))

        self._last_amount = amount
        self._last_amount_refs = amount_refs
        return None  # pass

    # -- Gate 3: MANDATE ----------------------------------------------------

    def _gate_mandate(self, proposal, amount):
        """Does the summed total fit under the granted ceiling?

        Non-autonomous aftercare (change_order, cancel_order) skips the mandate
        check — authority was committed at booking, and the post-order
        accounting is the DIFFERENCE, not the total.
        """
        group = ACTION_GROUPS.get(proposal.action)
        if group == GROUP_AFTERWRITE:
            return None  # authority committed at booking; settle_difference handles diff
        try:
            self.mandate.check(amount, proposal.action)
        except MandateError as exc:
            return self._refuse(proposal, "mandate", str(exc))
        # Settlement-aware check: payment commits authority the mandate's
        # hold no longer shows, so the executor measures against its own
        # remaining figure as well.
        if amount > self.remaining + 0.001:
            return self._refuse(
                proposal, "mandate",
                "amount %.2f exceeds remaining authority %.2f after "
                "settlements" % (amount, self.remaining))
        return None  # pass

    # -- Gate 4: CONFIRMATION -----------------------------------------------

    def _gate_confirmation(self, proposal, amount, payload):
        """Is there a standing confirmation, and is it still valid?

        Autonomous actions skip this gate.
        """
        group = ACTION_GROUPS.get(proposal.action)
        if group in AUTONOMOUS_GROUPS:
            return None  # autonomous — no confirmation needed

        if self.confirmation is None:
            return self._refuse(
                proposal, "confirmation",
                "no standing confirmation for action %r" % proposal.action)

        # price_shown is the WHOLE-TRIP per-person figure the human saw,
        # and amount is the WHOLE-TRIP order total, so this divides to the
        # same quantity. Comparing an outbound-only figure here would not
        # merely weaken the check — it would invert it.
        adults = int((payload or {}).get("adults") or 1)
        per_person = round(amount / adults, 2) if adults else amount
        if not self.confirmation.still_valid_for(per_person):
            return self._refuse(
                proposal, "stale_confirmation",
                "per-person price $%.2f exceeds confirmed $%.2f — "
                "fare moved since confirmation; returning control to user"
                % (per_person, self.confirmation.price_shown))

        return None  # pass

    # -- Gate 5: CALL -------------------------------------------------------

    def _gate_call(self, proposal, amount, amount_refs, payload):
        """Only now does Atlas get touched.

        Both money endpoints (orderCommit.do, pay.do) are UNCHARACTERISED —
        no HTTP request fires. Records executed_stub.
        """
        action = proposal.action

        # Autonomous actions (search, aftercare) don't need Atlas
        group = ACTION_GROUPS.get(action)

        if action in ACTION_ENDPOINTS:
            endpoint = ACTION_ENDPOINTS[action]
            # Both are UNCHARACTERISED — stub, never call
            if endpoint in UNCHARACTERISED:
                # Booking holds authority; payment settles it — the executor
                # keeps the settlement books, the mandate only holds.
                if action == "book_group":
                    self.mandate.reserve(amount)
                elif action == "pay_group":
                    self.mandate.release(amount)
                    self._settled = round(self._settled + amount, 2)
                # change_order, cancel_order, refund_order: authority
                # already committed at booking; the mandate stays untouched

                self.log.record(
                    action=action, target=proposal.target,
                    stage="stubbed", accepted=True, amount=amount,
                    reason=proposal.reason,
                    detail="executed_stub — %s is uncharacterised" % endpoint)

                return ExecutionResult(
                    accepted=True, proposal=proposal,
                    amount=amount, stage="stubbed",
                    reason="executed_stub — %s endpoint shape not published"
                           % endpoint,
                    atlas_response={"status": "SUCCESS", "stubbed": True,
                                    "bookingReference": "MOCK-stub"},
                    remaining=self.remaining,
                    amount_refs=amount_refs)

        # For autonomous actions, just log and return
        self.log.record(
            action=action, target=proposal.target,
            stage="atlas", accepted=True, amount=amount,
            reason=proposal.reason)

        return ExecutionResult(
            accepted=True, proposal=proposal,
            amount=amount, stage="atlas",
            reason="autonomous action — no Atlas call required",
            atlas_response=None,
            remaining=self.remaining,
            amount_refs=amount_refs)

    def record_credit(self, amount, ref=None):
        """Record a refund as owed — never spent, never applied to the grant.

        The receipt shows it as money coming back with its own cost_ref.
        """
        amount = round(amount, 2)
        self._credits = round(self._credits + amount, 2)
        self.log.record(
            action="refund_order", target=ref or "order",
            stage="credit", accepted=True, amount=amount,
            detail="credit of $%.2f recorded, never spent" % amount)

    # -- Refusal helper -----------------------------------------------------

    def _refuse(self, proposal, stage, reason):
        """Log and return a refusal."""
        action = getattr(proposal, "action", "unknown") if proposal else "unknown"
        target = getattr(proposal, "target", "unknown") if proposal else "unknown"
        self.log.record(
            action=action, target=target,
            stage=stage, accepted=False,
            reason=reason)
        return ExecutionResult(
            accepted=False, proposal=proposal,
            amount=0.0, stage=stage, reason=reason)
