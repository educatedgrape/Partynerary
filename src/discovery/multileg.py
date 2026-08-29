"""Multi-leg DAG search — real Atlas routes through stopover cities.

Each Option B route is a 3-leg chain:
    origin → stopover → final_destination → origin

Each leg is a real Atlas search via search_nodes(). The chain is built via
build_chain(), which derives PLACE/TEMPORAL/DURATION dependency edges
pairwise between consecutive legs.

Nothing here constructs synthetic flights. If Atlas returns no routings for
a leg, that stopover candidate is skipped — never a crash, never a fake price.
"""

from datetime import datetime, timedelta

from src.atlas.models import format_date
from src.discovery import dataset
from src.discovery.routes import search_nodes
from src.itinerary.graph import build_chain


def hub_candidates(client, origin, final_dest, ret_date, party_size,
                   exclude=(), limit=4):
    """Stopover candidates mined from real return routings.

    When the semantic shortlist has no connectivity to the final
    destination (dead-end hubs like Langkawi, reachable only through
    Kuala Lumpur), the return search final→origin itself reveals the
    gateway: every intermediate airport on a multi-segment routing is a
    city Atlas provably connects through both ways. Only cities present
    in the dataset qualify, ranked by how often they appear.

    Returns a list of {cityId, cityName} dicts shaped like shortlist
    entries, ready for search_multileg_routes().
    """
    nodes, _ = search_nodes(
        client, "inbound", final_dest, origin, ret_date, party_size,
        drop_unseatable=True)

    by_id = dataset.by_id()
    excluded = set(exclude) | {origin, final_dest}
    counts = {}
    for node in nodes:
        segs = node.segments
        if len(segs) < 2:
            continue
        # Every hop's arrival airport except the final one is an
        # intermediate gateway of the final→origin routing.
        for seg in segs[:-1]:
            code = seg.get("arr_airport", "")
            if not code or code in excluded or code not in by_id:
                continue
            counts[code] = counts.get(code, 0) + 1

    ranked = sorted(counts, key=lambda c: (-counts[c], c))[:limit]
    return [{"cityId": c, "cityName": by_id[c].city_name} for c in ranked]


def _add_days(date_str, n):
    """Add n days to a YYYYMMDD string, return YYYYMMDD."""
    d = format_date(date_str)
    try:
        dt = datetime.strptime(d, "%Y%m%d") + timedelta(days=n)
        return dt.strftime("%Y%m%d")
    except ValueError:
        return d


def _fmt_date_display(d_str):
    """YYYYMMDD → YYYY-MM-DD for display."""
    d = format_date(d_str)
    if len(d) == 8 and d.isdigit():
        return "%s-%s-%s" % (d[:4], d[4:6], d[6:8])
    return d


def search_multileg_routes(client, origin, stopover_candidates, final_dest,
                           out_date, return_dates, party_size,
                           min_stopover_days=2, limit=3):
    """Search Atlas for 3-leg routes through stopover cities.

    For each stopover candidate, searches three real Atlas segments:
      Leg 1: origin → stopover (out_date)
      Leg 2: stopover → final_dest (out_date + min_stopover_days)
      Return: final_dest → origin (ret_date)

    Builds ItineraryGraph chains via build_chain() with PLACE/TEMPORAL/
    DURATION dependency edges. Returns the cheapest feasible options.

    Args:
        client:              AtlasClient instance
        origin:              departure city code (e.g. 'SIN')
        stopover_candidates: list of dicts with 'cityId' and 'cityName'
        final_dest:          final destination city code
        out_date:            departure date (YYYYMMDD)
        return_dates:        list of return dates (YYYYMMDD)
        party_size:          number of travellers
        min_stopover_days:   minimum days between leg 1 and leg 2 (1-7)
        limit:               max number of options to return

    Returns:
        (options, errors) where options is a list of dicts with:
          - graph: ItineraryGraph
          - stopover: {city_id, name, days}
          - final: {city_id, name}
          - per_person: float
          - group_total: float
          - carriers: list
          - as_ui_dict(): dict for frontend
    """
    if not stopover_candidates:
        return ([], [])

    out_date = format_date(out_date)
    leg2_date = _add_days(out_date, min_stopover_days)
    ret_date = (return_dates[1] if len(return_dates) > 1
                else (return_dates[0] if return_dates else leg2_date))

    options = []
    errors = []

    for stop in stopover_candidates:
        stop_id = stop.get("cityId", "")
        stop_name = stop.get("cityName", stop_id)

        if stop_id == final_dest:
            continue  # Skip if stopover == final destination

        # Leg 1: origin → stopover
        leg1_nodes, leg1_err = search_nodes(
            client, "outbound", origin, stop_id, out_date, party_size,
            drop_unseatable=True)
        if leg1_err:
            errors.append({
                "stopover": stop_id,
                "leg": "leg1",
                "error": leg1_err,
            })
        if not leg1_nodes:
            continue

        # Separate-flight baseline: what a round trip to the stopover city
        # costs when booked as its own standalone flights (origin → stopover
        # → origin). Both sides use the cheapest standalone fare found by
        # the searches themselves, so the baseline is identical for every
        # route through this stopover — savings then reflect the route
        # choice, not the leg-1 fare of a particular option.
        stop_out_pp = min(n.per_person for n in leg1_nodes)
        stop_ret_nodes, stop_ret_err = search_nodes(
            client, "inbound", stop_id, origin, leg2_date, party_size,
            drop_unseatable=True)
        if stop_ret_err:
            errors.append({
                "stopover": stop_id,
                "leg": "stopover_return",
                "error": stop_ret_err,
            })
        stop_ret_pp = (min(n.per_person for n in stop_ret_nodes)
                       if stop_ret_nodes else None)

        # Leg 2: stopover → final_dest
        leg2_nodes, leg2_err = search_nodes(
            client, "stopover", stop_id, final_dest, leg2_date, party_size,
            drop_unseatable=True)
        if leg2_err:
            errors.append({
                "stopover": stop_id,
                "leg": "leg2",
                "error": leg2_err,
            })
        if not leg2_nodes:
            continue

        # Return: final_dest → origin
        ret_nodes, ret_err = search_nodes(
            client, "inbound", final_dest, origin, ret_date, party_size,
            drop_unseatable=True)
        if ret_err:
            errors.append({
                "stopover": stop_id,
                "leg": "return",
                "error": ret_err,
            })
        if not ret_nodes:
            continue

        # Cross-product: cheapest leg1 × cheapest leg2 × cheapest return
        # Take top 2 per leg to avoid combinatorial explosion
        for l1 in leg1_nodes[:2]:
            for l2 in leg2_nodes[:2]:
                for ret in ret_nodes[:2]:
                    try:
                        graph = build_chain(
                            legs=[l1, l2, ret],
                            party_size=party_size,
                            destination_name=final_dest,
                        )
                    except ValueError:
                        continue

                    # Skip infeasible graphs (dependency violations)
                    if not graph.feasible:
                        continue

                    options.append(MultilegOption(
                        graph=graph,
                        stopover_city_id=stop_id,
                        stopover_name=stop_name,
                        stopover_days=min_stopover_days,
                        final_dest_id=final_dest,
                        out_date=out_date,
                        leg2_date=leg2_date,
                        ret_date=ret_date,
                        stopover_outbound_pp=stop_out_pp,
                        stopover_return_pp=stop_ret_pp,
                    ))

    # Sort by per_person price ascending
    options.sort(key=lambda o: o.per_person)

    return (options[:limit], errors)


class MultilegOption:
    """One 3-leg route with its ItineraryGraph and stopover metadata.

    This is the Option B result — a real DAG with dependency edges,
    cost_ref traceability, and compatibility with propagation/replan.
    """

    def __init__(self, graph, stopover_city_id, stopover_name,
                 stopover_days, final_dest_id, out_date, leg2_date, ret_date,
                 stopover_outbound_pp=None, stopover_return_pp=None):
        self.graph = graph
        self.stopover_city_id = stopover_city_id
        self.stopover_name = stopover_name
        self.stopover_days = stopover_days
        self.final_dest_id = final_dest_id
        self.out_date = out_date
        self.leg2_date = leg2_date
        self.ret_date = ret_date
        # Cheapest standalone fares for the stopover round trip, searched
        # independently (for the separate-flights savings baseline).
        self.stopover_outbound_pp = stopover_outbound_pp
        self.stopover_return_pp = stopover_return_pp

    @property
    def per_person(self):
        return self.graph.per_person

    @property
    def group_total(self):
        return self.graph.group_total

    @property
    def carriers(self):
        """Unique carriers across all legs."""
        seen = []
        for leg in self.graph.legs:
            for c in leg.carriers:
                if c and c not in seen:
                    seen.append(c)
        return seen

    @property
    def label(self):
        return "%s + %s" % (self.stopover_name, self.final_dest_id)

    @property
    def cost_ref(self):
        """First leg's price ref for traceability."""
        return self.graph.legs[0].price_ref if self.graph.legs else ""

    @property
    def stopover_round_trip_pp(self):
        """Cost of flying to the stopover city as separate flights.

        origin → stopover plus stopover → origin, both as the cheapest
        standalone one-way fares found by the searches (identical for every
        route through this stopover). Falls back to a symmetric fare
        (return priced like the outbound) when the return search found
        nothing.
        """
        leg1 = self.graph.legs[0] if self.graph.legs else None
        out_pp = self.stopover_outbound_pp
        if out_pp is None:
            out_pp = leg1.per_person if leg1 else None
        if out_pp is None:
            return None
        ret_pp = self.stopover_return_pp
        if ret_pp is None:
            ret_pp = out_pp
        return round(out_pp + ret_pp, 2)

    @property
    def savings_label(self):
        """Computed at display time against the separate-flights baseline."""
        return ""

    def as_ui_dict(self, direct_per_person=None):
        """Dict compatible with the frontend Option B rendering.

        Includes graph metadata (dependencies, feasibility) and stopover
        details. Savings are measured against booking each destination as
        separate flights: a standalone round trip to the stopover city plus
        the direct round trip to the final destination (direct_per_person).
        """
        legs = self.graph.legs
        leg1 = legs[0] if len(legs) > 0 else None
        leg2 = legs[1] if len(legs) > 1 else None
        ret_leg = legs[2] if len(legs) > 2 else None

        savings = 0.0
        separate_flights_pp = None
        if direct_per_person is not None:
            stop_rt = self.stopover_round_trip_pp
            if stop_rt is not None:
                separate_flights_pp = round(
                    stop_rt + direct_per_person, 2)
                savings = round(separate_flights_pp - self.per_person, 2)
            else:
                savings = round(direct_per_person - self.per_person, 2)
        # Build outbound segments (leg1 + leg2 combined)
        outbound_segments = []
        if leg1:
            outbound_segments.extend(list(leg1.segments))
        if leg2:
            outbound_segments.extend(list(leg2.segments))

        # Build why text
        if savings > 0:
            delta = "save $%.0f pp vs separate flights" % savings
        elif savings < 0:
            delta = "$%.0f pp more than separate flights" % abs(savings)
        else:
            delta = "same price as separate flights"
        why = (
            "%d day%s in %s, then %s — %s (%s)"
        ) % (
            self.stopover_days,
            "s" if self.stopover_days > 1 else "",
            self.stopover_name,
            self.final_dest_id,
            delta,
            "+".join(self.carriers),
        )

        return {
            # Frontend-required fields
            "label": self.label,
            "stopover": {
                "city_id": self.stopover_city_id,
                "name": self.stopover_name,
                "days": self.stopover_days,
            },
            "final": {
                "city_id": self.final_dest_id,
                "name": self.final_dest_id,
            },
            "per_person": round(self.per_person, 2),
            "group_total": round(self.group_total, 2),
            "savings": round(savings, 2),
            "separate_flights_pp": separate_flights_pp,
            "carriers": self.carriers,
            "cost_ref": self.cost_ref,
            "outbound": {
                "origin": leg1.origin if leg1 else "",
                "destination": self.final_dest_id,
                "date": self.out_date,
                "flight_numbers": (list(leg1.flight_numbers) if leg1 else [])
                                  + (list(leg2.flight_numbers) if leg2 else []),
                "carriers": (list(leg1.carriers) if leg1 else [])
                            + (list(leg2.carriers) if leg2 else []),
                "elapsed_hours": round(
                    (leg1.elapsed_hours if leg1 else 0)
                    + (leg2.elapsed_hours if leg2 else 0), 1),
                "price": round(
                    (leg1.per_person if leg1 else 0)
                    + (leg2.per_person if leg2 else 0), 2),
                "segments": outbound_segments,
            },
            "inbound": {
                "origin": ret_leg.origin if ret_leg else "",
                "destination": ret_leg.destination if ret_leg else "",
                "date": self.ret_date,
                "flight_numbers": list(ret_leg.flight_numbers) if ret_leg else [],
                "carriers": list(ret_leg.carriers) if ret_leg else [],
                "elapsed_hours": ret_leg.elapsed_hours if ret_leg else 0,
                "price": round(ret_leg.per_person, 2) if ret_leg else 0,
                "segments": list(ret_leg.segments) if ret_leg else [],
            },
            "why": why,
            # Graph metadata — for propagation/replan/aftercare
            "graph_key": self.graph.key,
            "feasible": self.graph.feasible,
            "is_chain": self.graph.is_chain,
            "dependencies": [
                {
                    "kind": d.kind,
                    "from_leg": d.from_leg,
                    "to_leg": d.to_leg,
                    "satisfied": d.satisfied,
                    "detail": d.detail,
                }
                for d in self.graph.dependencies
            ],
            "cost_refs": list(self.graph.cost_refs),
        }

    def __repr__(self):
        return "MultilegOption(%s → %s → %s $%.2f pp)" % (
            self.graph.origin, self.stopover_city_id,
            self.final_dest_id, self.per_person)
