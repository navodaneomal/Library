"""Borrowing rules. Every check returns human-readable reasons so staff and
members always know exactly *why* something is blocked."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.catalogue.models import CopyCondition, CopyStatus
from apps.core.config import library_settings
from apps.core.templatetags.library import money
from apps.fees.models import FeeStatus

from .models import Loan, LoanStatus, Reservation, ReservationStatus
from .policy import due_date_for


@dataclass(frozen=True)
class Problem:
    code: str
    message: str


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


@dataclass
class Standing:
    """A member's current position: what they have, what they owe, and any
    account-level reasons they cannot borrow right now."""

    member: object
    active_loans: list = field(default_factory=list)
    overdue_loans: list = field(default_factory=list)
    outstanding_fees: Decimal = Decimal("0.00")
    estimated_fees: Decimal = Decimal("0.00")
    ready_reservations: list = field(default_factory=list)
    waiting_reservations: int = 0
    blocks: list[Problem] = field(default_factory=list)

    @property
    def can_borrow(self) -> bool:
        return not self.blocks


def outstanding_fees_for(member) -> Decimal:
    totals = member.fees.filter(status=FeeStatus.PENDING).aggregate(total=Sum("amount"), paid=Sum("amount_paid"))
    return (totals["total"] or Decimal("0")) - (totals["paid"] or Decimal("0"))


def member_standing(member, *, today: date | None = None, settings=None) -> Standing:
    s = settings or library_settings()
    today = today or timezone.localdate()
    loans = list(
        Loan.objects.filter(member=member, status=LoanStatus.ACTIVE)
        .select_related("copy__book", "copy__book__category")
        .order_by("due_date")
    )
    standing = Standing(member=member, active_loans=loans)
    standing.overdue_loans = [loan for loan in loans if loan.due_date < today]
    standing.outstanding_fees = outstanding_fees_for(member)
    standing.estimated_fees = sum((loan.fee_quote(on=today).amount for loan in standing.overdue_loans), Decimal("0"))
    reservations = Reservation.objects.filter(
        member=member, status__in=[ReservationStatus.WAITING, ReservationStatus.READY]
    ).select_related("book", "copy")
    standing.ready_reservations = [r for r in reservations if r.status == ReservationStatus.READY]
    standing.waiting_reservations = sum(1 for r in reservations if r.status == ReservationStatus.WAITING)

    if member.status == "suspended":
        reason = f" Reason: {member.status_reason}" if member.status_reason else ""
        standing.blocks.append(Problem("suspended", f"This membership is suspended.{reason}"))
    elif member.status == "inactive":
        standing.blocks.append(Problem("inactive", "This membership is inactive."))
    if s.block_on_overdue and standing.overdue_loans:
        n = len(standing.overdue_loans)
        standing.blocks.append(Problem("overdue", f"{_plural(n, 'overdue book')} must be returned first."))
    if standing.outstanding_fees > 0 and standing.outstanding_fees > s.fee_block_threshold:
        if s.fee_block_threshold > 0:
            message = (
                f"Unpaid fees of {money(standing.outstanding_fees)} exceed the {money(s.fee_block_threshold)} limit."
            )
        else:
            message = f"Unpaid fees of {money(standing.outstanding_fees)} must be settled first."
        standing.blocks.append(Problem("fees", message))
    if len(loans) >= s.max_active_loans:
        standing.blocks.append(
            Problem("limit", f"Borrowing limit reached ({len(loans)} of {s.max_active_loans} books).")
        )
    return standing


@dataclass
class BorrowCheck:
    member: object
    copy: object
    standing: Standing
    blocks: list[Problem]
    due_date: date | None
    reservation: Reservation | None = None

    @property
    def ok(self) -> bool:
        return not self.blocks

    @property
    def first_problem(self) -> str:
        return self.blocks[0].message if self.blocks else ""


COPY_STATE_PROBLEMS = {
    CopyStatus.MAINTENANCE: Problem("maintenance", "This copy is in maintenance."),
    CopyStatus.DAMAGED: Problem("damaged", "This copy is marked as damaged."),
    CopyStatus.LOST: Problem("lost", "This copy is recorded as lost."),
    CopyStatus.WITHDRAWN: Problem("withdrawn", "This copy has been withdrawn from circulation."),
}


def check_borrow(member, copy, *, today: date | None = None, settings=None) -> BorrowCheck:
    s = settings or library_settings()
    today = today or timezone.localdate()
    standing = member_standing(member, today=today, settings=s)
    blocks = list(standing.blocks)
    book = copy.book
    reservation = (
        Reservation.objects.filter(
            member=member, book=book, status__in=[ReservationStatus.WAITING, ReservationStatus.READY]
        )
        .select_related("copy")
        .first()
    )

    if book.reference_only:
        blocks.append(Problem("reference", "Reference only — this book can't be borrowed."))
    if book.is_archived:
        blocks.append(Problem("archived", "This book has been archived."))

    if copy.status == CopyStatus.BORROWED:
        active = Loan.objects.filter(copy=copy, status=LoanStatus.ACTIVE).first()
        if active and active.member_id == member.pk:
            blocks.append(
                Problem("own_copy", "This member already has this copy. Scan it on the Return tab to check it in.")
            )
        else:
            due = f" (due {active.due_date:%d %b %Y})" if active else ""
            blocks.append(Problem("on_loan", f"This copy is already on loan{due}. Return it first."))
    elif copy.status == CopyStatus.RESERVED:
        hold = Reservation.objects.filter(copy=copy, status=ReservationStatus.READY).first()
        if not hold or hold.member_id != member.pk:
            blocks.append(Problem("held", "This copy is on hold for another member."))
    elif copy.status in COPY_STATE_PROBLEMS:
        blocks.append(COPY_STATE_PROBLEMS[copy.status])

    if copy.condition in (CopyCondition.DAMAGED, CopyCondition.LOST) and copy.status not in COPY_STATE_PROBLEMS:
        blocks.append(Problem("condition", f"This copy's condition is '{copy.get_condition_display()}'."))

    if not s.allow_duplicate_title_loans and any(
        loan.copy.book_id == book.pk and loan.copy_id != copy.pk for loan in standing.active_loans
    ):
        blocks.append(Problem("duplicate", "This member already has a copy of this book on loan."))

    # Remove duplicate codes while keeping order.
    seen, unique = set(), []
    for problem in blocks:
        if problem.code not in seen:
            seen.add(problem.code)
            unique.append(problem)

    return BorrowCheck(
        member=member,
        copy=copy,
        standing=standing,
        blocks=unique,
        due_date=due_date_for(book, today, s) if not unique else None,
        reservation=reservation,
    )


def renewal_problems(loan, *, today: date | None = None, settings=None) -> list[Problem]:
    s = settings or library_settings()
    today = today or timezone.localdate()
    problems = []
    if loan.status != LoanStatus.ACTIVE:
        return [Problem("closed", "This loan is no longer active.")]
    if s.max_renewals == 0:
        problems.append(Problem("disabled", "Renewals are not available at this library."))
    elif loan.renew_count >= s.max_renewals:
        problems.append(Problem("max", f"This book has already been renewed {_plural(loan.renew_count, 'time')}."))
    if loan.due_date < today:
        problems.append(Problem("overdue", "Overdue books can't be renewed — please return it."))
    if Reservation.objects.filter(book_id=loan.copy.book_id, status=ReservationStatus.WAITING).exists():
        problems.append(Problem("reserved", "Another member is waiting for this book."))
    if loan.member.status != "active":
        problems.append(Problem("member", "This membership is not active."))
    return problems
