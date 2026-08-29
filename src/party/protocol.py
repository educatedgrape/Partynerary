"""Negotiation protocol — gate file.

The complete set of keys a move may carry. Anything else is a hallucination.

A move is the only object that crosses the agent boundary. It carries:
  - move:   the action type ('propose', 'concede', 'withdraw', 'accept')
  - member: who is speaking
  - subject: what they're talking about (a date, a destination)
  - reason: the ONLY free-text field — what they said out loud
  - cost_ref: a pointer into an Atlas response, never a literal number

Deliberately NO amount field — structural, not a prompt instruction.
The schema rejects anything that carries one.
"""

from dataclasses import dataclass

from src.agent import cost_ref


# The complete set of keys a move may carry. Anything else is a hallucination.
ALLOWED_KEYS = {"move", "member", "subject", "reason", "cost_ref"}

FORBIDDEN_KEYS = {"amount", "price", "fare", "total", "cost", "saving"}


class SchemaViolation(Exception):
    """A move carried a key outside the allowed set."""


@dataclass(frozen=True)
class NegotiationMove:
    """One speech act in the negotiation transcript.

    Carries NO amount field. A move with an amount key is rejected by the
    schema — the struct has no slot for it, so a generator that emits one
    is caught at parse time, not at review time.
    """
    move: str
    member: str
    subject: str
    reason: str           # the ONLY free-text field
    cost_ref: str = None  # pointer into an Atlas response, never a literal

    def validate(self):
        """Raise SchemaViolation if the move carries forbidden data."""
        # cost_ref must not look like a literal number
        if self.cost_ref is not None and not isinstance(self.cost_ref, str):
            raise SchemaViolation(
                "cost_ref must be a string pointer, got %s" % type(self.cost_ref))
        return True

    def as_dict(self):
        d = {
            "move": self.move,
            "member": self.member,
            "subject": self.subject,
            "reason": self.reason,
        }
        if self.cost_ref is not None:
            d["cost_ref"] = self.cost_ref
        # Verify no forbidden keys leaked in
        for key in d:
            if key in FORBIDDEN_KEYS:
                raise SchemaViolation(
                    "Move carries forbidden key %r" % key)
        return d


def validate_dict(d):
    """Validate a raw dict as a move. Raises SchemaViolation on bad keys."""
    extra = set(d.keys()) - ALLOWED_KEYS
    if extra:
        raise SchemaViolation(
            "Move carries unknown keys: %s" % sorted(extra))
    forbidden = set(d.keys()) & FORBIDDEN_KEYS
    if forbidden:
        raise SchemaViolation(
            "Move carries forbidden keys: %s" % sorted(forbidden))
    return NegotiationMove(
        move=d["move"],
        member=d["member"],
        subject=d["subject"],
        reason=d["reason"],
        cost_ref=d.get("cost_ref"),
    )


def render(move, cache=None):
    """Render a move for display.

    Dereferences the cost_ref at display time. A speech bubble carrying a
    digit in its reason is NOT the source of that number — the figure comes
    from the ref regardless of the words.

    Returns a dict with 'text' and optional 'figure' fields.
    """
    result = {
        "member": move.member,
        "move": move.move,
        "text": move.reason,
    }

    if move.cost_ref:
        try:
            figure = cost_ref.resolve(move.cost_ref, cache)
            result["figure"] = round(figure, 2)
            result["cost_ref"] = move.cost_ref
        except cost_ref.CostRefError:
            result["figure"] = None
            result["ref_error"] = "could not resolve %s" % move.cost_ref

    return result
