"""Loan-period and due-date policy."""

from datetime import date, timedelta

from apps.core.config import library_settings


def loan_period_for(book, settings=None) -> int:
    s = settings or library_settings()
    return book.loan_period_days or s.loan_period_days


def due_date_for(book, borrowed_on: date, settings=None) -> date:
    return borrowed_on + timedelta(days=loan_period_for(book, settings))
