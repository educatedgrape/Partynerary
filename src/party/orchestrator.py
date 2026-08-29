"""Orchestrator — the stage machine.

Stages:
  1  DATE CONSENSUS    agents concede over private date windows      [Group 01]
  2  ATLAS DISCOVERY   sweep both directions; Atlas proposes destinations
  3  CONSTRAINT CHECK  seats, structure, and EVERY member's ceiling
  3b RECONCILIATION    find unsatisfied members; query hubs
  4  DECISION NODE     the single user choice: Option 1 vs Option 2
  5  CONFIRM           the choice IS the confirmation                [Group 02]
  6  RE-PRICE          check every leg again, AFTER the choice
  7  ORDER + PAY       autonomous, under the standing confirmation   [Group 02/03]

  8  CHANGE / 9 PROPAGATE / 10 RE-PLAN / 11 RE-NEGOTIATE  -> back to 4

Nothing in the orchestrator talks to Atlas directly and nothing in it spends
anything; stages 5–7 hand a proposal to the executor, which applies its own
gates regardless of what the orchestrator believes.
"""

from src.party.concession import run_concession
from src.agents.member_agent import MemberAgent
from src.discovery.sweep import sweep, return_dates_for
from src.discovery.score import score_sweep, apply_ceilings
from src.discovery.reconcile import reconcile
from src.agents.pitch import pitch_booking, pitch_payment
from src.agent.executor import Executor, Confirmation
from src.agent.mandate import Mandate, ceiling_total_from_members
from src.agent.decision_log import DecisionLog
from src.booking.reprice import check_all, SEVERITY, DEARER, GONE
from src.itinerary.propagate import Change, propagate, PRICE, CEILING
from src.itinerary.replan import repair_loop, LoopOutcome


# Stage constants
DATE_CONSENSUS = 1
ATLAS_DISCOVERY = 2
CONSTRAINT_CHECK = 3
RECONCILIATION = 3.5
DECISION_NODE = 4
CONFIRM = 5
REPRICE = 6
ORDER_AND_PAY = 7
CHANGE = 8
PROPAGATE = 9
REPLAN = 10
RENEGOTIATE = 11


STAGE_NAMES = {
    DATE_CONSENSUS: "date consensus",
    ATLAS_DISCOVERY: "atlas discovery",
    CONSTRAINT_CHECK: "constraint check",
    RECONCILIATION: "reconciliation",
    DECISION_NODE: "decision node",
    CONFIRM: "confirm",
    REPRICE: "re-price",
    ORDER_AND_PAY: "order + pay",
    CHANGE: "change",
    PROPAGATE: "propagate",
    REPLAN: "re-plan",
    RENEGOTIATE: "re-negotiate",
}


class Orchestrator:
    """The stage machine. Coordinates the trip from date consensus through
    booking and into the repair loop.

    Does NOT talk to Atlas directly — delegates to the executor for any
    action that touches the money path.
    """

    def __init__(self, client, members=None, origin="SIN", party_size=2):
        self.client = client
        self.members = members or []
        self.origin = origin
        self.party_size = party_size

        self._stage = DATE_CONSENSUS
        self._agents = []
        self._agreed_date = None
        self._return_dates = []
        self._trips = []
        self._ranked = []
        self._option1 = None
        self._option2_result = None
        self._confirmation = None
        self._executor = None
        self._booked_trip = None
        self._log = DecisionLog()
        self._session_rounds = 0

    @property
    def stage(self):
        return self._stage

    @property
    def stage_name(self):
        return STAGE_NAMES.get(self._stage, "unknown")

    # -- Stage 1: Date consensus -------------------------------------------

    def run_date_consensus(self):
        """Run the concession protocol over private date windows."""
        self._stage = DATE_CONSENSUS
        self._agents = [MemberAgent(p) for p in self.members]

        if not self._agents:
            return {"settled": False, "reason": "no members"}

        state = run_concession(self._agents)
        if state.settled:
            self._agreed_date = state.agreed_date
            self._return_dates = return_dates_for(self._agreed_date)
            return {
                "settled": True,
                "agreed_date": self._agreed_date,
                "return_dates": self._return_dates,
                "rounds": state.round_no,
            }
        return {"settled": False, "rounds": state.round_no}

    # -- Stage 2: Atlas discovery -------------------------------------------

    def run_discovery(self, destinations=None):
        """Sweep both directions; Atlas proposes destinations."""
        self._stage = ATLAS_DISCOVERY

        if not self._agreed_date:
            return {"error": "no agreed date — run date consensus first"}

        trips, errors = sweep(
            self.client, self.origin, self._agreed_date,
            self._return_dates, self.party_size,
            destinations=destinations)

        self._trips = trips
        return {"trips": len(trips), "errors": errors}

    # -- Stage 3: Constraint check ------------------------------------------

    def run_constraint_check(self):
        """Score trips and filter by seats, structure, and ceilings."""
        self._stage = CONSTRAINT_CHECK

        ceilings = [m.ceiling for m in self.members if m.ceiling]
        ceiling_amounts = [c.amount for c in ceilings] if ceilings else None

        self._ranked = score_sweep(self._trips, ceilings=ceiling_amounts)
        survivors, vetoed = apply_ceilings(self._ranked, ceiling_amounts)

        if survivors:
            self._option1 = survivors[0].trip

        return {
            "total": len(self._ranked),
            "survivors": len(survivors),
            "vetoed": len(vetoed),
            "option1": self._option1,
        }

    # -- Stage 3b: Reconciliation -------------------------------------------

    def run_reconciliation(self):
        """Find unsatisfied members; build Option 2 via hub chains."""
        self._stage = RECONCILIATION

        if not self._option1:
            return {"option2": None, "reason": "no Option 1"}

        self._option2_result = reconcile(
            self.client, self._option1, self.members,
            self.origin, self._agreed_date, self._return_dates,
            self.party_size,
            destination_name=getattr(self._option1, "destination_name", ""))

        return self._option2_result

    # -- Stage 4: Decision node ---------------------------------------------

    def decide(self, choice):
        """The single user choice: 'option1' or 'option2'.

        Returns the chosen trip.
        """
        self._stage = DECISION_NODE

        if choice == "option2" and self._option2_result:
            trip = self._option2_result.get("option2")
            if trip:
                return trip

        return self._option1

    # -- Stage 5: Confirm ---------------------------------------------------

    def confirm(self, trip):
        """The choice IS the confirmation."""
        self._stage = CONFIRM

        per_person = round(trip.group_total / self.party_size, 2)
        self._confirmation = Confirmation(
            action="book_group",
            target=trip.key,
            approved_by="user",
            at="now",
            price_shown=per_person,
            price_refs=[leg.price_ref for leg in trip.legs],
        )

        # Set up the executor
        ceiling_total = ceiling_total_from_members(
            self.members, party_size=self.party_size)
        mandate = Mandate(ceiling_total)
        self._executor = Executor(
            mandate, confirmation=self._confirmation, log=self._log)

        return {
            "confirmed": True,
            "per_person": per_person,
            "group_total": round(trip.group_total, 2),
        }

    # -- Stage 6: Re-price --------------------------------------------------

    def run_reprice(self, trip):
        """Check every leg again, AFTER the choice."""
        self._stage = REPRICE

        legs = []
        for leg in trip.legs:
            legs.append({
                "origin": leg.origin,
                "destination": leg.destination,
                "date": leg.date,
                "price_ref": leg.price_ref,
            })

        worst, per_leg = check_all(
            self.client, legs, self._confirmation, self.party_size)

        if worst and worst.verdict in (DEARER, GONE):
            return {
                "reprice_ok": False,
                "verdict": worst.verdict,
                "detail": worst.detail,
            }

        return {"reprice_ok": True, "verdict": worst.verdict if worst else "N/A"}

    # -- Stage 7: Order + Pay -----------------------------------------------

    def run_order_and_pay(self, trip):
        """Autonomous booking and payment under the standing confirmation."""
        self._stage = ORDER_AND_PAY

        # Book
        proposal, payload = pitch_booking(trip, self.party_size)
        book_result = self._executor.execute(proposal, payload=payload)

        if not book_result.accepted:
            return {
                "booked": False,
                "stage": book_result.stage,
                "reason": book_result.reason,
            }

        # Pay
        pay_proposal, pay_payload = pitch_payment(trip, self.party_size)
        # Update confirmation for payment
        self._executor.confirmation = Confirmation(
            action="pay_group",
            target=trip.key,
            approved_by="user",
            at="now",
            price_shown=self._confirmation.price_shown,
        )
        pay_result = self._executor.execute(pay_proposal, payload=pay_payload)

        self._booked_trip = trip
        return {
            "booked": True,
            "paid": pay_result.accepted,
            "amount": book_result.amount,
            "remaining": self._executor.mandate.remaining,
            "stubbed": book_result.stage == "stubbed",
        }

    # -- Stages 8-11: Repair loop -------------------------------------------

    def handle_change(self, change, trip=None):
        """Handle a change — propagate, re-plan, return to decision."""
        trip = trip or self._booked_trip
        if not trip:
            return LoopOutcome(
                stopped_because="no_trip",
                rounds_used=0,
                detail="no trip to repair")

        self._stage = CHANGE

        # Stage 9: Propagate
        self._stage = PROPAGATE
        ceilings = [m.ceiling for m in self.members if m.ceiling]
        impact, repaired = propagate(trip, change, ceilings=ceilings)

        if impact.still_feasible and not impact.breached:
            return LoopOutcome(
                stopped_because="",
                rounds_used=0,
                best=repaired,
                detail="trip still holds after change")

        # Stage 10: Re-plan
        self._stage = REPLAN
        outcome = repair_loop(
            self.client, repaired, ceilings,
            self._agreed_date or "",
            self._return_dates or [],
            self.party_size,
            session_rounds_used=self._session_rounds,
        )

        self._session_rounds += outcome.rounds_used

        # Stage 11: Return to decision
        if outcome.best:
            self._stage = DECISION_NODE

        return outcome

    def change_ceiling(self, member_name, new_amount):
        """A member re-grants a different ceiling. Emits a CEILING change."""
        trip = self._booked_trip
        if not trip:
            return None

        change = Change(
            node_key="",
            kind=CEILING,
            was=None,
            now=new_amount,
            member=member_name,
            detail="%s re-granted ceiling to $%.2f" % (member_name, new_amount))

        return self.handle_change(change, trip)
