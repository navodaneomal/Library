"""The late fee engine — pure, deterministic and fully configurable.

    overdue days    = days between the due date and the return (or today)
    chargeable days = overdue days − grace period (never below zero)
    fee             = chargeable days × fee per day, capped at the maximum

A book returned on its due date is never charged. While a book is still out
the result is an *estimated* fee; on return it becomes the *final* fee.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

CENT = Decimal("0.01")


@dataclass(frozen=True)
class FeeQuote:
    due_date: date
    on_date: date
    days_overdue: int
    chargeable_days: int
    per_day: Decimal
    amount: Decimal
    capped: bool

    @property
    def is_overdue(self) -> bool:
        return self.days_overdue > 0


def quote_late_fee(
    due_date: date, on_date: date, *, per_day: Decimal, grace_days: int = 0, max_fee: Decimal | None = None
) -> FeeQuote:
    per_day = Decimal(per_day)
    days_overdue = max(0, (on_date - due_date).days)
    chargeable = max(0, days_overdue - max(0, grace_days))
    amount = (per_day * chargeable).quantize(CENT, rounding=ROUND_HALF_UP)
    capped = False
    if max_fee and Decimal(max_fee) > 0 and amount > Decimal(max_fee):
        amount = Decimal(max_fee).quantize(CENT)
        capped = True
    return FeeQuote(due_date, on_date, days_overdue, chargeable, per_day, amount, capped)


def quote_for_loan(loan, *, on: date | None = None, settings=None) -> FeeQuote:
    from apps.core.config import library_settings

    s = settings or library_settings()
    on = on or loan.returned_on or timezone.localdate()
    return quote_late_fee(
        loan.due_date,
        on,
        per_day=s.late_fee_per_day,
        grace_days=s.grace_period_days,
        max_fee=s.max_late_fee,
    )
