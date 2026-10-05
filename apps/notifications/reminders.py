"""Due-soon and overdue reminders. Safe to run as often as you like:
dedupe keys guarantee each reminder is sent once."""

from datetime import timedelta

from django.utils import timezone

from apps.circulation.models import Loan, LoanStatus
from apps.core.config import library_settings

from . import notify


def send_due_soon(today=None) -> int:
    s = library_settings()
    if not s.reminders_enabled or not s.due_soon_reminder_days:
        return 0
    today = today or timezone.localdate()
    loans = Loan.objects.filter(
        status=LoanStatus.ACTIVE,
        due_date__gte=today,
        due_date__lte=today + timedelta(days=s.due_soon_reminder_days),
    ).select_related("member__user", "copy__book")
    count = 0
    for loan in loans:
        key = f"due-soon:{loan.pk}:{loan.due_date.isoformat()}"
        notify.due_soon(loan, key)
        count += 1
    return count


def overdue_key(loan, days_overdue: int, interval: int) -> str:
    """First notice on day 1, then again every `interval` days."""
    round_number = (days_overdue - 1) // max(1, interval)
    return f"overdue:{loan.pk}:{loan.due_date.isoformat()}:{round_number}"


def send_overdue(today=None) -> int:
    s = library_settings()
    if not s.reminders_enabled:
        return 0
    today = today or timezone.localdate()
    count = 0
    for loan in Loan.objects.overdue(today).select_related("member__user", "copy__book"):
        quote = loan.fee_quote(on=today)
        notify.overdue(loan, quote, overdue_key(loan, quote.days_overdue, s.overdue_reminder_interval_days))
        count += 1
    return count


def send_manual_overdue(loan, *, today=None):
    """Staff pressed 'Send reminder' — always sends (unique key per press)."""
    today = today or timezone.localdate()
    quote = loan.fee_quote(on=today)
    notify.overdue(loan, quote, f"overdue-manual:{loan.pk}:{timezone.now():%Y%m%d%H%M%S%f}")
