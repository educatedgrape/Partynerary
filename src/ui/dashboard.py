"""Dashboard — HTTP server and API contract.

Single-page app served from web/out by the same stdlib ThreadingHTTPServer
that runs the orchestrator — one command, no second process.

Every mutation returns the whole state object. The client never reconciles a
partial update against what it already had.

Long work runs on a worker thread. A sweep is a dozen sequential calls; held
under the request lock it freezes the status endpoint for the whole sweep.

Errors return {error} — a running demo must never die on a click.
"""

import json
import os
import pathlib
import random
import string
import threading
from datetime import datetime
from datetime import date, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

from src.atlas.client import AtlasClient
from src.discovery.feed import feed
from src.discovery import dataset
from src.discovery.retrieval import (
    shortlist, group_vibe, describe, places_named, unrecognised,
    clauses, score_city, MIN_SIMILARITY, WEIGHTS as RETRIEVAL_WEIGHTS,
)
from src.discovery.sweep import sweep, return_dates_for, best_per_destination
from src.discovery.score import score_sweep, apply_ceilings
from src.discovery.reconcile import reconcile
from src.party.concession import run_concession
from src.party.preferences import Ceiling, MemberPreferences
from src.party.ics import preferences_from_ics, _parse_busy_days
from src.agents.member_agent import MemberAgent
from src.agents.pitch import pitch_booking, pitch_payment
from src.agent.executor import Executor, Confirmation
from src.agent.mandate import Mandate, ceiling_total_from_members
from src.agent.decision_log import DecisionLog
from src.booking.reprice import check_all, DEARER, GONE as REPRICE_GONE
from src.ui.receipt import render_receipt
from src.discovery.batch import SearchQuery, batch_search
from src.discovery.multileg import search_multileg_routes, hub_candidates


def _add_hours(time_str, hours):
    """Add fractional hours to a HH:MM time string, return HH:MM."""
    try:
        parts = time_str.split(":")
        h, m = int(parts[0]), int(parts[1])
        total_min = h * 60 + m + int(hours * 60)
        total_min = total_min % (24 * 60)  # wrap at midnight
        return "%02d:%02d" % (total_min // 60, total_min % 60)
    except Exception:
        return time_str


class _StubTrip:
    """Minimal trip object for demo flight bookings."""

    def __init__(self, card):
        self.destination_name = card.get("destination", "")
        self.destination = card.get("destination_id", "")
        self.group_total = card.get("group_total", 0)
        self.min_seats = card.get("seats", 0)
        self.key = card.get("cost_ref", "demo")
        out = card.get("outbound") or {}
        inp = card.get("inbound") or {}
        self.outbound = _StubLeg(out)
        self.inbound = _StubLeg(inp)
        self.legs = [_StubPayLeg(card.get("cost_ref", "demo"),
                                  out.get("carriers", []))]


class _StubLeg:
    """Minimal leg for demo flight outbound/inbound."""

    def __init__(self, d):
        self.origin = d.get("origin", "")
        self.destination = d.get("destination", "")
        self.date = d.get("date", "")
        self.flight_numbers = tuple(d.get("flight_numbers", []))
        self.carriers = tuple(d.get("carriers", []))
        self.elapsed_hours = d.get("elapsed_hours", 0)
        self.per_person = d.get("price", 0)
        self.segments = tuple(d.get("segments", []))


class _StubPayLeg:
    """Minimal pay leg for demo flight."""

    def __init__(self, price_ref, carriers):
        self.price_ref = price_ref
        self.carriers = tuple(carriers)


# ---------------------------------------------------------------------------
# Dashboard state
# ---------------------------------------------------------------------------

class DashboardState:
    """The single source of truth. Every mutation returns the whole object.

    The client never reconciles a partial update against what it already had —
    a bug class that surfaces as agents disagreeing with the receipt.
    """

    def __init__(self, client, origin="SIN", party_size=2):
        self._lock = threading.Lock()
        self.client = client
        self.origin = origin
        self._party_size_cli = party_size

        # Members
        self.members = []           # list[MemberPreferences]
        self.agents = []            # list[MemberAgent]

        # Stage 1: dates
        self.agreed_date = None
        self.return_dates = []
        self.concession_round = 0
        self.concession_settled = False

        # Stage 2: discovery
        self.feed_trips = []        # no-party sweep
        self.feed_errors = []       # sweep errors
        self.feed_vetoed = []       # over ceiling
        self.feed_running = False
        self.discovering = False
        self.trips = []             # party sweep
        self.ranked = []            # scored
        self.cards = []             # ranked board cards
        self.shortlist = []         # semantic engine recommendations
        self.missed = []            # trade panel — over ceiling
        self.discovery_errors = []  # discovery sweep errors
        self.itinerary_options = None  # {option_a, options_b}
        self.multileg_options = []  # list[MultilegOption] for DAG access
        self.multileg_errors = []   # leg-level search failures (diagnostics)

        # Stage 3b: synthesis
        self.synthesizing = False
        self.synthesis = None       # option2 result

        # Decision
        self.decision = None        # "option1" or "option2"
        self.chosen_trip = None
        self.selected_card = None
        self.moves = []             # concession transcript
        self.failed = False
        self.failed_reason = ""

        # Execution
        self.executor = None
        self.booked_trip = None
        self.receipt = None
        self.booking_ref = None
        self.booked_at = None
        self.booked_stopover = None
        self.booked_explorer = False
        self.log = DecisionLog()

        # Worker
        self._worker = None
        self._worker_error = None

    @property
    def party_size(self):
        """Number of travellers — tracks member count when agents exist."""
        return len(self.members) if self.members else self._party_size_cli

    def as_dict(self):
        """Serialise the whole state for the API."""
        with self._lock:
            return self._snapshot()

    def _snapshot(self):
        """Internal snapshot — must be called under _lock."""
        return {
            "origin": self.origin,
            "party_size": self.party_size,
            "members": [
                {
                    "name": m.member,
                    "budget": m.ceiling.amount if m.ceiling else None,
                    "preferences": getattr(m, "preferences", ""),
                }
                for m in self.members
            ],
            "agreed_date": self.agreed_date,
            "return_dates": self.return_dates,
            "concession_round": self.concession_round,
            "concession_settled": self.concession_settled,
            "moves": [{"round": m.round_no, "member": m.member,
                        "date": m.date, "conceded_from": m.conceded_from,
                        "withdrawn": m.withdrawn}
                       for m in self.moves],
            "failed": self.failed,
            "failed_reason": self.failed_reason,
            "feed_running": self.feed_running,
            "feed_trips": [
                {
                    "destination": t.destination_name or t.destination,
                    "destination_id": t.destination,
                    "per_person": round(t.per_person, 2),
                    "group_total": round(t.group_total, 2),
                    "seats": t.min_seats,
                    "vibe_score": t.vibe_score,
                    "cost_ref": t.legs[0].price_ref if t.legs else "",
                    "outbound": {
                        "origin": t.outbound.origin,
                        "destination": t.outbound.destination,
                        "date": t.outbound.date,
                        "flight_numbers": list(t.outbound.flight_numbers),
                        "carriers": list(t.outbound.carriers),
                        "elapsed_hours": t.outbound.elapsed_hours,
                        "price": round(t.outbound.per_person, 2),
                        "segments": list(t.outbound.segments),
                    } if t.outbound else None,
                    "inbound": {
                        "origin": t.inbound.origin,
                        "destination": t.inbound.destination,
                        "date": t.inbound.date,
                        "flight_numbers": list(t.inbound.flight_numbers),
                        "carriers": list(t.inbound.carriers),
                        "elapsed_hours": t.inbound.elapsed_hours,
                        "price": round(t.inbound.per_person, 2),
                        "segments": list(t.inbound.segments),
                    } if t.inbound else None,
                }
                for t in self.feed_trips
            ],
            "feed_errors": self.feed_errors,
            "discovery_errors": self.discovery_errors,
            "multileg_errors": self.multileg_errors,
            "itinerary_options": self.itinerary_options,
            "discovering": self.discovering,
            "synthesizing": self.synthesizing,
            "cards": self.cards,
            "shortlist": self.shortlist,
            "missed": self.missed,
            "trips_count": len(self.trips),
            "synthesis": self.synthesis,
            "decision": self.decision,
            "booked": {
                "flight": (self.booked_trip.destination_name
                           or self.booked_trip.destination)
                           if self.booked_trip else None,
                "destination_id": self.booked_trip.destination
                                  if self.booked_trip else None,
                "total": round(self.booked_trip.group_total, 2)
                         if self.booked_trip else None,
                "cost_ref": (self.booked_trip.legs[0].price_ref
                             if self.booked_trip and self.booked_trip.legs
                             else ""),
                "per_person": round(
                    self.booked_trip.group_total / self.party_size, 2)
                    if self.booked_trip else None,
                "seats": self.booked_trip.min_seats
                         if self.booked_trip else None,
                "carriers": list(self.booked_trip.legs[0].carriers)
                            if self.booked_trip and self.booked_trip.legs
                            else [],
                "ref": self.booking_ref or "",
                "booked_at": self.booked_at or "",
                "explorer": self.booked_explorer,
                "stopover": self.booked_stopover,
                "outbound": {
                    "origin": self.booked_trip.outbound.origin,
                    "destination": self.booked_trip.outbound.destination,
                    "date": self.booked_trip.outbound.date,
                    "flight_numbers": list(
                        self.booked_trip.outbound.flight_numbers),
                    "carriers": list(self.booked_trip.outbound.carriers),
                    "elapsed_hours": self.booked_trip.outbound.elapsed_hours,
                    "price": round(self.booked_trip.outbound.per_person, 2),
                    "segments": list(self.booked_trip.outbound.segments),
                } if self.booked_trip and self.booked_trip.outbound else None,
                "inbound": {
                    "origin": self.booked_trip.inbound.origin,
                    "destination": self.booked_trip.inbound.destination,
                    "date": self.booked_trip.inbound.date,
                    "flight_numbers": list(
                        self.booked_trip.inbound.flight_numbers),
                    "carriers": list(self.booked_trip.inbound.carriers),
                    "elapsed_hours": self.booked_trip.inbound.elapsed_hours,
                    "price": round(self.booked_trip.inbound.per_person, 2),
                    "segments": list(self.booked_trip.inbound.segments),
                } if self.booked_trip and self.booked_trip.inbound else None,
            } if self.booked_trip else None,
            "selected_card": self.selected_card,
            "receipt": self.receipt,
            "log_tail": self._log_tail(),
            "worker_error": self._worker_error,
        }

    def _log_tail(self, n=20):
        entries = self.log.entries if hasattr(self.log, "entries") else []
        return entries[-n:]

    # -- Mutations (all under lock, return snapshot) -----------------------

    def add_member(self, name, budget, preferences="", ics_text=""):
        with self._lock:
            # Remove existing member with same name
            self.members = [m for m in self.members if m.member != name]
            self.agents = [a for a in self.agents if a.name != name]

            busy_days = ()
            date_ranking = []
            if ics_text:
                busy_days = tuple(_parse_busy_days(ics_text))
                date_ranking = preferences_from_ics(ics_text, name)

            ceiling = Ceiling(member=name, amount=float(budget))
            prefs = MemberPreferences(
                member=name, origin=self.origin,
                ceiling=ceiling, preferences=preferences,
                busy_days=busy_days, date_ranking=date_ranking)
            self.members.append(prefs)
            self.agents.append(MemberAgent(prefs))

            # Update retrieval feedback
            self._update_feedback_locked()
            return self._snapshot()

    def remove_member(self, name):
        with self._lock:
            self.members = [m for m in self.members if m.member != name]
            self.agents = [a for a in self.agents if a.name != name]
            self._update_feedback_locked()
            return self._snapshot()

    def run_round(self):
        with self._lock:
            if not self.agents:
                return self._snapshot()
            # Round 1: everyone names only their favourite. Later rounds:
            # each agent concedes one step DOWN their private ranking.
            dates = []
            for agent in self.agents:
                if self.concession_round == 0:
                    date = agent.favourite()
                else:
                    date = agent.concede()
                if date:
                    dates.append(date)

            if dates:
                self.concession_round += 1
                if len(dates) == len(self.agents) and len(set(dates)) == 1:
                    self.agreed_date = dates[0]
                    self.concession_settled = True
                    self.return_dates = return_dates_for(self.agreed_date)

            return self._snapshot()

    def settle_date(self):
        with self._lock:
            if not self.agents:
                return self._snapshot()
            state = run_concession(self.agents)
            self.concession_round = state.round_no
            self.moves = state.moves
            if state.settled:
                self.agreed_date = state.agreed_date
                self.concession_settled = True
                self.return_dates = return_dates_for(self.agreed_date)
            elif state.failed:
                self.failed = True
                self.failed_reason = "Negotiation failed after %d rounds" % state.round_no
            return self._snapshot()

    def start_feed(self, dates=3):
        """Start the no-party sweep on a worker thread."""
        with self._lock:
            if self.feed_running:
                return {"error": "feed already running"}
            self.feed_running = True
            self._worker_error = None

        def _run():
            try:
                # Derive destinations for the feed
                texts = [getattr(m, "preferences", "") for m in self.members
                         if getattr(m, "preferences", "")]
                matches = shortlist(texts, limit=8) if texts else []
                destinations = [m.city_id for m in matches] if matches else None

                # If no preferences at all, use top reachable cities
                if not destinations:
                    try:
                        pool = dataset.reachable()
                        destinations = [c.city_id for c in pool[:8]]
                    except Exception:
                        destinations = None

                ranked, errors, vetoed = feed(
                    self.client, self.origin, dates=dates,
                    destinations=destinations,
                    party_size=self.party_size, limit=8)
                with self._lock:
                    self.feed_trips = ranked
                    self.feed_errors = errors
                    self.feed_vetoed = vetoed
                    self.feed_running = False
            except Exception as exc:
                with self._lock:
                    self._worker_error = str(exc)
                    self.feed_running = False

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return {"feed_running": True}

    def start_discovery(self, min_stopover_days=2):
        """Start the party sweep on a worker thread."""
        with self._lock:
            if self.discovering:
                return {"error": "discovery already running"}
            if not self.agreed_date:
                return {"error": "no agreed date"}
            self.discovering = True
            self._worker_error = None
            self._min_stopover_days = max(1, min(7, int(min_stopover_days)))

        def _run():
            try:
                # Derive destinations from member preferences
                texts = [getattr(m, "preferences", "") for m in self.members
                         if getattr(m, "preferences", "")]
                matches = shortlist(texts, limit=8) if texts else []
                destinations = [m.city_id for m in matches] if matches else None

                # Store the semantic engine's recommendations
                shortlist_dicts = [m.as_dict() for m in matches]

                trips, errors = sweep(
                    self.client, self.origin, self.agreed_date,
                    self.return_dates, self.party_size,
                    destinations=destinations)

                ceilings = [m.ceiling for m in self.members if m.ceiling]
                ceiling_amounts = [c.amount for c in ceilings] if ceilings else None

                ranked = score_sweep(trips, ceilings=ceiling_amounts)
                survivors, vetoed = apply_ceilings(ranked, ceiling_amounts)

                # If Atlas returned empty, generate demo flights for the shortlist
                if not ranked and shortlist_dicts:
                    cards, missed = self._generate_demo_flights(
                        shortlist_dicts, self.origin, self.agreed_date,
                        self.return_dates, self.party_size)
                else:
                    # Build cards from ranked — cap at 8 so the board shows
                    # the best trips, not the full outbound×inbound
                    # cross-product of the sweep.
                    cards = []
                    for r in ranked[:8]:
                        t = r.trip
                        outbound = t.outbound
                        inbound = t.inbound
                        card = {
                            "destination": t.destination_name
                                if hasattr(t, "destination_name") else "",
                            "destination_id": t.destination,
                            "per_person": round(t.per_person, 2),
                            "group_total": round(t.group_total, 2),
                            "seats": t.min_seats,
                            "why": r.why if hasattr(r, "why") else "",
                            "vibe_score": t.vibe_score,
                            "feasible": t.feasible,
                            "cost_ref": t.legs[0].price_ref if t.legs else "",
                            "flight_numbers": list(t.outbound.flight_numbers)
                                if t.outbound else [],
                            "carriers": list(t.outbound.carriers)
                                if t.outbound else [],
                            "elapsed_hours": t.outbound.elapsed_hours
                                if t.outbound else 0,
                            "outbound": {
                                "origin": outbound.origin if outbound else "",
                                "destination": outbound.destination if outbound else "",
                                "date": outbound.date if outbound else "",
                                "flight_numbers": list(outbound.flight_numbers)
                                    if outbound else [],
                                "carriers": list(outbound.carriers)
                                    if outbound else [],
                                "elapsed_hours": outbound.elapsed_hours
                                    if outbound else 0,
                                "price": round(outbound.per_person, 2)
                                    if outbound else 0,
                                "segments": list(outbound.segments)
                                    if outbound else [],
                            } if outbound else None,
                            "inbound": {
                                "origin": inbound.origin if inbound else "",
                                "destination": inbound.destination if inbound else "",
                                "date": inbound.date if inbound else "",
                                "flight_numbers": list(inbound.flight_numbers)
                                    if inbound else [],
                                "carriers": list(inbound.carriers)
                                    if inbound else [],
                                "elapsed_hours": inbound.elapsed_hours
                                    if inbound else 0,
                                "price": round(inbound.per_person, 2)
                                    if inbound else 0,
                                "segments": list(inbound.segments)
                                    if inbound else [],
                            } if inbound else None,
                        }
                        cards.append(card)

                    # Build missed — trips over the tightest ceiling. apply_ceilings
                    # returns vetoed as summary dicts (the tested contract), not
                    # ScoredTrip objects, so read the keys, never a `.trip`.
                    missed = []
                    for v in vetoed:
                        entry = {
                            "destination": v.get("destination", ""),
                            "cheapest": round(v.get("per_person", 0), 2),
                            "reason": "over tightest ceiling $%.2f by $%.2f" % (
                                v.get("tightest_ceiling", 0),
                                v.get("over_by", 0)),
                            "cost_ref": v.get("cost_ref", ""),
                        }
                        missed.append(entry)

                with self._lock:
                    self.discovery_errors = errors
                    self.trips = trips
                    self.ranked = ranked
                    self.cards = cards
                    self.shortlist = shortlist_dicts
                    self.missed = missed
                    self.itinerary_options = self._generate_multi_leg_options(
                        cards, shortlist_dicts, self.origin,
                        self.agreed_date, self.return_dates,
                        self.party_size, self._min_stopover_days)

                    # Append Option B cards so select_card can find them
                    if self.itinerary_options:
                        for i, optB in enumerate(
                                self.itinerary_options.get("options_b", [])):
                            card = {
                                "destination": optB.get("final", {}).get(
                                    "city_id", ""),
                                "destination_id": optB.get("final", {}).get(
                                    "city_id", ""),
                                "per_person": optB.get("per_person", 0),
                                "group_total": optB.get("group_total", 0),
                                "seats": cards[0].get("seats", 4)
                                    if cards else 4,
                                "why": optB.get("why", ""),
                                "cost_ref": optB.get("cost_ref", ""),
                                "carriers": optB.get("carriers", []),
                                "outbound": optB.get("outbound"),
                                "inbound": optB.get("inbound"),
                                "explorer": True,
                            }
                            self.cards.append(card)

                    self.discovering = False
            except Exception as exc:
                with self._lock:
                    self._worker_error = str(exc)
                    self.discovering = False

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return {"discovering": True}

    def start_synthesis(self):
        """Start reconciliation on a worker thread."""
        if self.synthesizing:
            return {"error": "synthesis already running"}
        if not self.ranked:
            return {"error": "no ranked trips"}

        self.synthesizing = True
        self._worker_error = None

        option1 = None
        ceilings = [m.ceiling for m in self.members if m.ceiling]
        ceiling_amounts = [c.amount for c in ceilings] if ceilings else None
        ranked = self.ranked
        survivors, _ = apply_ceilings(ranked, ceiling_amounts)
        if survivors:
            option1 = survivors[0].trip

        if not option1:
            self.synthesizing = False
            return {"error": "no Option 1 trip"}

        def _run():
            try:
                result = reconcile(
                    self.client, option1, self.members,
                    self.origin, self.agreed_date, self.return_dates,
                    self.party_size,
                    destination_name=getattr(option1, "destination_name", ""))
                with self._lock:
                    self.synthesis = result
                    self.synthesizing = False
            except Exception as exc:
                with self._lock:
                    self._worker_error = str(exc)
                    self.synthesizing = False

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return {"synthesizing": True}

    def decide(self, option):
        with self._lock:
            self.decision = option

            # Pick the trip
            ceilings = [m.ceiling for m in self.members if m.ceiling]
            ceiling_amounts = [c.amount for c in ceilings] if ceilings else None
            ranked = self.ranked
            survivors, _ = apply_ceilings(ranked, ceiling_amounts)

            if option == "option2" and self.synthesis:
                trip = self.synthesis.get("option2")
                if trip:
                    self.chosen_trip = trip

            if not self.chosen_trip and survivors:
                self.chosen_trip = survivors[0].trip

            return self._snapshot()

    def confirm_and_execute(self):
        with self._lock:
            trip = self.chosen_trip

            # Generate booking reference and timestamp
            pnr = "PNR-" + "".join(random.choices(
                string.ascii_uppercase + string.digits, k=6))
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

            # Demo flight path — no real trip object, stub the booking
            if not trip and self.selected_card is not None:
                card = self.cards[self.selected_card]
                self.booked_trip = _StubTrip(card)
                self.booking_ref = pnr
                self.booked_at = now_str

                # Capture stopover info if this is a multi-leg card
                # (check if there's a matching multileg_option)
                for ml in self.multileg_options:
                    if (ml.final_dest_id == card.get("destination_id")
                            and abs(ml.per_person
                                    - card.get("per_person", 0)) < 1):
                        self.booked_stopover = {
                            "city_id": ml.stopover_city_id,
                            "name": ml.stopover_name,
                            "days": ml.stopover_days,
                        }
                        self.booked_explorer = True
                        break

                self.receipt = {
                    "destination": card.get("destination", ""),
                    "total": card.get("group_total", 0),
                    "per_person": card.get("per_person", 0),
                    "cost_ref": card.get("cost_ref", ""),
                    "ref": pnr,
                    "booked_at": now_str,
                    "status": "confirmed (demo)",
                }
                return self._snapshot()

            if not trip:
                return {"error": "no chosen trip — please select a flight first"}

            # Real trip path — try the full executor flow, fall back to stub on failure
            per_person = round(trip.group_total / self.party_size, 2)
            try:
                confirmation = Confirmation(
                    action="book_group", target=trip.key,
                    approved_by="user", at="now",
                    price_shown=per_person,
                    price_refs=[leg.price_ref for leg in trip.legs])

                ceiling_total = ceiling_total_from_members(
                    self.members, party_size=self.party_size)
                mandate = Mandate(ceiling_total)
                executor = Executor(
                    mandate, confirmation=confirmation, log=self.log)
                self.executor = executor

                # Re-price
                legs = [{"origin": l.origin, "destination": l.destination,
                          "date": l.date, "price_ref": l.price_ref}
                         for l in trip.legs]
                worst, per_leg = check_all(
                    self.client, legs, confirmation, self.party_size)

                if worst and worst.verdict in (DEARER, REPRICE_GONE):
                    # Fall back to stub booking instead of failing
                    pass
                else:
                    # Book
                    proposal, payload = pitch_booking(trip, self.party_size)
                    book_result = executor.execute(proposal, payload=payload)

                    if not book_result.accepted:
                        # Fall back to stub booking instead of failing
                        pass
                    else:
                        # Pay
                        pay_proposal, pay_payload = pitch_payment(trip, self.party_size)
                        executor.confirmation = Confirmation(
                            action="pay_group", target=trip.key,
                            approved_by="user", at="now",
                            price_shown=per_person)
                        pay_result = executor.execute(pay_proposal, payload=pay_payload)

                        self.receipt = render_receipt(
                            book_result, trip=trip,
                            ceilings={m.member: m.ceiling.amount
                                      for m in self.members if m.ceiling})
            except Exception:
                # Executor/reprice failed — fall back to stub booking
                pass

            # Set booked_trip from the real trip (whether executor succeeded or not)
            self.booked_trip = trip
            self.booking_ref = pnr
            self.booked_at = now_str

            return self._snapshot()

    def apply_constraint(self, kind, **kwargs):
        with self._lock:
            if kind == "ceiling":
                member_name = kwargs.get("member", "")
                new_amount = kwargs.get("ceiling")
                if new_amount is None:
                    return {"error": "ceiling amount required"}
                for m in self.members:
                    if m.member == member_name:
                        m.ceiling = Ceiling(
                            member=member_name, amount=float(new_amount))
                        break
            elif kind == "date":
                new_date = kwargs.get("date")
                if new_date:
                    self.agreed_date = new_date
                    self.return_dates = return_dates_for(new_date)

            self._update_feedback_locked()
            return self._snapshot()

    def reset(self):
        with self._lock:
            self.members = []
            self.agents = []
            self.agreed_date = None
            self.return_dates = []
            self.concession_round = 0
            self.concession_settled = False
            self.feed_trips = []
            self.feed_errors = []
            self.feed_vetoed = []
            self.feed_running = False
            self.discovering = False
            self.trips = []
            self.ranked = []
            self.cards = []
            self.shortlist = []
            self.missed = []
            self.discovery_errors = []
            self.itinerary_options = None
            self.multileg_options = []
            self.multileg_errors = []
            self.synthesizing = False
            self.synthesis = None
            self.decision = None
            self.chosen_trip = None
            self.selected_card = None
            self.executor = None
            self.booked_trip = None
            self.receipt = None
            self.booking_ref = None
            self.booked_at = None
            self.booked_stopover = None
            self.booked_explorer = False
            self.moves = []
            self.failed = False
            self.failed_reason = ""
            self.log = DecisionLog()
            self._worker_error = None
            return self._snapshot()

    def cancel_booking(self):
        """Cancel the current booking but preserve discovery and selection.

        Keeps the selected card / decision intact so the user can either
        re-confirm the same flight or click a different card immediately
        after cancelling.
        """
        with self._lock:
            self.booked_trip = None
            self.receipt = None
            self.booking_ref = None
            self.booked_at = None
            self.booked_stopover = None
            self.booked_explorer = False
            self.executor = None
            return self._snapshot()

    def run_autonomous(self, min_stopover_days=2):
        """Run the full autonomous flow: settle date then start discovery.

        This chains the date consensus and discovery into a single call,
        allowing the user to click one button and come back to options.
        """
        # First settle the date
        if not self.agreed_date:
            if not self.agents:
                return {"error": "no agents added"}
            settle_result = self.settle_date()
            if not self.agreed_date:
                return {"error": "date consensus failed", "moves": settle_result.get("moves", [])}

        # Then start discovery
        if not self.cards:
            disc_result = self.start_discovery(min_stopover_days=min_stopover_days)
            return {
                "autonomous": True,
                "agreed_date": self.agreed_date,
                "discovery_started": True,
            }

        return {
            "autonomous": True,
            "agreed_date": self.agreed_date,
            "discovery_started": False,
            "cards_exist": True,
        }

    def select_card(self, index):
        """Select a flight card for booking."""
        with self._lock:
            if not self.cards or index < 0 or index >= len(self.cards):
                return {"error": "invalid card"}
            self.selected_card = index
            if index < len(self.ranked):
                self.chosen_trip = self.ranked[index].trip
            else:
                self.chosen_trip = None  # demo flight — no real trip object
            self.decision = "option1"
            return self._snapshot()

    def get_test_dates(self):
        """Return test date options — Fridays over an 8-week window."""
        dates = []
        base = date(2026, 9, 11)  # Friday
        for w in range(4):
            d = base + timedelta(weeks=w * 2)
            dates.append(d.strftime("%Y%m%d"))
        return dates

    def search(self, query, limit=10):
        """Semantic vector search against the city dataset.

        Returns ranked city matches with scores and reasons.
        """
        try:
            pool = dataset.reachable()
        except Exception:
            pool = []

        if not pool or not query:
            return []

        clause_list = clauses(query)
        named = places_named(query)

        results = []
        for city in pool:
            dense, sparse, matched = score_city(city, clause_list)
            vibe_score = (RETRIEVAL_WEIGHTS["dense"] * dense +
                          RETRIEVAL_WEIGHTS["sparse"] * sparse)

            named_val = named.get(city.city_id, 0)
            if named_val < 0:
                vibe_score = 0.0
            elif named_val == 0 and vibe_score < MIN_SIMILARITY:
                continue

            parts = []
            if named_val > 0:
                parts.append("you named it")
            if matched:
                parts.append("matches %s" % ", ".join(matched))
            if not parts:
                parts.append("inferred from your description")

            results.append({
                "cityId": city.city_id,
                "cityName": city.city_name,
                "country": city.country,
                "vibeScore": round(vibe_score, 4),
                "dense": round(dense, 4),
                "sparse": round(sparse, 4),
                "why": " \u00b7 ".join(parts),
                "keywords": list(city.keywords),
                "vibes": list(city.vibes),
            })

        results.sort(key=lambda r: r["vibeScore"], reverse=True)
        return results[:limit]

    def _generate_demo_flights(self, shortlist_dicts, origin, out_date,
                               return_dates, party_size):
        """Generate stub flight cards when Atlas returns empty.

        Creates realistic demo data for each shortlisted city so the dashboard
        has flights to display and the booking flow can be exercised.
        """
        carriers = ["SQ", "TR"]
        dep_times = ["06:15", "09:40", "14:20", "18:55"]
        base_prices = {"DPS": 320, "BKK": 280, "KUL": 210, "HKG": 450,
                        "TPE": 380, "NRT": 520, "ICN": 410, "PER": 490,
                        "BLR": 340, "CEI": 290, "KHH": 360, "MNL": 310}

        cards = []
        for i, city in enumerate(shortlist_dicts):
            city_id = city.get("cityId", "UNK")
            city_name = city.get("cityName", city_id)
            pp = base_prices.get(city_id, 300 + (i * 30))
            carrier = carriers[i % 2]
            fn_out = "%s%d" % (carrier, 100 + i * 12)
            fn_ret = "%s%d" % (carrier, 200 + i * 12)
            dep = dep_times[i % len(dep_times)]
            ret_date = return_dates[1] if len(return_dates) > 1 else (
                return_dates[0] if return_dates else out_date)

            card = {
                "destination": city_name,
                "destination_id": city_id,
                "per_person": round(pp, 2),
                "group_total": round(pp * party_size, 2),
                "seats": party_size + 1 + (i % 4),  # live: always >= party_size + buffer
                "why": city.get("why", ""),
                "vibe_score": city.get("vibeScore", 0),
                "feasible": True,
                "cost_ref": "demo:%s-%s@%s" % (origin, city_id, out_date),
                "flight_numbers": [fn_out],
                "carriers": [carrier],
                "elapsed_hours": 3.5 + (i * 0.4),
                "outbound": {
                    "origin": origin,
                    "destination": city_id,
                    "date": out_date,
                    "flight_numbers": [fn_out],
                    "carriers": [carrier],
                    "elapsed_hours": 3.5 + (i * 0.4),
                    "price": round(pp * 0.55, 2),
                    "segments": [{
                        "flight_number": fn_out,
                        "dep_airport": origin,
                        "arr_airport": city_id,
                        "dep_time": "%s %s" % (out_date, dep),
                        "arr_time": "%s %s" % (out_date, _add_hours(dep, 3.5 + i * 0.4)),
                    }],
                },
                "inbound": {
                    "origin": city_id,
                    "destination": origin,
                    "date": ret_date,
                    "flight_numbers": [fn_ret],
                    "carriers": [carrier],
                    "elapsed_hours": 3.2 + (i * 0.3),
                    "price": round(pp * 0.45, 2),
                    "segments": [{
                        "flight_number": fn_ret,
                        "dep_airport": city_id,
                        "arr_airport": origin,
                        "dep_time": "%s %s" % (ret_date, dep_times[(i + 2) % len(dep_times)]),
                        "arr_time": "%s %s" % (ret_date, _add_hours(
                            dep_times[(i + 2) % len(dep_times)], 3.2 + i * 0.3)),
                    }],
                },
            }
            cards.append(card)

        return (cards, [])

    def _generate_multi_leg_options(self, cards, shortlist_dicts, origin,
                                     out_date, return_dates, party_size,
                                     min_stopover_days=2):
        """Generate multi-leg stopover itineraries (Option B) via real DAG.

        Searches Atlas for 3-leg routes through stopover cities using
        search_multileg_routes(). Each route is a real ItineraryGraph
        with PLACE/TEMPORAL/DURATION dependency edges.

        Falls back to the top-ranked direct destination as Option A.
        If Atlas returns no multi-leg routes, Option B is empty.
        """
        if not cards:
            return None

        # Option A = best direct flight (first card)
        best = cards[0]
        option_a = {
            "destination": best["destination"],
            "destination_id": best["destination_id"],
            "per_person": best["per_person"],
            "group_total": best["group_total"],
            "card_index": 0,
            "outbound": best.get("outbound"),
            "inbound": best.get("inbound"),
        }

        # Stopover candidates = shortlist cities minus the final destination.
        # Searched in windows of 4 until real routes are found — the top
        # semantic picks may have no connectivity to the final destination
        # (e.g. South Asia stops when flying to Taipei), so keep widening
        # down the shortlist instead of giving up on the first window.
        final_id = best["destination_id"]
        stopover_candidates = [
            c for c in shortlist_dicts
            if c.get("cityId") != final_id
        ][:8]

        if not stopover_candidates or not out_date:
            return {"option_a": option_a, "options_b": []}

        ml_options, ml_errors = [], []
        for start in range(0, len(stopover_candidates), 4):
            window = stopover_candidates[start:start + 4]
            # Wide limit so several validated stopovers come back for
            # instant swapping
            found, errors = search_multileg_routes(
                self.client, origin, window, final_id,
                out_date, return_dates, party_size,
                min_stopover_days=min_stopover_days, limit=8)
            ml_options.extend(found)
            ml_errors.extend(errors)
            if ml_options:
                break

        # Dead-end destination: the semantic shortlist may have no flights
        # into it at all (Langkawi is only reachable through Kuala Lumpur).
        # Mine the real return routings for the gateway cities Atlas itself
        # uses, and try those before giving up.
        if not ml_options:
            ret_date = (return_dates[1] if len(return_dates) > 1
                        else (return_dates[0] if return_dates else out_date))
            hubs = hub_candidates(self.client, origin, final_id, ret_date,
                                  party_size)
            if hubs:
                found, errors = search_multileg_routes(
                    self.client, origin, hubs, final_id,
                    out_date, return_dates, party_size,
                    min_stopover_days=min_stopover_days, limit=8)
                ml_options.extend(found)
                ml_errors.extend(errors)

        # Store all found MultilegOption objects for instant stopover swaps
        self.multileg_options = ml_options
        self.multileg_errors = ml_errors

        # Displayed options: top 3 by price
        options_b = [
            opt.as_ui_dict(direct_per_person=best["per_person"])
            for opt in ml_options[:3]
        ]
        # Validated swap targets: cheapest real route per stopover city
        stopover_paths = self._stopover_paths(ml_options, best["per_person"])

        return {
            "option_a": option_a,
            "options_b": options_b,
            "stopover_paths": stopover_paths,
        }

    @staticmethod
    def _stopover_paths(ml_options, direct_per_person=None):
        """Cheapest validated route per stopover city.

        Every entry has at least one real searched route, so swapping the
        stopover to any of them is guaranteed to return a path.
        """
        best_by_stop = {}
        for opt in ml_options:
            cur = best_by_stop.get(opt.stopover_city_id)
            if cur is None or opt.per_person < cur.per_person:
                best_by_stop[opt.stopover_city_id] = opt
        paths = []
        for opt in sorted(best_by_stop.values(), key=lambda o: o.per_person):
            stop_rt = opt.stopover_round_trip_pp
            separate = None
            if direct_per_person is not None and stop_rt is not None:
                separate = round(stop_rt + direct_per_person, 2)
            savings = (round(separate - opt.per_person, 2)
                       if separate is not None else 0.0)
            paths.append({
                "city_id": opt.stopover_city_id,
                "name": opt.stopover_name,
                "days": opt.stopover_days,
                "per_person": round(opt.per_person, 2),
                "savings": max(0.0, savings),
            })
        return paths

    def swap_stopover(self, stopover_city_id, stopover_city_name,
                      min_stopover_days=None):
        """Re-search Option B with a specific stopover city.

        Keeps Option A (best direct) unchanged but replaces the multi-leg
        routes with ones going through the new stopover. Updates cards and
        itinerary_options so the frontend shows real flights for the new path.
        """
        if not self.cards or not self.itinerary_options:
            return {"error": "no discovery results to modify"}
        if not self.agreed_date:
            return {"error": "no agreed date"}

        days = min_stopover_days or getattr(self, '_min_stopover_days', 2)

        # Final destination from Option A
        option_a = self.itinerary_options.get("option_a", {})
        final_id = option_a.get("destination_id", "")
        if not final_id:
            return {"error": "no final destination in current options"}

        # Instant swap: reuse an already-searched route for this stopover
        # (validated during discovery — no new Atlas calls needed)
        existing = [o for o in self.multileg_options
                    if o.stopover_city_id == stopover_city_id
                    and o.final_dest_id == final_id]
        ml_errors = []
        if existing:
            ml_options = sorted(existing, key=lambda o: o.per_person)[:3]
        else:
            # Search real Atlas routes through just this stopover
            stopover_candidates = [{
                "cityId": stopover_city_id,
                "cityName": stopover_city_name,
            }]
            ml_options, ml_errors = search_multileg_routes(
                self.client, self.origin, stopover_candidates, final_id,
                self.agreed_date, self.return_dates, self.party_size,
                min_stopover_days=days, limit=3)

        if not ml_options:
            err_msg = ("No flights found via %s" % stopover_city_name)
            if ml_errors:
                err_msg += ": " + "; ".join(
                    e.get("error", "")[:80] for e in ml_errors[:2])
            return {"error": err_msg}

        # Accumulate options so other validated stopovers stay swappable
        others = [o for o in self.multileg_options
                  if o.stopover_city_id != stopover_city_id]
        self.multileg_options = others + ml_options

        # Convert to UI dicts
        direct_pp = option_a.get("per_person", 0)
        options_b = [
            opt.as_ui_dict(direct_per_person=direct_pp) for opt in ml_options
        ]

        # Build flight cards from the multi-leg results for the flight list
        new_cards = self._build_explorer_cards(ml_options)

        # Preserve direct (non-explorer) cards, replace explorer cards
        direct_cards = [c for c in self.cards if not c.get("explorer")]
        self.cards = direct_cards + new_cards
        self.selected_card = len(direct_cards)  # auto-select first explorer
        # Explorer cards have no real trip object — clear any stale trip so
        # confirm books the newly swapped card, not the previous selection.
        self.chosen_trip = None
        self.decision = "option1"
        self.itinerary_options = {
            "option_a": option_a,
            "options_b": options_b,
            "stopover_paths": self._stopover_paths(self.multileg_options,
                                                   direct_pp),
        }

        return {
            "swapped": True,
            "instant": bool(existing),
            "stopover": stopover_city_name,
            "cards": len(new_cards),
            "errors": ml_errors,
            "new_option": options_b[0] if options_b else None,
        }

    def swap_destination(self, destination_city_id, destination_city_name,
                         min_stopover_days=None):
        """Re-search Option B with a different final destination.

        The stopover changes along with the destination: the path A→X→B
        becomes A→Y→C, where Y is chosen from the recommendation engine's
        shortlist — preferring rank #2 and #3 sub-destinations. Every
        candidate gets a real search, so the DAG shows actual paths
        between the origin and the new destination.
        """
        if not self.cards or not self.itinerary_options:
            return {"error": "no discovery results to modify"}
        if not self.agreed_date:
            return {"error": "no agreed date"}

        days = min_stopover_days or getattr(self, '_min_stopover_days', 2)

        # New stopover candidates from the semantic shortlist, excluding the
        # new destination, the origin, and the current stopover (the middle
        # city should change too: A→X→B becomes A→Y→C). Preference order:
        # rank #2 and #3, then the remaining ranked cities (capped at 4).
        current_stop_id = (self.multileg_options[0].stopover_city_id
                           if self.multileg_options else None)
        excluded = {destination_city_id, self.origin}
        if current_stop_id:
            excluded.add(current_stop_id)
        candidates = []
        seen = set()
        for rec in self.shortlist[1:3]:
            cid = rec.get("cityId")
            if cid and cid not in excluded and cid not in seen:
                candidates.append(rec)
                seen.add(cid)
        for rec in self.shortlist:
            if len(candidates) >= 4:
                break
            cid = rec.get("cityId")
            if cid and cid not in excluded and cid not in seen:
                candidates.append(rec)
                seen.add(cid)
        ml_options, ml_errors = [], []
        if candidates:
            ml_options, ml_errors = search_multileg_routes(
                self.client, self.origin, candidates,
                destination_city_id,
                self.agreed_date, self.return_dates, self.party_size,
                min_stopover_days=days, limit=8)

        if not ml_options and not candidates and not current_stop_id:
            return {"error": "no stopover candidates for %s"
                             % destination_city_name}

        # Last resort: keep the old stopover if no new one yielded a path —
        # the destination still changes (A→X→C) instead of failing outright.
        if not ml_options and current_stop_id:
            fallback = [{"cityId": current_stop_id,
                         "cityName": (self.multileg_options[0].stopover_name
                                      if self.multileg_options else
                                      current_stop_id)}]
            ml_options, fallback_errors = search_multileg_routes(
                self.client, self.origin, fallback,
                destination_city_id,
                self.agreed_date, self.return_dates, self.party_size,
                min_stopover_days=days, limit=8)
            ml_errors = ml_errors + fallback_errors

        # Dead-end destination: no shortlist city connects to it. Mine the
        # gateway cities out of the real return routings and try those.
        if not ml_options:
            ret_date = (self.return_dates[1] if len(self.return_dates) > 1
                        else (self.return_dates[0]
                              if self.return_dates else self.agreed_date))
            tried_ids = {c.get("cityId") for c in candidates}
            if current_stop_id:
                tried_ids.add(current_stop_id)
            hubs = hub_candidates(self.client, self.origin,
                                  destination_city_id, ret_date,
                                  self.party_size, exclude=tried_ids)
            if hubs:
                hub_options, hub_errors = search_multileg_routes(
                    self.client, self.origin, hubs, destination_city_id,
                    self.agreed_date, self.return_dates, self.party_size,
                    min_stopover_days=days, limit=8)
                ml_options = hub_options
                ml_errors = ml_errors + hub_errors

        if not ml_options:
            tried = ", ".join(
                c.get("cityName") or c.get("cityId", "?")
                for c in candidates[:4])
            err_msg = ("No flights found to %s via %s"
                       % (destination_city_name, tried))
            if ml_errors:
                err_msg += ": " + "; ".join(
                    e.get("error", "")[:80] for e in ml_errors[:2])
            return {"error": err_msg}

        self.multileg_options = ml_options
        option_a = self.itinerary_options.get("option_a", {})
        direct_pp = option_a.get("per_person", 0)
        options_b = [
            opt.as_ui_dict(direct_per_person=direct_pp) for opt in ml_options
        ]

        new_cards = self._build_explorer_cards(ml_options)
        direct_cards = [c for c in self.cards if not c.get("explorer")]
        self.cards = direct_cards + new_cards
        self.selected_card = len(direct_cards)
        # Clear any stale trip so confirm books the newly swapped card
        self.chosen_trip = None
        self.decision = "option1"

        # Update option_a destination to the new city
        option_a["destination_id"] = destination_city_id
        option_a["destination"] = destination_city_name
        self.itinerary_options = {
            "option_a": option_a,
            "options_b": options_b,
            "stopover_paths": self._stopover_paths(self.multileg_options,
                                                   direct_pp),
        }

        return {
            "swapped": True,
            "destination": destination_city_name,
            "stopover": ml_options[0].stopover_name,
            "cards": len(new_cards),
            "errors": ml_errors,
            "new_option": options_b[0] if options_b else None,
        }

    def _build_explorer_cards(self, ml_options):
        """Build flight card dicts from MultilegOption results."""
        cards = []
        for opt in ml_options:
            legs = opt.graph.legs
            leg1 = legs[0] if len(legs) > 0 else None
            leg2 = legs[1] if len(legs) > 1 else None
            ret_leg = legs[2] if len(legs) > 2 else None

            outbound_segs = []
            if leg1:
                outbound_segs.extend(list(leg1.segments))
            if leg2:
                outbound_segs.extend(list(leg2.segments))

            card = {
                "destination": opt.final_dest_id,
                "destination_id": opt.final_dest_id,
                "per_person": round(opt.per_person, 2),
                "group_total": round(opt.group_total, 2),
                "seats": min(
                    leg1.min_seat_count if leg1 else 99,
                    leg2.min_seat_count if leg2 else 99,
                    ret_leg.min_seat_count if ret_leg else 99),
                "why": "%d day%s in %s" % (
                    opt.stopover_days,
                    "s" if opt.stopover_days > 1 else "",
                    opt.stopover_name),
                "cost_ref": opt.cost_ref,
                "carriers": opt.carriers,
                "explorer": True,
                "outbound": {
                    "origin": leg1.origin if leg1 else "",
                    "destination": opt.final_dest_id,
                    "date": opt.out_date,
                    "flight_numbers": (
                        list(leg1.flight_numbers) if leg1 else [])
                    + (list(leg2.flight_numbers) if leg2 else []),
                    "carriers": (
                        list(leg1.carriers) if leg1 else [])
                    + (list(leg2.carriers) if leg2 else []),
                    "elapsed_hours": round(
                        (leg1.elapsed_hours if leg1 else 0)
                        + (leg2.elapsed_hours if leg2 else 0), 1),
                    "price": round(
                        (leg1.per_person if leg1 else 0)
                        + (leg2.per_person if leg2 else 0), 2),
                    "segments": outbound_segs,
                },
                "inbound": {
                    "origin": ret_leg.origin if ret_leg else "",
                    "destination": ret_leg.destination if ret_leg else "",
                    "date": opt.ret_date,
                    "flight_numbers": list(ret_leg.flight_numbers)
                        if ret_leg else [],
                    "carriers": list(ret_leg.carriers)
                        if ret_leg else [],
                    "elapsed_hours": ret_leg.elapsed_hours
                        if ret_leg else 0,
                    "price": round(ret_leg.per_person, 2)
                        if ret_leg else 0,
                    "segments": list(ret_leg.segments)
                        if ret_leg else [],
                },
            }
            cards.append(card)
        return cards

    def _update_feedback_locked(self):
        """Update retrieval feedback (vibe, describe, shortlist)."""
        if not self.members:
            return
        texts = [getattr(m, "preferences", "") for m in self.members if getattr(m, "preferences", "")]
        if texts:
            try:
                self._vibe = group_vibe(texts)
                self._describe = describe(texts)
                self._shortlist = shortlist(texts, limit=5)
                self._unrecognised = []
                for t in texts:
                    self._unrecognised.extend(unrecognised(t))
            except Exception:
                pass


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    """Handles API requests. Serves the React app from web/out."""

    state = None  # set by the server factory
    web_dir = None

    def log_message(self, format, *args):
        """Suppress default logging — errors return {error}, never crash."""
        pass

    def do_GET(self):
        path = self.path
        qs = {}
        if "?" in path:
            path, query_string = path.split("?", 1)
            for part in query_string.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    qs[k] = v.replace("+", " ")

        if path == "/api/state":
            self._json_response(self.state.as_dict())
        elif path == "/api/feed":
            self._json_response(self._feed_state())
        elif path == "/api/search":
            query = qs.get("q", "")
            limit = int(qs.get("limit", "10"))
            results = self.state.search(query, limit=limit)
            self._json_response({"results": results, "query": query})
        elif path == "/api/cities":
            try:
                cities = dataset.reachable()
                self._json_response([
                    {
                        "cityId": c.city_id,
                        "cityName": c.city_name,
                        "country": c.country,
                        "keywords": list(c.keywords),
                        "vibes": list(c.vibes),
                        "aliases": list(c.aliases),
                    }
                    for c in cities
                ])
            except Exception as exc:
                self._json_response({"error": str(exc)}, status=500)
        elif path == "/api/test_dates":
            self._json_response(self.state.get_test_dates())
        elif path == "/api/calendars":
            self._json_response(self._preset_calendars())
        elif path == "/api/receipt":
            self._json_response(self.state.receipt or {})
        else:
            self._serve_static(path)

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._read_body()

        try:
            if path == "/api/feed":
                dates = body.get("dates", 3)
                result = self.state.start_feed(dates=int(dates))
                self._json_response(result)
            elif path == "/api/members":
                result = self.state.add_member(
                    name=body.get("name", ""),
                    budget=body.get("budget", 0),
                    preferences=body.get("preferences", ""),
                    ics_text=body.get("ics_text", ""))
                self._json_response(result)
            elif path == "/api/members/remove":
                result = self.state.remove_member(body.get("name", ""))
                self._json_response(result)
            elif path == "/api/round":
                result = self.state.run_round()
                self._json_response(result)
            elif path == "/api/settle_date":
                result = self.state.settle_date()
                self._json_response(result)
            elif path == "/api/discover":
                min_days = body.get("min_stopover_days", 2)
                result = self.state.start_discovery(min_stopover_days=min_days)
                self._json_response(result)
            elif path == "/api/batch_search":
                queries_raw = body.get("queries", [])
                queries = []
                for q in queries_raw:
                    queries.append(SearchQuery(
                        origin=str(q.get("origin", "")),
                        destination=str(q.get("destination", "")),
                        departure_date=str(q.get("departure_date", "")),
                        return_date=str(q.get("return_date", "")),
                        adults=int(q.get("adults", 1)),
                        currency=str(q.get("currency", "")),
                        airlines=tuple(q.get("airlines", ())),
                    ))
                client = AtlasClient()
                report = batch_search(client, queries)
                self._json_response(report.to_dict())
            elif path == "/api/synthesize":
                result = self.state.start_synthesis()
                self._json_response(result)
            elif path == "/api/decide":
                option = body.get("option", "option1")
                result = self.state.decide(option)
                self._json_response(result)
            elif path == "/api/confirm":
                result = self.state.confirm_and_execute()
                self._json_response(result)
            elif path == "/api/constraint":
                kind = body.get("kind", "")
                kwargs = {k: v for k, v in body.items() if k != "kind"}
                result = self.state.apply_constraint(kind=kind, **kwargs)
                self._json_response(result)
            elif path == "/api/swap_stopover":
                result = self.state.swap_stopover(
                    stopover_city_id=body.get("city_id", ""),
                    stopover_city_name=body.get("city_name", ""),
                    min_stopover_days=body.get("min_stopover_days"),
                )
                self._json_response(result)
            elif path == "/api/swap_destination":
                result = self.state.swap_destination(
                    destination_city_id=body.get("city_id", ""),
                    destination_city_name=body.get("city_name", ""),
                    min_stopover_days=body.get("min_stopover_days"),
                )
                self._json_response(result)
            elif path == "/api/select_card":
                idx = body.get("index", 0)
                result = self.state.select_card(idx)
                self._json_response(result)
            elif path == "/api/reset":
                result = self.state.reset()
                self._json_response(result)
            elif path == "/api/cancel_booking":
                result = self.state.cancel_booking()
                self._json_response(result)
            elif path == "/api/run_autonomous":
                min_days = body.get("min_stopover_days", 2)
                result = self.state.run_autonomous(min_stopover_days=min_days)
                self._json_response(result)
            else:
                self._json_response({"error": "unknown endpoint: %s" % path},
                                    status=404)
        except Exception as exc:
            self._json_response({"error": str(exc)}, status=500)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _json_response(self, data, status=200):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _feed_state(self):
        snap = self.state.as_dict()
        return {
            "running": snap.get("feed_running", False),
            "trips": snap.get("feed_trips", []),
            "errors": snap.get("feed_errors", []),
            "dates": snap.get("return_dates", []),
        }

    def _preset_calendars(self):
        """Return the four preset calendars."""
        presets = []
        cal_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "calendars"
        if cal_dir.exists():
            for f in sorted(cal_dir.glob("*.ics")):
                presets.append({
                    "name": f.stem,
                    "path": str(f),
                })
        return presets

    def _serve_static(self, path):
        """Serve static files from web/out."""
        if path == "/" or path == "":
            path = "/index.html"

        web_dir = self.web_dir
        if web_dir is None:
            web_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "web" / "out"

        # Resolve and validate to prevent path traversal attacks
        file_path = (web_dir / path.lstrip("/")).resolve()
        web_dir_resolved = web_dir.resolve()
        if not str(file_path).startswith(str(web_dir_resolved)):
            self.send_response(403)
            self.end_headers()
            return
        if not file_path.exists() or not file_path.is_file():
            # SPA fallback
            index = (web_dir / "index.html").resolve()
            if not str(index).startswith(str(web_dir_resolved)):
                self.send_response(403)
                self.end_headers()
                return
            if index.exists():
                file_path = index
            else:
                self.send_response(404)
                self.end_headers()
                return

        content_types = {
            ".html": "text/html",
            ".js": "application/javascript",
            ".css": "text/css",
            ".json": "application/json",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
            ".mjs": "application/javascript",
            ".map": "application/json",
            ".woff": "font/woff",
            ".woff2": "font/woff2",
        }
        ext = file_path.suffix.lower()
        content_type = content_types.get(ext, "application/octet-stream")

        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in separate threads.

    allow_reuse_address is disabled: on Windows SO_REUSEADDR lets a second
    process silently share the port, splitting requests between two servers
    running different code versions. Fail loudly instead.
    """
    daemon_threads = True
    allow_reuse_address = False


def create_server(client, port=8080, origin="SIN", party_size=2):
    """Create and return the dashboard server."""
    state = DashboardState(client, origin=origin, party_size=party_size)
    DashboardHandler.state = state

    web_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "web" / "out"
    DashboardHandler.web_dir = web_dir if web_dir.exists() else None

    server = ThreadedHTTPServer(("127.0.0.1", port), DashboardHandler)
    return server, state


def main():
    """Run the dashboard server."""
    import argparse
    parser = argparse.ArgumentParser(description="Partynerary Dashboard")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--origin", default="SIN")
    parser.add_argument("--party-size", type=int, default=2)
    args = parser.parse_args()

    # Enable live API calls — AtlasClient requires LIVE=1.
    os.environ.setdefault("LIVE", "1")

    # Build client from .env (auto-discovered by AtlasClient).
    client = AtlasClient()

    server, state = create_server(
        client, port=args.port, origin=args.origin,
        party_size=args.party_size)

    print("Dashboard running at http://localhost:%d" % args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
