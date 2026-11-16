"""Monotonic concession — the date resolution engine.

Rules:
  1. Each agent holds its principal's ranking PRIVATELY.
  2. Round 1: every agent names only its single favourite date.
  3. No date named by everyone -> each agent CONCEDES: names the next date
     down its own ranking. It may NEVER go back up.
  4. Consensus is the first date every agent has named.
  5. An agent that would concede past its reservation depth WITHDRAWS.

Termination is guaranteed: each round every agent either reveals one new date
or withdraws, and both are finite. Monotonicity is what makes the transcript
a record of narrowing positions rather than a progress bar.

Rounds are synchronous and simultaneous by design.
"""

from dataclasses import dataclass, field


@dataclass
class ConcessionMove:
    """One move in the concession transcript."""
    round_no: int
    member: str
    date: str           # the date they named this round
    conceded_from: str  # the previous date they held (empty on round 1)
    withdrawn: bool = False


@dataclass
class ConcessionState:
    """Full state of the concession process."""
    round_no: int = 0
    named: dict = field(default_factory=dict)       # {member: [dates named]}
    withdrawn: list = field(default_factory=list)    # members who withdrew
    agreed_date: str = ""
    settled: bool = False
    failed: bool = False
    moves: list = field(default_factory=list)        # ConcessionMove list

    @property
    def active_members(self):
        """Members still in the negotiation."""
        return [m for m in self.named if m not in self.withdrawn]


def run_concession(agents, max_rounds=20):
    """Run the concession protocol to completion.

    Args:
        agents:     list of MemberAgent objects
        max_rounds: safety ceiling on round count

    Returns:
        ConcessionState with the final outcome.
    """
    state = ConcessionState()

    # Initialize — each agent starts at position 0 in their ranking
    positions = {a.name: 0 for a in agents}

    for round_no in range(1, max_rounds + 1):
        state.round_no = round_no
        round_moves = []

        for agent in agents:
            if agent.name in state.withdrawn:
                continue

            ranking = agent.date_ranking
            pos = positions[agent.name]
            depth = agent.reservation_depth

            # Check reservation depth
            if depth is not None and pos >= depth:
                state.withdrawn.append(agent.name)
                state.moves.append(ConcessionMove(
                    round_no=round_no, member=agent.name,
                    date="", conceded_from="", withdrawn=True))
                continue

            # Name the date at current position
            if pos < len(ranking):
                date = ranking[pos]
            else:
                # Exhausted ranking — withdraw
                state.withdrawn.append(agent.name)
                state.moves.append(ConcessionMove(
                    round_no=round_no, member=agent.name,
                    date="", conceded_from="", withdrawn=True))
                continue

            # Record this round's move
            prev_dates = state.named.get(agent.name, [])
            conceded_from = prev_dates[-1] if prev_dates else ""

            if agent.name not in state.named:
                state.named[agent.name] = []
            state.named[agent.name].append(date)

            move = ConcessionMove(
                round_no=round_no,
                member=agent.name,
                date=date,
                conceded_from=conceded_from,
            )
            state.moves.append(move)
            round_moves.append(move)

        # Check for consensus — a date every active agent has named
        active = state.active_members
        if active:
            consensus = _find_consensus(state.named, active)
            if consensus:
                state.agreed_date = consensus
                state.settled = True
                return state

        # Check if everyone withdrew
        if not active:
            state.failed = True
            return state

        # Concede: advance each agent's position by 1
        for agent in agents:
            if agent.name not in state.withdrawn:
                positions[agent.name] += 1

    # Hit max_rounds without resolution
    state.failed = True
    return state


def _find_consensus(named, active_members):
    """Find a date that every active member has named.

    Returns the earliest consensus date, or empty string if none.
    """
    if not active_members:
        return ""

    # Collect all dates each active member has named
    sets = []
    for member in active_members:
        dates = set(named.get(member, []))
        sets.append(dates)

    # Intersection — dates named by everyone
    if not sets:
        return ""

    common = sets[0]
    for s in sets[1:]:
        common = common & s

    if not common:
        return ""

    # Return the earliest consensus date
    return min(common)
