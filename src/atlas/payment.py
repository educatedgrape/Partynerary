"""Payment handling — card data from .env only, never in source or fixtures.

mask() and redact() on every path that could print a PAN. TestCard.__repr__
is defensively masked so a stray traceback cannot leak it.

as_payload() is the ONLY function returning full card data, called at exactly
one place under a standing confirmation.
"""

import copy
import os
import pathlib


def _load_env(path=None):
    """Parse a .env file for card data. Simple KEY=VALUE."""
    if path is None:
        here = pathlib.Path(__file__).resolve()
        for parent in [here.parent] + list(here.parents):
            candidate = parent / ".env"
            if candidate.is_file():
                path = candidate
                break
            if parent.name == "src":
                candidate = parent.parent / ".env"
                if candidate.is_file():
                    path = candidate
                    break
    if path is None or not pathlib.Path(path).is_file():
        return {}
    result = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip()
    return result


# ---------------------------------------------------------------------------
# Masking and redaction
# ---------------------------------------------------------------------------

def mask(pan):
    """Mask a PAN — show only the last four digits.

    '4111111111111111' → '**** **** **** 1111'
    """
    if not pan or not isinstance(pan, str):
        return "****"
    digits = pan.replace(" ", "").replace("-", "")
    if len(digits) < 4:
        return "****"
    last4 = digits[-4:]
    return "**** **** **** %s" % last4


def redact(payload):
    """Deep-copy a payload with cardNumber masked and cvv blanked.

    Runs on every path that could print a payload. Recurses into nested
    dicts and lists.
    """
    if payload is None:
        return None
    result = copy.deepcopy(payload)
    _redact_in_place(result)
    return result


def _redact_in_place(obj):
    """Recursively mask card fields in a mutable structure."""
    if isinstance(obj, dict):
        for key in obj:
            if key.lower() in ("cardnumber", "card_number"):
                obj[key] = mask(str(obj[key]))
            elif key.lower() in ("cvv", "cvc", "securitycode", "security_code"):
                obj[key] = ""
            else:
                _redact_in_place(obj[key])
    elif isinstance(obj, list):
        for item in obj:
            _redact_in_place(item)


# ---------------------------------------------------------------------------
# TestCard — reads from .env, never stores in source
# ---------------------------------------------------------------------------

class TestCard:
    """Card data from .env only. Never in source, never in a fixture,
    never in a prompt, never in the decision log, never committed.
    """

    def __init__(self, env=None):
        if env is None:
            env = _load_env()
        self._number = env.get("ATLAS_TEST_CARD_NUMBER", "")
        self._expiry = env.get("ATLAS_TEST_CARD_EXPIRY", "")
        self._cvv = env.get("ATLAS_TEST_CARD_CVV", "")
        self._holder = env.get("ATLAS_TEST_CARD_HOLDER", "")
        self._family = env.get("ATLAS_TEST_PAYMENT_FAMILY", "VCC")

    @property
    def configured(self):
        """True when all required fields are present."""
        return bool(self._number and self._expiry and self._cvv)

    @property
    def missing(self):
        """List of missing field names."""
        fields = {
            "ATLAS_TEST_CARD_NUMBER": self._number,
            "ATLAS_TEST_CARD_EXPIRY": self._expiry,
            "ATLAS_TEST_CARD_CVV": self._cvv,
        }
        return [k for k, v in fields.items() if not v]

    @property
    def family(self):
        """MOR (merchant of record) or VCC (card passed through)."""
        return self._family

    def describe(self):
        """The only card data any UI sees — brand, masked PAN, expiry."""
        brand = _guess_brand(self._number)
        return {
            "brand": brand,
            "masked_pan": mask(self._number) if self._number else "not configured",
            "expiry": self._expiry or "not configured",
            "configured": self.configured,
            "missing": self.missing,
            "family": self._family,
            "family_disclosure": _family_disclosure(self._family),
        }

    def as_payload(self):
        """The ONLY function that returns full card data.

        Called at exactly one place: the moment the executor builds a Group 03
        request under a standing confirmation.
        """
        if not self.configured:
            return None
        return {
            "cardNumber": self._number,
            "expiry": self._expiry,
            "cvv": self._cvv,
            "holder": self._holder,
        }

    def __repr__(self):
        """Defensively masked — a stray traceback cannot leak a PAN."""
        return "TestCard(masked=%s, expiry=%s, configured=%s)" % (
            mask(self._number) if self._number else "none",
            self._expiry or "none",
            self.configured,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _guess_brand(number):
    """Guess card brand from the first digits. Not a validator."""
    if not number:
        return "unknown"
    digits = number.replace(" ", "").replace("-", "")
    if digits.startswith("4"):
        return "Visa"
    if digits[:2] in ("51", "52", "53", "54", "55"):
        return "Mastercard"
    if digits[:4] == "2221" or digits[:2] in ("25", "26", "27"):
        return "Mastercard"
    if digits[:2] in ("34", "37"):
        return "Amex"
    return "unknown"


def _family_disclosure(family):
    """Human-readable disclosure for the payment family."""
    if family == "MOR":
        return ("Atlas is merchant of record and settles. "
                "The agent selects a stored method and never handles "
                "the instrument.")
    if family == "VCC":
        return ("Card data is passed through to the airline by this "
                "integration. This weakens the 'never processes payments' "
                "claim — disclosed.")
    return "Payment family %r is not characterised." % family
