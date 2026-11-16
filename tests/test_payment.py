"""Phase 7 — Payment tests.

Verifies mask(), redact(), TestCard safety, and the re-price module.
"""

import unittest
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.atlas.payment import mask, redact, TestCard, _guess_brand
from src.booking.reprice import (
    check, check_all, RepriceResult,
    GONE, DEARER, UNCHANGED, CHEAPER, SEVERITY,
)
from src.agent.executor import Confirmation
from src.atlas import cache as response_cache


# Fixture cache keys
OUT_KEY = "search.do:SIN-DPS@20260918"
RET_KEY = "search.do:DPS-SIN@20260922"


class TestMask(unittest.TestCase):
    """mask() shows only the last four digits."""

    def test_standard_visa(self):
        self.assertEqual(mask("4111111111111111"), "**** **** **** 1111")

    def test_with_spaces(self):
        self.assertEqual(mask("4111 1111 1111 1111"), "**** **** **** 1111")

    def test_short_number(self):
        self.assertEqual(mask("123"), "****")

    def test_empty_string(self):
        self.assertEqual(mask(""), "****")

    def test_none(self):
        self.assertEqual(mask(None), "****")


class TestRedact(unittest.TestCase):
    """redact() masks cardNumber and blanks cvv anywhere they appear."""

    def test_top_level_card(self):
        payload = {"cardNumber": "4111111111111111", "cvv": "123", "amount": 50}
        r = redact(payload)
        self.assertIn("****", r["cardNumber"])
        self.assertEqual(r["cvv"], "")
        self.assertEqual(r["amount"], 50)  # non-card fields preserved

    def test_nested_card(self):
        payload = {
            "order": {
                "payment": {"cardNumber": "5500000000000004", "cvv": "999"},
                "amount": 100,
            }
        }
        r = redact(payload)
        self.assertIn("****", r["order"]["payment"]["cardNumber"])
        self.assertEqual(r["order"]["payment"]["cvv"], "")

    def test_list_with_cards(self):
        payload = {
            "payments": [
                {"cardNumber": "4111111111111111", "cvv": "123"},
                {"cardNumber": "5500000000000004", "cvv": "456"},
            ]
        }
        r = redact(payload)
        for item in r["payments"]:
            self.assertIn("****", item["cardNumber"])
            self.assertEqual(item["cvv"], "")

    def test_none_payload(self):
        self.assertIsNone(redact(None))

    def test_original_unchanged(self):
        """redact deep-copies — the original is unchanged."""
        payload = {"cardNumber": "4111111111111111", "cvv": "123"}
        r = redact(payload)
        self.assertEqual(payload["cardNumber"], "4111111111111111")
        self.assertEqual(payload["cvv"], "123")


class TestTestCard(unittest.TestCase):
    """TestCard safety — repr is masked, describe never exposes PAN."""

    def _make_card(self):
        return TestCard(env={
            "ATLAS_TEST_CARD_NUMBER": "4111111111111111",
            "ATLAS_TEST_CARD_EXPIRY": "12/28",
            "ATLAS_TEST_CARD_CVV": "123",
            "ATLAS_TEST_CARD_HOLDER": "Test User",
            "ATLAS_TEST_PAYMENT_FAMILY": "VCC",
        })

    def test_repr_masked(self):
        """__repr__ never shows the full PAN."""
        card = self._make_card()
        r = repr(card)
        self.assertNotIn("4111111111111111", r)
        self.assertIn("**** **** **** 1111", r)

    def test_describe_no_pan(self):
        """describe() never exposes the full PAN."""
        card = self._make_card()
        d = card.describe()
        self.assertNotIn("4111111111111111", str(d))
        self.assertIn("****", d["masked_pan"])

    def test_describe_has_family_disclosure(self):
        card = self._make_card()
        d = card.describe()
        self.assertIn("family_disclosure", d)
        # The disclosure explains the VCC pass-through in words
        self.assertIn("passed through", d["family_disclosure"])

    def test_as_payload_returns_full_data(self):
        """as_payload() is the ONLY function returning full card data."""
        card = self._make_card()
        p = card.as_payload()
        self.assertEqual(p["cardNumber"], "4111111111111111")
        self.assertEqual(p["cvv"], "123")

    def test_unconfigured_card(self):
        card = TestCard(env={})
        self.assertFalse(card.configured)
        self.assertIsNone(card.as_payload())
        self.assertGreater(len(card.missing), 0)

    def test_describe_unconfigured(self):
        card = TestCard(env={})
        d = card.describe()
        self.assertEqual(d["masked_pan"], "not configured")
        self.assertFalse(d["configured"])


class TestGuessBrand(unittest.TestCase):

    def test_visa(self):
        self.assertEqual(_guess_brand("4111111111111111"), "Visa")

    def test_mastercard(self):
        self.assertEqual(_guess_brand("5500000000000004"), "Mastercard")

    def test_amex(self):
        self.assertEqual(_guess_brand("378282246310005"), "Amex")

    def test_unknown(self):
        self.assertEqual(_guess_brand(""), "unknown")


class TestSeverity(unittest.TestCase):
    """Severity ordering is data, not a chain of comparisons."""

    def test_gone_worst(self):
        self.assertGreater(SEVERITY[GONE], SEVERITY[DEARER])

    def test_dearer_worse_than_unchanged(self):
        self.assertGreater(SEVERITY[DEARER], SEVERITY[UNCHANGED])

    def test_unchanged_worse_than_cheaper(self):
        self.assertGreater(SEVERITY[UNCHANGED], SEVERITY[CHEAPER])


class TestReprice(unittest.TestCase):
    """Re-price module: check() and check_all()."""

    def setUp(self):
        response_cache.clear()
        # Seed the cache with original prices
        response_cache.put(OUT_KEY, {
            "success": True,
            "routings": [
                {"adultPrice": 45.50, "adultTax": 12.30, "transactionFee": 0.00,
                 "fromSegments": [{"flightNumber": "TR560"}]},
            ]
        })
        response_cache.put(RET_KEY, {
            "success": True,
            "routings": [
                {"adultPrice": 48.25, "adultTax": 13.00, "transactionFee": 0.00,
                 "fromSegments": [{"flightNumber": "TR561"}]},
            ]
        })

    def test_one_cheaper_one_dearer_yields_dearer(self):
        """One leg CHEAPER and one DEARER yields DEARER as the worst."""
        results = [
            RepriceResult(CHEAPER, 120.0, 110.0, -10.0, "ref1"),
            RepriceResult(DEARER, 130.0, 150.0, 20.0, "ref2"),
        ]
        worst = results[0]
        for r in results[1:]:
            if SEVERITY.get(r.verdict, 0) > SEVERITY.get(worst.verdict, 0):
                worst = r
        self.assertEqual(worst.verdict, DEARER)

    def test_one_gone_yields_gone(self):
        """One GONE among results yields GONE as the worst."""
        results = [
            RepriceResult(CHEAPER, 120.0, 110.0, -10.0, "ref1"),
            RepriceResult(GONE, 130.0, None, None, "ref2"),
        ]
        worst = results[0]
        for r in results[1:]:
            if SEVERITY.get(r.verdict, 0) > SEVERITY.get(worst.verdict, 0):
                worst = r
        self.assertEqual(worst.verdict, GONE)

    def tearDown(self):
        response_cache.clear()


class TestRepriceWithClient(unittest.TestCase):
    """Re-price using the mock client with frozen fixtures."""

    def setUp(self):
        response_cache.clear()
        from tests.helpers import make_client
        self.client = make_client()
        # Load fixtures into cache
        self.client.post("search.do", {}, fixture_key=OUT_KEY)
        self.client.post("search.do", {}, fixture_key=RET_KEY)

    def test_unchanged_when_same_fixture(self):
        """Re-pricing against the same fixture returns UNCHANGED."""
        confirmation = Confirmation(
            action="book_group", target="trip-1",
            approved_by="user", at="2026-09-15T10:00",
            price_shown=119.05)
        result = check(
            self.client, None, confirmation,
            date="20260918", party_size=2,
            origin="SIN", destination="DPS",
            price_ref="%s#routings[0].adultPrice" % OUT_KEY,
            force_key=OUT_KEY)
        self.assertEqual(result.verdict, UNCHANGED)

    def tearDown(self):
        response_cache.clear()


if __name__ == "__main__":
    unittest.main()
