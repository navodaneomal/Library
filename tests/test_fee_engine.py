from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from apps.fees.engine import quote_late_fee

DUE = date(2026, 10, 15)


def fee(on, **kw):
    params = {"per_day": Decimal("50"), "grace_days": 0, "max_fee": Decimal("1000")}
    params.update(kw)
    return quote_late_fee(DUE, on, **params)


class FeeEngineTests(SimpleTestCase):
    def test_returned_on_due_date_costs_nothing(self):
        quote = fee(date(2026, 10, 15))
        self.assertEqual(quote.amount, Decimal("0.00"))
        self.assertEqual(quote.days_overdue, 0)

    def test_returned_early_costs_nothing(self):
        self.assertEqual(fee(date(2026, 10, 1)).amount, Decimal("0.00"))

    def test_one_day_late_is_one_daily_fee(self):
        quote = fee(date(2026, 10, 16))
        self.assertEqual(quote.days_overdue, 1)
        self.assertEqual(quote.amount, Decimal("50.00"))

    def test_prompt_example_four_days_is_200(self):
        self.assertEqual(fee(date(2026, 10, 19)).amount, Decimal("200.00"))

    def test_grace_period_days_are_free(self):
        quote = fee(date(2026, 10, 19), grace_days=2)
        self.assertEqual(quote.days_overdue, 4)
        self.assertEqual(quote.chargeable_days, 2)
        self.assertEqual(quote.amount, Decimal("100.00"))

    def test_within_grace_period_is_free(self):
        self.assertEqual(fee(date(2026, 10, 17), grace_days=3).amount, Decimal("0.00"))

    def test_maximum_fee_caps_amount(self):
        quote = fee(date(2026, 12, 31))
        self.assertTrue(quote.capped)
        self.assertEqual(quote.amount, Decimal("1000.00"))

    def test_zero_maximum_means_no_cap(self):
        quote = fee(date(2026, 12, 31), max_fee=Decimal("0"))
        self.assertFalse(quote.capped)
        self.assertEqual(quote.amount, Decimal("50") * 77)

    def test_fractional_fee_is_rounded_to_cents(self):
        self.assertEqual(fee(date(2026, 10, 18), per_day=Decimal("12.345")).amount, Decimal("37.04"))
