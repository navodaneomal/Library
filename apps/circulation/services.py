"""Borrow, return, renew and lost-book workflows.

All state changes happen inside database transactions with row locks so two
desks scanning the same copy at the same moment cannot both succeed.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.catalogue.models import Book, BookCopy, CopyCondition, CopyStatus
from apps.core import audit
from apps.core.config import library_settings
from apps.core.templatetags.library import money
from apps.fees.engine import FeeQuote, quote_for_loan
from apps.fees.models import Fee, FeeKind
from apps.members.models import Member

from . import reservations as reservation_service
from .models import Loan, LoanStatus, Reservation, ReservationStatus
from .rules import BorrowCheck, Problem, check_borrow, renewal_problems


class CirculationError(Exception):
    def __init__(self, message: str, problems: list[Problem] | None = None):
        super().__init__(message)
        self.message = message
        self.problems = problems or []


# ---------------------------------------------------------------------------
# Borrow
# ---------------------------------------------------------------------------


@transaction.atomic
def borrow(member: Member, copy: BookCopy, *, staff, now: datetime | None = None) -> Loan:
    now = now or timezone.now()
    today = timezone.localdate(now)
    member = Member.objects.select_for_update().select_related("user").get(pk=member.pk)
    copy = BookCopy.objects.select_for_update().select_related("book").get(pk=copy.pk)
    check: BorrowCheck = check_borrow(member, copy, today=today)
    if not check.ok:
        raise CirculationError(check.first_problem, check.blocks)

    try:
        with transaction.atomic():
            loan = Loan.objects.create(
                copy=copy, member=member, borrowed_at=now, due_date=check.due_date, issued_by=staff
            )
    except IntegrityError:
        raise CirculationError("This copy was just checked out at another desk.") from None
    copy.status = CopyStatus.BORROWED
    copy.save(update_fields=["status", "updated_at"])
    Book.objects.filter(pk=copy.book_id).update(times_borrowed=F("times_borrowed") + 1)

    reservation = check.reservation
    if reservation is not None:
        reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
        held = reservation.copy if reservation.status == ReservationStatus.READY else None
        was_picked = bool(reservation.picked_at)
        reservation.status = ReservationStatus.COLLECTED
        reservation.closed_at = now
        reservation.loan = loan
        reservation.save()
        if held and held.pk != copy.pk:
            # Member took a different copy from the shelf — release the held one.
            reservation_service.allocate_copy(held, picked=was_picked, now=now)

    audit.record(
        staff,
        "loan.created",
        f"Lent '{copy.book.title}' ({copy.copy_code}) to {member.user.full_name}, due {loan.due_date:%d %b %Y}",
        target=loan,
    )
    return loan


# ---------------------------------------------------------------------------
# Return
# ---------------------------------------------------------------------------


@dataclass
class ReturnResult:
    loan: Loan
    copy: BookCopy
    borrowed_on: date
    due_date: date
    returned_on: date
    days_borrowed: int
    quote: FeeQuote
    fee: Fee | None
    hold: Reservation | None

    @property
    def days_overdue(self) -> int:
        return self.quote.days_overdue


def active_loan_for(copy: BookCopy) -> Loan | None:
    return Loan.objects.filter(copy=copy, status=LoanStatus.ACTIVE).select_related("member__user").first()


@transaction.atomic
def return_copy(copy: BookCopy, *, staff, now: datetime | None = None) -> ReturnResult:
    from apps.notifications import notify

    now = now or timezone.now()
    copy = BookCopy.objects.select_for_update().select_related("book").get(pk=copy.pk)
    loan = (
        Loan.objects.select_for_update()
        .filter(copy=copy, status=LoanStatus.ACTIVE)
        .select_related("member__user")
        .first()
    )
    if loan is None:
        raise CirculationError("This copy isn't on loan.")

    returned_on = timezone.localdate(now)
    loan.returned_at = now
    loan.returned_by = staff
    loan.status = LoanStatus.RETURNED
    loan.save()

    quote = quote_for_loan(loan, on=returned_on)
    fee = None
    if quote.amount > 0:
        fee = Fee.objects.create(
            member=loan.member,
            loan=loan,
            kind=FeeKind.LATE,
            amount=quote.amount,
            days_overdue=quote.days_overdue,
            description=f"Late return of '{copy.book.title}' ({quote.days_overdue} days overdue)",
            created_by=staff,
            created_at=now,
        )
        notify.fee_generated(fee)

    hold = None
    if copy.condition in (CopyCondition.DAMAGED, CopyCondition.LOST):
        copy.status = CopyStatus.DAMAGED
        copy.save(update_fields=["status", "updated_at"])
    else:
        copy.status = CopyStatus.AVAILABLE
        copy.save(update_fields=["status", "updated_at"])
        hold = reservation_service.allocate_copy(copy, picked=True, now=now)

    audit.record(
        staff,
        "loan.returned",
        f"Returned '{copy.book.title}' ({copy.copy_code}) from {loan.member.user.full_name}"
        + (f" — late fee {money(fee.amount)}" if fee else ""),
        target=loan,
    )
    copy.refresh_from_db()
    return ReturnResult(
        loan=loan,
        copy=copy,
        borrowed_on=loan.borrowed_on,
        due_date=loan.due_date,
        returned_on=returned_on,
        days_borrowed=max(0, (returned_on - loan.borrowed_on).days),
        quote=quote,
        fee=fee,
        hold=hold,
    )


# ---------------------------------------------------------------------------
# Renew
# ---------------------------------------------------------------------------


@transaction.atomic
def renew(loan: Loan, *, actor, today: date | None = None) -> Loan:
    today = today or timezone.localdate()
    loan = Loan.objects.select_for_update().select_related("copy__book", "member").get(pk=loan.pk)
    problems = renewal_problems(loan, today=today)
    if problems:
        raise CirculationError(problems[0].message, problems)
    s = library_settings()
    old_due = loan.due_date
    loan.due_date = max(loan.due_date, today) + timedelta(days=s.renewal_days)
    loan.renew_count += 1
    loan.save(update_fields=["due_date", "renew_count"])
    audit.record(
        actor,
        "loan.renewed",
        f"Renewed '{loan.copy.book.title}' for {loan.member.user.full_name}: "
        f"{old_due:%d %b} → {loan.due_date:%d %b %Y}",
        target=loan,
    )
    return loan


# ---------------------------------------------------------------------------
# Lost
# ---------------------------------------------------------------------------


@transaction.atomic
def mark_lost(loan: Loan, *, staff, now: datetime | None = None) -> list[Fee]:
    """Book marked lost → loan closed → replacement fee → member notified."""
    from apps.notifications import notify

    now = now or timezone.now()
    loan = Loan.objects.select_for_update().select_related("copy__book", "member__user").get(pk=loan.pk)
    if loan.status != LoanStatus.ACTIVE:
        raise CirculationError("Only books currently on loan can be marked as lost.")
    copy = BookCopy.objects.select_for_update().get(pk=loan.copy_id)
    book = copy.book
    today = timezone.localdate(now)

    loan.status = LoanStatus.LOST
    loan.returned_at = now
    loan.returned_by = staff
    loan.save()
    copy.status = CopyStatus.LOST
    copy.condition = CopyCondition.LOST
    copy.save(update_fields=["status", "condition", "updated_at"])

    fees = []
    quote = quote_for_loan(loan, on=today)
    if quote.amount > 0:
        fees.append(
            Fee.objects.create(
                member=loan.member,
                loan=loan,
                kind=FeeKind.LATE,
                amount=quote.amount,
                days_overdue=quote.days_overdue,
                description=f"Late fee for '{book.title}' up to the day it was reported lost",
                created_by=staff,
                created_at=now,
            )
        )
    replacement = (
        book.replacement_cost if book.replacement_cost is not None else library_settings().default_replacement_fee
    )
    if replacement and replacement > Decimal("0"):
        fees.append(
            Fee.objects.create(
                member=loan.member,
                loan=loan,
                kind=FeeKind.REPLACEMENT,
                amount=replacement,
                description=f"Replacement of lost book '{book.title}'",
                created_by=staff,
                created_at=now,
            )
        )
    notify.book_lost(loan, fees)
    audit.record(
        staff,
        "loan.lost",
        f"Marked '{book.title}' ({copy.copy_code}) as lost by {loan.member.user.full_name}",
        target=loan,
    )
    return fees


# ---------------------------------------------------------------------------
# Copy status changes made by staff
# ---------------------------------------------------------------------------

STAFF_SETTABLE_STATUSES = (
    CopyStatus.AVAILABLE,
    CopyStatus.MAINTENANCE,
    CopyStatus.DAMAGED,
    CopyStatus.LOST,
    CopyStatus.WITHDRAWN,
)


@transaction.atomic
def change_copy_state(copy: BookCopy, *, status: str, condition: str, actor) -> BookCopy:
    """Apply a status/condition edit while keeping holds and the queue consistent."""
    copy = BookCopy.objects.select_for_update().select_related("book").get(pk=copy.pk)
    old_status, old_condition = copy.status, copy.condition
    on_loan = copy.status == CopyStatus.BORROWED

    if condition == CopyCondition.LOST and not on_loan:
        status = CopyStatus.LOST
    elif condition == CopyCondition.DAMAGED and status in (CopyStatus.AVAILABLE, CopyStatus.RESERVED):
        status = CopyStatus.DAMAGED

    if on_loan:
        if status != CopyStatus.BORROWED and status != old_status:
            raise CirculationError("This copy is on loan. Return it (or mark the loan as lost) first.")
        status = CopyStatus.BORROWED
    elif status == old_status:
        pass
    elif (
        old_status == CopyStatus.RESERVED
        and status == CopyStatus.AVAILABLE
        and condition
        not in (
            CopyCondition.DAMAGED,
            CopyCondition.LOST,
        )
    ):
        status = CopyStatus.RESERVED  # still on hold — nothing to change
    elif status not in STAFF_SETTABLE_STATUSES:
        raise CirculationError("That status can't be set by hand.")

    copy.status = status
    copy.condition = condition
    copy.save(update_fields=["status", "condition", "updated_at"])

    if old_status == CopyStatus.RESERVED and status != CopyStatus.RESERVED:
        reservation_service.copy_became_unavailable(copy)
    if status == CopyStatus.AVAILABLE and old_status != CopyStatus.AVAILABLE:
        reservation_service.allocate_copy(copy)

    if (old_status, old_condition) != (status, condition):
        audit.record(
            actor,
            "copy.status_changed",
            f"{copy.book.title} {copy.label}: {old_status}/{old_condition} → {status}/{condition}",
            target=copy,
            changes={"status": [old_status, status], "condition": [old_condition, condition]},
        )
    return copy
