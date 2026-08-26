# Best Practices — Agentic Travel Build

What separates an agent from a generator, how the agent loop fails silently, and
what this build learned the hard way.

---

# 1. The maturity ladder

Four bands. The gap between them is architectural, not cosmetic.

| Band | What it does | Example |
|---|---|---|
| **Common** | Detects a condition, lists options | *Detect a delay, suggest alternative flights* |
| **Interesting** | Generates a plan from unstructured input | *Build an itinerary from someone's social posts* |
| **Notable** | Reconciles state across systems and fixes a discrepancy before it becomes a fault | *Reconcile room inventory across channels before a double booking* |
| **Breakthrough** | **Treats the itinerary as a dependency graph, autonomously re-plans every downstream leg, and settles the difference** | — |

Read the first band again. **Detecting a disruption and listing alternatives is
the floor.** A trip generator with a good UI lands there, and most builds do.

The distance to the top band is four properties, not more features:

1. **A dependency graph** — the itinerary is a structure with edges, not a list.
2. **Downstream re-planning** — a change propagates, and you repair what it broke.
3. **Autonomy** — the agent decides the repair rather than surfacing options.
4. **Settlement** — the difference is actually handled, not just reported.

Build 1–3 and you are near the top. Skip the graph and no amount of polish gets
you past band 2.

The dividing question is simple: **does the system hold a commitment, or does it
answer a query?** A generator has nothing to invalidate. An agent has a
commitment that the world can contradict, and it has to notice and repair.

## The axis that decides everything

An agent that **decides** inside a money flow is the strongest form of the idea.
An agent that **generates prose** inside one is the weakest — and actively
dangerous.

They are the same axis. Design for a decision that a human would otherwise have
to make — release this, refund that, accept this fare difference — and you are
automatically immune to the failure mode at the other end. Free-form text
generation anywhere near settlement is the thing to design out.

---

# 2. The three ways an agent loop fails silently

The most important failure modes in agent engineering share one property:

> **None of them throw an error. The run finishes, the log looks clean, and the
> outcome is wrong.**

| Failure | What it looks like | The control |
|---|---|---|
| **Infinite loop** | No termination rule. The agent re-plans forever, burning budget on a goal it cannot reach. | A step budget **and an explicit way to give up** |
| **Stale data** | Observe returned a cached seat map. Act books a seat that sold two minutes ago. | **Re-read the world immediately before every write** |
| **False success** | The API returned 200, so the agent marked the task done. Nobody checked the outcome. | Assert the real-world outcome |

Each maps to something concrete to build.

**Stale data → re-verify after the human decision, before the write.** A price
check *before* confirmation tells you what the fare was when you asked. The
window that matters is between the human saying yes and the order existing, and
fares move in exactly that window. Bind the confirmation to the price displayed
and void it if the fare rose. A cheaper fare does not invalidate consent; a
dearer one does.

**False success → never render a stub as a success.** If an endpoint is
undocumented and you stubbed it, the status stream says so. A run that prints
"Payment complete" over a call that never fired is exactly this failure, and it
is the one claim that could actually mislead someone.

**Infinite loop → cap the repair loop.** This is the one this build got wrong
first. A repair loop with no ceiling will re-plan, re-negotiate, re-price and
re-plan again. Set a maximum round count, and define what happens when it runs
out: hand back to the human with the best surviving option and a plain statement
that the gap could not be closed. **"I could not solve this" is a successful
outcome. Looping until the budget is gone is not.**

---

# 3. Specify before building, verify before releasing

Where vibe-coding stops:

> **Working locally ≠ release ready.** Architecture, contracts and acceptance
> criteria were never decided before the code was generated.

The generate-run-refine cycle optimises for learning speed and is genuinely good
at exploration and reversible changes. It does not prove production readiness,
and the failure surfaces at integration: interface mismatch, broken flow,
validation gap — all in code that ran fine locally on the happy path.

**The lifecycle:** PRD → SPECIFY → PLAN → IMPLEMENT → VALIDATE, with three
controls running across all five — project context (codebase, standards, prior
decisions), tool access and approvals (permissions, human confirmation), and
automated checks (acceptance tests, security, release criteria).

Practical translation:

- **A written spec and a written build plan are artifacts, not overhead.** They
  are what lets a second person — or a second agent — continue the work, and they
  are the only durable record of what "finished" was supposed to mean.
- **Approve the plan before code is written** for new features. The plan itself
  becomes the review evidence.
- **Give measurable end states** for unattended work — "raise coverage above
  80%" — rather than open-ended direction.
- **Initialise the repo before the first long-running task**, not after.
  Isolated worktrees and clean rollbacks depend on it.
- **Install the capabilities you need on day one.** Time spent up front on
  tested skills and wired connectors is time not spent prompting the same
  behaviour by hand for the rest of the build.
- **Keep a knowledge artifact inside the repo** so what was learned survives
  into the next run instead of dying with the session.

**The one-shot trap:** a build that ships once, with no spec, nothing written
down, and nothing kept, cannot be picked up, extended or fixed three weeks
later. It also cannot be run anywhere but the machine it was built on — which is
a much bigger problem than it looks, because everything downstream of "does it
run in production" depends on it.

---

# 4. Money-path discipline

Capability groups, in the order a journey uses them:

| Group | Capability | Rule |
|---|---|---|
| **Discovery** | Fare search, schedules, availability | Read-only. Safe to hammer. |
| **Commitment** | Verify, hold, confirm | First write. Put a human in front of it. |
| **Money** | Payment, ancillaries, fare differences | No free-form generation anywhere near this. |
| **Aftercare** | Reshop, change, cancel, refund | **Where most travel value actually is.** |

Discovery is read-only, so an agent can explore freely. Everything past it moves
money or changes a traveller's plans, and every one of those belongs behind a
human confirmation.

Four practices follow.

**Make prices unforgeable, structurally.** Every figure on screen should be a
pointer into a provider response, dereferenced at render time — never a number a
model wrote. The strongest form is a message schema with **no amount field at
all**, so a model that emits one is rejected by the parser rather than caught by
a filter. Policy can be prompted around; a missing field cannot.

**Delegate a ceiling, not a wallet.** A spending limit the agent decides
*within* is a fundamentally different object from funds the agent holds. A
ceiling can be granted once, withdrawn later, and can veto — and it gives you
autonomous decision-making without the agent ever touching money.

**Reserve on commitment, settle on payment — never both.** Booking holds
authority; only payment spends it. Committing at both points charges the
traveller twice for one set of seats.

**Reserve the whole thing or refuse.** This build shipped a bug where the
mandate reserved only the outbound leg of a return trip — 37% under — because
the proposal carried one price pointer and the return leg lived under a
different response key that no derivation could reach. Two lessons generalise:

- **A pointer addresses one response.** If a purchase spans several, carry
  several pointers. Make the field plural in the schema and **refuse the
  singular form** rather than coercing it — coercion makes the broken shape
  legal input, and something will keep emitting it.
- **Guards must compare the same quantity on both sides.** That bug also
  silently disabled the staleness check: it compared an outbound-only figure
  against a whole-trip figure, and the smaller number always satisfies "not more
  than". **A guard whose two sides have different dimensions does not fail
  loudly — it passes silently, which is worse than having no guard at all.**

**Aftercare is the least crowded ground.** Most builds stop at booking. A repair
loop that re-shops and re-plans *after* commitment is both the hardest thing to
build and where the actual value sits.

---

# 5. What this build learned

Hard-won, and mostly not obvious in advance.

## Probe before you build a catalogue

Provider documentation typically names a handful of supported routes and warns
that arbitrary ones may return empty. A destination list assembled from
intuition produces a board full of gaps.

**Sweep the candidates live, record reachable / empty / error, and let the
catalogue be the result.** Record the empty ones explicitly so nobody re-adds
them hopefully six days later.

Probe hubs on **both** legs. A stopover city that answers in one direction
cannot carry a chain, and you will not discover that until it fails on camera.

## The city you search is not the airport you fly

A city-code search can return flights into a different airport in that city.
Anything that re-queries later — a re-price, a leg swap — must re-run *the query
that produced the offer*, never a new query built from the flown airport. Store
the search key on the node, not the airport.

## A hard constraint will silently overrule the clever part

The sharpest product finding from playtesting, and it generalises well beyond
travel.

A member typed *"kimchi and kpop"*. The semantic layer correctly ranked Seoul
and Busan first. The tightest budget in the group then deleted every Korean and
Japanese option, and the board returned five cities nobody had asked for.

Every rule was individually correct — the ceiling must be absolute, and it must
never be out-voted. The composite experience was *"it ignored what I said."*

**The fix is not to weaken the constraint. It is to show the collision.** Keep a
"matched, available, unaffordable" list naming the destination, the cheapest
real fare, the limit it broke, and by how much:

> *Seoul — matched on food and street food — cheapest 230.56, over the tightest
> ceiling of 210.00 by 20.56.*

That one line turns a silent veto into a decision somebody can act on. Any time
a hard constraint kills the thing the user explicitly asked for, **say so with
numbers.** Silence there is what makes a correct system feel broken.

## Silence is the worst failure mode in a recommender

Input the system could not interpret must be reported, never absorbed. A
recommender that cannot say *"I don't know that word"* will confidently
recommend the wrong thing forever — and look authoritative doing it.

Dense retrieval makes this **worse**, not better, because it always returns
something. A zero-similarity query must be surfaced as a failure to understand,
not allowed to fall through to a baseline ranking.

## Index by identity, not by a derived key

A key like `route@date` is shared by every leg combination on that date. Dozens
of distinct trips collapse into one entry, and each gets scored against
whichever sibling was written last. The ranking looks arbitrary and nothing
errors. Objects should be indexed as objects.

## Swap the node, never edit the price

When a change arrives, replace the object the provider returned rather than
mutating a number in place. Editing leaves the display showing one figure while
its pointer resolves to another — the screen saying 151.50 and the pointer
saying 96.00 — and the traceability guarantee becomes quietly false.

Return a **copy**, so before and after can be shown side by side and the new
world can be rejected.

## The best engineering does not photograph

The strongest thing in this build — the substrate that makes an invented price
impossible — is invisible in use. A viewer sees a flight board. Architecture
that has to be narrated always lands worse than behaviour that can be watched.

**Find the visible consequence of the invisible property.** For a repair loop,
it is the propagation chain rendered one line at a time:

```
outbound +42.00
fare 200.00 → 242.00
Marcus exceeds the ceiling he granted
group consensus invalidated
re-planning the dependent itinerary
```

Five lines a viewer understands instantly, every one a consequence of the
dependency graph. That is how you show an architecture instead of describing it.

---

# 6. Making the work visible

Nobody reads the repo. Whatever you show is the whole of what is understood.

**Show, in this order:**

1. **The outcome first.** Establish what the agent achieved before any
   mechanism. Mechanism without outcome reads as a science project.
2. **One live provider call**, visibly real. Everything downstream depends on
   whether the audience believes the data.
3. **The propagate-and-repair beat.** Something changes, the graph propagates,
   the agent re-plans, a constraint vetoes. This is the highest-value stretch in
   anything you show — rehearse it until it is deterministic.
4. **The control moment.** The human confirmation before a write, and the
   re-check after it.
5. **The receipt.** Every figure traceable to a response.

**Do not:**

- Open with architecture. Ship something real and let it run.
- Demo the happy path only. **A refused action — a ceiling veto, a stale
  confirmation — is stronger evidence than a success**, because a success proves
  nothing about the controls.
- Claim the platform's numbers as your own. A provider's "140+ airlines" is
  their capability, not your coverage. Say what **you** reached and how you
  measured it. A measured smaller number is more credible than a borrowed
  larger one.
- Narrate anything the viewer cannot see happening.

**Make it reproducible.** Record against a frozen capture of real responses so a
rate limit or an outage cannot kill the take — but capture the real thing first,
and say that is what you are replaying. Replay of real data is rigour. Synthetic
data dressed as real is the failure this whole discipline exists to avoid.

---

# 7. Keep the model out of the decision path

A core flow that depends on a top-tier model is expensive, non-reproducible, and
hard to audit. Two ways to avoid it, in increasing order of strength.

**Route deliberately.** Cheap tiers for routine edits and bulk work; the
expensive model reserved for the genuinely hard fraction. Choosing on purpose,
rather than defaulting to the best model for everything, is most of the win.

**Better: keep the model out of the core flow entirely.** If the decision path
is deterministic — static embeddings computed offline, arithmetic over provider
figures, a rules-based protocol — then the core flow depends on **no** model at
all. A language model is confined to rewriting a reason into readable text, and
the system still works correctly with it switched off.

That is worth saying out loud: **the agent's decisions are deterministic and
auditable; the model writes the sentences, not the numbers.** It also happens to
make the system reproducible, which is what lets you have acceptance tests that
mean anything.

**Have a story for running it somewhere other than your laptop**, even if the
demo runs locally. A build that only runs in one place has no answer to any
question about operating it, and everything downstream of that — cost, scale,
reliability — becomes unanswerable at the same moment.

---

# 8. Checklist

**Idea**

- [ ] Is there a dependency graph, or a list with a good UI?
- [ ] Does a change to one element **propagate** to what depends on it?
- [ ] Does the agent **decide** the repair, or only surface options?
- [ ] Is there a decision inside a money flow a human would otherwise make?
- [ ] Does the system hold a commitment, or only answer a query?

**Controls**

- [ ] Human confirmation in front of every write.
- [ ] The world re-read immediately before each write, and consent voided if it
      moved.
- [ ] A step budget on every loop, and an explicit give-up path.
- [ ] Outcomes asserted, never inferred from a 200.
- [ ] No free-form generation anywhere near settlement.
- [ ] Every figure traceable to a provider response.
- [ ] Authority reserved for the whole purchase, or the action refused.
- [ ] Stubs labelled as stubs, everywhere they surface.

**Evidence**

- [ ] Spec and plan written **before** implementation, kept in the repo.
- [ ] Acceptance tests that fail when a guarantee fails — especially the ones
      pinning behaviour a wrong implementation would pass silently.
- [ ] Coverage figures traceable to a recorded probe run, not asserted.
- [ ] Knowledge artifact exported into the repo.

**What you show**

- [ ] Outcome before mechanism.
- [ ] One live call, visibly real.
- [ ] The propagate-and-repair beat rehearsed until deterministic.
- [ ] At least one **refusal** on camera.
- [ ] No claim you cannot back with code.
