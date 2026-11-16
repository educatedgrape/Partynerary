"""Reconciliation — multi-city synthesis, the engine behind Option 2.

Option 1 optimises for the single best-fit or lowest-cost destination.
Option 2 asks who that leaves out, and whether a stopover fixes it at a
competitive overall price.

Gap analysis → hub selection → chained search → selection.
"""

from src.discovery import dataset
from src.discovery.routes import search_nodes
from src.discovery.vectors import embed, load_tokens, cosine, max_pool
from src.itinerary.graph import build_chain


MAX_HUB_CANDIDATES = 3
MAX_CHAIN_COMBOS = 200
MIN_STOPOVER_HOURS = 6
MAX_STOPOVER_HOURS = 72
MAX_DETOUR_FACTOR = 3.0


# ---------------------------------------------------------------------------
# Step 1 — Gap analysis
# ---------------------------------------------------------------------------

def gap_analysis(option1, members):
    """For each member, compute which stated preferences Option 1 does NOT satisfy.

    Returns a list of gap dicts:
        { member, satisfied, unsatisfied, weight }

    A member with no unmet preferences generates no gap.
    """
    if not members or not option1:
        return []

    dest_name = ""
    if hasattr(option1, "destination_name"):
        dest_name = option1.destination_name
    elif isinstance(option1, dict):
        dest_name = option1.get("destination_name", "")

    # Load the city matching the option1 destination
    cities = dataset.load()
    dest_city = None
    for c in cities:
        if c.city_name.lower() == dest_name.lower() or c.city_id == dest_name:
            dest_city = c
            break

    if dest_city is None:
        return []

    dest_keywords = set(kw.lower() for kw in dest_city.keywords)
    dest_vibes = set(v.lower() for v in dest_city.vibes)
    dest_terms = dest_keywords | dest_vibes

    gaps = []
    for member in members:
        prefs_text = getattr(member, "preferences", "")
        if not prefs_text:
            continue

        tokens = [t.lower() for t in prefs_text.split() if len(t) > 2]
        if not tokens:
            continue

        satisfied = [t for t in tokens if t in dest_terms]
        unsatisfied = [t for t in tokens if t not in dest_terms]

        if unsatisfied:
            weight = len(unsatisfied) / len(tokens) if tokens else 0
            gaps.append({
                "member": getattr(member, "member", str(member)),
                "satisfied": satisfied,
                "unsatisfied": unsatisfied,
                "weight": round(weight, 4),
            })

    return gaps


# ---------------------------------------------------------------------------
# Step 2 — Hub selection
# ---------------------------------------------------------------------------

def rank_hubs(gaps, option1_destination, origin="SIN"):
    """Rank candidate hubs by gap coverage, not by fare.

    The candidate pool is every REACHABLE city except the Option 1 destination.
    There is no hub list and no city carries a 'hub' flag.
    """
    if not gaps:
        return []

    cities = dataset.reachable()
    tokens, dim = load_tokens()

    # Exclude Option 1 destination and origin
    exclude = {option1_destination, origin}

    candidates = []
    for city in cities:
        if city.city_id in exclude:
            continue

        # Score this hub against all gaps
        hub_score = 0.0
        for gap in gaps:
            unsatisfied = gap["unsatisfied"]
            text = " ".join(unsatisfied)
            query_vec = embed(text, tokens, dim)
            if not query_vec or all(v == 0 for v in query_vec):
                continue

            # Match against city vectors
            city_vecs = []
            for key in ("keywords", "vibes"):
                v = city.vectors.get(key)
                if v:
                    city_vecs.append(v)
            for pv in city.vectors.get("phrases", []):
                if pv:
                    city_vecs.append(pv)

            if city_vecs:
                sim = max_pool(query_vec, city_vecs)
                hub_score += gap["weight"] * sim

        if hub_score > 0:
            candidates.append((city.city_id, hub_score))

    # Sort by hub_score descending
    candidates.sort(key=lambda x: -x[1])
    return candidates[:MAX_HUB_CANDIDATES]


# ---------------------------------------------------------------------------
# Step 3 — Chained search
# ---------------------------------------------------------------------------

def chain_for(client, origin, hub, destination, out_date,
              stopover_nights, destination_nights, party_size,
              destination_name=""):
    """One three-leg itinerary through hub. Returns (graphs, error).

    Reuses search_nodes(), so the seat filter and gap reporting are inherited.
    Every leg passes the seat filter independently — one unseatable leg kills
    the chain.
    """
    from datetime import datetime, timedelta

    out_date_str = str(out_date).replace("-", "")
    out_dt = datetime.strptime(out_date_str, "%Y%m%d")

    # Leg 1: origin → hub on out_date
    leg1_nodes, leg1_err = search_nodes(
        client, "outbound", origin, hub, out_date_str, party_size)
    if leg1_err or not leg1_nodes:
        return ([], "no routings %s→%s @%s" % (origin, hub, out_date_str))

    # Leg 2: hub → destination after stopover
    hub_date = (out_dt + timedelta(days=stopover_nights)).strftime("%Y%m%d")
    leg2_nodes, leg2_err = search_nodes(
        client, "stopover", hub, destination, hub_date, party_size)
    if leg2_err or not leg2_nodes:
        return ([], "no routings %s→%s @%s" % (hub, destination, hub_date))

    # Leg 3: destination → origin after destination stay
    dest_date = (datetime.strptime(hub_date, "%Y%m%d") +
                 timedelta(days=destination_nights)).strftime("%Y%m%d")
    leg3_nodes, leg3_err = search_nodes(
        client, "inbound", destination, origin, dest_date, party_size)
    if leg3_err or not leg3_nodes:
        return ([], "no routings %s→%s @%s" % (destination, origin, dest_date))

    # Cross-product, capped
    graphs = []
    combos = 0
    for n1 in leg1_nodes:
        for n2 in leg2_nodes:
            for n3 in leg3_nodes:
                if combos >= MAX_CHAIN_COMBOS:
                    break
                combos += 1
                try:
                    g = build_chain(
                        [n1, n2, n3],
                        party_size=party_size,
                        destination_name=destination_name,
                    )
                    if g.feasible:
                        graphs.append(g)
                except ValueError:
                    continue

    return (graphs, None)


# ---------------------------------------------------------------------------
# Step 4 — Reconcile: build Option 2
# ---------------------------------------------------------------------------

def reconcile(client, option1, members, origin, out_date, return_dates,
              party_size, destination_name=""):
    """Build Option 2 if a viable chain exists. Returns a result dict.

    If no gap exists, Option 2 is not constructed and the reason says so.
    No viable chain ⇒ no Option 2. Nothing is synthesised to fill the slot.
    """
    # Step 1: Gap analysis
    gaps = gap_analysis(option1, members)
    if not gaps:
        return {
            "option2": None,
            "gaps": [],
            "reason_if_none": "the group already agrees — no second option needed",
        }

    # Step 2: Hub selection
    opt1_dest = getattr(option1, "destination", "") or \
                (option1.get("destination", "") if isinstance(option1, dict) else "")
    hubs = rank_hubs(gaps, opt1_dest, origin)
    if not hubs:
        return {
            "option2": None,
            "gaps": gaps,
            "reason_if_none": "no hub candidates could close the preference gaps",
        }

    # Step 3: Chained search through each hub
    best_chain = None
    best_gap_coverage = 0

    for hub_id, hub_score in hubs:
        # Find hub name for destination_name
        cities = dataset.by_id()
        hub_city = cities.get(hub_id)
        hub_name = hub_city.city_name if hub_city else hub_id

        chains, err = chain_for(
            client, origin, hub_id, opt1_dest, out_date,
            stopover_nights=1, destination_nights=2,
            party_size=party_size, destination_name=destination_name)

        if err or not chains:
            continue

        for chain in chains:
            # Check ceilings
            ceilings = []
            for m in members:
                c = getattr(m, "ceiling", None)
                if c:
                    ceilings.append(c.amount)

            if ceilings and chain.per_person > min(ceilings):
                continue

            # Use hub_score as gap coverage proxy
            if hub_score > best_gap_coverage:
                best_gap_coverage = hub_score
                best_chain = chain

    if best_chain is None:
        return {
            "option2": None,
            "gaps": gaps,
            "reason_if_none": "no viable chain found through any hub",
        }

    # Step 4: Build the Option 2 result
    opt1_per_person = 0.0
    if hasattr(option1, "per_person"):
        opt1_per_person = option1.per_person
    elif isinstance(option1, dict):
        opt1_per_person = option1.get("per_person", 0)

    delta = round(best_chain.per_person - opt1_per_person, 2)

    return {
        "option2": best_chain,
        "gaps": gaps,
        "comparator": {
            "against": destination_name or opt1_dest,
            "delta": delta,
        },
        "reason_if_none": None,
    }
