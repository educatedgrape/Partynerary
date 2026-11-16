"""Decision log — audit trail for every executor decision.

Every proposal that enters the executor gets logged: accepted or refused,
at which gate, and why. The log is append-only and carries no card data,
no full PAN, and no CVV.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class DecisionEntry:
    """One entry in the decision log."""
    timestamp: str = ""
    action: str = ""
    target: str = ""
    stage: str = ""
    accepted: bool = False
    amount: float = 0.0
    reason: str = ""
    detail: str = ""

    def as_dict(self):
        return {
            "timestamp": self.timestamp,
            "action": self.action,
            "target": self.target,
            "stage": self.stage,
            "accepted": self.accepted,
            "amount": self.amount,
            "reason": self.reason,
            "detail": self.detail,
        }


class DecisionLog:
    """Append-only audit log for executor decisions.

    No card data, no PAN, no CVV, no full payment payload. The log records
    what happened and why, not the instrument used.
    """

    def __init__(self):
        self._entries = []

    @property
    def entries(self):
        return list(self._entries)

    def record(self, action, target, stage, accepted, amount=0.0,
               reason="", detail=""):
        """Append one decision entry."""
        entry = DecisionEntry(
            timestamp=datetime.now().isoformat(),
            action=action,
            target=target,
            stage=stage,
            accepted=accepted,
            amount=round(amount, 2),
            reason=reason,
            detail=detail,
        )
        self._entries.append(entry)
        return entry

    def clear(self):
        """Empty the log. Used by the test suite between cases."""
        self._entries.clear()

    def __len__(self):
        return len(self._entries)

    def __repr__(self):
        return "DecisionLog(%d entries)" % len(self._entries)
