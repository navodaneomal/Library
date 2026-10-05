"""Reservation queue — strictly first come, first served.

Invariant: a copy is only ever AVAILABLE when nobody is waiting for its
title. Whenever a copy becomes free (returned, repaired, newly added, hold
expired or cancelled) `allocate_copy` offers it to the first member in the
queue and notifies them; otherwise it goes back on the shelf.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.catalogue.models import UNUSABLE_CONDITIONS, Book, BookCopy, CopyStatus
from apps.core import audit
from apps.core.config import library_settings
from apps.members.models import MemberStatus

from .models import (
    OPEN_RESERVATION_STATUSES,
    Loan,
    LoanStatus,
    Reservation,
    ReservationStatus,
)


class ReservationError(Exception):
    pass


def _end_of_day(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(23, 59, 59)))


def hold_expiry(now: datetime | None = None, settings=None) -> datetime:
    s = settings or library_settings()
    today = timezone.localdate(now or timezone.now())
    return _end_of_day(today + timedelta(days=s.reservation_hold_days))


def waiting_expiry(now: datetime | None = None, settings=None) -> datetime | None:
    s = settings or library_settings()
    if not s.reservation_waiting_expiry_days:
        return None
    today = timezone.localdate(now or timezone.now())
    return _end_of_day(today + timedelta(days=s.reservation_waiting_expiry_days))


def _make_ready(reservation: Reservation, copy: BookCopy, *, picked: bool, now: datetime) -> Reservation:
    from apps.notifications import notify

    reservation.status = ReservationStatus.READY
    reservation.copy = copy
    reservation.ready_at = now
    reservation.picked_at = now if picked else None
    reservation.expires_at = hold_expiry(now)
    reservation.expiry_notified = False
    reservation.save()
    copy.status = CopyStatus.RESERVED
    copy.save(update_fields=["status", "updated_at"])
    notify.reservation_ready(reservation)
    return reservation


@transaction.atomic
def allocate_copy(copy: BookCopy, *, picked: bool = False, now: datetime | None = None) -> Reservation | None:
    """Offer a newly free copy to the first eligible member in the queue.

    Returns the reservation that received the copy, or None if the copy went
    back on the shelf. `picked` means the copy is already at the desk (e.g.
    just returned), so it can go straight onto the hold shelf.
    """
    now = now or timezone.now()
    copy = BookCopy.objects.select_for_update().select_related("book").get(pk=copy.pk)
    if copy.condition in UNUSABLE_CONDITIONS or copy.status not in (CopyStatus.AVAILABLE, CopyStatus.RESERVED):
        return None
    if Reservation.objects.filter(copy=copy, status=ReservationStatus.READY).exists():
        return None  # already held for someone
    if not copy.book.reference_only and not copy.book.is_archived:
        queue = (
            Reservation.objects.select_for_update()
            .filter(book=copy.book, status=ReservationStatus.WAITING)
            .select_related("member")
            .order_by("created_at", "id")
        )
        for reservation in queue:
            if reservation.member.status != MemberStatus.ACTIVE:
                continue  # keeps their place; skipped while not active
            return _make_ready(reservation, copy, picked=picked, now=now)
    if copy.status != CopyStatus.AVAILABLE:
        copy.status = CopyStatus.AVAILABLE
        copy.save(update_fields=["status", "updated_at"])
    return None


def loanable_copies(book: Book):
    return book.copies.exclude(status__in=[CopyStatus.WITHDRAWN, CopyStatus.LOST]).exclude(
        condition__in=UNUSABLE_CONDITIONS
    )


def reservation_problems(member, book: Book, settings=None) -> list[str]:
    s = settings or library_settings()
    problems = []
    if member.status != MemberStatus.ACTIVE:
        problems.append(f"Your membership is {member.get_status_display().lower()}. Please contact the library.")
    if s.require_email_verification and member.user.email and not member.user.email_verified:
        problems.append("Please verify your email address before reserving books.")
    if book.is_archived:
        problems.append("This book is no longer in the catalogue.")
    if book.reference_only:
        problems.append("Reference books are for in-library use and can't be reserved.")
    elif not loanable_copies(book).exists():
        problems.append("This book has no copies that can be lent at the moment.")
    if Reservation.objects.filter(member=member, book=book, status__in=OPEN_RESERVATION_STATUSES).exists():
        problems.append("You already have an active reservation for this book.")
    if (
        not s.allow_duplicate_title_loans
        and Loan.objects.filter(member=member, copy__book=book, status=LoanStatus.ACTIVE).exists()
    ):
        problems.append("You already have this book on loan.")
    open_count = Reservation.objects.filter(member=member, status__in=OPEN_RESERVATION_STATUSES).count()
    if open_count >= s.max_reservations_per_member:
        problems.append(f"You can have at most {s.max_reservations_per_member} active reservations.")
    return problems


@transaction.atomic
def reserve(member, book: Book, *, actor=None) -> Reservation:
    problems = reservation_problems(member, book)
    if problems:
        raise ReservationError(problems[0])
    now = timezone.now()
    try:
        with transaction.atomic():
            reservation = Reservation.objects.create(
                book=book,
                member=member,
                status=ReservationStatus.WAITING,
                created_at=now,
                expires_at=waiting_expiry(now),
            )
    except IntegrityError:
        raise ReservationError("You already have an active reservation for this book.") from None

    available = (
        BookCopy.objects.select_for_update()
        .filter(book=book, status=CopyStatus.AVAILABLE)
        .exclude(condition__in=UNUSABLE_CONDITIONS)
        .order_by("copy_number")
        .first()
    )
    if (
        available
        and not Reservation.objects.filter(
            book=book, status=ReservationStatus.WAITING, created_at__lt=reservation.created_at
        ).exists()
    ):
        _make_ready(reservation, available, picked=False, now=now)
    if actor is not None and actor.is_staff_member:
        audit.record(actor, "reservation.created", f"Reserved '{book.title}' for {member}", target=reservation)
    return reservation


@transaction.atomic
def cancel(reservation: Reservation, *, actor=None, reason: str = "") -> Reservation:
    reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
    if not reservation.is_open:
        raise ReservationError("This reservation is no longer active.")
    held = reservation.copy if reservation.status == ReservationStatus.READY else None
    was_picked = bool(reservation.picked_at)
    reservation.status = ReservationStatus.CANCELLED
    reservation.closed_at = timezone.now()
    reservation.cancelled_by = actor
    reservation.save()
    if held:
        allocate_copy(held, picked=was_picked)
    if actor is not None and actor.is_staff_member:
        audit.record(
            actor,
            "reservation.cancelled",
            f"Cancelled reservation of '{reservation.book.title}' for {reservation.member}"
            + (f" — {reason}" if reason else ""),
            target=reservation,
        )
    return reservation


@transaction.atomic
def mark_picked(reservation: Reservation, *, actor) -> Reservation:
    reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
    if reservation.status == ReservationStatus.READY and not reservation.picked_at:
        reservation.picked_at = timezone.now()
        reservation.save(update_fields=["picked_at"])
    return reservation


@transaction.atomic
def copy_became_unavailable(copy: BookCopy) -> None:
    """A held copy went missing/damaged: move the hold to another available
    copy, or put the member back at the front of the queue."""
    reservation = Reservation.objects.select_for_update().filter(copy=copy, status=ReservationStatus.READY).first()
    if not reservation:
        return
    replacement = (
        BookCopy.objects.select_for_update()
        .filter(book=copy.book, status=CopyStatus.AVAILABLE)
        .exclude(condition__in=UNUSABLE_CONDITIONS)
        .exclude(pk=copy.pk)
        .order_by("copy_number")
        .first()
    )
    if replacement:
        reservation.copy = replacement
        reservation.picked_at = None
        reservation.save(update_fields=["copy", "picked_at"])
        replacement.status = CopyStatus.RESERVED
        replacement.save(update_fields=["status", "updated_at"])
    else:
        reservation.status = ReservationStatus.WAITING
        reservation.copy = None
        reservation.ready_at = None
        reservation.picked_at = None
        reservation.expires_at = None
        reservation.save()


def expire_reservations(now: datetime | None = None) -> dict:
    """Close holds that were not collected in time and pass the copy on to
    the next member (FIFO). Also expires long-waiting reservations if the
    library has configured that."""
    from apps.notifications import notify

    now = now or timezone.now()
    expired_holds = expired_waiting = 0
    for pk in Reservation.objects.filter(status=ReservationStatus.READY, expires_at__lt=now).values_list(
        "pk", flat=True
    ):
        with transaction.atomic():
            reservation = Reservation.objects.select_for_update().select_related("book", "member__user").get(pk=pk)
            if reservation.status != ReservationStatus.READY:
                continue
            held, was_picked = reservation.copy, bool(reservation.picked_at)
            reservation.status = ReservationStatus.EXPIRED
            reservation.closed_at = now
            reservation.save()
            notify.reservation_expired(reservation)
            if held:
                allocate_copy(held, picked=was_picked, now=now)
            expired_holds += 1
    for pk in Reservation.objects.filter(
        status=ReservationStatus.WAITING, expires_at__isnull=False, expires_at__lt=now
    ).values_list("pk", flat=True):
        with transaction.atomic():
            reservation = Reservation.objects.select_for_update().select_related("book", "member__user").get(pk=pk)
            if reservation.status != ReservationStatus.WAITING:
                continue
            reservation.status = ReservationStatus.EXPIRED
            reservation.closed_at = now
            reservation.save()
            notify.reservation_expired(reservation)
            expired_waiting += 1
    return {"expired_holds": expired_holds, "expired_waiting": expired_waiting}


def notify_expiring_holds(now: datetime | None = None) -> int:
    """Remind members whose hold expires within the next day."""
    from apps.notifications import notify

    now = now or timezone.now()
    soon = now + timedelta(hours=24)
    count = 0
    for reservation in Reservation.objects.filter(
        status=ReservationStatus.READY, expires_at__gte=now, expires_at__lte=soon, expiry_notified=False
    ).select_related("book", "member__user"):
        notify.reservation_expiring(reservation)
        reservation.expiry_notified = True
        reservation.save(update_fields=["expiry_notified"])
        count += 1
    return count


# ---------------------------------------------------------------------------
# Queue information shown to members
# ---------------------------------------------------------------------------


def queue_position(reservation: Reservation) -> int | None:
    if reservation.status != ReservationStatus.WAITING:
        return None
    ahead = (
        Reservation.objects.filter(book_id=reservation.book_id, status=ReservationStatus.WAITING)
        .filter(created_at__lt=reservation.created_at)
        .count()
    )
    same_time = Reservation.objects.filter(
        book_id=reservation.book_id,
        status=ReservationStatus.WAITING,
        created_at=reservation.created_at,
        id__lt=reservation.id,
    ).count()
    return ahead + same_time + 1


@dataclass(frozen=True)
class Estimate:
    date: date | None
    text: str


def estimated_availability(reservation: Reservation, *, today: date | None = None) -> Estimate:
    """Rough estimate from the due dates of copies currently on loan."""
    today = today or timezone.localdate()
    if reservation.status == ReservationStatus.READY:
        return Estimate(today, "Ready now")
    if reservation.status != ReservationStatus.WAITING:
        return Estimate(None, "—")
    position = queue_position(reservation) or 1
    due_dates = list(
        Loan.objects.filter(copy__book_id=reservation.book_id, status=LoanStatus.ACTIVE)
        .order_by("due_date")
        .values_list("due_date", flat=True)
    )
    if not due_dates:
        return Estimate(None, "We'll notify you as soon as a copy is free")
    # Each returned copy serves one person in the queue; later positions
    # wait for later returns (cycling through copies for long queues).
    copies = len(due_dates)
    cycle, index = divmod(position - 1, copies)
    loan_days = library_settings().loan_period_days
    estimate = due_dates[index] + timedelta(days=cycle * loan_days)
    if estimate <= today:
        return Estimate(today, "Any day now")
    return Estimate(estimate, f"Around {estimate.day} {estimate:%B}")
