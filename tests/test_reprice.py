"""Re-price gate — the flightNumber-sequence proof.

The gate once matched the confirmed flight by LIST POSITION in the fresh
response. Atlas reorders results when fares move — the normal case in exactly
the window the gate polices — so index matching routinely compared the
confirmed flight against an unrelated one and reported UNCHANGED with total
confidence.

These tests fail against the old index-based implementation:
  * the DEARER test asserts the exact delta of the matched routing, which
    the index-based code computes against the wrong flight;
  * the GONE test asserts GONE where the index-based code finds *some*
    routing at position zero and prices it instead.
"""

import unittest

from src.agent.executor import Confirmation
from src.atlas import cache as response_cache
from src.booking.reprice import check, GONE, DEARER, UNCHANGED

from tests.helpers import make_client


# The original response the confirmation was minted against.
OUT_KEY = "search.do:SIN-DPS@20260918"
# Same route/date — Atlas reordered the results and TR560 got dearer.
REORDERED_KEY = "search.do:SIN-DPS@20260918-reordered"
# Same route/date — the confirmed flight TR560 is absent entirely.
ABSENT_KEY = "search.do:SIN-DPS@20260918-absent"


class RepriceFlightNumberTest(unittest.TestCase):

    def setUp(self):
        response_cache.clear()
        self.client = make_client()
        # Mint the confirmation against the ORIGINAL response. routings[0]
        # is TR560 at 45.50 + 12.30.
        self.client.post("search.do", {}, fixture_key=OUT_KEY)
        self.confirmation = Confirmation(
            action="book_group", target="trip-1",
            approved_by="user", at="2026-09-15T10:00",
            price_shown=115.60)
        self.price_ref = "%s#routings[0].adultPrice" % OUT_KEY

    def tearDown(self):
        response_cache.clear()

    def test_reordered_response_matches_by_flight_number(self):
        """Fresh response reorders routings; the confirmed TR560 is now at
        index 1 and dearer. The gate must match it by flight number, not by
        position zero (which now holds GA836 at a different price)."""
        result = check(
            self.client, None, self.confirmation,
            date="20260918", party_size=2,
            origin="SIN", destination="DPS",
            price_ref=self.price_ref,
            force_key=REORDERED_KEY)

        self.assertEqual(result.verdict, DEARER)
        # The matched routing's flight numbers must equal the original's —
        # provable via the exact arithmetic of TR560's new price:
        # (55.00 + 13.10) * 2 + 0.00 = 136.20, not GA836's 202.80.
        self.assertEqual(result.new_price, 136.20)
        self.assertEqual(result.old_price, 115.60)
        self.assertEqual(result.delta, 20.60)

    def test_absent_flight_is_gone(self):
        """The confirmed TR560 is absent from the fresh response. The
        verdict is GONE — no fallback to position zero (TR562), no nearest
        price, no cheapest remaining routing."""
        result = check(
            self.client, None, self.confirmation,
            date="20260918", party_size=2,
            origin="SIN", destination="DPS",
            price_ref=self.price_ref,
            force_key=ABSENT_KEY)

        self.assertEqual(result.verdict, GONE)
        self.assertIn("TR560", result.detail)

    def test_unchanged_when_reordering_only(self):
        """Reordering without a price move is UNCHANGED once matched by
        flight number."""
        # Confirm TR562 (original routings[1]) — it is unchanged across the
        # reordered fixture despite moving from index 1 to index 2.
        ref = "%s#routings[1].adultPrice" % OUT_KEY
        confirmation = Confirmation(
            action="book_group", target="trip-1",
            approved_by="user", at="2026-09-15T10:00",
            price_shown=132.20)
        result = check(
            self.client, None, confirmation,
            date="20260918", party_size=2,
            origin="SIN", destination="DPS",
            price_ref=ref,
            force_key=REORDERED_KEY)

        self.assertEqual(result.verdict, UNCHANGED)
        self.assertEqual(result.delta, 0.0)


if __name__ == "__main__":
    unittest.main()
